"""Self-contained real-adapter contract tests; no model server in ordinary pytest."""
from __future__ import annotations
from dataclasses import replace
import hashlib
import csv
import errno
import json
import re
import time
from pathlib import Path
from urllib.error import URLError
import signal
import subprocess
import pytest

from saferoll.artifacts import canonical_json, write_artifacts, write_json
from saferoll.backends.llama_cpp import LlamaCppSession, BackendFailure, preflight
from saferoll.behavior.real import normalize_output, prompt_for_item
from saferoll.config import load_config
from saferoll.runner import simulate
from saferoll.validator import validate_run

ROOT = Path(__file__).parents[1]


class ContractSession:
    """Fixed local transport fixture, never described as a model result."""
    def __init__(self) -> None:
        self.counts = {'baseline': 0, 'candidate': 0}

    def complete(self, prompt: str):
        start = time.monotonic()
        found = re.search(r'Record (\d+)', prompt)
        number = int(found.group(1)) if found else 24
        label = 'EVEN' if number % 2 == 0 else 'ODD'
        if 'without JSON' in prompt:
            output = f'The number is {number} and it is {label.lower()}.'
        elif 'JSON object' in prompt:
            output = json.dumps({'number': number, 'parity': label.lower()})
        else:
            output = label
        end = time.monotonic()
        return output, True, start, end, (end - start) * 1000, {}


def _config(tmp_path: Path, variant: str = 'healthy'):
    original = load_config(ROOT / f'configs/phase4g-real-{variant}.yaml')
    model = tmp_path / 'fixture.gguf'
    model.write_bytes(b'unit-test-placeholder-not-a-real-model')
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    return replace(original, real_runtime=replace(original.real_runtime,
                   model_path=str(model), model_sha256=digest))


def _fixture_run(tmp_path: Path, variant: str = 'healthy') -> Path:
    config = _config(tmp_path, variant)
    session = ContractSession()
    responses, events, summary = simulate(config, session)
    root = tmp_path / 'run'
    root.mkdir()
    (root / 'llama-server.log').write_text('transport fixture only\n', encoding='utf-8')
    write_json(root / 'preflight.json', {'valid': True, 'checks': {'binary_found': True,
        'model_exists': True, 'port_available': True, 'model_sha256_matches': True},
        'binary': '/fixture/llama-server', 'model_path': config.real_runtime.model_path,
        'expected_model_sha256': config.real_runtime.model_sha256})
    metadata = {'backend_instance_id': 'local-llama-server-1', 'backend_type': 'llama_cpp',
        'runtime_binary': '/fixture/llama-server', 'runtime_arguments': ['/fixture/llama-server',
            '--n-gpu-layers', '0', '--seed', str(config.experiment.seed)],
        'llama_cpp_version': 'fixture', 'llama_cpp_git_revision': None,
        'model_path': config.real_runtime.model_path, 'model_sha256': config.real_runtime.model_sha256,
        'model_file_size': Path(config.real_runtime.model_path).stat().st_size, 'model_metadata': None,
        'thread_count': config.real_runtime.thread_count, 'context_size': config.real_runtime.context_size,
        'batch_size': config.real_runtime.batch_size, 'temperature': config.real_runtime.temperature,
        'seed': config.experiment.seed, 'sampling_parameters': {'max_tokens': config.real_runtime.max_tokens,
            'temperature': config.real_runtime.temperature}, 'host_architecture': 'fixture',
        'operating_system': 'fixture', 'logical_cpu_count': 1,
        'startup_result': 'READY', 'ready_after_ms': 1.0, 'shutdown_result': 'STOPPED',
        'backend_exit_status': -15, 'process_exit_status': -15, 'process_exited': True,
        'listener_closed': True, 'shutdown_success': True, 'owned_pid': 12345,
        'listener_detected': False, 'cleanup_attempts': 1,
        'request_count_by_role': session.counts, 'log_reference': 'llama-server.log'}
    write_artifacts(root, config, responses, events, summary, real_provenance=metadata)
    return root


