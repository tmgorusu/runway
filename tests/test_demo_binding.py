import csv
import json
import subprocess
import sys

import pytest

from runway import demo, dispatcher, ingest


def test_demo_calls_the_dispatcher_under_test(monkeypatch):
    calls = []
    real = dispatcher.run_chaos

    def spy(**kw):
        calls.append(kw)
        return real(**kw)

    monkeypatch.setattr(dispatcher, "run_chaos", spy)
    monkeypatch.setattr(demo, "OUT", ingest.ROOT / "outputs" / "scratch")
    assert demo.main(["--seed", "7", "--fault", "offline_wave", "--n", "200"]) == 0
    assert calls == [{"seed": 7, "fault": "offline_wave", "n": 200}]


def test_demo_command_offline_matches_chaos_test(offline_env):
    r = subprocess.run([sys.executable, "-m", "runway.demo", "--seed", "7", "--fault", "offline_wave"],
                       cwd=ingest.ROOT, env=offline_env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "SYNTHETIC FLEET" in r.stdout and "reserve_violations = 0" in r.stdout
    run = json.loads((ingest.ROOT / "outputs/demo/run.json").read_text())
    n = run["n_units"]
    assert n == dispatcher.filmed_n()
    chaos = dispatcher.run_chaos(seed=7, fault="offline_wave", n=n)
    assert run["setpoints_before"] == [[s.unit_id, s.power_kw] for s in chaos["setpoints_before"]]
    assert run["faulted"] == chaos["faulted"]

    m = json.loads((ingest.ROOT / "outputs/track2/metrics.json").read_text())
    assert list(m) == list(dispatcher.metrics(chaos))
    assert m["reserve_violations"] == 0 and m["seed"] == 7 and m["fault"] == "offline_wave"
    if m["cost"] == "stub":
        assert "cost=stub" in r.stdout

    with open(ingest.ROOT / "outputs/demo/decision_table.csv") as fh:
        rows = list(csv.DictReader(fh))
    assert rows and list(rows[0]) == demo.DECISION_COLUMNS
    before = dict(map(tuple, run["setpoints_before"]))
    after = dict(map(tuple, run["setpoints_after"]))
    assert {int(r["unit_id"]) for r in rows} == {u for u in after if abs(after[u] - before[u]) > 1e-9}
