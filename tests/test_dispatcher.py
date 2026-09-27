import json
import os
import signal
import subprocess
import sys
import time

import pytest

from runway import ingest
from runway.dispatcher import metrics, run_chaos, scaled_mw
from runway.journal import Journal

METRIC_KEYS = ["seed", "n_units", "mw_requested", "fault", "fraction_offline", "tracking_error_kw",
               "shortfall_kw", "reserve_violations", "resole_latency_ms", "seq_before", "seq_after", "cost"]


@pytest.fixture(scope="module")
def chaos(tmp_path_factory):
    return run_chaos(seed=7, fault="offline_wave", n=500,
                     journal_path=tmp_path_factory.mktemp("j") / "chaos.jsonl")


def test_seed7_wave_no_reserve_violations_new_seq(chaos):
    m = metrics(chaos)
    assert list(m) == METRIC_KEYS
    assert m["reserve_violations"] == 0
    assert m["seq_after"] == m["seq_before"] + 1
    assert m["mw_requested"] == scaled_mw(500) == 5.0
    assert m["shortfall_kw"] >= 0 and m["resole_latency_ms"] > 0
    assert m["cost"] in ("stub", "marginal_wear")


def test_wave_hits_largest_setpoints_and_resolve_excludes_them(chaos):
    faulted = set(chaos["faulted"])
    assert len(faulted) == 75
    before = {s.unit_id: s.power_kw for s in chaos["setpoints_before"]}
    cutoff = min(before[u] for u in faulted)
    assert all(p <= cutoff + 1e-9 for u, p in before.items() if u not in faulted)
    after = {s.unit_id: s.power_kw for s in chaos["setpoints_after"]}
    assert all(after[u] == 0.0 for u in faulted)


def test_shortfall_identity_after_resolve(chaos):
    delivered_cmd = sum(s.power_kw for s in chaos["setpoints_after"])
    assert delivered_cmd + chaos["shortfall_kw"] == pytest.approx(chaos["mw_requested"] * 1000, abs=1e-6)




def _cmd(journal, *extra):
    return [sys.executable, "-m", "runway.dispatcher", "--journal", str(journal), "--n", "200",
            "--segments", "6", *extra]


def _wait_for(path, text, proc, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if path.exists() and text in path.read_text():
            return
        if proc.poll() is not None:
            raise AssertionError(f"dispatcher exited early: {proc.returncode}")
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {text!r}")


def test_subprocess_kill_and_restart_applies_each_seq_once(tmp_path, offline_env):
    # Reference: one uninterrupted run.
    ref = tmp_path / "ref.jsonl"
    subprocess.run(_cmd(ref), cwd=ingest.ROOT, env=offline_env, check=True, capture_output=True)
    ref_sum = json.loads((tmp_path / "ref.jsonl.summary.json").read_text())

    # Real kill: SIGKILL the dispatcher after seq 3 is journaled but before it commits.
    j = tmp_path / "crash.jsonl"
    log = tmp_path / "crash.log"
    with open(log, "w") as fh:
        p = subprocess.Popen(_cmd(j, "--stall-at-seq", "3"), cwd=ingest.ROOT, env=offline_env,
                             stdout=fh, stderr=subprocess.STDOUT)
        _wait_for(log, "stall at seq 3", p)
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
    assert p.returncode == -signal.SIGKILL
    mid = Journal.replay(j)
    assert sorted(mid.commits) == [1, 2] and mid.pending["seq"] == 3

    # Restart with the same journal.
    r = subprocess.run(_cmd(j), cwd=ingest.ROOT, env=offline_env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "re-sending uncommitted seq 3" in r.stdout
    (ingest.ROOT / "outputs" / "track2").mkdir(parents=True, exist_ok=True)
    (ingest.ROOT / "outputs" / "track2" / "restart.log").write_text(
        log.read_text() + "--- SIGKILL ---\n" + r.stdout)

    final = Journal.replay(j)
    assert sorted(final.commits) == ref_sum["seqs"] == [1, 2, 3, 4, 5, 6]
    assert all(c == 1 for c in final.commit_counts.values()), final.commit_counts
    assert sum(final.energy_by_unit().values()) == pytest.approx(ref_sum["energy_total_kwh"], rel=1e-9)
    ref_energy = Journal.replay(ref).energy_by_unit()
    for uid, kwh in final.energy_by_unit().items():
        assert kwh == pytest.approx(ref_energy[uid], abs=1e-9)
