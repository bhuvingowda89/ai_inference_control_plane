#!/usr/bin/env python3
"""Paired every-eighth-candidate error calibration, separate from the 78-run plan."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

from saferoll.artifacts import canonical_json, config_hash
from saferoll.config import config_from_dict
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

CAMPAIGN=ROOT/'campaigns/phase6c-prefreeze-v1'


def main(severe: bool = False) -> None:
    design_name='error_severe_extension_design.json' if severe else 'error_extension_design.json'
    design=json.loads((CAMPAIGN/design_name).read_text(encoding='utf-8'))
    plan={row['run_id']:row for row in json.loads((CAMPAIGN/'campaign_plan.json').read_text(encoding='utf-8'))['rows']}
    nth=2 if severe else 8
    if design['new_every_nth_candidate']!=nth or len(design['selected_parent_runs'])!=6:
        raise ValueError('unexpected error-extension design')
    results=[]
    for selected in design['selected_parent_runs']:
        row=plan[selected['run_id']]
        if (row['scenario']!='error_levels' or row['policy']!=selected['policy']
                or row['seed']!=selected['seed'] or row['repetition']!=selected['repetition']
                or row['sweep']!={'phase4e.request_faults.0.every_nth_candidate':10}):
            raise ValueError('error extension no longer matches the frozen parent plan')
        config=config_from_dict(json.loads((CAMPAIGN/row['config_path']).read_text(encoding='utf-8')))
        fault=replace(config.phase4e.request_faults[0],every_nth_candidate=nth)
        config=replace(config,phase4e=replace(config.phase4e,request_faults=(fault,)),
            experiment=replace(config.experiment,name=config.experiment.name+f'-every{nth}-extension'),
            rollout=replace(config.rollout,rollout_id=config.rollout.rollout_id+f'-every{nth}-extension'))
        slot=CAMPAIGN/('error_severe_extension' if severe else 'error_extension')/row['run_id']
        existing=next((p for p in sorted(slot.glob('run-*')) if
            json.loads((p/'manifest.json').read_text(encoding='utf-8')).get('config_sha256')==config_hash(config)
            and validate_run(p)['valid']),None) if slot.exists() else None
        run=existing or run_experiment(config,slot)
        if not validate_run(run)['valid']:
            raise ValueError(f'invalid error extension: {run}')
        inputs=[json.loads(line) for line in (run/'policy_inputs.jsonl').read_text(encoding='utf-8').splitlines()]
        window=inputs[0]['expected_window_id']
        candidate=next(json.loads(line) for line in (run/'system_evidence.jsonl').read_text(encoding='utf-8').splitlines()
                       if json.loads(line)['window_id']==window and json.loads(line)['backend_role']=='candidate')
        decision=json.loads((run/'decisions.jsonl').read_text(encoding='utf-8').splitlines()[0])
        evaluation=json.loads((run/'decision_evaluations.jsonl').read_text(encoding='utf-8').splitlines()[0])
        results.append({'policy':row['policy'],'repetition':row['repetition'],'seed':row['seed'],
                        'run_dir':run.relative_to(ROOT).as_posix(),
                        'candidate_samples':candidate['sample_count'],
                        'candidate_failures':candidate['failure_count'],
                        'candidate_error_rate':candidate['error_rate'],
                        'decision':decision['decision'],
                        'ground_truth':evaluation['ground_truth_state'],
                        'correctness':evaluation['correctness_class']})
    path=CAMPAIGN/('error_severe_extension_results.json' if severe else 'error_extension_results.json')
    content=canonical_json({'schema_version':'1.0','campaign_id':'phase6c-prefreeze-v1',
                            'design':design_name,'rows':results})+'\n'
    if path.exists() and path.read_text(encoding='utf-8')!=content:
        raise ValueError('error extension results differ from prior execution')
    path.write_text(content,encoding='utf-8')
    print(json.dumps({'runs':len(results),'min_error_rate':min(r['candidate_error_rate'] for r in results),
                      'max_error_rate':max(r['candidate_error_rate'] for r in results)},sort_keys=True))


if __name__=='__main__':
    main('--severe' in sys.argv[1:])
