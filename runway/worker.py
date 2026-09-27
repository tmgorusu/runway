"""One async actor per battery, all inside one process.

A worker heartbeats its telemetry onto a shared board the dispatcher reads,
executes setpoints from its inbox, and refuses any setpoint that would enter
backup reserve or exceed its (possibly derated) power limit, whatever the
dispatcher asked for.

Injectable faults, each visible in the telemetry the dispatcher reads:
- offline:        heartbeats stop and commands go unanswered
- derated:        effective p_max_kw drops by a factor
- stale:          heartbeats continue but the payload (soc, last_seen) freezes
- under_delivery: delivered power is a fraction of the commanded power

Two clocks: heartbeat liveness uses the wall clock (time.monotonic); energy and
`last_seen` use simulated call time supplied by the dispatcher.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass
from datetime import datetime

from runway.contracts import ReserveViolation, Setpoint, UnitState, check_setpoint

FAULTS = ("offline", "derated", "stale", "under_delivery")


@dataclass(frozen=True, slots=True)
class Telemetry:
    unit_id: int
    soc: float
    p_max_kw: float
    power_kw: float
    last_seen: datetime
    seq: int
    wall: float  # time.monotonic() of the heartbeat


@dataclass(frozen=True, slots=True)
class Ack:
    unit_id: int
    seq: int
    accepted: bool
    commanded_kw: float
    delivered_kw: float
    energy_kwh: float
    reason: str = ""


class UnitWorker:
    def __init__(self, unit: UnitState, board: dict[int, Telemetry], heartbeat_s: float = 0.25):
        self.unit = dataclasses.replace(unit, metadata=dict(unit.metadata))
        self.board = board
        self.heartbeat_s = heartbeat_s
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.faults: dict[str, float] = {}
        self.applied_seq = 0
        self.power_kw = 0.0
        self.energy_kwh = 0.0
        self._frozen: Telemetry | None = None
        self._tasks: list[asyncio.Task] = []

    # lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self._beat()
        self._tasks = [asyncio.create_task(self._heartbeat()), asyncio.create_task(self._serve())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # faults ----------------------------------------------------------------
    def inject(self, kind: str, value: float = 0.0) -> None:
        if kind not in FAULTS:
            raise ValueError(f"unknown fault {kind!r}")
        self.faults[kind] = value
        if kind == "derated":
            self.unit.derate = value
        if kind == "stale":
            self._frozen = self._telemetry()

    def clear(self, kind: str) -> None:
        self.faults.pop(kind, None)
        if kind == "derated":
            self.unit.derate = 1.0
        if kind == "stale":
            self._frozen = None

    # actor loops -----------------------------------------------------------
    def _telemetry(self) -> Telemetry:
        u = self.unit
        return Telemetry(u.unit_id, u.soc, u.p_max_kw * u.derate, self.power_kw, u.last_seen, self.applied_seq,
                         time.monotonic())

    def _beat(self) -> None:
        if "offline" in self.faults:
            return
        t = self._telemetry()
        if self._frozen is not None:
            t = dataclasses.replace(self._frozen, wall=t.wall)
        self.board[self.unit.unit_id] = t

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            self._beat()

    async def _serve(self) -> None:
        while True:
            sp, hours, apply_h, now, fut = await self.inbox.get()
            if "offline" in self.faults:
                continue  # silence: the dispatcher sees a missing ack and a missing heartbeat
            fut.set_result(self.execute(sp, hours, apply_h, now))

    def execute(self, sp: Setpoint, hours: float, apply_h: float, now: datetime) -> Ack:
        """Apply one setpoint for apply_h simulated hours. Each seq applies at most once."""
        u = self.unit
        if sp.seq <= self.applied_seq:
            return Ack(u.unit_id, sp.seq, False, sp.power_kw, 0.0, 0.0, "duplicate_seq")
        try:
            check_setpoint(u, sp, hours)
        except ReserveViolation as exc:
            self.power_kw = 0.0
            return Ack(u.unit_id, sp.seq, False, sp.power_kw, 0.0, 0.0, f"refused: {exc}")
        delivered = sp.power_kw * self.faults.get("under_delivery", 1.0)
        energy = delivered * apply_h
        u.soc -= energy / (u.capacity_kwh * u.health)
        u.last_seen = now
        self.applied_seq = sp.seq
        self.power_kw = delivered
        self.energy_kwh += energy
        self._beat()
        return Ack(u.unit_id, sp.seq, True, sp.power_kw, delivered, energy)

    def submit(self, sp: Setpoint, hours: float, apply_h: float, now: datetime) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self.inbox.put_nowait((sp, hours, apply_h, now, fut))
        return fut
