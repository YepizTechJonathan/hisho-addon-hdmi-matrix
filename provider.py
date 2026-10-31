"""HDMI matrix integration for Hisho (see addon.yaml). Runs inside Core; talks to the matrix over HTTP
with matrix.py (next to this file), which only sends a short list of reads and writes.

summary() also journals what changed since the last check (routing, monitors detected, input signal,
EDID, names) as ``hdmi-matrix-change`` events; buttons pressed on the page are journaled by Core.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location("hisho_addon_hdmi_matrix_client", Path(__file__).with_name("matrix.py"))
mx = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(mx)


def client(entry: dict[str, Any], ctx: Any) -> Any:
    ref = entry.get("password")
    return mx.connect(entry, password=(lambda: ctx.secret(ref)) if ref else None)


def _watched(entry: dict[str, Any], st: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for w in str(entry.get("watch") or "").split(","):
        if w.strip():
            try:
                out.append(st["outputs"][mx.find(st, "output", w) - 1])
            except mx.MatrixError:
                pass
    return out


def _state_file(name: str) -> Path:
    base = Path(os.environ.get("HISHO_STATE_DIR") or "~/.local/state/hisho").expanduser()
    return base / "addons" / "hdmi-matrix" / f"{name}.json"


def journal_changes(name: str, st: dict[str, Any]) -> list[str]:
    """Compare with the last check and journal what changed. The first check only remembers."""
    path, now = _state_file(name), mx.snapshot(st)
    try:
        before = json.loads(path.read_text())
    except (OSError, ValueError):
        before = None
    said = mx.changes(before, now) if before else []
    if said:
        try:
            from hisho.jobs import append_event
            append_event(Path(os.environ.get("HISHO_ROOT") or "~/Hisho").expanduser(), "hdmi-matrix-change",
                         {"addon": "hdmi-matrix", "entry": name, "changes": said})
        except Exception:  # noqa: BLE001 -- journaling never breaks the card
            pass
    if before != now:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(now, indent=1))
        os.replace(tmp, path)
    return said


def _output_problem(o: dict[str, Any], st: dict[str, Any]) -> str | None:
    """A screen's problem worth an alert. A source PC that is off (no signal) is not one: it is off on purpose
    most of the time (Jonathan, 2026-10-31); the card still shows which inputs are live."""
    if not o["sink"]:
        return "no monitor detected (monitor off, or a bad cable / fiber run)"
    return None


def _lit(o: dict[str, Any], st: dict[str, Any]) -> bool:
    src = st["inputs"][o["source"] - 1] if o["source"] else None
    return bool(o["sink"]) and not (src and not src["signal"])


def summary(entries: dict[str, dict[str, Any]], ctx: Any) -> dict[str, Any]:
    if not entries:
        return {"state": "unknown", "headline": "No matrix yet", "problems": []}
    problems, heads = [], []
    for name, entry in entries.items():
        try:
            st = client(entry, ctx).status()
        except Exception as exc:  # noqa: BLE001
            problems.append({"entry": name, "severity": "warn", "title": f"Can't reach {name}",
                             "detail": f"{entry['address']}: {exc}"})
            continue
        journal_changes(name, st)
        lit = sum(1 for o in st["outputs"] if _lit(o, st))
        heads.append(f"{lit}/{sum(o['sink'] for o in st['outputs'])} screens lit"
                     f" · {sum(i['signal'] for i in st['inputs'])} inputs live")
        fallback = mx.edid_warnings(st)
        for o in _watched(entry, st):
            why = _output_problem(o, st)
            if why:
                copiers = [w["input"]["name"] for w in fallback if w["output"]["n"] == o["n"]]
                problems.append({"entry": name, "severity": "warn", "title": f"{o['name']}: {why}",
                                 "detail": f"Output {o['n']} on {name}, showing {o['source_name']}."
                                 + (f" {', '.join(copiers)} copy their EDID from it, so the matrix gives them its "
                                    "1080p fallback EDID." if copiers else "")})
    return {"state": "warn" if problems else "ok", "headline": " · ".join(heads) or "not answering",
            "problems": problems}


def details(entries: dict[str, dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    sections = []
    for name, entry in entries.items():
        try:
            st = client(entry, ctx).status()
        except Exception as exc:  # noqa: BLE001
            sections.append({"title": name, "entry": name, "state": "down",
                             "facts": [f"{entry['address']}: {exc}"], "items": []})
            continue
        watched = {o["n"] for o in _watched(entry, st)}
        inputs = ", ".join(f"{i['n']} {i['name']}" for i in st["inputs"])
        items = []
        for o in st["outputs"]:
            why = _output_problem(o, st)
            items.append({"id": f"out-{o['n']}", "name": f"{o['name']} ← {o['source_name']}",
                          "state": "up" if not why else "warn" if o["n"] in watched else "off",
                          "facts": [f"output {o['n']}"] + ([why] if why else ["monitor detected", "showing a picture"])
                                   + (["watched"] if o["n"] in watched else []),
                          "actions": [{"id": "route", "label": "switch input",
                                       "ask": f"Which input should {o['name']} show (now {o['source_name']})? "
                                              f"Name or number: {inputs}",
                                       "confirm": f"Switch {o['name']} to another input? The screen on it blanks "
                                                  "for a moment."},
                                      {"id": "wake", "label": "wake screen",
                                       "confirm": f"Turn {o['name']}'s signal off and on? Its screen goes dark for a "
                                                  "few seconds, like power-cycling the monitor, so it looks for the "
                                                  "picture again."}]})
        copying = {w["input"]["n"]: w for w in mx.edid_warnings(st)}
        for i in st["inputs"]:
            w = copying.get(i["n"])
            items.append({"id": f"in-{i['n']}", "name": f"{i['name']} (input {i['n']})",
                          "state": "warn" if w else "up" if i["signal"] else "off",
                          "facts": ["signal" if i["signal"] else "no signal", f"EDID: {i['edid_name']}"]
                                   + ([f"{w['output']['name']} has no monitor detected, so this input gets the "
                                       "matrix's 1080p fallback EDID"] if w else []),
                          "actions": [{"id": "edid", "label": "change EDID",
                                       "ask": f"EDID for {i['name']} (now {i['edid']}: {i['edid_name']}). Number 0-38, "
                                              "or e.g. 'copy 8' (from HDMI output 8), '4K60 4:4:4 HDR stereo'",
                                       "confirm": f"Change {i['name']}'s EDID? The PC on it re-detects its screen "
                                                  "and may change resolution."}]})
        presets = ", ".join(f"{n} {p}" for n, p in enumerate(st["presets"], 1))
        items.append({"id": "presets", "name": "Presets", "state": "up", "facts": [presets],
                      "actions": [{"id": "recall", "label": "recall", "ask": "Recall which preset (1-8)?",
                                   "confirm": "Recall a preset? Every output switches to the routing it saved."},
                                  {"id": "save", "label": "save current",
                                   "ask": "Save the current routing as preset (1-8)?",
                                   "confirm": "Overwrite that preset with the current routing?"}]})
        facts = [f"{entry['address']} · {entry.get('driver') or mx.DEFAULT_DRIVER} · firmware {st['version']}"]
        if st["locked"]:
            facts.append("front panel locked")
        if entry.get("note"):
            facts.append(entry["note"])
        sections.append({"title": name, "entry": name, "state": "ok", "facts": facts, "items": items})
    return sections


def test(entry: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Connection test for the page's Test button: reads only."""
    try:
        st = client(entry, ctx).status()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{entry.get('address')}: {exc}"}
    return {"ok": True, "message": f"Connected: firmware {st['version']} · "
                                   f"{sum(o['sink'] for o in st['outputs'])} monitors detected"}


