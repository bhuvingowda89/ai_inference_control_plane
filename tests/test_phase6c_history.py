"""Historical observations require a real validated source, never fabricated values."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from saferoll.artifacts import canonical_json
from saferoll.config import load_config
from saferoll.faults.phase4e_config import BehaviorFault, ObservationFault, Phase4EConfig, RequestFault
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

ROOT = Path(__file__).parents[1]


def lines(path: Path, name: str) -> list[dict]:
    return [json.loads(line) for line in (path / name).read_text(encoding='utf-8').splitlines()]


def historical_pair(work: Path):
    base = load_config(ROOT / 'configs/phase6b-latency_severity.yaml')
    prior = replace(base, rollout=replace(base.rollout, epoch=1),
                    candidate=replace(base.candidate, revision='candidate-v2'))
    source = run_experiment(prior, work / 'prior')
    assert validate_run(source)['valid']
    current = replace(base, rollout=replace(base.rollout, epoch=2),
        candidate=replace(base.candidate, revision='candidate-v3'),
        phase4e=Phase4EConfig(True))
    evidence = source / 'behavior_evidence.jsonl'
    index = work / 'history_index.json'
    index.write_text(canonical_json({'schema_version':'1.0','campaign_id':'phase6c-prefreeze-v1',
        'entries':{str(base.experiment.seed):{'run_dir':source.relative_to(ROOT).as_posix(),
            'behavior_evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}}})+'\n',encoding='utf-8')
    fault=ObservationFault('real-history','historical_evidence_replay','behavior','candidate',2,1,
        source_window=2,historical_index_path=index.relative_to(ROOT).as_posix(),
        historical_index_sha256=hashlib.sha256(index.read_bytes()).hexdigest())
    current=replace(current,phase4e=Phase4EConfig(True,
        behavior_faults=(BehaviorFault('v3-regression','classification_regression','candidate',2,1,4),),
        observation_faults=(fault,)))
    return current, source


def test_real_history_attribution_and_corruption() -> None:
    with TemporaryDirectory(dir=ROOT / 'runs', prefix='phase6c-history-test-') as directory:
        current, source=historical_pair(Path(directory))
        run=run_experiment(current,Path(directory)/'current')
        assert validate_run(run)=={'valid':True,'issues':[]}
        observations=lines(run,'observations.jsonl')
        replay=next(o for o in observations if 'real-history' in o['fault_ids'])
        assert replay['source_revision']=='candidate-v2'
        assert replay['ground_truth_reference']['epoch']==1
        assert replay['observed_value']['revision']=='candidate-v2'
        assert replay['observed_value']['epoch']==1
        assert replay['expected_ground_truth_reference']['epoch']==2
        assert replay['observed_value']['accuracy']==1.0
        assert replay['ground_truth_reference']['source_run_dir']==source.relative_to(ROOT).as_posix()
        assert lines(run,'decisions.jsonl')[0]['decision']=='HOLD'
        assert lines(run,'decision_evaluations.jsonl')[0]['ground_truth_state']=='UNSAFE'

        replay['ground_truth_reference']['revision']='candidate-v3'
        (run/'observations.jsonl').write_text(''.join(canonical_json(o)+'\n' for o in observations),encoding='utf-8')
        assert not validate_run(run)['valid']


def test_historical_source_mutation_is_rejected() -> None:
    with TemporaryDirectory(dir=ROOT / 'runs', prefix='phase6c-history-test-') as directory:
        current, source=historical_pair(Path(directory))
        run=run_experiment(current,Path(directory)/'current')
        assert validate_run(run)['valid']
        records=lines(source,'behavior_evidence.jsonl')
        records[5]['revision']='candidate-v3'
        (source/'behavior_evidence.jsonl').write_text(''.join(canonical_json(o)+'\n' for o in records),encoding='utf-8')
        assert not validate_run(run)['valid']


def test_periodic_candidate_failures_have_exact_candidate_count(tmp_path: Path) -> None:
    base=load_config(ROOT/'configs/phase6b-latency_severity.yaml')
    fault=RequestFault('periodic','candidate_request_failure',45,64,every_nth_candidate=4)
    config=replace(base,phase4e=Phase4EConfig(True,request_faults=(fault,)))
    run=run_experiment(config,tmp_path)
    assert validate_run(run)['valid']
    import csv
    with (run/'requests.csv').open(newline='',encoding='utf-8') as stream:
        rows=list(csv.DictReader(stream))
    candidate=[r for r in rows if 45<=int(r['request_index'])<=64 and r['backend_role']=='candidate']
    failed=[r for r in candidate if r['success']=='False']
    assert len(failed)==len(candidate)//4
