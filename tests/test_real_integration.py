"""Installed-model integration checks. Run with: python3 -m pytest -q -m real_backend."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import pytest

from saferoll.backends.llama_cpp import preflight
from saferoll.config import load_config
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

ROOT = Path(__file__).parents[1]


@pytest.mark.real_backend
@pytest.mark.parametrize('scenario', ['healthy', 'behavior-regression', 'classification'])
def test_pinned_llama_cpp_run(scenario: str, tmp_path: Path) -> None:
    config = load_config(ROOT / f'configs/phase4g-real-{scenario}.yaml')
    checks = preflight(config)['checks']
    if not all(checks.values()):
        pytest.skip(f'pinned model/runtime/local port unavailable: {checks}')
    root = run_experiment(config, tmp_path)
    assert validate_run(root)['valid']
    with (root / 'requests.csv').open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert rows and all(r['timing_mode'] == 'MEASURED' and float(r['latency_ms']) >= 0 for r in rows)
    assert {r['backend_role'] for r in rows} == {'baseline', 'candidate'}
    probes = [json.loads(line) for line in (root / 'behavior_probes.jsonl').read_text().splitlines()]
    assert probes and all('raw_output' in p and 'normalized_output' in p for p in probes)
    backend = json.loads((root / 'backend_runs.jsonl').read_text())
    assert backend['startup_result'] == 'READY' and backend['shutdown_result'] == 'STOPPED'
    assert backend['model_sha256'] == config.real_runtime.model_sha256
