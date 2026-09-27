"""Dispatcher: solve, journal, send, watch heartbeats, re-solve.

- One async worker per unit (runway.worker), all in this process.
- Every setpoint batch carries a monotonic `seq` and is journaled before it is sent.
- A missed heartbeat (no beat for TIMEOUT_BEATS * heartbeat_s of wall clock) marks
  that unit offline and triggers a new solve over the units still online.
- Reserve is never used to cover a shortfall; the gap is reported.
- Killing this process and starting it again with the same journal applies each
  seq once and keeps the same energy totals (tests/test_dispatcher.py does this
  with a real SIGKILL).

Clocks: call progress is simulated time; `resole_latency_ms` is wall clock
(time.perf_counter), from the moment the missed heartbeat is detected until every
online unit has acked the new seq.

CLI:
  python -m runway.dispatcher --journal J --n 200 --segments 6 [--stall-at-seq K]
  python -m runway.dispatcher --ladder
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

from runway import ingest
from runway.allocate import allocate, cost_name
from runway.contracts import Call, ReserveViolation, Setpoint, UnitState, check_setpoint
from runway.fleet import make_fleet
from runway.journal import Journal
from runway.worker import Ack, UnitWorker

ROOT = ingest.ROOT
TRACK2 = ROOT / "outputs" / "track2"
HEARTBEAT_S = 0.25
TIMEOUT_BEATS = 3
FAULT_FRACTION = 0.15
LADDER = (4000, 2000, 500)
LADDER_BUDGET_MS = 1000.0


def scaled_mw(n: int) -> float:
    return 40.0 * n / 4000


class Dispatcher:
    def __init__(self, units: list[UnitState], call: Call, journal: Journal, policy: str = "runway",
                 heartbeat_s: float = HEARTBEAT_S, assumption: str = "nominal"):
        self.call = call
        self.policy = policy
        self.assumption = assumption
        self.journal = journal
        self.heartbeat_s = heartbeat_s
        self.board: dict = {}
        self.workers = {u.unit_id: UnitWorker(u, self.board, heartbeat_s) for u in units}
        self.offline: set[int] = set()
        self.seq = 0
        self.elapsed_min = 0.0
        self.reserve_violations = 0
        self.refusals: list[Ack] = []
        self.missed = asyncio.Event()
        self.missed_at = 0.0
        self._watch: asyncio.Task | None = None
        self.stall_at_seq = 0  # test hook: pause between write-ahead and delivery
        self.stall_s = 0.0

    # lifecycle -------------------------------------------------------------
    async def start(self) -> None:
        for w in self.workers.values():
            w.start()
        self._watch = asyncio.create_task(self._watch_heartbeats())

    async def stop(self) -> None:
        if self._watch:
            self._watch.cancel()
        await asyncio.gather(*(w.stop() for w in self.workers.values()), return_exceptions=True)

    # heartbeat watch -------------------------------------------------------
    async def _watch_heartbeats(self) -> None:
        timeout = TIMEOUT_BEATS * self.heartbeat_s
        while True:
            await asyncio.sleep(self.heartbeat_s / 2)
            now = time.monotonic()
            late = {uid for uid, t in self.board.items() if uid not in self.offline and now - t.wall > timeout}
            if late:
                self.offline |= late
                self.missed_at = time.perf_counter()
                self.missed.set()

    # view and solve --------------------------------------------------------
    def view(self) -> list[UnitState]:
        """The dispatcher's belief about each unit, from the telemetry board only."""
        units = []
        for uid, w in self.workers.items():
            t = self.board.get(uid)
            base = w.unit
            units.append(dataclasses.replace(
                base, soc=t.soc if t else base.soc, last_seen=t.last_seen if t else base.last_seen,
                derate=(t.p_max_kw / base.p_max_kw) if t else base.derate,
                online=uid not in self.offline, metadata=dict(base.metadata),
            ))
        return units

    @property
    def remaining_min(self) -> float:
        return self.call.duration_min - self.elapsed_min

    def remaining_call(self) -> Call:
        return dataclasses.replace(self.call, start=self.call.start + timedelta(minutes=self.elapsed_min),
                                   duration_min=self.remaining_min)

    def solve(self) -> tuple[list[Setpoint], float, list[UnitState]]:
        call = self.remaining_call()
        units = self.view()
        setpoints, shortfall = allocate(call, units, self.policy, self.assumption)
        self.seq += 1
        setpoints = [dataclasses.replace(s, seq=self.seq, call_id=call.call_id) for s in setpoints]
        by_id = {u.unit_id: u for u in units}
        for s in setpoints:  # independent audit of the allocator against the dispatcher's own view
            try:
                check_setpoint(by_id[s.unit_id], s, call.hours)
            except ReserveViolation:
                self.reserve_violations += 1
        return setpoints, shortfall, units

    # dispatch --------------------------------------------------------------
    async def send(self, setpoints: list[Setpoint], shortfall: float, apply_min: float) -> dict[int, Ack]:
        """Journal the batch, send it, wait for acks, journal the commit."""
        seq = setpoints[0].seq
        now = self.call.start + timedelta(minutes=self.elapsed_min)
        hours = self.remaining_min / 60.0
        self.journal.append({
            "t": "batch", "seq": seq, "call_id": self.call.call_id, "elapsed_min": self.elapsed_min,
            "apply_min": apply_min, "shortfall_kw": shortfall, "offline": sorted(self.offline),
            "setpoints": [[s.unit_id, s.power_kw] for s in setpoints],
        })
        if seq == self.stall_at_seq:
            print(f"stall at seq {seq}", flush=True)  # the worst moment to die
            await asyncio.sleep(self.stall_s)
        return await self._deliver(seq, setpoints, hours, apply_min, now)

    async def _deliver(self, seq, setpoints, hours, apply_min, now) -> dict[int, Ack]:
        live = [s for s in setpoints if s.unit_id not in self.offline]
        futs = {s.unit_id: self.workers[s.unit_id].submit(s, hours, apply_min / 60.0, now) for s in live}
        done, pending = await asyncio.wait(futs.values(), timeout=TIMEOUT_BEATS * self.heartbeat_s)
        for f in pending:
            f.cancel()
        acks = {uid: f.result() for uid, f in futs.items() if f in done}
        self.offline |= set(futs) - set(acks)  # no ack is as good as a missed heartbeat
        for a in acks.values():
            if not a.accepted and a.reason.startswith("refused"):
                self.refusals.append(a)  # refused at the edge, so nothing entered reserve
        self.journal.append({"t": "commit", "seq": seq,
                             "energy": [[uid, a.energy_kwh] for uid, a in sorted(acks.items()) if a.accepted]})
        self.elapsed_min += apply_min
        return acks

    async def resend(self, batch: dict) -> dict[int, Ack]:
        """Re-send a journaled batch that never committed, with its original seq."""
        self.seq = batch["seq"]
        self.elapsed_min = batch["elapsed_min"]
        self.offline |= set(batch["offline"])
        setpoints = [Setpoint(uid, p, batch["call_id"], batch["seq"]) for uid, p in batch["setpoints"]]
        now = self.call.start + timedelta(minutes=self.elapsed_min)
        return await self._deliver(batch["seq"], setpoints, self.remaining_min / 60.0, batch["apply_min"], now)


