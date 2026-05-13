from pathlib import Path

import pytest

from autoapplyer.config import load_profile


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile():
    return load_profile(ROOT / "profile.example.yml")