def act(entries: dict[str, dict[str, Any]], ctx: Any, entry: str, item: str, action: str) -> dict[str, Any]:
    """A button: switch an output's input, wake its screen, change an input's EDID, recall or save a preset. Nothing else
    (factory reset, reboot, network, firmware) exists here."""
    typed = str(getattr(ctx, "input", "") or "").strip()
    if not typed and action != "wake":
        return {"ok": False, "error": "nothing typed"}
    try:
        m = client(entries[entry], ctx)
        st = m.status()
        if action == "wake" and item.startswith("out-"):
            out = int(item[4:])
            res = m.wake(out)
            msg = (f"{st['outputs'][out - 1]['name']}: signal turned off and on"
                   + ("" if res["sink"] else "; still no monitor detected"))
        elif action == "route" and item.startswith("out-"):
            out, inp = int(item[4:]), mx.find(st, "input", typed)
            res = m.route(inp, out)
            msg = f"{st['outputs'][out - 1]['name']} now shows {st['inputs'][inp - 1]['name']}"
        elif action == "edid" and item.startswith("in-"):
            inp, opt = int(item[3:]), mx.edid_option(typed)
            res = m.set_edid(inp, opt)
            msg = f"{st['inputs'][inp - 1]['name']} EDID: {mx.EDID[opt]}"
        elif item == "presets" and action in ("recall", "save") and typed.isdigit():
            res = m.recall_preset(int(typed)) if action == "recall" else m.save_preset(int(typed))
            msg = f"preset {typed} {'recalled' if action == 'recall' else 'saved'}"
        else:
            return {"ok": False, "error": f"{action} on {item} is not a button of the HDMI matrix"}
    except (mx.MatrixError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    if not res["ok"]:
        return {"ok": False, "error": f"the matrix took the command but still reports {res['now']}"
                                      " (does it need a login? set user and password on the entry)"}
    return {"ok": True, "message": msg}
