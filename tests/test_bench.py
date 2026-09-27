import asyncio

from runway import bench


def test_three_timings_no_workers_cpu_recorded(monkeypatch):
    started = []
    monkeypatch.setattr(asyncio, "create_task", lambda *a, **k: started.append(a))
    out = bench.bench(sizes=(1_000, 2_000, 3_000), repeats=2)
    assert list(out["sizes"]) == ["1000", "2000", "3000"]
    assert all(row["p50_ms"] > 0 for row in out["sizes"].values())
    assert out["cpu"] and "allocate() only" in out["timer"]
    assert not started


def test_default_sizes():
    assert bench.SIZES == (1_000, 10_000, 100_000)