# chaos run: the Track 2 proof, and the demo -------------------------------
def chaos_call(n: int) -> Call:
    from runway.e2e import june_call

    return dataclasses.replace(june_call(n), call_id="chaos-published-4cp-2025-06-19")


async def _chaos(seed: int, n: int, journal_path: Path, first_segment_min: float) -> dict:
    journal_path.unlink(missing_ok=True)
    call = chaos_call(n)
    units = make_fleet(n, seed)
    d = Dispatcher(units, call, Journal(journal_path))
    await d.start()
    try:
        sp1, short1, _ = d.solve()
        acks1 = await d.send(sp1, short1, apply_min=first_segment_min)
        seq_before = d.seq
        k = max(1, round(FAULT_FRACTION * n))
        faulted = [s.unit_id for s in sorted(sp1, key=lambda s: (-s.power_kw, s.unit_id))[:k]]
        for uid in faulted:
            d.workers[uid].inject("offline")
        await asyncio.wait_for(d.missed.wait(), timeout=10 * TIMEOUT_BEATS * d.heartbeat_s)
        while not set(faulted) <= d.offline:  # the whole wave, not just the first late unit
            await asyncio.sleep(d.heartbeat_s / 4)
        t_detect = d.missed_at
        t0 = time.perf_counter()
        sp2, short2, view2 = d.solve()
        solve_ms = (time.perf_counter() - t0) * 1000
        acks2 = await d.send(sp2, short2, apply_min=d.remaining_min)
        latency_ms = (time.perf_counter() - t_detect) * 1000
    finally:
        await d.stop()
        d.journal.close()
    delivered = sum(a.delivered_kw for a in acks2.values() if a.accepted)
    commanded = sum(s.power_kw for s in sp2 if s.unit_id not in d.offline)
    return {
        "seed": seed, "n_units": n, "mw_requested": call.mw_requested, "fault": "offline_wave",
        "fraction_offline": FAULT_FRACTION, "faulted": faulted, "call": call,
        "setpoints_before": sp1, "setpoints_after": sp2, "view_after": view2,
        "acks_before": acks1, "acks_after": acks2,
        "delivered_kw": delivered, "commanded_kw": commanded, "shortfall_before_kw": short1,
        "shortfall_kw": short2, "reserve_violations": d.reserve_violations, "refusals": len(d.refusals),
        "resole_latency_ms": latency_ms, "solve_ms": solve_ms,
        "seq_before": seq_before, "seq_after": d.seq, "cost": cost_name(),
    }


def run_chaos(seed: int = 7, fault: str = "offline_wave", n: int = 2000,
              journal_path: Path | None = None, first_segment_min: float = 15.0) -> dict:
    """The seed-7 offline wave. The demo entrypoint and the chaos test both call this."""
    if fault != "offline_wave":
        raise ValueError("the filmed fault is offline_wave")
    journal_path = journal_path or TRACK2 / f"chaos_seed{seed}_n{n}.jsonl"
    return asyncio.run(_chaos(seed, n, Path(journal_path), first_segment_min))


