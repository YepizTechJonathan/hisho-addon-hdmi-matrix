"""Drivers for HDMI matrix switches. Standard library only, so both Hisho Core (provider.py) and
bin/matrixctl use it. An entry's ``driver`` picks one from DRIVERS; each driver gives the same
methods (status, route, set_edid, recall_preset, save_preset, rename) and the same status() shape
(see parse_status), so the provider and matrixctl never look at the protocol.

Adding a matrix that speaks something else (telnet, RS-232 over TCP, another vendor's HTTP API): write
a class with those methods and ``driver``/``size`` attributes, add it to DRIVERS, and add its name to
``driver: list(...)`` in addon.yaml. Raise MatrixError for anything the device can't do.

instr-json: the "18Gbps 8x8 HDMI & Matrix" web UI (HDP-MXB88D70M, firmware V1.08.16). Every call is ``POST http://<address>/cgi-bin/instr`` with ``{"comhead": "<command>", ...params,
"language": 0}``; the reply echoes ``comhead``. Reads need no login. The web UI's login is a
client-side guard (its token lives in sessionStorage) and its write calls carry no token, so a login
is only sent before a write when the entry has a password, as the web UI sends it:
``{"comhead": "login", "username": ..., "password": ...}``. A write answers ``{"result": 1}``.

Only the commands in READS and WRITES can be sent. Factory reset, reboot, network settings, EDID and
firmware uploads are never sent from here: they can cut the matrix off the network or brick it.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

READS = ("get system status", "get video status", "get input status", "get output status")
#: command -> its parameter(s), as the web UI sends them
WRITES = {
    "video switch": ("source",),        # source: [input 1-8, output 1-8]
    "set edid": ("edid",),              # edid: [input 1-8, option 0-38]
    "preset set": ("index",),           # recall preset 1-8
    "preset save": ("index",),          # save the current routing as preset 1-8
    "preset name": ("index", "name"),
    "set input name": ("input",),       # all 8 names
    "set output name": ("output",),     # all 8 names
    "tx stream": ("out",),              # out: [output 1-8, 0 off | 1 on] (the web UI's Output > Stream)
}
SIZE = 8
READ_TRIES = 5   # a read asked again when the reply was another client's (see InstrJson._send)

#: EDID option ids (the web UI's list): what each input tells its source the screen can do
EDID = {
    0: "1080p, stereo 2.0", 1: "1080p, Dolby/DTS 5.1", 2: "1080p, HD audio 7.1",
    3: "1080i, stereo 2.0", 4: "1080i, Dolby/DTS 5.1", 5: "1080i, HD audio 7.1",
    6: "3D, stereo 2.0", 7: "3D, Dolby/DTS 5.1", 8: "3D, HD audio 7.1",
    9: "4K30 4:4:4, stereo 2.0", 10: "4K30 4:4:4, Dolby/DTS 5.1", 11: "4K30 4:4:4, HD audio 7.1",
    12: "4K60 4:2:0, stereo 2.0", 13: "4K60 4:2:0, Dolby/DTS 5.1", 14: "4K60 4:2:0, HD audio 7.1",
    15: "4K60 4:4:4, stereo 2.0", 16: "4K60 4:4:4, Dolby/DTS 5.1", 17: "4K60 4:4:4, HD audio 7.1",
    18: "4K60 4:4:4 HDR, stereo 2.0", 19: "4K60 4:4:4 HDR, Dolby/DTS 5.1", 20: "4K60 4:4:4 HDR, HD audio 7.1",
    21: "user-defined 1", 22: "user-defined 2",
    **{23 + i: f"copy from HDMI output {i + 1}" for i in range(SIZE)},
    **{31 + i: f"copy from HDBaseT output {i + 1}" for i in range(SIZE)},
}


class MatrixError(RuntimeError):
    pass


def copies_from_output(option: int) -> int | None:
    """The HDMI output (1-8) an EDID option copies from, or None."""
    return option - 22 if 23 <= option <= 30 else None


def edid_option(text: str) -> int:
    """``17``, ``copy 7`` / ``hdmi7`` (copy from HDMI output 7), or words of a profile ("4K60 4:4:4 HDR")."""
    t = str(text).strip().lower()
    if t.isdigit() and int(t) in EDID:
        return int(t)
    digits = "".join(c for c in t if c.isdigit())
    if t.startswith(("copy", "hdmi")) and digits and 1 <= int(digits[-1]) <= SIZE and "cat" not in t:
        return 22 + int(digits[-1])
    hits = [k for k, v in EDID.items() if all(w in v.lower() for w in t.replace(",", " ").split())]
    if len(hits) == 1:
        return hits[0]
    raise MatrixError(f"{text!r} is not one EDID option" + (f" (matches {len(hits)})" if hits else "")
                      + "; give its number (matrixctl edid --list)")


Post = Callable[[str, dict[str, Any], float], dict[str, Any]]


def http_post(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise MatrixError(f"no answer from the matrix ({getattr(exc, 'reason', exc)})") from None
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise MatrixError(f"the matrix answered something that isn't JSON: {raw[:120]!r}") from None


class InstrJson:
    driver, size = "instr-json", SIZE

    def __init__(self, address: str, user: str | None = None, password: Callable[[], str] | None = None,
                 timeout: float = 5.0, post: Post = http_post) -> None:
        self.url = f"http://{address}/cgi-bin/instr"
        self.user, self._password, self.timeout, self._post = user or "Admin", password, timeout, post

    def _send(self, body: dict[str, Any], tries: int = 1) -> dict[str, Any]:
        """One reply per request, but the matrix keeps a single reply slot: when another client (its own
        web UI polls every 4 s) asks at the same moment, a reply can be someone else's. A read is asked
        again (tries); a write is sent once and its reply may be crossed (``{}``), so every write checks
        by reading the matrix back."""
        for n in range(tries):
            reply = self._post(self.url, {**body, "language": 0}, self.timeout)
            if reply.get("comhead") in (None, body["comhead"]):
                return reply
            if n + 1 < tries:
                time.sleep(0.3 * (n + 1))
        if tries == 1:
            return {}
        raise MatrixError(f"{body['comhead']}: the matrix kept answering {reply.get('comhead')!r} "
                          "(another client polling it?)")

    def read(self, comhead: str) -> dict[str, Any]:
        if comhead not in READS:
            raise MatrixError(f"{comhead!r} is not a read this client sends")
        return self._send({"comhead": comhead}, tries=READ_TRIES)

    def write(self, comhead: str, **params: Any) -> dict[str, Any]:
        if comhead not in WRITES:
            raise MatrixError(f"{comhead!r} is not a command this client sends")
        if set(params) != set(WRITES[comhead]):
            raise MatrixError(f"{comhead} takes {', '.join(WRITES[comhead])}")
        if self._password:
            # the password is fetched only now and only goes to the matrix; never logged or returned
            self._send({"comhead": "login", "username": self.user, "password": self._password()})
        reply = self._send({"comhead": comhead, **params})
        if reply.get("result") == 0:
            raise MatrixError(f"the matrix refused {comhead}")
        return reply

    def status(self) -> dict[str, Any]:
        """Everything the four reads say, as one picture: inputs (signal, EDID), outputs (source, sink)."""
        system, video = self.read("get system status"), self.read("get video status")
        inp, out = self.read("get input status"), self.read("get output status")
        return parse_status(system, video, inp, out)

    # -- writes: each re-reads the matrix and says whether it took ------------------------------------

    def route(self, inp: int, out: int) -> dict[str, Any]:
        _check(inp, "input"), _check(out, "output")
        self.write("video switch", source=[inp, out])
        now = self.status()["outputs"][out - 1]["source"]
        return {"ok": now == inp, "now": now}

    def set_edid(self, inp: int, option: int) -> dict[str, Any]:
        _check(inp, "input")
        if option not in EDID:
            raise MatrixError(f"EDID option {option} does not exist (0-38)")
        self.write("set edid", edid=[inp, option])
        now = self.status()["inputs"][inp - 1]["edid"]
        return {"ok": now == option, "now": now}

    def recall_preset(self, n: int) -> dict[str, Any]:
        _check(n, "preset")
        self.write("preset set", index=n)
        return {"ok": True, "now": [o["source"] for o in self.status()["outputs"]]}

    def wake(self, out: int, pause: float = 3.0) -> dict[str, Any]:
        """Turn one output's signal off and on again: the monitor sees its input drop and come back and
        looks for a picture again, as when it is power-cycled by hand. The signal is always turned back
        on, and once more if the read-back says it is still off."""
        _check(out, "output")
        try:
            self.write("tx stream", out=[out, 0])
            time.sleep(pause)
        finally:
            self.write("tx stream", out=[out, 1])
        o = self.status()["outputs"][out - 1]
        if not o["enabled"]:
            self.write("tx stream", out=[out, 1])
            o = self.status()["outputs"][out - 1]
        return {"ok": o["enabled"], "now": "on" if o["enabled"] else "off", "sink": o["sink"]}

    def save_preset(self, n: int) -> dict[str, Any]:
        _check(n, "preset")
        self.write("preset save", index=n)
        return {"ok": True}

    def rename(self, kind: str, n: int, name: str) -> dict[str, Any]:
        """Rename one input or output (the matrix takes all eight names at once)."""
        _check(n, kind)
        name = name.strip()
        if not (0 < len(name) <= 16) or not name.isprintable():
            raise MatrixError("a name is 1-16 printable characters")
        names = [x["name"] for x in self.status()[f"{kind}s"]]
        names[n - 1] = name
        self.write(f"set {kind} name", **{kind: names})
        now = self.status()[f"{kind}s"][n - 1]["name"]
        return {"ok": now == name, "now": now}


#: driver name -> class. The second matrix's driver goes here once its protocol is known.
DRIVERS: dict[str, type] = {"instr-json": InstrJson}
DEFAULT_DRIVER = "instr-json"


def connect(entry: dict[str, Any], password: Callable[[], str] | None = None, **kw: Any) -> Any:
    """The driver for one entry (``address``, ``driver``, ``user``)."""
    name = entry.get("driver") or DEFAULT_DRIVER
    if name not in DRIVERS:
        raise MatrixError(f"no driver {name!r} (known: {', '.join(DRIVERS)})")
    return DRIVERS[name](entry["address"], user=entry.get("user"), password=password, **kw)


def _check(n: int, what: str) -> None:
    if not isinstance(n, int) or not 1 <= n <= SIZE:
        raise MatrixError(f"{what} must be 1-{SIZE}")


def _at(values: Any, i: int, default: Any = None) -> Any:
    return values[i] if isinstance(values, list) and i < len(values) else default


def parse_status(system: dict, video: dict, inp: dict, out: dict) -> dict[str, Any]:
    in_names = video.get("allinputname") or inp.get("inname") or []
    out_names = video.get("alloutputname") or out.get("name") or []
    inputs = []
    for i in range(SIZE):
        edid = _at(inp.get("edid"), i)
        inputs.append({"n": i + 1, "name": _at(in_names, i, f"IN{i + 1}"),
                       # "inactive" is named backwards: 1 means the input has a live signal
                       "signal": bool(_at(inp.get("inactive"), i, 0)),
                       "edid": edid, "edid_name": EDID.get(edid, f"option {edid}")})
    outputs = []
    sources = out.get("allsource") or video.get("allsource") or []
    for i in range(SIZE):
        src = _at(sources, i)
        outputs.append({"n": i + 1, "name": _at(out_names, i, f"OUT{i + 1}"), "source": src,
                        "source_name": inputs[src - 1]["name"] if isinstance(src, int) and 1 <= src <= SIZE else None,
                        "sink": bool(_at(out.get("allconnect"), i, 0)),
                        "enabled": bool(_at(out.get("allout"), i, 1))})
    return {"version": system.get("version"), "power": bool(system.get("power", 1)),
            "locked": bool(system.get("lock")), "inputs": inputs, "outputs": outputs,
            "presets": list(video.get("allname") or [])}


def find(st: dict[str, Any], kind: str, text: str) -> int:
    """An input/output by number (1-8) or name (case-insensitive, a unique prefix is enough)."""
    t = str(text).strip()
    if t.isdigit():
        _check(int(t), kind)
        return int(t)
    rows = st[f"{kind}s"]
    exact = [r["n"] for r in rows if r["name"].lower() == t.lower()]
    if exact:
        return exact[0]
    near = [r["n"] for r in rows if r["name"].lower().startswith(t.lower())]
    if len(near) == 1:
        return near[0]
    names = ", ".join(r["name"] for r in rows)
    raise MatrixError(f"no single {kind} called {text!r} ({names})")


def edid_warnings(st: dict[str, Any]) -> list[dict[str, Any]]:
    """Inputs that copy their EDID from an output with no monitor detected: the matrix then hands the
    source its fallback EDID ("HDMI MATRIX", 1080p), and the PC drops to 1080p or loses the screen."""
    out = []
    for i in st["inputs"]:
        o = copies_from_output(i["edid"]) if isinstance(i["edid"], int) else None
        if o and not st["outputs"][o - 1]["sink"]:
            out.append({"input": i, "output": st["outputs"][o - 1]})
    return out


def snapshot(st: dict[str, Any]) -> dict[str, Any]:
    """What is compared between checks to journal changes."""
    return {"inputs": {str(i["n"]): {"name": i["name"], "signal": i["signal"], "edid": i["edid"]} for i in st["inputs"]},
            "outputs": {str(o["n"]): {"name": o["name"], "source": o["source"], "sink": o["sink"]} for o in st["outputs"]},
            "locked": st["locked"], "version": st["version"]}


def changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Plain-words differences between two snapshots."""
    said = []
    for n, a in after["outputs"].items():
        b = (before.get("outputs") or {}).get(n) or {}
        name = a["name"]
        if b.get("name") not in (None, a["name"]):
            said.append(f"output {n} renamed {b['name']} → {a['name']}")
        if b.get("source") not in (None, a["source"]):
            src = lambda s: (after["inputs"].get(str(s)) or {}).get("name", f"input {s}")
            said.append(f"{name} now shows {src(a['source'])} (was {src(b['source'])})")
        if "sink" in b and b["sink"] != a["sink"]:
            said.append(f"{name}: monitor {'detected' if a['sink'] else 'no longer detected'}")
    for n, a in after["inputs"].items():
        b = (before.get("inputs") or {}).get(n) or {}
        name = a["name"]
        if b.get("name") not in (None, a["name"]):
            said.append(f"input {n} renamed {b['name']} → {a['name']}")
        if "signal" in b and b["signal"] != a["signal"]:
            said.append(f"{name}: signal {'came up' if a['signal'] else 'lost'}")
        if b.get("edid") not in (None, a["edid"]):
            said.append(f"{name}: EDID {EDID.get(b['edid'], b['edid'])} → {EDID.get(a['edid'], a['edid'])}")
    if before.get("locked") is not None and before.get("locked") != after["locked"]:
        said.append(f"front panel {'locked' if after['locked'] else 'unlocked'}")
    if before.get("version") not in (None, after["version"]):
        said.append(f"firmware {before['version']} → {after['version']}")
    return said
