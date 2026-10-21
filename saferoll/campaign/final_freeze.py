"""Final-v1 immutable input verification and canonical freeze hashing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from ..artifacts import canonical_json, config_hash, write_json
from ..config import config_from_dict
from ..research.configuration import POLICY_NAMES, POLICY_VERSION
from ..schemas import SCHEMA_VERSION
from ..validator import validate_run
from ..behavior.workload import build_workload
from .plan import derived_seed

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'campaigns/final-v1'
REFERENCES = ('campaign_plan.json','seed_design.json','history_index.json','real_campaign_spec.json')


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hash(paths: list[Path]) -> str:
    h=hashlib.sha256()
    for path in sorted(paths):
        h.update(path.relative_to(ROOT).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return h.hexdigest()


def canonical_freeze_hash(spec: dict[str,Any], directory: Path = DIRECTORY) -> str:
    """Hash normalized FROZEN spec plus exact referenced scientific manifests."""
    data=dict(spec)
    data.pop('freeze_sha256',None)
    if data.get('status')!='FROZEN':
        raise ValueError('only a reviewed FROZEN specification has a freeze hash')
    references={name:file_hash(directory/name) for name in REFERENCES}
    return hashlib.sha256(canonical_json({'specification':data,'references':references}).encode()).hexdigest()


def preflight(directory: Path = DIRECTORY, *, require_frozen: bool = False, check_clean: bool = True) -> dict[str,Any]:
    issues=[]
    freeze=json.loads((directory/'campaign_freeze.json').read_text())
    plan=json.loads((directory/'campaign_plan.json').read_text())
    seed_design=json.loads((directory/'seed_design.json').read_text())
    history=json.loads((directory/'history_index.json').read_text())
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    dirty=bool(subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True,check=True).stdout)
    if check_clean and dirty: issues.append('DIRTY_TREE')
    if freeze.get('git_dirty') is not False: issues.append('RECORDED_DIRTY_TREE')
    if revision!=freeze['source_git_revision']: issues.append('SOURCE_REVISION_MISMATCH')
    source_paths=list((ROOT/'saferoll').rglob('*.py'))+list((ROOT/'scripts').glob('*.py'))+list((ROOT/'configs').rglob('*.yaml'))
    analysis_paths=list((ROOT/'analysis').rglob('*.py'))+[ROOT/'saferoll/research/oracle.py']
    if source_hash(source_paths)!=freeze['source_tree_sha256']: issues.append('SOURCE_TREE_MISMATCH')
    if source_hash(analysis_paths)!=freeze['analysis_source_sha256']: issues.append('ANALYSIS_SOURCE_MISMATCH')
    if freeze.get('analysis_version')!='1.0': issues.append('ANALYSIS_VERSION_MISMATCH')
    for filename,key in [('campaign_plan.json','plan_sha256'),('seed_design.json','seed_design_sha256'),
                         ('history_index.json','history_index_sha256'),('real_campaign_spec.json','real_campaign_spec_sha256')]:
        if file_hash(directory/filename)!=freeze[key]: issues.append(filename.upper()+'_MISMATCH')
    if freeze['artifact_schema_version']!=SCHEMA_VERSION or plan['schema_version']!=freeze['schema_version']:
        issues.append('SCHEMA_VERSION_MISMATCH')
    if tuple(plan['policy_order'])!=POLICY_NAMES or set(freeze['policies'])!=set(POLICY_NAMES):
        issues.append('POLICY_SET_MISMATCH')
    if any(p['version']!=POLICY_VERSION for p in freeze['policies'].values()): issues.append('POLICY_VERSION_MISMATCH')
    if seed_design['base']!=7300 or seed_design['routing_repetitions']!=10 or seed_design['fixed_repetitions']!=3:
        issues.append('SEED_DESIGN_MISMATCH')
    if len(plan['rows'])!=freeze['planned_mock_runs']: issues.append('PLAN_COUNT_MISMATCH')
    scenarios={(x['scenario'],x['severity']):x for x in freeze['scenarios']}
    if any(x.get('validated')!='VALIDATED' for x in freeze['scenarios']): issues.append('UNVALIDATED_SCENARIO')
    forbidden={'expected_decision','expected_policy_decision','expected_outcome','expected_promotion'}
    if any(forbidden & set(x) for x in freeze['scenarios']): issues.append('EXPECTED_DECISION_IN_SCENARIO')
    by_pair={}
    for row in plan['rows']:
        item=scenarios.get((row['scenario'],row['severity']))
        if item is None or row['scenario_version']!=item['scenario_version']:
            issues.append('SCENARIO_VERSION_MISMATCH'); continue
        expected=derived_seed(7300,row['scenario'],'paired',row['repetition'],{'severity':row['severity']})
        if row['seed']!=expected: issues.append('SEED_MISMATCH')
        if row['repetition'] >= (10 if item['routing_sensitive'] else 3): issues.append('REPETITION_MISMATCH')
        by_pair.setdefault(row['pairing_key'],[]).append(row)
        config_path=directory/row['config_path']
        if not config_path.is_file(): issues.append('MISSING_RUN_CONFIG'); continue
        raw=json.loads(config_path.read_text())
        if config_hash(config_from_dict(raw))!=row['config_sha256'] or hashlib.sha256(canonical_json(raw['policy']).encode()).hexdigest()!=row['threshold_signature']:
            issues.append('CONFIG_OR_THRESHOLD_MISMATCH')
        t=freeze['thresholds']; p=raw['policy']
        actual={'max_error_rate':p['system']['max_error_rate'],
                'max_latency_regression_fraction':p['system']['max_latency_regression_fraction'],
                'max_accuracy_drop':p['behavior']['max_accuracy_drop'],
                'max_exact_match_drop':p['behavior']['max_exact_match_drop'],
                'min_structure_valid_rate':p['behavior']['min_structure_valid_rate'],
                'minimum_system_samples':raw['evidence']['minimum_samples_per_revision'],
                'minimum_behavior_samples':raw['behavior_evidence']['minimum_samples_per_revision'],
                'require_fresh_system':p['evidence']['require_fresh_system'],
                'require_fresh_behavior':p['evidence']['require_fresh_behavior'],
                'routing_consistency_tolerance':p['numeric_tolerance']}
        if actual!=t: issues.append('FROZEN_THRESHOLD_MISMATCH')
        template=ROOT/item['template']
        if file_hash(template)!=item['template_sha256']: issues.append('SCENARIO_TEMPLATE_MISMATCH')
        if row['policy'] not in POLICY_NAMES or row['policy_version']!=POLICY_VERSION:
            issues.append('RUN_POLICY_VERSION_MISMATCH')
        if row['scenario']=='historical_attribution':
            fault=raw['phase4e']['observation_faults'][0]
            if fault['historical_index_sha256']!=file_hash(directory/'history_index.json') or str(row['seed']) not in history['entries']:
                issues.append('HISTORY_REFERENCE_MISMATCH')
            else:
                source_workload=json.loads((ROOT/history['entries'][str(row['seed'])]['run_dir']/'behavior_workload.json').read_text())
                current_workload=build_workload(config_from_dict(raw))
                if current_workload['items']!=source_workload['items'] or current_workload['workload_id']!=source_workload['workload_id']:
                    issues.append('HISTORY_WORKLOAD_MISMATCH')
    if any({r['policy'] for r in rows}!=set(POLICY_NAMES) or len({r['seed'] for r in rows})!=1 for rows in by_pair.values()):
        issues.append('PAIRING_MISMATCH')
    for seed,entry in history['entries'].items():
        run=ROOT/entry['run_dir']
        if not run.is_dir() or not validate_run(run)['valid'] or file_hash(run/'behavior_evidence.jsonl')!=entry['behavior_evidence_sha256']:
            issues.append('HISTORY_SOURCE_INVALID'); continue
        raw=json.loads((run/'config.resolved.json').read_text())
        if raw['experiment']['seed']!=int(seed) or raw['rollout']['epoch']!=1 or raw['candidate']['revision']!='candidate-v2' or file_hash(run/'behavior_workload.json')!=entry['workload_sha256'] or file_hash(run/'config.resolved.json')!=entry['source_config_sha256']:
            issues.append('HISTORY_IDENTITY_OR_WORKLOAD_MISMATCH')
    real=json.loads((directory/'real_campaign_spec.json').read_text())
    if real['model_sha256_actual']!=real['runtime']['model_sha256'] or file_hash(ROOT/real['runtime']['model_path'])!=real['runtime']['model_sha256']:
        issues.append('REAL_MODEL_MISMATCH')
    if 'build 11146' not in real['llama_server_version'] or '7fe450e19' not in real['llama_server_version']:
        issues.append('REAL_RUNTIME_MISMATCH')
    binary=Path(real['llama_server_binary'])
    if not binary.is_file() or file_hash(binary)!=real['llama_server_binary_sha256']:
        issues.append('REAL_RUNTIME_BINARY_MISMATCH')
    if not real['pilot_valid'] or real['final_repetitions'] is None: issues.append('REAL_PILOT_INCOMPLETE')
    for row in real.get('final_runs',[]):
        path=directory/row['config_path']
        if not path.is_file() or config_hash(config_from_dict(json.loads(path.read_text())))!=row['config_sha256']:
            issues.append('REAL_FINAL_CONFIG_MISMATCH')
    if real.get('pilot_valid') and len(real.get('final_runs',[]))!=2*real['final_repetitions']:
        issues.append('REAL_REPETITION_COUNT_MISMATCH')
    if require_frozen:
        if freeze['status']!='FROZEN': issues.append('NOT_FROZEN')
        elif canonical_freeze_hash(freeze,directory)!=freeze.get('freeze_sha256'): issues.append('FREEZE_HASH_MISMATCH')
    return {'schema_version':'1.0','valid':not issues,'issues':sorted(set(issues)),'status':freeze['status'],
            'source_git_revision':revision,'git_dirty':dirty,'plan_sha256':file_hash(directory/'campaign_plan.json'),
            'freeze_sha256':freeze.get('freeze_sha256')}


def main() -> None:
    result=preflight()
    write_json(DIRECTORY/'preflight.json',result)
    print(canonical_json(result))
    if not result['valid']: raise SystemExit(1)


if __name__=='__main__': main()