def test_real_normalization_and_prompt_rules():
    item = {'task_type': 'structured_output', 'input': {'text': 'Record 73 has parity odd.'}}
    assert 'JSON object' in prompt_for_item(item, 'standard')
    assert 'without JSON' in prompt_for_item(item, 'weakened_json')
    assert normalize_output('classification', ' EVEN. ') == 'even'
    assert normalize_output('classification', 'EVEN or ODD') == ''
    assert normalize_output('structured_output', '```json\n{"parity":"odd","number":73}\n```') == '{"number":73,"parity":"odd"}'
    assert normalize_output('structured_output', '{"number":73,"number":74}') == '{"number":73,"number":74}'


def test_model_hash_preflight_rejects_wrong_file(tmp_path):
    config = _config(tmp_path)
    wrong = replace(config, real_runtime=replace(config.real_runtime, model_sha256='0' * 64))
    result = preflight(wrong)
    assert result['checks']['model_exists']
    assert not result['checks']['model_sha256_matches']
    assert not result['valid']


def test_measured_path_uses_existing_evidence_and_validator(tmp_path):
    root = _fixture_run(tmp_path)
    assert validate_run(root)['valid']
    system = [json.loads(s) for s in (root / 'system_evidence.jsonl').read_text().splitlines()]
    behavior = [json.loads(s) for s in (root / 'behavior_evidence.jsonl').read_text().splitlines()]
    assert any(s['sample_count'] > 0 and s['latency_p95_ms'] >= 0 for s in system)
    assert any(b['backend_role'] == 'candidate' and b['exact_match_rate'] == 1 for b in behavior)
    probes = [json.loads(s) for s in (root / 'behavior_probes.jsonl').read_text().splitlines()]
    assert all(p['raw_output'] and p['normalized_output'] and p['evaluation_result']['exact_match'] for p in probes)


def test_real_revision_prompt_change_is_measured(tmp_path):
    root = _fixture_run(tmp_path, 'behavior-regression')
    assert validate_run(root)['valid']
    behavior = [json.loads(s) for s in (root / 'behavior_evidence.jsonl').read_text().splitlines()]
    assert any(b['backend_role'] == 'candidate' and b['structure_valid_rate'] == 0 for b in behavior)
    assert all(b['structure_valid_rate'] == 1 for b in behavior if b['backend_role'] == 'baseline')


def test_real_classification_evidence_uses_same_collector(tmp_path):
    root = _fixture_run(tmp_path, 'classification')
    assert validate_run(root)['valid']
    evidence = [json.loads(s) for s in (root / 'behavior_evidence.jsonl').read_text().splitlines()]
    assert evidence and all(row['accuracy'] == 1 for row in evidence)


def test_real_validator_rejects_raw_output_and_timing_corruption(tmp_path):
    root = _fixture_run(tmp_path)
    path = root / 'behavior_probes.jsonl'
    records = [json.loads(s) for s in path.read_text().splitlines()]
    records[0]['raw_output'] = 'corrupt'
    path.write_text(''.join(canonical_json(r) + '\n' for r in records))
    assert not validate_run(root)['valid']


@pytest.mark.parametrize('artifact,key,value', [
    ('real_provenance.json', 'model_sha256', '0' * 64),
    ('backend_runs.jsonl', 'request_count_by_role', {'baseline': 0, 'candidate': 0}),
])
def test_real_provenance_corruption_rejected(tmp_path, artifact, key, value):
    root = _fixture_run(tmp_path)
    path = root / artifact
    data = json.loads(path.read_text())
    data[key] = value
    path.write_text(canonical_json(data) + '\n')
    assert not validate_run(root)['valid']


def test_measured_serving_latency_corruption_rejected(tmp_path):
    root = _fixture_run(tmp_path)
    path = root / 'requests.csv'
    with path.open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        rows, fields = list(reader), reader.fieldnames
    rows[0]['latency_ms'] = '999999'
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    assert not validate_run(root)['valid']


def test_request_timeout_is_explicit_failed_request(tmp_path, monkeypatch):
    config = _config(tmp_path)
    session = LlamaCppSession(config, tmp_path / 'unused')
    class Process:
        def poll(self):
            return None
    session.process = Process()
    from saferoll.backends import llama_cpp
    def unavailable(*args, **kwargs):
        raise URLError(TimeoutError('timed out'))
    monkeypatch.setattr(llama_cpp.request, 'urlopen', unavailable)
    output, success, start, end, latency, usage = session.complete('probe')
    assert output == '' and success is False and end >= start and latency >= 0
    assert usage['request_error'] == 'TimeoutError'


