"""Paths and deterministic writers shared by every stage."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PARQUET = DATA / "parquet"
FIXTURE = DATA / "fixture"
HANDOFF = ROOT / "handoff"
OUTPUTS = ROOT / "outputs"
ASSUMPTIONS = ROOT / "assumptions"
PHYSICS = ROOT / "physics"
WEB = ROOT / "web"
CALLING_RULE = ROOT / "CALLING_RULE.md"


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def dumps(obj) -> str:
    return json.dumps(_clean(obj), indent=2) + "\n"


def write_json(path: Path, obj) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(obj))
    return path


def read_json(path: Path):
    return json.loads(Path(path).read_text())


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rule_hash() -> str:
    return sha256_file(CALLING_RULE)
