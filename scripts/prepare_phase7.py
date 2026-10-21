#!/usr/bin/env python3
"""Build a reviewable final-v1 proposal; never execute planned campaign runs."""
from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import shutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from saferoll.artifacts import canonical_json, config_hash, write_json
from saferoll.campaign.plan import derived_seed
from saferoll.config import config_from_dict, load_config
from saferoll.research.configuration import POLICY_NAMES, POLICY_VERSION
from saferoll.research.matrix import with_policy
from saferoll.research.policies import Reason
from saferoll.schemas import SCHEMA_VERSION
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

OUT = ROOT / 'campaigns/final-v1'
SCHEMA = '2.0'
POLICIES = list(POLICY_NAMES)
SEED_BASE = 7300


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(path: Path) -> str:
    return sha(path.read_bytes())


def save(path: Path, value: object, *, proposal_update: bool = False) -> None:
    content = canonical_json(value) + '\n'
    if path.exists() and path.read_text() != content:
        if not proposal_update or (OUT/'campaign_freeze.json').exists() and json.loads((OUT/'campaign_freeze.json').read_text()).get('status') == 'FROZEN':
            raise ValueError(f'existing final-v1 artifact differs: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def source_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.relative_to(ROOT).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return h.hexdigest()


def condition(category: str, scenario: str, severity: str, template: str, *, setting: tuple[str, int] | None = None,
              routing: bool = False, truth: str = 'UNSAFE', note: str = '') -> dict:
    return {'category': category, 'scenario': scenario, 'severity': severity, 'scenario_version': '1.0',
            'template': template, 'template_sha256': digest(ROOT/template), 'setting': setting,
            'routing_sensitive': routing, 'ground_truth_intent': truth, 'validated': 'VALIDATED', 'note': note}


def catalog() -> list[dict]:
    latency='configs/phase6c-latency_levels.yaml'
    error='configs/phase6c-error_levels.yaml'
    behavior='configs/phase6c-behavior_levels.yaml'
    c=[condition('CONTROL','healthy','control_10pct_latency',latency,setting=('fault.added_latency_ms',0),truth='SAFE'),
       condition('CONTROL','structured_healthy','control_100pct_valid_exact','configs/phase6c-structured_moderate.yaml',truth='SAFE',note='candidate behavior_profile reset to correct')]
    c += [condition('OPERATIONAL','latency',label,latency,setting=('fault.added_latency_ms',n)) for label,n in [('mild_30pct',2),('moderate_60pct',5),('severe_100pct',9)]]
    c += [condition('OPERATIONAL','candidate_error',label,error,setting=('phase4e.request_faults.0.every_nth_candidate',n),routing=True,
                    truth='SAFE' if n==100 else 'UNSAFE') for label,n in [('control_0pct',100),('mild_9_5_to_12pct',8),('moderate_21_1_to_23_8pct',4),('severe_47_4_to_48pct',2)]]
    c += [condition('BEHAVIORAL','classification',label,behavior,setting=('candidate.regression_every_nth_item',n)) for label,n in [('mild_10pct',10),('moderate_20pct',5),('severe_25pct',4)]]
    c += [condition('BEHAVIORAL','structured','primary_85pct_valid_75pct_exact','configs/phase6c-structured_moderate.yaml')]
    c += [condition('STATE_CONSISTENCY','historical_attribution','epoch1_v2_to_epoch2_v3','configs/phase6c-historical_attribution.yaml',routing=True)]
    c += [condition('OBSERVATION','stale_behavior','one_prior_window','configs/phase4f-stale-behavior.yaml',routing=True),
          condition('OBSERVATION','dropped_system','candidate_observation_dropped','configs/phase4f-dropped-observation.yaml',routing=True,truth='INDETERMINATE')]
    c += [condition('RECOVERY','rollback_delay','12_requests','configs/phase4e-compound-behavior-rollback-delay.yaml',routing=True)]
    c += [condition('COMPOUND','behavior_routing','25pct_accuracy_plus_routing','configs/phase4f-behavior-routing-compound.yaml',routing=True)]
    c += [condition('COST_CONTROL','observed_only_routing','safe_observed_mismatch','configs/phase6c-observed_only_cost.yaml',routing=True,truth='SAFE'),
          condition('COST_CONTROL','insufficient_behavior','two_probes_below_three','configs/phase6b-behavior_sufficiency.yaml',truth='SAFE')]
    return c


def set_path(raw: dict, key: str, value: object) -> None:
    target=raw
    parts=key.split('.')
    for part in parts[:-1]:
        target=target[int(part)] if isinstance(target,list) else target[part]
    target[parts[-1]]=value


def make_raw(item: dict, policy: str, repetition: int, seed: int, index_hash: str | None) -> dict:
    raw=load_config(ROOT/item['template']).to_dict()
    if item['setting']:
        set_path(raw,*item['setting'])
    if item['scenario']=='structured_healthy':
        raw['candidate']['behavior_profile']='correct'
    if item['scenario']=='historical_attribution':
        raw['phase4e']['observation_faults'][0]['historical_index_path']='campaigns/final-v1/history_index.json'
        raw['phase4e']['observation_faults'][0]['historical_index_sha256']=index_hash or '0'*64
    if item['routing_sensitive'] and raw['routing']['candidate_weight']==0.2:
        raw['lifecycle']['evaluation_requests']=60
        raw['fault']['end_request']=max(raw['fault']['end_request'],raw['lifecycle']['validation_requests']+raw['lifecycle']['canary_requests']+59)
    ident=f"final-v1-{item['scenario']}-{item['severity']}-{policy}-r{repetition}"
    raw['experiment'].update(name=ident,seed=seed)
    raw['rollout']['rollout_id']=ident
    l=raw['lifecycle']
    raw['experiment']['request_count']=l['validation_requests']+l['canary_requests']+(1+l['max_holds'])*l['evaluation_requests']+l['max_holds']*l['hold_requests']+l['completion_requests']
    raw['policy']['type']=policy
    return with_policy(config_from_dict(raw),policy).to_dict()


def build_history(items: list[dict]) -> str:
    item=next(x for x in items if x['scenario']=='historical_attribution')
    entries={}
    for rep in range(10):
        seed=derived_seed(SEED_BASE,item['scenario'],'paired',rep,{'severity':item['severity']})
        raw=make_raw(item,'saferoll',rep,seed,None)
        raw['rollout']['epoch']=1
        raw['candidate']['revision']='candidate-v2'
        raw['candidate']['behavior_profile']='correct'
        raw['phase4e']={'enabled':False}
        raw['experiment']['name']=f'final-v1-history-source-{rep}'
        raw['rollout']['rollout_id']=f'final-v1-history-source-{rep}'
        run_root=OUT/'history'/f'seed-{seed}'
        existing=list(run_root.glob('run-*')) if run_root.exists() else []
        if existing:
            if len(existing)!=1: raise ValueError('ambiguous historical source')
            run=existing[0]
        else:
            run=run_experiment(config_from_dict(raw),run_root)
        if not validate_run(run)['valid']: raise ValueError('historical source failed validation')
        evidence=run/'behavior_evidence.jsonl'
        entries[str(seed)]={'run_dir':run.relative_to(ROOT).as_posix(),'behavior_evidence_sha256':digest(evidence),
                            'source_config_sha256':digest(run/'config.resolved.json'), 'rollout_id':raw['rollout']['rollout_id'],
                            'epoch':1,'candidate_revision':'candidate-v2','workload_sha256':digest(run/'behavior_workload.json'),
                            'matching_rule':'same seed and workload; source generated before current run'}
    save(OUT/'history_index.json',{'schema_version':'1.0','campaign_id':'final-v1','entries':entries})
    return digest(OUT/'history_index.json')


def build_plan(items: list[dict], index_hash: str) -> tuple[dict,list[dict]]:
    rows=[]; configs={}
    for item in items:
        count=10 if item['routing_sensitive'] else 3
        for rep in range(count):
            seed=derived_seed(SEED_BASE,item['scenario'],'paired',rep,{'severity':item['severity']})
            for policy in POLICIES:
                raw=make_raw(item,policy,rep,seed,index_hash)
                cfg=config_from_dict(raw)
                cid=config_hash(cfg)
                run_id=sha(canonical_json([item['scenario'],item['severity'],policy,rep,seed,cid]).encode())[:24]
                if run_id in configs: raise ValueError('duplicate run')
                configs[run_id]=raw
                rows.append({'run_id':run_id,'category':item['category'],'scenario':item['scenario'],'severity':item['severity'],
                             'scenario_version':item['scenario_version'],'policy':policy,'policy_version':POLICY_VERSION,
                             'repetition':rep,'seed':seed,'pairing_key':f"{item['scenario']}:{item['severity']}:{rep}",
                             'routing_sensitive':item['routing_sensitive'],'config_path':f'configs/{run_id}.json',
                             'config_sha256':cid,'threshold_signature':sha(canonical_json(raw['policy']).encode()),
                             'history_seed':seed if item['scenario']=='historical_attribution' else None})
    plan={'schema_version':SCHEMA,'campaign_id':'final-v1','policy_order':POLICIES,'rows':rows,
          'source_history_sha256':index_hash,'seed_design_sha256':digest(OUT/'seed_design.json')}
    for run_id,raw in configs.items(): save(OUT/'configs'/f'{run_id}.json',raw)
    save(OUT/'campaign_plan.json',plan)
    csvpath=OUT/'campaign_plan.csv'
    from io import StringIO
    stream=StringIO(newline=''); writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n')
    writer.writeheader()
    for row in rows: writer.writerow(row)
    content=stream.getvalue()
    if csvpath.exists() and csvpath.read_text()!=content: raise ValueError('plan CSV mutation')
    csvpath.write_text(content)
    return plan,rows


def main() -> None:
    OUT.mkdir(parents=True,exist_ok=True)
    items=catalog()
    save(OUT/'seed_design.json',{'schema_version':'1.0','base':SEED_BASE,'routing_repetitions':10,'fixed_repetitions':3,
         'derivation':'SHA-256 first eight bytes masked to 63 bits of canonical JSON [7300,scenario,paired,repetition,{severity:label}]',
         'paired_stream_id':'paired','seed_selection':'prespecified; no outcome-based changes',
         'routing_budget':{'candidate_exposure':0.2,'evaluation_requests':60,'limitation':'validated at 0.20 exposure only; lower exposure requires independent validation'}})
    index_hash=build_history(items)
    plan,rows=build_plan(items,index_hash)
    pilot_path=OUT/'real_timing_pilot/pilot_results.json'
    pilot=json.loads(pilot_path.read_text()) if pilot_path.exists() else {'runs':[]}
    pilot_valid=len(pilot['runs'])==10 and all(r['validity']['valid'] for r in pilot['runs'])
    runtime=load_config(ROOT/'configs/phase4g-real-healthy.yaml').to_dict()['real_runtime']
    model=ROOT/runtime['model_path']
    binary=Path(shutil.which(runtime['binary']) or runtime['binary'])
    version=subprocess.run(['llama-server','--version'],capture_output=True,text=True).stdout+subprocess.run(['llama-server','--version'],capture_output=True,text=True).stderr
    from saferoll.behavior.real import prompt_template_sha256
    real={'conditions':['healthy','structured_configuration_regression'],'role':'external validity only','pilot_valid':pilot_valid,
          'pilot_run_count':len(pilot['runs']),'final_repetitions':None,'runtime':runtime,'model_sha256_actual':digest(model),
          'llama_server_version':version.strip(),'llama_cpp_git_revision':'7fe450e19',
          'llama_server_binary':str(binary.resolve()),'llama_server_binary_sha256':digest(binary),
          'prompt_template_sha256':prompt_template_sha256(),'prompt_template_version':'real-v1',
          'workload_id':'real-workload-v1-structured_output','n_gpu_layers':0,
          'seed_semantics':'fixed seed 7400 through 7404 in pilot; final seed list fixed before final execution',
          'source_configs':['configs/phase4g-real-healthy.yaml','configs/phase4g-real-behavior-regression.yaml']}
    if pilot_valid:
        for condition_name in ['healthy','structured_regression']:
            subset=[r for r in pilot['runs'] if r['condition']==condition_name]
            measured=[]
            for row in subset:
                run=ROOT/row['run_dir']
                summary=json.loads((run/'summary.json').read_text())
                evidence=[json.loads(line) for line in (run/'behavior_evidence.jsonl').read_text().splitlines()]
                backend=json.loads((run/'backend_runs.jsonl').read_text())
                candidate=[e for e in evidence if e['backend_role']=='candidate' and e['lifecycle_state']=='EVALUATING']
                measured.append({'repetition':row['repetition'],'baseline_serving_latency_ms':summary['mean_baseline_latency_ms'],
                                 'candidate_serving_latency_ms':summary['mean_candidate_latency_ms'],
                                 'candidate_structure_valid_rate':candidate[0]['structure_valid_rate'] if candidate else None,
                                 'candidate_exact_match_rate':candidate[0]['exact_match_rate'] if candidate else None,
                                 'decision':row['decision'],'run_valid':row['validity']['valid'],
                                 'backend_startup':backend['startup_result'],'backend_cleanup':backend['shutdown_result'],
                                 'backend_exit_status':backend['backend_exit_status']})
            real[condition_name+'_workload_sha256']=digest(ROOT/subset[0]['run_dir']/'behavior_workload.json')
            measures=['baseline_serving_latency_ms','candidate_serving_latency_ms','candidate_structure_valid_rate','candidate_exact_match_rate']
            real[condition_name]={'duration_seconds':[r['duration_seconds'] for r in subset],
                                  'duration_mean':statistics.mean(r['duration_seconds'] for r in subset),
                                  'duration_stdev':statistics.stdev(r['duration_seconds'] for r in subset),
                                  'decisions':[r['decision'] for r in subset], 'runs':measured,
                                  'variance':{key:{'mean':statistics.mean(x[key] for x in measured if x[key] is not None),
                                                   'stdev':statistics.stdev(x[key] for x in measured if x[key] is not None)}
                                              for key in measures}}
        max_cv=max(x['variance'][key]['stdev']/x['variance'][key]['mean'] if x['variance'][key]['mean'] else 0
                   for x in [real['healthy'],real['structured_regression']]
                   for key in ['baseline_serving_latency_ms','candidate_serving_latency_ms'])
        behavior_stable=all(x['variance'][key]['stdev']==0 for x in [real['healthy'],real['structured_regression']]
                            for key in ['candidate_structure_valid_rate','candidate_exact_match_rate'])
        real['latency_max_coefficient_of_variation']=max_cv
        real['behavior_stable']=behavior_stable
        real['final_repetitions']=5 if max_cv>0.10 or not behavior_stable else 3
        real['repetition_rationale']='Five if observed serving latency CV exceeds 10% or behavior varies; otherwise three for external-validity confirmation. Pilot runs excluded from final analysis.'
        final_rows=[]
        for condition_name,filename in [('healthy','phase4g-real-healthy.yaml'),
                                        ('structured_regression','phase4g-real-behavior-regression.yaml')]:
            for repetition in range(real['final_repetitions']):
                raw=load_config(ROOT/'configs'/filename).to_dict()
                seed=derived_seed(SEED_BASE,'real_'+condition_name,'paired',repetition,{})
                name=f'final-v1-real-{condition_name}-r{repetition}'
                raw['experiment'].update(name=name,seed=seed)
                raw['rollout']['rollout_id']=name
                raw['policy']['system']['max_latency_regression_fraction']=0.20
                raw['experiment']['request_count']=config_from_dict(raw).planned_requests
                cfg=config_from_dict(raw)
                path=OUT/'real_configs'/f'{condition_name}-r{repetition}.json'
                save(path,cfg.to_dict())
                final_rows.append({'condition':condition_name,'repetition':repetition,'seed':seed,
                                   'config_path':path.relative_to(OUT).as_posix(),'config_sha256':config_hash(cfg)})
        real['final_runs']=final_rows
        real['final_policy_note']='Final CPU block uses frozen +20% latency threshold; the older validated pilot configurations used +200%. Timing/behavior variability supports repetition selection; pilot decisions are calibration only.'
    save(OUT/'real_campaign_spec.json',real,proposal_update=True)
    source_paths=list((ROOT/'saferoll').rglob('*.py'))+list((ROOT/'scripts').glob('*.py'))+list((ROOT/'configs').rglob('*.yaml'))
    analysis_paths=list((ROOT/'analysis').rglob('*.py'))+[ROOT/'saferoll/research/oracle.py']
    git=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    dirty=bool(subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True,check=True).stdout)
    policies={p:{'version':POLICY_VERSION,'capabilities':{'readiness_gate':['readiness'], 'ops_gate':['system'],
              'behavior_gate':['behavior'],'ai_gate':['system','behavior'],'saferoll':['system','behavior','routing','identity','lifecycle']}[p],
              'reason_code_version':'1.0','reason_codes_sha256':sha(canonical_json([r.value for r in Reason]).encode()),
              'threshold_signatures':sorted({r['threshold_signature'] for r in rows if r['policy']==p})} for p in POLICIES}
    freeze={'schema_version':SCHEMA,'campaign_id':'final-v1','status':'PROPOSED','research_questions':{
      'RQ1':'Which harmful AI-serving revisions are incorrectly promoted by infrastructure-oriented rollout policies?',
      'RQ2':'How do system, behavior, and rollout-consistency evidence affect decision correctness individually and jointly?',
      'RQ3':'How do stale, missing, or incorrectly attributed observations affect rollout decisions?',
      'RQ4':'How do policies behave under simultaneous behavioral, system, and state faults?',
      'RQ5':'How effectively do rollback mechanisms limit candidate exposure after an unsafe decision is detected?',
      'RQ6':'What HOLD, unnecessary rollback, rollout delay, and availability or exposure cost results from stronger safety validation?'},
      'policies':policies,'thresholds':{'max_error_rate':0.05,'max_latency_regression_fraction':0.20,'max_accuracy_drop':0.05,
          'max_exact_match_drop':0.05,'min_structure_valid_rate':0.95,'minimum_system_samples':3,
          'minimum_behavior_samples':3,'require_fresh_system':True,'require_fresh_behavior':True,'routing_consistency_tolerance':1e-9},
      'threshold_evidence':'campaigns/phase6c-prefreeze-v1/phase6c_prefreeze_analysis.md: proposed threshold table',
      'threshold_evidence_by_value':{
          'max_error_rate':'Phase 6C error calibration: control 0%; every eighth 9.5–12%; every tenth 5.26–9.52% boundary sensitivity',
          'max_latency_regression_fraction':'Phase 6C candidate p95 +10% control versus +30/+60/+100% validated faults',
          'max_accuracy_drop':'Phase 6C 20-item classification losses 10/20/25%',
          'max_exact_match_drop':'Phase 6C structured exact-match 0.75 versus baseline 1.00',
          'min_structure_valid_rate':'Phase 6C structured validity 0.85 versus baseline 1.00',
          'minimum_system_samples':'Phase 6C routing study: insufficient at 20 in 3/10, 40 in 1/10, 60 in 0/10 at exposure 0.20',
          'minimum_behavior_samples':'Phase 6B two-probe insufficiency control versus minimum three',
          'require_fresh_system':'Phase 6/6C current-window system evidence and observation freshness validator',
          'require_fresh_behavior':'Phase 6 stale observation and Phase 6C historical replay',
          'routing_consistency_tolerance':'Phase 6C observed-only mismatch control at 0.30 candidate-weight discrepancy'},
      'scenarios':items,'excluded_proposals':{'stale_behavior':['two_prior_windows','three_prior_windows'],
          'rollback_delay':['five_requests','twenty_requests'],'routing_realization':['desired_0.5_effective_0.1_due_budget_at_0.1'],
          'structured':['additional_unvalidated_severities'],'compound':['latency_plus_missing_evidence']},
      'proposed_severity_classification':[{'scenario':scenario,'severity':severity,'status':'PROPOSED_NOT_INCLUDED',
                                            'reason':'insufficient pre-freeze validation'}
          for scenario,levels in {'stale_behavior':['two_prior_windows','three_prior_windows'],
              'rollback_delay':['five_requests','twenty_requests'],
              'routing_realization':['desired_0.5_effective_0.1'],
              'structured':['additional_severities'],'compound':['latency_plus_missing_evidence']}.items()
          for severity in levels],
      'routing':{'treatment':'SEEDED_RANDOM_WITH_INCREASED_BUDGET','candidate_exposure':0.2,'evaluation_requests':60,
                 'limitation':'The 60-request budget was validated at 0.20 candidate exposure. Lower candidate exposure requires independent sample-budget validation.'},
      'seed_design_sha256':digest(OUT/'seed_design.json'),'history_index_sha256':index_hash,
      'seed_design_review_note':'Phase 6C proposed policy ID in the SHA-256 input. final-v1 proposes the constant paired stream ID in that slot so policies share routing draws. Human review must accept this prespecified pairing refinement.',
      'repetition_counts':{'fixed_signal':3,'routing_sensitive':10,'real_cpu':real['final_repetitions']},
      'repetition_rationale':'Fixed signal evidence showed zero observed variance; three repetitions check reproducibility, not power. Routing-sensitive evidence uses ten prespecified seeds.',
      'plan_sha256':digest(OUT/'campaign_plan.json'),'planned_mock_runs':len(rows),'conditions':len(items),
      'oracle_version':'1.0','truth_states':['SAFE','UNSAFE','INDETERMINATE'],
      'correctness_classes':['CORRECT_PROMOTION','UNNECESSARY_HOLD','UNNECESSARY_ROLLBACK','UNSAFE_PROMOTION','DELAYED_ROLLBACK','CORRECT_ROLLBACK','UNSUPPORTED_PROMOTION','CORRECT_HOLD','UNSUPPORTED_ROLLBACK'],
      'critical_error':'known UNSAFE candidate promoted',
      'primary_metrics':{'UPR':'unsafe promotions / unsafe candidate evaluations','SPR':'safe promotions / safe candidate evaluations',
          'harmful_exposure':'unsafe candidate requests / relevant rollout requests',
          'effective_containment_time':'logical interval until physical candidate exposure reaches zero; not rollback acknowledgement',
          'decision_delay':'mock deterministic logical time; separate from real wall-clock latency'},
      'incremental_state_benefit':'paired count where AIGate PROMOTE and truth UNSAFE and SafeRoll != PROMOTE; ties excluded',
      'cost_endpoints':['unnecessary HOLD rate','unnecessary rollback rate','safe rollouts failed due to HOLD bound',
                        'additional logical decision delay','additional candidate requests attributable to HOLD'],
      'secondary_metrics':['HOLD rate','rollback rate','critical error rate','unnecessary HOLD','unnecessary rollback',
                           'candidate request count','post-rollback candidate requests','system latency deltas','behavior quality deltas'],
      'exclusion_codes':['ARTIFACT_VALIDATION_FAILURE','HARNESS_FAILURE','UNRELATED_BACKEND_FAILURE','PROVENANCE_MISMATCH',
                         'SOURCE_HISTORY_MISMATCH','INCOMPLETE_RUN'],
      'rerun_policy':'Preserve invalid attempt, fix documented defect, make new attempt. Never rerun valid result for outcome. Stop and audit on post-start code defect; new version if scientific semantics change.',
      'analysis_plan':{'binary':['counts','proportions','95% confidence intervals','absolute risk difference','risk ratio where defined'],
          'continuous':['median/IQR','mean/std where informative','paired bootstrap 95% intervals','ECDF'],
          'paired_key':'scenario:severity:repetition','paired_tests':'two-sided exact McNemar for discordant binary pairs; Wilcoxon signed-rank for nonzero continuous paired differences where appropriate',
          'effect_sizes':['risk difference','risk ratio where defined','paired median difference'],
          'comparison_families':{'primary':'SafeRoll vs AIGate','secondary':'SafeRoll vs OpsGate','severity':'within each fault domain'},
          'multiplicity':'Holm within each inferential family; descriptive summaries unadjusted'},
      'analysis_version':'1.0',
      'real_campaign_spec_sha256':digest(OUT/'real_campaign_spec.json'),'artifact_schema_version':SCHEMA_VERSION,
      'source_git_revision':git,'git_dirty':dirty,'source_tree_sha256':source_hash(source_paths),
      'analysis_source_sha256':source_hash(analysis_paths),'full_final_campaign_executed':False}
    save(OUT/'campaign_freeze.json',freeze,proposal_update=True)
    md=['# SafeRoll final-v1 freeze review','',f"Status: **PROPOSED**. Git `{git}`; source tree `{freeze['source_tree_sha256']}`.",
        f"Plan: {len(items)} conditions, {len(POLICIES)} policies, {len(rows)} mock runs; SHA-256 `{freeze['plan_sha256']}`.",'',
        '## Research questions','']+[f'- {k}: {v}' for k,v in freeze['research_questions'].items()]
    md+=['','## Validated conditions','', '| Category | Scenario | Severity | Repetitions |','|---|---|---|---:|']
    md += [f"| {x['category']} | {x['scenario']} | {x['severity']} | {10 if x['routing_sensitive'] else 3} |" for x in items]
    md+=['','## Scientific freeze','',f"Policies: {', '.join(POLICIES)} (version {POLICY_VERSION}).",
         'Thresholds: '+', '.join(f'{k}={v}' for k,v in freeze['thresholds'].items())+'.',
         'Routing: seeded random, 60 evaluation requests at 0.20 candidate exposure, ten paired seeds. Three fixed-signal repetitions.',
         freeze['routing']['limitation'],'Primary metrics: '+', '.join(freeze['primary_metrics'])+'.',
         'SafeRoll benefit: '+freeze['incremental_state_benefit']+'.','Costs: '+', '.join(freeze['cost_endpoints'])+'.',
         'Real CPU block: five healthy and five structured pilot runs; separate final external-validity block. Final repetitions: '+str(real['final_repetitions'])+'.',
         'Exclusions: '+', '.join(freeze['exclusion_codes'])+'.',
         'Statistics: paired design; descriptive intervals and distributions; exact McNemar/Wilcoxon where appropriate; Holm within prespecified families.',
         'Known limits: low candidate exposure unvalidated; unmeasured stale/recovery levels omitted; deterministic fixed-signal repeats are not a power analysis.',
         freeze['seed_design_review_note'],
         'No expected policy decisions are encoded in scenario metadata. No final campaign runs or final hypothesis tests were executed.','']
    if pilot_valid:
        md+=['## Real CPU calibration (not final observations)','',
             f"Five healthy and five structured regression runs were valid. Maximum serving latency CV: {real['latency_max_coefficient_of_variation']:.3f}.",
             f"Behavior stable: {real['behavior_stable']}. Healthy decisions: {real['healthy']['decisions']}; regression decisions: {real['structured_regression']['decisions']}.",
             f"Chosen final repetitions: {real['final_repetitions']} per condition. {real['repetition_rationale']}",
             real['final_policy_note'],'']
    mdpath=OUT/'campaign_freeze.md'; content='\n'.join(md)
    if mdpath.exists() and mdpath.read_text()!=content and json.loads((OUT/'campaign_freeze.json').read_text()).get('status')=='FROZEN': raise ValueError('review document changed')
    mdpath.write_text(content)
    print(canonical_json({'status':'PROPOSED','conditions':len(items),'planned_mock_runs':len(rows),
                          'plan_sha256':freeze['plan_sha256'],'real_pilot_valid':pilot_valid,'git_dirty':dirty}))


if __name__=='__main__': main()
