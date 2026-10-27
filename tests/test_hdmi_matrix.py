"""Run with Hisho's venv at the repository's top: ~/code/hisho/.venv/bin/python -m pytest -q

The fixture is what the real matrix (firmware V1.08.16) answered on 2026-10-21: TopMonitor (output 7)
had no monitor detected while MainPC1, MainPC2 and StreamingPC2 copied their EDID from it."""
import copy
import json
from pathlib import Path

import pytest

from hisho import addon_store, addons

HERE = Path(__file__).resolve().parents[1]
REPLIES = json.loads((HERE / "tests/fixtures/instr-json-v1.08.16.json").read_text())
ENTRY = {"office": {"address": "192.0.2.9", "watch": "TopMonitor,BotMonitor"}}
SECOND = {"rack": {"address": "192.0.2.10", "driver": "telnet-to-be-written"}}


class FakeMatrix:
    """Stands in for http_post: answers reads from the fixture, applies writes, records every body."""

    def __init__(self, ignore_writes: bool = False) -> None:
        self.replies, self.sent, self.ignore = copy.deepcopy(REPLIES), [], ignore_writes

    def __call__(self, url, body, timeout):
        self.sent.append(body)
        c = body["comhead"]
        if c in self.replies:
            return self.replies[c]
        if not self.ignore and c == "video switch":
            inp, out = body["source"]
            for r in ("get video status", "get output status"):
                self.replies[r]["allsource"][out - 1] = inp
        if not self.ignore and c == "tx stream":
            self.replies["get output status"]["allout"][body["out"][0] - 1] = body["out"][1]
        if not self.ignore and c == "set edid":
            self.replies["get input status"]["edid"][body["edid"][0] - 1] = body["edid"][1]
        return {"comhead": c}


@pytest.fixture
def mod(addon):
    return addons.provider(addon)


@pytest.fixture
def fake(mod, monkeypatch):
    f = FakeMatrix()
    monkeypatch.setattr(mod, "client", lambda entry, ctx: mod.mx.connect(entry, post=f))
    return f


def test_manifest_is_valid_and_readme_matches(addon):
    assert addons.validate_manifest(addon.manifest) == []
    assert addon_store.readme_problems(HERE) == []


def test_no_entries_is_unknown_not_an_alert(addon, ctx_for):
    assert addons.summarize(addon, ctx_for(addon))["state"] in ("unknown", "ok")
    assert addons.provider(addon).summary({}, ctx_for(addon))["state"] == "unknown"


def test_status_reads_the_real_replies(mod):
    st = mod.mx.parse_status(*(REPLIES[c] for c in mod.mx.READS))
    assert [i["name"] for i in st["inputs"] if i["signal"]] == ["MainPC2"]   # "inactive": 1 = live
    top = st["outputs"][6]
    assert (top["name"], top["source_name"], top["sink"]) == ("TopMonitor", "MainPC1", False)
    assert {w["input"]["name"] for w in mod.mx.edid_warnings(st)} == {"MainPC1", "MainPC2", "StreamingPC2"}


def test_watched_dark_screen_is_a_problem_that_names_the_edid_fallback(mod, fake, ctx_for, addon, tmp_path,
                                                                         monkeypatch):
    monkeypatch.setenv("HISHO_STATE_DIR", str(tmp_path))
    s = mod.summary(ENTRY, ctx_for(addon))
    assert s["state"] == "warn"
    assert [p["title"].split(":")[0] for p in s["problems"]] == ["TopMonitor"]
    assert "MainPC1, MainPC2, StreamingPC2 copy their EDID" in s["problems"][0]["detail"]
    # outputs nobody watches show on the page but raise nothing
    assert mod.summary({"office": {"address": "192.0.2.9"}}, ctx_for(addon))["state"] == "ok"


def test_details_page_has_every_output_input_and_presets(mod, fake, ctx_for, addon):
    sec = mod.details(ENTRY, ctx_for(addon))[0]
    ids = [i["id"] for i in sec["items"]]
    assert ids == [f"out-{n}" for n in range(1, 9)] + [f"in-{n}" for n in range(1, 9)] + ["presets"]
    acts = [a for i in sec["items"] for a in i["actions"]]
    assert all(a.get("confirm") for a in acts)                                   # every button asks first
    assert all(a.get("ask") for a in acts if a["id"] != "wake")                  # wake needs nothing typed
    top = sec["items"][6]
    assert top["name"] == "TopMonitor ← MainPC1" and top["state"] == "warn"


