"""matrixctl: read and drive the HDMI matrix from a terminal (run it as bin/matrixctl).

    matrixctl list                     the matrices in the add-on's settings
    matrixctl [MATRIX] status [--json] routing, monitors detected, input signal, EDID
    matrixctl route IN OUT             show input IN on output OUT (names or numbers)
    matrixctl edid IN OPTION           set an input's EDID (matrixctl edid --list)
    matrixctl preset N | preset save N recall / save a routing preset
    matrixctl wake OUT                 turn an output's signal off and on (a monitor that won't wake)
    matrixctl rename input|output N NAME

Which matrix: MATRIX (an entry's name in ~/Hisho/System/Config/Addons/hdmi-matrix.yaml), else
--address / $MATRIXCTL_ADDRESS (with --driver), else the only entry there is. Every write says what it
will do and asks first; --yes answers for scripts. The web UI password, when the entry has one, is
resolved by Hisho (Infisical / Bitwarden) at the moment of a write and never printed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location("hisho_addon_hdmi_matrix_client", Path(__file__).with_name("matrix.py"))
mx = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(mx)

SETTINGS = Path(os.environ.get("HISHO_ROOT") or "~/Hisho").expanduser() / "System/Config/Addons/hdmi-matrix.yaml"


COMMANDS = ("list", "status", "route", "wake", "edid", "preset", "rename")


def _entries() -> dict[str, dict[str, Any]]:
    try:
        import yaml
        return (yaml.safe_load(SETTINGS.read_text()) or {}).get("entries") or {}
    except Exception:  # noqa: BLE001 -- no Hisho here: --address or $MATRIXCTL_ADDRESS
        return {}


def _entry(name: str | None, address: str | None) -> tuple[str, dict[str, Any]]:
    entries = _entries()
    if name:
        if name not in entries:
            raise SystemExit(f"no matrix {name!r} in {SETTINGS} (have: {', '.join(entries) or 'none'})")
        return name, entries[name]
    if address:
        return address, {}
    if len(entries) == 1:
        return next(iter(entries.items()))
    raise SystemExit(f"which matrix? matrixctl NAME ... ({', '.join(entries)})" if entries else
                     f"which matrix? --address, $MATRIXCTL_ADDRESS, or an entry in {SETTINGS}")


def _secret(ref: str) -> str:
    try:
        from hisho.power import SecretResolver
    except ImportError:
        raise mx.MatrixError(f"the entry's password is the secret {ref}; run matrixctl with Hisho's venv to "
                             "resolve it") from None
    return SecretResolver().resolve(ref)


def _journal(meta: dict[str, Any]) -> None:
    try:
        from hisho.jobs import append_event
        append_event(Path(os.environ.get("HISHO_ROOT") or "~/Hisho").expanduser(), "hdmi-matrix-action",
                     {"addon": "hdmi-matrix", "by": "matrixctl", **meta})
    except Exception:  # noqa: BLE001 -- the journal is Hisho's; without it the change still happened
        pass


GLOBAL = ("--address", "--driver")


def _global_options(argv: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Take --yes, --address and --driver from anywhere on the line (``matrixctl main route 2 7 --yes``),
    so an option's value is never mistaken for the matrix's name."""
    rest, opts, i = [], {}, 0
    while i < len(argv):
        w, key = argv[i], argv[i].split("=", 1)[0]
        if w == "--yes":
            opts["yes"] = True
        elif key in GLOBAL:
            if "=" in w:
                opts[key[2:]] = w.split("=", 1)[1]
            elif i + 1 < len(argv):
                opts[key[2:]], i = argv[i + 1], i + 1
            else:
                raise SystemExit(f"matrixctl: {w} needs a value")
        else:
            rest.append(w)
        i += 1
    return rest, opts


