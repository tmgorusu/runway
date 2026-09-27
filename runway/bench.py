"""Allocator benchmark: p50 wall-clock ms of allocate() alone at 1k, 10k, 100k units.

Fleet generation happens outside the timer. No workers start.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time

from runway import ingest
from runway.allocate import allocate, cost_name
from runway.dispatcher import chaos_call
from runway.fleet import make_fleet

OUT = ingest.ROOT / "outputs" / "bench" / "allocate.json"
SIZES = (1_000, 10_000, 100_000)


def cpu_model() -> str:
    if sys.platform == "darwin":
        try:
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                                  text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def bench(sizes=SIZES, repeats: int = 5, seed: int = 7) -> dict:
    rows = {}
    for n in sizes:
        units = make_fleet(n, seed)
        call = chaos_call(n)  # 40 * n / 4000 MW
        times = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            allocate(call, units, "runway")
            times.append((time.perf_counter() - t0) * 1000)
        rows[str(n)] = {"p50_ms": round(statistics.median(times), 3), "repeats": repeats,
                        "mw_requested": call.mw_requested}
    return {
        "synthetic": True,
        "policy": "runway",
        "cost": cost_name(),
        "timer": "time.perf_counter around allocate() only; fleet built outside; no workers",
        "cpu": cpu_model(),
        "python": platform.python_version(),
        "sizes": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeats", type=int, default=5)
    args = ap.parse_args(argv)
    out = bench(repeats=args.repeats)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"cpu: {out['cpu']}  cost={out['cost']}")
    for n, row in out["sizes"].items():
        print(f"  N={int(n):>7,}  p50 {row['p50_ms']:9.2f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