def test_changes_are_journaled_after_the_first_check(mod, fake, fleet, tmp_path, monkeypatch):
    monkeypatch.setenv("HISHO_STATE_DIR", str(tmp_path / "state"))
    assert mod.journal_changes("office", mod.client(ENTRY["office"], None).status()) == []  # first look remembers
    fake.replies["get output status"]["allconnect"][6] = 1
    fake.replies["get output status"]["allsource"][6] = 2
    said = mod.journal_changes("office", mod.client(ENTRY["office"], None).status())
    assert said == ["TopMonitor now shows MainPC2 (was MainPC1)", "TopMonitor: monitor detected"]
    journal = "".join(p.read_text() for p in (fleet / "System" / "Journal").rglob("*") if p.is_file())
    assert "hdmi-matrix-change" in journal and "TopMonitor: monitor detected" in journal


def test_route_button_switches_and_checks_it_took(mod, fake, ctx_for, addon):
    ctx = ctx_for(addon)
    ctx.input = "MainPC2"
    assert mod.act(ENTRY, ctx, "office", "out-7", "route") == {"ok": True, "message": "TopMonitor now shows MainPC2"}
    assert {"comhead": "video switch", "source": [2, 7], "language": 0} in fake.sent


def test_a_write_the_matrix_ignores_is_reported(mod, ctx_for, addon, monkeypatch):
    f = FakeMatrix(ignore_writes=True)
    monkeypatch.setattr(mod, "client", lambda entry, ctx: mod.mx.connect(entry, post=f))
    ctx = ctx_for(addon)
    ctx.input = "copy 8"
    res = mod.act(ENTRY, ctx, "office", "in-1", "edid")
    assert not res["ok"] and "login" in res["error"]
    assert {"comhead": "set edid", "edid": [1, 30], "language": 0} in f.sent


def test_unknown_buttons_send_nothing(mod, fake, ctx_for, addon):
    ctx = ctx_for(addon)
    ctx.input = "1"
    for item, action in (("system", "reboot"), ("system", "factory"), ("out-1", "edid"), ("presets", "clear")):
        assert not mod.act(ENTRY, ctx, "office", item, action)["ok"]
    assert {b["comhead"] for b in fake.sent} <= set(mod.mx.READS)


def test_only_listed_commands_can_be_sent(mod):
    m = mod.mx.InstrJson("192.0.2.9", post=FakeMatrix())
    for bad in ("set factory", "reboot", "set network", "set defaults network", "login"):
        with pytest.raises(mod.mx.MatrixError):
            m.write(bad)
        with pytest.raises(mod.mx.MatrixError):
            m.read(bad)
    with pytest.raises(mod.mx.MatrixError):
        m.write("video switch", source=[1, 2], extra=1)
    with pytest.raises(mod.mx.MatrixError):
        m.route(9, 1)


def test_password_is_fetched_only_for_a_write(mod):
    f, asked = FakeMatrix(), []
    m = mod.mx.InstrJson("192.0.2.9", user="Admin", post=f, password=lambda: asked.append(1) or "pw")
    m.status()
    assert asked == [] and all(b["comhead"] != "login" for b in f.sent)
    m.route(2, 7)
    assert asked == [1] and {"comhead": "login", "username": "Admin", "password": "pw", "language": 0} in f.sent


def test_names_and_edid_options(mod):
    st = mod.mx.parse_status(*(REPLIES[c] for c in mod.mx.READS))
    assert mod.mx.find(st, "output", "top") == 7 and mod.mx.find(st, "input", "8") == 8
    with pytest.raises(mod.mx.MatrixError):
        mod.mx.find(st, "output", "hdmi")                    # four outputs start with it
    assert mod.mx.edid_option("copy 8") == 30 and mod.mx.edid_option("hdmi7") == 29
    assert mod.mx.edid_option("4K60 4:4:4 HDR stereo") == 18 and mod.mx.edid_option("15") == 15
    with pytest.raises(mod.mx.MatrixError):
        mod.mx.edid_option("4K60")


