"""Once Wear's monotonicity test is green, the demo must not run on the stub cost."""

import subprocess
import sys

import pytest

from runway import allocate, demo, ingest
from runway.stub_cost import stub_cost

MARGINAL_TEST = ingest.ROOT / "tests" / "test_marginal.py"


def test_demo_uses_marginal_wear_once_monotone():
    if not MARGINAL_TEST.exists():
        pytest.skip("Wear's tests/test_marginal.py not in the repo yet")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(MARGINAL_TEST), "-k", "monoton or decrease"],
                       cwd=ingest.ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("Wear's monotonicity test is not green yet; stub stays, labeled cost=stub")
    assert allocate.cost_name() == "marginal_wear"
    assert demo._cost is not stub_cost