def metrics(r: dict) -> dict:
    """outputs/track2/metrics.json, exactly the MACHIENE.md section 5 shape."""
    return {
        "seed": r["seed"],
        "n_units": r["n_units"],
        "mw_requested": r["mw_requested"],
        "fault": r["fault"],
        "fraction_offline": r["fraction_offline"],
        "tracking_error_kw": round(abs(r["commanded_kw"] - r["delivered_kw"]), 6),
        "shortfall_kw": round(r["shortfall_kw"], 6),
        "reserve_violations": r["reserve_violations"],
        "resole_latency_ms": round(r["resole_latency_ms"], 3),
        "seq_before": r["seq_before"],
        "seq_after": r["seq_after"],
        "cost": r["cost"],
    }


def ladder(repeats: int = 3, seed: int = 7) -> dict:
    """Largest N in 4000, 2000, 500 whose re-solve p50 is under one second."""
    rows = {}
    for n in LADDER:
        lat = [run_chaos(seed, n=n, journal_path=TRACK2 / "ladder.jsonl")["resole_latency_ms"] for _ in range(repeats)]
        rows[str(n)] = {"p50_ms": round(statistics.median(lat), 3), "runs_ms": [round(x, 3) for x in lat],
                        "mw_requested": scaled_mw(n)}
    (TRACK2 / "ladder.jsonl").unlink(missing_ok=True)
    chosen = next((n for n in LADDER if rows[str(n)]["p50_ms"] < LADDER_BUDGET_MS), LADDER[-1])
    out = {"budget_ms": LADDER_BUDGET_MS, "clock": "wall", "chosen_n": chosen, "rungs": rows}
    TRACK2.mkdir(parents=True, exist_ok=True)
    (TRACK2 / "ladder.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def filmed_n(default: int = 2000) -> int:
    path = TRACK2 / "ladder.json"
    return json.loads(path.read_text())["chosen_n"] if path.exists() else default


# plain segmented call, restartable: the subprocess-kill test drives this --------
async def _segmented(journal_path: Path, n: int, seed: int, segments: int, stall_at_seq: int, stall_s: float) -> dict:
    state = Journal.replay(journal_path)
    energy = state.energy_by_unit()
    units = make_fleet(n, seed)
    for u in units:  # restore each battery from committed energy only
        u.soc -= energy.get(u.unit_id, 0.0) / (u.capacity_kwh * u.health)
    call = dataclasses.replace(chaos_call(n), call_id="segmented")
    d = Dispatcher(units, call, Journal(journal_path))
    committed = list(state.commits)
    last_committed = max(committed, default=0)
    for w in d.workers.values():
        w.applied_seq = last_committed
    d.seq = last_committed
    if state.commits:
        last = state.batches[last_committed]
        d.elapsed_min = last["elapsed_min"] + last["apply_min"]
        d.offline |= set(last["offline"])
    seg_min = call.duration_min / segments
    d.stall_at_seq, d.stall_s = stall_at_seq, stall_s
    await d.start()
    try:
        if state.pending:
            print(f"restart: re-sending uncommitted seq {state.pending['seq']}", flush=True)
            await d.resend(state.pending)
            print(f"committed seq {d.seq}", flush=True)
        elif committed:
            print(f"restart: seqs {min(committed)}..{last_committed} already committed", flush=True)
        while d.remaining_min > 1e-9:
            sp, short, _ = d.solve()
            await d.send(sp, short, apply_min=seg_min)
            print(f"committed seq {d.seq}", flush=True)
    finally:
        await d.stop()
        d.journal.close()
    final = Journal.replay(journal_path)
    return {"seqs": sorted(final.commits), "energy_kwh": final.energy_by_unit(),
            "commit_counts": final.commit_counts}


def run_segmented(journal_path, n=200, seed=7, segments=6, stall_at_seq=0, stall_s=30.0) -> dict:
    return asyncio.run(_segmented(Path(journal_path), n, seed, segments, stall_at_seq, stall_s))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Runway dispatcher")
    ap.add_argument("--journal", type=Path)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--segments", type=int, default=6)
    ap.add_argument("--stall-at-seq", type=int, default=0)
    ap.add_argument("--stall-s", type=float, default=30.0)
    ap.add_argument("--ladder", action="store_true")
    args = ap.parse_args(argv)
    if args.ladder:
        out = ladder(seed=args.seed)
        for n, row in out["rungs"].items():
            print(f"N={n:>5}  re-solve p50 {row['p50_ms']:8.1f} ms (wall clock)")
        print(f"filmed N = {out['chosen_n']}")
        return 0
    if not args.journal:
        ap.error("--journal is required")
    out = run_segmented(args.journal, args.n, args.seed, args.segments, args.stall_at_seq, args.stall_s)
    summary = {"seqs": out["seqs"], "energy_total_kwh": sum(out["energy_kwh"].values()),
               "commit_counts": out["commit_counts"]}
    Path(str(args.journal) + ".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"done seqs={out['seqs']} energy_total_kwh={summary['energy_total_kwh']:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
