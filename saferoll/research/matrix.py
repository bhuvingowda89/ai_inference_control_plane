"""One scenario, fixed thresholds and initial exposure, five controller capabilities."""
from __future__ import annotations
from dataclasses import replace
import json
from pathlib import Path
from typing import Any
from ..config import Config
from .configuration import POLICY_NAMES


def with_policy(config: Config, name: str) -> Config:
    if name not in POLICY_NAMES:
        raise ValueError('unsupported policy override')
    updated=replace(config,policy=replace(config.policy,type=name))
    return replace(updated,experiment=replace(updated.experiment,request_count=updated.planned_requests))


def run_policy_matrix(config: Config, output_root: str | Path='runs') -> Path:
    from ..runner import run_experiment
    from ..artifacts import config_hash,write_json
    root=Path(output_root);root.mkdir(parents=True,exist_ok=True)
    stem='matrix-'+config_hash(config)
    directory=root/stem;index=1
    while directory.exists():
        directory=root/f'{stem}-{index:03d}';index+=1
    directory.mkdir()
    rows=[]
    for name in POLICY_NAMES:
        run=run_experiment(with_policy(config,name),directory)
        decisions=[json.loads(line) for line in (run/'decisions.jsonl').read_text().splitlines()]
        evaluations=[json.loads(line) for line in (run/'decision_evaluations.jsonl').read_text().splitlines()]
        summary=json.loads((run/'summary.json').read_text())
        for sequence,(decision,evaluation) in enumerate(zip(decisions,evaluations)):
            rows.append({'scenario':config.experiment.name,'policy':name,'decision_index':sequence,
                'decision':decision['decision'],'ground_truth_state':evaluation['ground_truth_state'],
                'correctness_class':evaluation['correctness_class'],'critical_error':evaluation['critical_error'],
                'reason_codes':decision['reason_codes'],'run_dir':str(run),'decision_id':decision['decision_id'],
                'final_lifecycle_state':summary['final_lifecycle_state'],'final_outcome':summary['final_outcome']})
    path=directory/'policy_matrix.json'
    write_json(path,{'schema_version':'1.0','scenario':config.experiment.name,'policy_order':list(POLICY_NAMES),'rows':rows})
    return path
