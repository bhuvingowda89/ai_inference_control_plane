from pathlib import Path
import pytest
from saferoll.config import load_config
from saferoll.runner import run_experiment


@pytest.fixture
def config():
    return load_config(Path(__file__).parents[1] / "configs/phase4a-smoke.yaml")


@pytest.fixture
def run_dir(config, tmp_path):
    return run_experiment(config, tmp_path)