def _confirm(what: str, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit(f"would {what}; not a terminal, so nothing sent (add --yes to send it)")
    if input(f"{what}? [y/N] ").strip().lower() not in ("y", "yes"):
        raise SystemExit("nothing sent")


def show(st: dict[str, Any], watch: set[int]) -> str:
    lines = [f"firmware {st['version']}" + (" · front panel LOCKED" if st["locked"] else ""), "",
             "OUTPUT            SHOWS            MONITOR   PICTURE"]
    for o in st["outputs"]:
        src = st["inputs"][o["source"] - 1] if o["source"] else None
        pic = "yes" if o["sink"] and src and src["signal"] else "no"
        lines.append(f"{o['n']} {o['name']:<15} {src['name'] if src else '?':<16} "
                     f"{'yes' if o['sink'] else 'NO':<9} {pic}" + ("   (watched)" if o["n"] in watch else ""))
    lines += ["", "INPUT             SIGNAL   EDID"]
    for i in st["inputs"]:
        lines.append(f"{i['n']} {i['name']:<15} {'yes' if i['signal'] else 'no':<8} {i['edid']:>2} {i['edid_name']}")
    for w in mx.edid_warnings(st):
        lines.append(f"! {w['input']['name']} copies its EDID from {w['output']['name']}, which has no monitor "
                     "detected: the matrix gives it the 1080p fallback EDID")
    lines += ["", "presets: " + ", ".join(f"{n} {p}" for n, p in enumerate(st["presets"], 1))]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="matrixctl", description=__doc__.split("\n\n")[0])
    p.add_argument("--address", default=os.environ.get("MATRIXCTL_ADDRESS"))
    p.add_argument("--driver", help=f"protocol with --address ({', '.join(mx.DRIVERS)})")
    p.add_argument("--yes", action="store_true", help="send writes without asking")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("route")
    r.add_argument("input"), r.add_argument("output")
    e = sub.add_parser("edid")
    e.add_argument("input", nargs="?"), e.add_argument("option", nargs="?")
    e.add_argument("--list", action="store_true")
    pr = sub.add_parser("preset")
    pr.add_argument("words", nargs="+", help="N to recall, or: save N")
    w = sub.add_parser("wake")
    w.add_argument("output")
    rn = sub.add_parser("rename")
    rn.add_argument("kind", choices=["input", "output"]), rn.add_argument("which"), rn.add_argument("name")
    argv, opts = _global_options(list(sys.argv[1:] if argv is None else argv))
    # matrixctl MATRIX COMMAND ...: the first word that isn't an option or a command names the matrix
    first = next((i for i, w in enumerate(argv) if not w.startswith("-")), None)
    device = argv.pop(first) if first is not None and argv[first] not in COMMANDS else None
    a = p.parse_args(argv)
    for k, v in opts.items():
        setattr(a, k, v)

    if a.cmd == "list":
        for n, e in _entries().items():
            print(f"{n:<12} {e.get('address', '?'):<16} {e.get('driver') or mx.DEFAULT_DRIVER:<12} {e.get('note', '')}")
        return 0
    name, entry = _entry(device, a.address)
    if not device and a.address:
        entry = {"address": a.address, "driver": a.driver}
    ref = entry.get("password")
    try:
        m = mx.connect(entry, password=(lambda: _secret(ref)) if ref else None)
    except mx.MatrixError as exc:
        raise SystemExit(f"matrixctl: {exc}")

    try:
        if a.cmd == "edid" and a.list:
            print("\n".join(f"{k:>2} {v}" for k, v in mx.EDID.items()))
            return 0
        st = m.status()
        if a.cmd == "status":
            watch = set()
            for w in str(entry.get("watch") or "").split(","):
                try:
                    watch.add(mx.find(st, "output", w)) if w.strip() else None
                except mx.MatrixError:
                    pass
            print(json.dumps(st, indent=1) if a.json else show(st, watch))
            return 0
        if a.cmd == "route":
            inp, out = mx.find(st, "input", a.input), mx.find(st, "output", a.output)
            o, i = st["outputs"][out - 1], st["inputs"][inp - 1]
            if o["source"] == inp:
                print(f"{o['name']} already shows {i['name']}")
                return 0
            what = f"switch {o['name']} (output {out}) from {o['source_name']} to {i['name']} (input {inp})"
            _confirm(what, a.yes)
            res = m.route(inp, out)
        elif a.cmd == "edid":
            if not (a.input and a.option):
                raise SystemExit("matrixctl edid IN OPTION (or --list)")
            inp, opt = mx.find(st, "input", a.input), mx.edid_option(a.option)
            i = st["inputs"][inp - 1]
            what = f"set {i['name']}'s EDID from {i['edid_name']} to {mx.EDID[opt]} (the PC re-detects its screen)"
            _confirm(what, a.yes)
            res = m.set_edid(inp, opt)
        elif a.cmd == "wake":
            out = mx.find(st, "output", a.output)
            what = (f"turn {st['outputs'][out - 1]['name']} (output {out})'s signal off and on "
                    "(its screen goes dark for a few seconds)")
            _confirm(what, a.yes)
            res = m.wake(out)
        elif a.cmd == "preset":
            save = a.words[0] == "save"
            n = a.words[-1]
            if not n.isdigit() or len(a.words) != (2 if save else 1):
                raise SystemExit("matrixctl preset N, or matrixctl preset save N")
            label = st["presets"][int(n) - 1] if 1 <= int(n) <= len(st["presets"]) else n
            what = (f"overwrite preset {n} ({label}) with the current routing" if save
                    else f"recall preset {n} ({label}): every output switches")
            _confirm(what, a.yes)
            res = m.save_preset(int(n)) if save else m.recall_preset(int(n))
        else:
            n = mx.find(st, a.kind, a.which)
            what = f"rename {a.kind} {n} from {st[a.kind + 's'][n - 1]['name']} to {a.name}"
            _confirm(what, a.yes)
            res = m.rename(a.kind, n, a.name)
    except mx.MatrixError as exc:
        print(f"matrixctl: {exc}", file=sys.stderr)
        return 1
    _journal({"entry": name, "command": what, "ok": res["ok"]})
    if not res["ok"]:
        print(f"sent, but the matrix still reports {res['now']} (does it need a login? set user and password "
              "on the add-on's entry)", file=sys.stderr)
        return 1
    print(f"done: {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
