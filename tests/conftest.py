from pathlib import Path

import pytest

from jap.profile import load_profile, load_tracks

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile():
    return load_profile(ROOT / "examples" / "profile" / "master_profile.yaml")


@pytest.fixture
def tracks():
    return load_tracks(ROOT / "examples" / "profile" / "tracks.yaml")


@pytest.fixture
def plan_json():
    return (ROOT / "fixtures" / "sample_plan.json").read_text()


@pytest.fixture
def templates():
    return ROOT / "templates"
