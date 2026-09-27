import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from runway.contracts import Setpoint, UnitState
from runway.worker import UnitWorker

T0 = datetime(2025, 6, 19, 21, 15, tzinfo=timezone.utc)
HB = 0.01


async def _worker(**kw):
    board = {}
    w = UnitWorker(UnitState(unit_id=1, **kw), board, heartbeat_s=HB)
    w.start()
    return w, board


def run(coro):
    return asyncio.run(coro)


def test_heartbeat_and_execute():
    async def go():
        w, board = await _worker()
        first = board[1].wall
        await asyncio.sleep(HB * 3)
        assert board[1].wall > first
        ack = await w.submit(Setpoint(1, 10.0, seq=1), 1.5, 0.25, T0)
        assert ack.accepted and ack.energy_kwh == pytest.approx(2.5)
        assert board[1].power_kw == 10.0 and board[1].seq == 1 and board[1].soc < 1.0
        await w.stop()
    run(go())


def test_offline_stops_heartbeats_and_acks():
    async def go():
        w, board = await _worker()
        w.inject("offline")
        before = board[1].wall
        await asyncio.sleep(HB * 5)
        assert board[1].wall == before
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(w.submit(Setpoint(1, 5.0, seq=1), 1.5, 0.25, T0), HB * 5)
        await w.stop()
    run(go())


def test_derate_changes_telemetry_and_limits():
    async def go():
        w, board = await _worker()
        w.inject("derated", 0.5)
        await asyncio.sleep(HB * 3)
        assert board[1].p_max_kw == pytest.approx(10.0)
        ack = await w.submit(Setpoint(1, 15.0, seq=1), 0.5, 0.25, T0)
        assert not ack.accepted and "refused" in ack.reason
        await w.stop()
    run(go())


def test_stale_freezes_payload_but_keeps_liveness():
    async def go():
        w, board = await _worker()
        w.inject("stale")
        await w.submit(Setpoint(1, 10.0, seq=1), 1.5, 0.25, T0 + timedelta(minutes=15))
        await asyncio.sleep(HB * 3)
        assert board[1].soc == 1.0 and board[1].seq == 0 and board[1].last_seen < T0  # frozen payload
        assert w.unit.soc < 1.0  # the battery itself moved
        wall = board[1].wall
        await asyncio.sleep(HB * 3)
        assert board[1].wall > wall  # still alive
        await w.stop()
    run(go())


def test_under_delivery_visible():
    async def go():
        w, board = await _worker()
        w.inject("under_delivery", 0.6)
        ack = await w.submit(Setpoint(1, 10.0, seq=1), 1.5, 0.25, T0)
        assert ack.delivered_kw == pytest.approx(6.0) and board[1].power_kw == pytest.approx(6.0)
        await w.stop()
    run(go())


def test_worker_refuses_setpoint_entering_reserve():
    async def go():
        w, board = await _worker()
        ack = await w.submit(Setpoint(1, 20.0, seq=1), 1.5, 0.25, T0)  # 30 kWh > 27.44 above reserve
        assert not ack.accepted and w.unit.soc == 1.0
        w2, _ = await _worker(soc=0.30)
        ack2 = await w2.submit(Setpoint(1, 0.1, seq=1), 1.0, 0.25, T0)
        assert not ack2.accepted
        await w.stop(); await w2.stop()
    run(go())


def test_each_seq_applies_once():
    async def go():
        w, _ = await _worker()
        a1 = await w.submit(Setpoint(1, 10.0, seq=1), 1.5, 0.25, T0)
        a2 = await w.submit(Setpoint(1, 10.0, seq=1), 1.5, 0.25, T0)
        assert a1.accepted and not a2.accepted and a2.reason == "duplicate_seq"
        assert w.energy_kwh == pytest.approx(2.5)
        await w.stop()
    run(go())