def test_malformed_server_response_invalidates_run(tmp_path, monkeypatch):
    config = _config(tmp_path)
    session = LlamaCppSession(config, tmp_path / 'unused')
    class Process:
        def poll(self):
            return None
    session.process = Process()
    class HTTP:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return b'{"choices":[]}'
    from saferoll.backends import llama_cpp
    monkeypatch.setattr(llama_cpp.request, 'urlopen', lambda *a, **k: HTTP())
    with pytest.raises(BackendFailure, match='malformed'):
        session.complete('probe')


def _cleanup_session(tmp_path, monkeypatch, listener_states, ignore_grace=False, group=False):
    from saferoll.backends import llama_cpp
    config = _config(tmp_path)
    config = replace(config, real_runtime=replace(config.real_runtime, shutdown_timeout_seconds=0.01))
    session = LlamaCppSession(config, tmp_path / 'unused')
    class Process:
        pid = 12345
        stopped = False
        forced = False
        def terminate(self):
            if not ignore_grace: self.stopped = True
        def kill(self):
            self.stopped = True; self.forced = True
        def wait(self, timeout):
            if not self.stopped: raise subprocess.TimeoutExpired('fake-llama-server', timeout)
            return -9 if self.forced else -15
        def poll(self):
            return (-9 if self.forced else -15) if self.stopped else None
    process = Process()
    session.process = process
    session.owned_pid = process.pid
    session.owns_process_group = group
    states = iter(listener_states)
    last = listener_states[-1]
    def probe(*args, **kwargs):
        nonlocal last
        last = next(states, last)
        return last, None
    monkeypatch.setattr(llama_cpp, '_probe_listener', probe)
    return session, process


def test_normal_cleanup_process_exits_and_listener_closes(tmp_path, monkeypatch):
    session, process = _cleanup_session(tmp_path, monkeypatch, [False])
    session.__exit__(None, None, None)
    assert process.stopped and session.process_exited and session.listener_closed
    assert session.shutdown_success and session.cleanup_attempts == 1
    assert session.exit_status == -15


def test_delayed_listener_closure_is_polled(tmp_path, monkeypatch):
    session, _ = _cleanup_session(tmp_path, monkeypatch, [True, True, False])
    monkeypatch.setattr('saferoll.backends.llama_cpp.time.sleep', lambda _: None)
    session.__exit__(None, None, None)
    assert session.shutdown_success and session.listener_closed
    assert session.cleanup_attempts == 3


def test_persistent_listener_causes_diagnostic_failure(tmp_path, monkeypatch):
    session, _ = _cleanup_session(tmp_path, monkeypatch, [True])
    with pytest.raises(BackendFailure) as error:
        session.__exit__(None, None, None)
    assert session.process_exited and not session.listener_closed and not session.shutdown_success
    assert all(key in str(error.value) for key in ('owned_pid', 'process_poll_result',
           'process_exit_status', 'host', 'port', 'listener_detected', 'cleanup_attempts'))


def test_forced_group_termination_targets_only_owned_group(tmp_path, monkeypatch):
    session, process = _cleanup_session(tmp_path, monkeypatch, [False], ignore_grace=True, group=True)
    calls = []
    monkeypatch.setattr('saferoll.backends.llama_cpp.os.getpgid', lambda pid: pid)
    def killpg(pgid, sig):
        calls.append((pgid, sig))
        if sig == signal.SIGKILL:
            process.stopped = True
            process.forced = True
    monkeypatch.setattr('saferoll.backends.llama_cpp.os.killpg', killpg)
    session.__exit__(None, None, None)
    assert calls == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert session.shutdown_success and session.exit_status == -9


def test_preflight_distinguishes_refused_connection_from_active_listener(tmp_path, monkeypatch):
    from saferoll.backends import llama_cpp
    config = _config(tmp_path)
    monkeypatch.setattr(llama_cpp.shutil, 'which', lambda name: '/fixture/llama-server')
    def refused(*args, **kwargs):
        raise ConnectionRefusedError(errno.ECONNREFUSED, 'Connection refused')
    monkeypatch.setattr(llama_cpp.socket, 'create_connection', refused)
    assert preflight(config)['checks']['port_available']
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return None
    monkeypatch.setattr(llama_cpp.socket, 'create_connection', lambda *a, **k: Connection())
    busy = preflight(config)
    assert busy['listener_detected'] is True and not busy['checks']['port_available']
