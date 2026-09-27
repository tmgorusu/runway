"""Append-only, fsynced JSONL journal for the dispatcher.

Two record types:
- batch:  written *before* setpoints go out (write-ahead). Carries the seq, the
          simulated time, and every setpoint.
- commit: written after the workers acked, with the energy each unit delivered.

On restart, committed seqs are never re-applied; a batch without a commit is
re-sent once with the same seq and the same setpoints. A torn trailing line from
a kill mid-write is ignored.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JournalState:
    batches: dict[int, dict] = field(default_factory=dict)
    commits: dict[int, dict] = field(default_factory=dict)
    commit_counts: dict[int, int] = field(default_factory=dict)

    @property
    def last_seq(self) -> int:
        return max(self.batches, default=0)

    @property
    def pending(self) -> dict | None:
        """The newest batch that was written but never committed."""
        seq = self.last_seq
        return self.batches[seq] if seq and seq not in self.commits else None

    def energy_by_unit(self) -> dict[int, float]:
        out: dict[int, float] = {}
        for rec in self.commits.values():
            for uid, kwh in rec["energy"]:
                out[uid] = out.get(uid, 0.0) + kwh
        return out


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def append(self, record: dict) -> None:
        self._fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()

    @staticmethod
    def replay(path: str | Path) -> JournalState:
        state = JournalState()
        path = Path(path)
        if not path.exists():
            return state
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn write from a kill
            if rec["t"] == "batch":
                state.batches[rec["seq"]] = rec
            elif rec["t"] == "commit":
                state.commits.setdefault(rec["seq"], rec)
                state.commit_counts[rec["seq"]] = state.commit_counts.get(rec["seq"], 0) + 1
        return state
