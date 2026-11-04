"""Chat Effort selector — operator-facing compute ceiling.

ZCODE-AMCA-STUDIO-NEXT-MODEL-SETTINGS-SURFACE-06

The composer's Effort selector (Low / High / Max) scales ONE thing: the
run's bounded frontier-call ceiling — low is the v1.1.10 baseline, high x2,
max x3. It changes how long a run may keep working (more tool iterations and
continuations within the same scope); it never changes what the run may
touch, never skips approvals, and never widens a grant.

Pinned contracts:

* only low/high/max are accepted — anything else is a clean ValueError;
* low is the default and is the exact v1.1.10 ceiling (factor 1), so
  existing runs behave identically unless the operator asks for more;
* the effort travels on the persisted job.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

import pytest  # noqa: E402

from amca.service import (  # noqa: E402
    CHAT_EFFORT_FACTORS,
    RunJob,
    normalize_chat_effort,
)


def test_effort_factors_pin_the_baseline_and_the_steps():
    assert CHAT_EFFORT_FACTORS == {"low": 1, "high": 2, "max": 3}


def test_normalize_accepts_the_three_levels_and_the_default():
    assert normalize_chat_effort("low") == "low"
    assert normalize_chat_effort("high") == "high"
    assert normalize_chat_effort("max") == "max"
    assert normalize_chat_effort("MAX") == "max"
    # Absent / empty means the baseline, never an error.
    assert normalize_chat_effort(None) == "low"
    assert normalize_chat_effort("") == "low"


def test_normalize_rejects_unknown_efforts_cleanly():
    for bad in ("turbo", "unbounded", "infinite", "1", ["low"], object()):
        with pytest.raises(ValueError, match="effort must be one of low, high, max"):
            normalize_chat_effort(bad)


def test_run_job_carries_effort_and_defaults_to_the_baseline():
    job = RunJob(run_id="r", project_id=None, goal="g", effort="max")
    assert job.effort == "max"
    assert RunJob(run_id="r", project_id=None, goal="g").effort == "low"
    assert job.to_dict()["effort"] == "max"