def test_two_matrices_and_a_driver_not_written_yet(mod, fake, ctx_for, addon, tmp_path, monkeypatch):
    monkeypatch.setenv("HISHO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "client", lambda entry, ctx: mod.mx.connect(entry, post=fake))
    both = {**ENTRY, **SECOND}
    s = mod.summary(both, ctx_for(addon))
    assert [p["entry"] for p in s["problems"]] == ["office", "rack"]
    assert "no driver 'telnet-to-be-written'" in s["problems"][1]["detail"]
    secs = mod.details(both, ctx_for(addon))
    assert [x["state"] for x in secs] == ["ok", "down"] and "instr-json" in secs[0]["facts"][0]
    ctx = ctx_for(addon)
    ctx.input = "1"
    assert "no driver" in mod.act(both, ctx, "rack", "out-1", "route")["error"]


def test_a_refused_write_is_an_error(mod):
    f = FakeMatrix()
    f.replies["video switch"] = {"comhead": "video switch", "result": 0}
    with pytest.raises(mod.mx.MatrixError, match="refused"):
        mod.mx.connect({"address": "192.0.2.9"}, post=f).route(2, 7)


def test_matrixctl_takes_the_matrix_name_first(addon, monkeypatch, tmp_path, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("matrixctl_under_test", HERE / "matrixctl.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "_entries", lambda: {**ENTRY, **SECOND})
    seen = {}
    monkeypatch.setattr(cli.mx, "connect", lambda entry, password=None: seen.setdefault("e", entry)
                        and cli.mx.InstrJson(entry["address"], post=FakeMatrix()))
    assert cli.main(["office", "status"]) == 0 and seen["e"]["address"] == "192.0.2.9"
    assert "TopMonitor" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="which matrix"):
        cli.main(["status"])                               # two matrices: say which
    assert cli.main(["list"]) == 0 and "rack" in capsys.readouterr().out


def test_matrixctl_options_go_anywhere(addon, monkeypatch, capsys):
    """--address's value is not a matrix name, and --yes works after the command (as the README shows)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("matrixctl_opts_under_test", HERE / "matrixctl.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "_entries", lambda: {**ENTRY, **SECOND})
    fake, seen = FakeMatrix(), {}
    monkeypatch.setattr(cli.mx, "connect", lambda entry, password=None: seen.setdefault("e", entry)
                        and cli.mx.InstrJson(entry["address"], post=fake))
    assert cli.main(["--address", "192.0.2.50", "status"]) == 0 and seen["e"]["address"] == "192.0.2.50"
    assert cli._global_options(["office", "route", "2", "7", "--yes"]) == (["office", "route", "2", "7"], {"yes": True})
    assert cli._global_options(["--address=192.0.2.50", "--driver", "instr-json", "status"]) == (
        ["status"], {"address": "192.0.2.50", "driver": "instr-json"})


def test_a_crossed_reply_is_read_again_and_a_write_is_sent_once(mod, monkeypatch):
    """The matrix has one reply slot: with its web UI open, a reply can be the answer to someone else."""
    monkeypatch.setattr(mod.mx.time, "sleep", lambda s: None)
    fake, sent, crossed = FakeMatrix(), [], {"get output status": 2, "video switch": 1}

    def post(url, body, timeout):
        sent.append(body["comhead"])
        reply = fake(url, body, timeout)               # the matrix does it either way...
        if crossed.get(body["comhead"]):
            crossed[body["comhead"]] -= 1
            return {"comhead": "get input status"}     # ...but the reply is someone else's
        return reply
    m = mod.mx.InstrJson("192.0.2.9", post=post)
    assert m.read("get output status")["comhead"] == "get output status"
    assert sent.count("get output status") == 3
    res = m.route(2, 7)
    assert sent.count("video switch") == 1 and res["ok"]   # sent once; the read-back says it took
    monkeypatch.setattr(mod.mx, "READ_TRIES", 2)
    crossed["get system status"] = 9
    with pytest.raises(mod.mx.MatrixError, match="kept answering"):
        m.read("get system status")


def test_wake_turns_the_signal_off_and_always_back_on(mod, fake, ctx_for, addon, monkeypatch):
    monkeypatch.setattr(mod.mx.time, "sleep", lambda s: None)
    res = mod.act(ENTRY, ctx_for(addon), "office", "out-8", "wake")      # no typed text needed
    streams = [b["out"] for b in fake.sent if b["comhead"] == "tx stream"]
    assert res["ok"] and streams == [[8, 0], [8, 1]]
    assert fake.replies["get output status"]["allout"][7] == 1
    page = mod.details(ENTRY, ctx_for(addon))[0]["items"]
    assert "wake" in [a["id"] for a in next(i for i in page if i["id"] == "out-8")["actions"]]


def test_wake_turns_it_back_on_even_when_the_pause_fails(mod, monkeypatch):
    fake = FakeMatrix()
    m = mod.mx.InstrJson("192.0.2.9", post=fake)

    def boom(s):
        raise KeyboardInterrupt
    monkeypatch.setattr(mod.mx.time, "sleep", boom)
    with pytest.raises(KeyboardInterrupt):
        m.wake(7)
    assert [b["out"] for b in fake.sent if b["comhead"] == "tx stream"] == [[7, 0], [7, 1]]
    assert fake.replies["get output status"]["allout"][6] == 1
