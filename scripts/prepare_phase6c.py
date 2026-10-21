#!/usr/bin/env python3
"""Prepare fixed Phase 6C inputs and genuine prior-epoch evidence sources."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from saferoll.artifacts import canonical_json
from saferoll.campaign.plan import derived_seed, expand_campaign, write_plan
from saferoll.campaign.spec import load_campaign_spec
from saferoll.config import config_from_dict
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

CAMPAIGN = ROOT / 'campaigns/phase6c-prefreeze-v1'
SPEC_PATH = ROOT / 'configs/campaigns/phase6c-prefreeze-v1.yaml'
INDEX_REL = 'campaigns/phase6c-prefreeze-v1/history_index.json'


def source(name: str) -> dict:
    return yaml.safe_load((ROOT / 'configs' / name).read_text(encoding='utf-8'))


def write_template(name: str, value: dict, *, index_field: bool = False) -> str:
    path = ROOT / 'configs' / f'phase6c-{name}.yaml'
    if index_field and path.exists():
        saved = yaml.safe_load(path.read_text(encoding='utf-8'))
        value['phase4e']['observation_faults'][0]['historical_index_sha256'] = (
            saved['phase4e']['observation_faults'][0]['historical_index_sha256'])
    content = yaml.safe_dump(value, sort_keys=False)
    if path.exists() and path.read_text(encoding='utf-8') != content:
        raise ValueError(f'existing Phase 6C template differs: {path}')
    path.write_text(content, encoding='utf-8')
    return f'configs/{path.name}'


def main() -> None:
    base = source('phase6b-latency_severity.yaml')
    base['experiment']['request_count'] = 133
    base['lifecycle']['evaluation_requests'] = 40
    base['candidate']['fixed_latency_ms'] = 11
    base['fault']['end_request'] = 84
    scenarios = []

    def add(name: str, value: dict, domains: list[str], intent: str, condition: str,
            sweep: dict | None = None, *, historical: bool = False) -> None:
        value = deepcopy(value)
        value['experiment']['name'] = f'phase6c-{name}'
        value['rollout']['rollout_id'] = f'phase6c-{name}-rollout'
        template = write_template(name, value, index_field=historical)
        row = dict(id=name, version='1.0', template=template, fault_domains=domains,
                   ground_truth_intent=intent, expected_condition=condition, backend='MOCK')
        if sweep:
            row['sweep'] = sweep
        scenarios.append(row)

    historical = deepcopy(base)
    historical['rollout']['epoch'] = 2
    historical['candidate']['revision'] = 'candidate-v3'
    historical['phase4e'] = dict(enabled=True,
        behavior_faults=[dict(id='current-v3-regression', type='classification_regression',
                              target='candidate', start_window=2, duration_windows=1,
                              every_nth_item=4)],
        observation_faults=[dict(id='prior-epoch-v2-replay', type='historical_evidence_replay',
                                 source='behavior', target='candidate', start_window=2,
                                 duration_windows=1, source_window=2,
                                 historical_index_path=INDEX_REL,
                                 historical_index_sha256='0' * 64)])
    add('historical_attribution', historical, ['BEHAVIOR','OBSERVATION'], 'UNSAFE',
        'healthy persisted candidate-v2 epoch-1 evidence replayed during unsafe candidate-v3 epoch-2 evaluation',
        historical=True)

    add('latency_levels', base, ['SYSTEM'], 'INDETERMINATE',
        'candidate p95 relative increases 10, 30, 60, 100 percent',
        {'fault.added_latency_ms': [0,2,5,9]})

    behavior = deepcopy(base)
    behavior['candidate']['behavior_profile'] = 'classification_regression'
    add('behavior_levels', behavior, ['BEHAVIOR'], 'UNSAFE',
        'candidate accuracy loss 10, 20, 25 percent',
        {'candidate.regression_every_nth_item': [10,5,4]})

    error = deepcopy(base)
    error['phase4e'] = dict(enabled=True,
        request_faults=[dict(id='periodic-candidate-failure', type='candidate_request_failure',
                             start_request=45, end_request=84, target='candidate',
                             added_latency_ms=0, every_nth_candidate=100)])
    add('error_levels', error, ['SYSTEM'], 'INDETERMINATE',
        'candidate failure control, mild, moderate via every 100th, 10th, 4th candidate request',
        {'phase4e.request_faults.0.every_nth_candidate': [100,10,4]})

    structured = source('phase6b-structured_threshold.yaml')
    structured['experiment']['request_count'] = 133
    structured['lifecycle']['evaluation_requests'] = 40
    structured['lifecycle']['max_holds'] = 1
    structured['policy'] = deepcopy(base['policy'])
    structured['fault']['end_request'] = 84
    add('structured_moderate', structured, ['BEHAVIOR'], 'UNSAFE',
        '20-item structured profile produces moderate schema and exact-match regression')

    cost = source('phase6b-observed_only_cost_control.yaml')
    cost['experiment']['request_count'] = 133
    cost['lifecycle']['evaluation_requests'] = 40
    add('observed_only_cost', cost, ['ROUTING'], 'SAFE',
        'desired/effective match, observed-only mismatch; abstention-cost control')

    spec = {'campaign': {'id':'phase6c-prefreeze-v1','seed_base':6300,'repetitions':2},
            'scenarios':scenarios,'policies':['ops_gate','ai_gate','saferoll']}
    content = yaml.safe_dump(spec, sort_keys=False)
    if SPEC_PATH.exists() and SPEC_PATH.read_text(encoding='utf-8') != content:
        raise ValueError('existing Phase 6C campaign spec differs')
    SPEC_PATH.write_text(content, encoding='utf-8')

    expanded, configs = expand_campaign(load_campaign_spec(SPEC_PATH))
    archive = CAMPAIGN / 'history'
    archive.mkdir(parents=True, exist_ok=True)
    index_path = ROOT / INDEX_REL
    if not index_path.exists():
        entries = {}
        for row in expanded:
            if row['scenario'] != 'historical_attribution':
                continue
            current = deepcopy(configs[row['run_id']])
            seed = current['experiment']['seed']
            current['experiment']['name'] += '-epoch1-source'
            current['rollout']['epoch'] = 1
            current['candidate']['revision'] = 'candidate-v2'
            current['candidate']['behavior_profile'] = 'correct'
            current['phase4e'] = {'enabled':False}
            prior = config_from_dict(current)
            run = run_experiment(prior, archive / f'seed-{seed}')
            if not validate_run(run)['valid']:
                raise ValueError('generated historical run is invalid')
            evidence = run / 'behavior_evidence.jsonl'
            entries[str(seed)] = {'run_dir':run.relative_to(ROOT).as_posix(),
                                  'behavior_evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),
                                  'rollout_id':prior.rollout.rollout_id,'epoch':1,
                                  'candidate_revision':'candidate-v2'}
        index_path.write_text(canonical_json({'schema_version':'1.0',
            'campaign_id':'phase6c-prefreeze-v1','entries':entries})+'\n', encoding='utf-8')
    digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
    attr_path = ROOT / 'configs/phase6c-historical_attribution.yaml'
    attr = yaml.safe_load(attr_path.read_text(encoding='utf-8'))
    configured = attr['phase4e']['observation_faults'][0]['historical_index_sha256']
    if configured not in {'0'*64,digest}:
        raise ValueError('historical index differs from pinned template')
    attr['phase4e']['observation_faults'][0]['historical_index_sha256'] = digest
    attr_path.write_text(yaml.safe_dump(attr, sort_keys=False), encoding='utf-8')
    rows, _ = expand_campaign(load_campaign_spec(SPEC_PATH))
    final_spec = load_campaign_spec(SPEC_PATH)
    if (CAMPAIGN / 'campaign_plan.json').exists():
        directory = write_plan(final_spec)
    else:
        # Historical source evidence must exist before the plan can pin its hash.
        # Stage the ordinary immutable plan, then install it beside that archive.
        with tempfile.TemporaryDirectory(prefix='saferoll-phase6c-plan-') as temporary:
            staged = write_plan(final_spec, root=temporary)
            for name in ('campaign_plan.json','campaign_plan.csv'):
                shutil.copy2(staged / name, CAMPAIGN / name)
            shutil.copytree(staged / 'configs', CAMPAIGN / 'configs')
            for name in ('runs','aggregate','validation'):
                (CAMPAIGN / name).mkdir(exist_ok=True)
        directory = write_plan(final_spec)
    seed_design = {'schema_version':'1.0','campaign_id':'phase6c-prefreeze-v1',
        'routing_study':{'seed_base':6301,'seed_count':10,
            'seeds':[derived_seed(6301,'routing-budget','ops_gate',i,{}) for i in range(10)],
            'evaluation_request_counts':[20,40],
            'candidate_weight':0.2,
            'rationale':'paired local RNG streams isolate evaluation-window budget effects'},
        'proposed_final':{'seed_base':7300,'seed_count_per_routing_sensitive_condition':10,
            'derivation':'SHA-256 first 8 bytes masked to 63 bits over canonical JSON [seed_base, scenario_id, policy_id, seed_index, sweep]',
            'example_scenario':'routing_sensitive_reference',
            'example_policy':'ops_gate',
            'example_seeds':[derived_seed(7300,'routing_sensitive_reference','ops_gate',i,{}) for i in range(10)],
            'applicable_scenarios':['routing_realization','rollback_delay','partial_rollback',
                                    'observation_routing_compound','candidate_exposure_sensitive'],
            'rationale':'preserve seeded traffic variability without selecting favorable draws'}}
    seed_path=CAMPAIGN/'seed_design.json'
    seed_content=canonical_json(seed_design)+'\n'
    if seed_path.exists() and seed_path.read_text(encoding='utf-8')!=seed_content:
        raise ValueError('existing seed design differs')
    seed_path.write_text(seed_content,encoding='utf-8')
    print(json.dumps({'campaign_dir':str(directory),'planned_runs':len(rows),
                      'historical_sources':len(json.loads(index_path.read_text())['entries']),
                      'historical_index_sha256':digest},sort_keys=True))


if __name__ == '__main__':
    main()
