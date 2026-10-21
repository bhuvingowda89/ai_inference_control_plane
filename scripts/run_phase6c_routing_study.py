#!/usr/bin/env python3
"""Run paired ten-seed 20-versus-40-request sufficiency study."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

from saferoll.artifacts import canonical_json, config_hash
from saferoll.campaign.plan import derived_seed
from saferoll.config import load_config
from saferoll.research.matrix import with_policy
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

CAMPAIGN=ROOT/'campaigns/phase6c-prefreeze-v1'


def records(path: Path, name: str) -> list[dict]:
    return [json.loads(line) for line in (path/name).read_text(encoding='utf-8').splitlines()]


def main(extension: bool = False) -> None:
    design_path=CAMPAIGN/('routing_study_extension_design.json' if extension else 'seed_design.json')
    design=json.loads(design_path.read_text(encoding='utf-8'))
    if not extension:
        design=design['routing_study']
    expected=[derived_seed(6301,'routing-budget','ops_gate',i,{}) for i in range(10)]
    if design['seeds']!=expected or design['evaluation_request_counts']!=([60] if extension else [20,40]):
        raise ValueError('routing-study seeds or budgets differ from the prespecified design')
    base=load_config(ROOT/'configs/phase6c-latency_levels.yaml')
    rows=[]
    for budget in design['evaluation_request_counts']:
        for index,seed in enumerate(expected):
            config=replace(base,routing=replace(base.routing,baseline_weight=.8,candidate_weight=.2),
                lifecycle=replace(base.lifecycle,evaluation_requests=budget),
                rollout=replace(base.rollout,rollout_id=f'phase6c-routing-study-n{budget}-s{index}'),
                experiment=replace(base.experiment,name=f'phase6c-routing-study-n{budget}-s{index}',seed=seed))
            config=with_policy(config,'ops_gate')
            slot=CAMPAIGN/'routing_study'/f'n{budget}'/f'seed-{index}'
            existing=next((p for p in sorted(slot.glob('run-*')) if
                json.loads((p/'manifest.json').read_text(encoding='utf-8')).get('config_sha256')==config_hash(config)
                and validate_run(p)['valid']),None) if slot.exists() else None
            run=existing or run_experiment(config,slot)
            if not validate_run(run)['valid']:
                raise ValueError(f'invalid routing-study run: {run}')
            system=records(run,'system_evidence.jsonl')
            behavior=records(run,'behavior_evidence.jsonl')
            evaluation=next(r for r in system if r['lifecycle_state']=='EVALUATING')
            window=evaluation['window_id']
            b,c=[next(r for r in system if r['window_id']==window and r['backend_role']==role)
                 for role in ('baseline','candidate')]
            bb,cb=[next(r for r in behavior if r['window_id']==window and r['backend_role']==role)
                   for role in ('baseline','candidate')]
            decisions=records(run,'decisions.jsonl')
            summary=json.loads((run/'summary.json').read_text(encoding='utf-8'))
            rows.append({'budget':budget,'seed_index':index,'seed':seed,
                'run_dir':run.relative_to(ROOT).as_posix(),
                'candidate_requests':c['request_count'],'baseline_requests':b['request_count'],
                'candidate_system_samples':c['sample_count'],'baseline_system_samples':b['sample_count'],
                'candidate_behavior_samples':cb['sample_count'],'baseline_behavior_samples':bb['sample_count'],
                'candidate_system_sufficient':c['sufficient'],'baseline_system_sufficient':b['sufficient'],
                'candidate_behavior_sufficient':cb['sufficient'],'baseline_behavior_sufficient':bb['sufficient'],
                'first_decision':decisions[0]['decision'],'hold_count':summary['hold_count'],
                'valid':True})
    result={'schema_version':'1.0','campaign_id':'phase6c-prefreeze-v1',
            'study_design':design,'rows':rows,
            'summary':{str(budget):{'runs':sum(r['budget']==budget for r in rows),
                'candidate_system_insufficient':sum(r['budget']==budget and not r['candidate_system_sufficient'] for r in rows),
                'candidate_behavior_insufficient':sum(r['budget']==budget and not r['candidate_behavior_sufficient'] for r in rows),
                'hold_first_decisions':sum(r['budget']==budget and r['first_decision']=='HOLD' for r in rows),
                'candidate_sample_min':min(r['candidate_system_samples'] for r in rows if r['budget']==budget),
                'candidate_sample_max':max(r['candidate_system_samples'] for r in rows if r['budget']==budget)}
                for budget in design['evaluation_request_counts']}}
    path=CAMPAIGN/('routing_study_extension_results.json' if extension else 'routing_study_results.json')
    content=canonical_json(result)+'\n'
    if path.exists() and path.read_text(encoding='utf-8')!=content:
        raise ValueError('routing-study results differ from prior execution')
    path.write_text(content,encoding='utf-8')
    print(json.dumps(result['summary'],sort_keys=True))


if __name__=='__main__':
    main('--extension' in sys.argv[1:])
