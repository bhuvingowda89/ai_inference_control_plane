#!/usr/bin/env python3
"""Phase 8 entrypoint: execute only a reviewed, preflighted final-v1 plan."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from saferoll.artifacts import canonical_json, config_hash
from saferoll.campaign.final_freeze import DIRECTORY, preflight
from saferoll.config import config_from_dict
from saferoll.runner import run_experiment
from saferoll.validator import validate_run


def main() -> None:
    check=preflight(require_frozen=True)
    if not check['valid']:
        raise SystemExit('final campaign preflight failed: '+', '.join(check['issues']))
    plan=json.loads((DIRECTORY/'campaign_plan.json').read_text())
    index=DIRECTORY/'run_index.jsonl'
    prior=[json.loads(x) for x in index.read_text().splitlines()] if index.exists() else []
    completed={x['run_id'] for x in prior if x['status']=='VALID'}
    for row in plan['rows']:
        if row['run_id'] in completed: continue
        raw=json.loads((DIRECTORY/row['config_path']).read_text())
        config=config_from_dict(raw)
        if config_hash(config)!=row['config_sha256']: raise SystemExit('planned config mutation')
        slot=DIRECTORY/'runs'/row['run_id']
        slot.mkdir(parents=True,exist_ok=True)
        run=run_experiment(config,slot)
        validation=validate_run(run)
        record={'run_id':row['run_id'],'run_dir':run.relative_to(DIRECTORY).as_posix(),
                'status':'VALID' if validation['valid'] else 'INVALID','validation':validation,
                'freeze_sha256':check['freeze_sha256']}
        with index.open('a') as stream: stream.write(canonical_json(record)+'\n')
        if not validation['valid']: raise SystemExit('invalid attempt preserved; campaign stopped')


if __name__=='__main__': main()
