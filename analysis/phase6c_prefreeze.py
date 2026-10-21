"""Descriptive pre-freeze analysis over validated Phase 6C artifacts only."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from analysis import phase6b_refinement as shared
from saferoll.campaign.validation import validate_campaign

ROOT=Path(__file__).resolve().parents[1]
CAMPAIGN=ROOT/'campaigns/phase6c-prefreeze-v1'
shared.CAMPAIGN=CAMPAIGN


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def taxonomy() -> list[dict]:
    all_policies=['readiness_gate','ops_gate','behavior_gate','ai_gate','saferoll']
    research=['ops_gate','ai_gate','saferoll']
    rows=[
      ('CONTROL','healthy','NONE','10% candidate p95 increase','SAFE','RQ1',all_policies,'fixed-signal'),
      ('OPERATIONAL','latency_levels','LATENCY','30/60/100% candidate p95 increase','UNSAFE beyond +20%','RQ1',all_policies,'fixed-signal'),
      ('OPERATIONAL','error_levels','REQUEST_FAILURE','~10/~25/~48% candidate error','UNSAFE beyond 5%','RQ1',all_policies,'routing-sensitive'),
      ('BEHAVIORAL','classification_levels','BEHAVIOR','10/20/25% accuracy loss','UNSAFE beyond 5% loss','RQ1',all_policies,'fixed-signal'),
      ('BEHAVIORAL','structured_moderate','BEHAVIOR','85% structure validity; 75% exact match','UNSAFE','RQ1',all_policies,'fixed-signal'),
      ('STATE_CONSISTENCY','routing_realization','ROUTING','desired 0.5, effective 0.1; visible stale state','INDETERMINATE until convergence','RQ2',research,'routing-sensitive'),
      ('OBSERVATION','historical_attribution','BEHAVIOR+OBSERVATION','epoch-1 candidate-v2 replay during epoch-2 candidate-v3 regression','UNSAFE current candidate','RQ3',all_policies,'routing-sensitive'),
      ('OBSERVATION','stale_behavior','BEHAVIOR+OBSERVATION','one prior healthy window','UNSAFE current candidate','RQ3',all_policies,'routing-sensitive'),
      ('OBSERVATION','dropped_system','OBSERVATION','mandatory candidate system observation dropped','SAFE or UNSAFE, prespecified','RQ3',research,'routing-sensitive'),
      ('RECOVERY','rollback_delay','RECOVERY','12 request propagation delay (tested); 5/20 proposed sensitivity','UNSAFE before containment','RQ4',all_policies,'routing-sensitive'),
      ('RECOVERY','partial_rollback','RECOVERY','nonzero effective candidate exposure after rollback','INDETERMINATE until containment','RQ4',research,'routing-sensitive'),
      ('COMPOUND','behavior_routing','BEHAVIOR+ROUTING','25% accuracy loss plus visible routing mismatch','UNSAFE','RQ2/RQ3',all_policies,'routing-sensitive'),
      ('COST_CONTROL','observed_only_routing','ROUTING','desired/effective match; observed mismatch','SAFE','RQ2',all_policies,'routing-sensitive'),
      ('COST_CONTROL','insufficient_behavior','EVIDENCE','two candidate probes below three required','SAFE known to oracle','RQ3',research,'routing-sensitive')]
    return [{'category':category,'scenario_id':sid,'version':'proposed-1.0','fault_domain':domain,
             'severity':severity,'ground_truth_semantics':truth,'primary_rq':rq,
             'applicable_policies':policies,'seed_treatment':seed} for
            category,sid,domain,severity,truth,rq,policies,seed in rows]


def statistics_by_level(facts: list[dict], scenario: str, sweep_key: str, getter):
    levels=defaultdict(list)
    for fact in facts:
        if fact['scenario']==scenario:
            levels[fact['sweep'][sweep_key]].append(getter(fact))
    return {str(level):{'values':values,'min':min(values),'max':max(values)}
            for level,values in sorted(levels.items())}


def main() -> dict:
    validation=validate_campaign(CAMPAIGN)
    if not validation['valid']:
        raise ValueError(f'Phase 6C campaign invalid: {validation}')
    plan=read(CAMPAIGN/'campaign_plan.json')
    index=lines(CAMPAIGN/'run_index.jsonl')
    indexed={r['run_id']:r for r in index}
    if len(plan['rows'])!=78 or len(index)!=78 or any(r['status']!='VALID' for r in index):
        raise ValueError('Phase 6C main plan did not yield 78 valid runs')
    history=read(CAMPAIGN/'history_index.json')
    if len(history['entries'])!=6:
        raise ValueError('historical archive incomplete')
    facts=[shared.run_fact(row,indexed[row['run_id']]) for row in plan['rows']]
    first_by_condition=defaultdict(list)
    for fact in facts:
        first_by_condition[(fact['scenario'],json.dumps(fact['sweep'],sort_keys=True),fact['policy'])].append(fact)
    matrix=[]
    for (scenario,sweep,policy),items in sorted(first_by_condition.items()):
        actions=Counter(x for item in items for x in item['decision_sequence'])
        truth=Counter(x for item in items for x in item['truth_sequence'])
        correctness=Counter(x for item in items for x in item['correctness_sequence'])
        matrix.append({'scenario':scenario,'sweep':json.loads(sweep),'policy':policy,
            'runs':len(items),'promote':actions['PROMOTE'],'hold':actions['HOLD'],
            'rollback':actions['ROLLBACK'],'truth':dict(sorted(truth.items())),
            'correctness':dict(sorted(correctness.items())),
            'critical_errors':sum(item['critical_errors'] for item in items),
            'first_decisions_stable':len({(item['decision_sequence'][0],item['correctness_sequence'][0]) for item in items})==1})
    attribution=[]
    for row in plan['rows']:
        if row['scenario']!='historical_attribution':
            continue
        run=CAMPAIGN/indexed[row['run_id']]['run_dir']
        inp=lines(run/'policy_inputs.jsonl')[0]
        decision=lines(run/'decisions.jsonl')[0]
        evaluation=lines(run/'decision_evaluations.jsonl')[0]
        current_window=inp['expected_window_id']
        current=next(x for x in lines(run/'behavior_evidence.jsonl') if
                     x['window_id']==current_window and x['backend_role']=='candidate')
        obs=next(x for x in lines(run/'observations.jsonl') if 'prior-epoch-v2-replay' in x['fault_ids'])
        ref=obs['ground_truth_reference']
        source_run=ROOT/ref['source_run_dir']
        source=lines(source_run/'behavior_evidence.jsonl')[ref['record_index']]
        if (source['revision']!='candidate-v2' or source['epoch']!=1 or source['accuracy']!=1.0
                or current['revision']!='candidate-v3' or current['epoch']!=2 or current['accuracy']!=0.75
                or ref['artifact_sha256']!=hashlib.sha256((source_run/'behavior_evidence.jsonl').read_bytes()).hexdigest()):
            raise ValueError('historical source or active ground truth differs from attribution design')
        attribution.append({'policy':row['policy'],'repetition':row['repetition'],'seed':row['seed'],
            'decision':decision['decision'],'reason_codes':decision['reason_codes'],
            'ground_truth':evaluation['ground_truth_state'],
            'correctness':evaluation['correctness_class'],'critical_error':evaluation['critical_error'],
            'source_run_dir':ref['source_run_dir'],'source_window_id':source['window_id'],
            'source_revision':source['revision'],'source_epoch':source['epoch'],'source_accuracy':source['accuracy'],
            'visible_revision':obs['observed_value']['revision'],'visible_epoch':obs['observed_value']['epoch'],
            'visible_accuracy':obs['observed_value']['accuracy'],
            'active_revision':current['revision'],'active_epoch':current['epoch'],
            'active_accuracy':current['accuracy'],
            'visible_fresh':inp['behavior'][1]['fresh'] if 'behavior' in inp else None,
            'visible_sufficient':inp['behavior'][1]['sufficient'] if 'behavior' in inp else None,
            'source_sample_item_ids_match_current':source['sample_item_ids']==current['sample_item_ids']})
    pair={}
    for item in attribution:
        pair.setdefault(item['repetition'],{})[item['policy']]=item
    comparisons=[]
    for repetition,policies in sorted(pair.items()):
        ai,safe=policies['ai_gate'],policies['saferoll']
        comparisons.append({'repetition':repetition,'ai_decision':ai['decision'],
            'ai_correctness':ai['correctness'],'saferoll_decision':safe['decision'],
            'saferoll_correctness':safe['correctness'],
            'incremental_benefit':ai['correctness']=='UNSAFE_PROMOTION' and safe['decision']=='HOLD'
                and safe['ground_truth']=='UNSAFE'})
    routing_initial=read(CAMPAIGN/'routing_study_results.json')
    routing_extension=read(CAMPAIGN/'routing_study_extension_results.json')
    routing_rows=routing_initial['rows']+routing_extension['rows']
    if len(routing_rows)!=30 or any(not row['valid'] for row in routing_rows):
        raise ValueError('routing study incomplete')
    routing_summary={**routing_initial['summary'],**routing_extension['summary']}
    error_mild=read(CAMPAIGN/'error_extension_results.json')['rows']
    error_severe=read(CAMPAIGN/'error_severe_extension_results.json')['rows']
    if len(error_mild)!=6 or len(error_severe)!=6:
        raise ValueError('error extension incomplete')
    error_main=statistics_by_level(facts,'error_levels',
        'phase4e.request_faults.0.every_nth_candidate',lambda f:f['system']['candidate_error_rate'])
    error_levels={
        'control_every_100th':error_main['100'],
        'sensitivity_every_10th':error_main['10'],
        'final_mild_every_8th':{'values':[r['candidate_error_rate'] for r in error_mild],
            'min':min(r['candidate_error_rate'] for r in error_mild),
            'max':max(r['candidate_error_rate'] for r in error_mild)},
        'moderate_every_4th':error_main['4'],
        'severe_every_2nd':{'values':[r['candidate_error_rate'] for r in error_severe],
            'min':min(r['candidate_error_rate'] for r in error_severe),
            'max':max(r['candidate_error_rate'] for r in error_severe)}}
    latency=statistics_by_level(facts,'latency_levels','fault.added_latency_ms',
        lambda f:f['system']['candidate_p95_ms']/f['system']['baseline_p95_ms']-1)
    behavior=statistics_by_level(facts,'behavior_levels','candidate.regression_every_nth_item',
        lambda f:f['behavior']['baseline_accuracy']-f['behavior']['candidate_accuracy'])
    structured=[f for f in facts if f['scenario']=='structured_moderate']
    structured_summary={'structure_valid_rate':sorted({f['behavior']['candidate_structure_valid'] for f in structured}),
        'exact_match_rate':sorted({f['behavior']['candidate_exact_match'] for f in structured}),
        'decisions':dict(Counter(f['decision_sequence'][0] for f in structured))}
    safe=[f for f in facts if f['policy']=='saferoll']
    costs={'unnecessary_hold_decisions':sum(f['correctness_sequence'].count('UNNECESSARY_HOLD') for f in safe),
        'unnecessary_rollback_decisions':sum(f['correctness_sequence'].count('UNNECESSARY_ROLLBACK') for f in safe),
        'failed_safe_rollouts':sum(f['final_state']=='FAILED' and f['truth_sequence'][0]=='SAFE' for f in safe),
        'cost_control':[{k:f[k] for k in ('repetition','decision_sequence','correctness_sequence',
            'candidate_requests_during_holding','candidate_requests_after_first_hold')}|
            {'extra_decision_delay_logical_units':f['last_decision_time']-f['first_decision_time']}
            for f in safe if f['scenario']=='observed_only_cost']}
    seed_design=read(CAMPAIGN/'seed_design.json')
    thresholds=[
        ('max_error_rate',.05,'0 control; every-8th 0.095–0.12; every-4th 0.21–0.25','yes','every-10th sensitivity near boundary'),
        ('max_latency_regression_fraction',.20,'healthy 0.10; fault levels 0.30/0.60/1.00','yes','no primary condition near boundary'),
        ('max_accuracy_drop',.05,'0.10/0.20/0.25 achieved','yes','no'),
        ('max_exact_match_drop',.05,'structured exact-match drop 0.25','yes','no'),
        ('min_structure_valid_rate',.95,'structured candidate 0.85','yes','no'),
        ('minimum_system_samples',3,'0–9 candidate samples at n20; 6–20 at n60','yes','current budget starvation; increased budget clear in ten seeds'),
        ('minimum_behavior_samples',3,'20 probes per revision/window; Phase 6B two-probe insufficiency','yes in Phase 6B','no'),
        ('require_fresh_system',True,'healthy and current system observations fresh','yes','not numeric'),
        ('require_fresh_behavior',True,'historical replay current-looking yet wrong epoch/revision; Phase 6 stale observation','yes','not numeric'),
        ('routing_consistency_tolerance',1e-9,'observed-only mismatch 0.30 candidate weight','yes','no')]
    threshold_table=[{'name':n,'value':v,'pilot_evidence':e,'exercised':x,'near_boundary':b} for n,v,e,x,b in thresholds]
    severities=[
        {'fault':'latency','control':'10%','mild':'30%','moderate':'60%','severe':'100%','validation':'all four Phase 6C levels validated'},
        {'fault':'error_rate','control':'0%','mild':'9.5–12% every eighth','moderate':'observed every-fourth range','severe':'47–48% every second','validation':'all levels validated; every-tenth 5.26% boundary sensitivity excluded from primary levels'},
        {'fault':'classification_behavior_loss','control':'0%','mild':'10%','moderate':'20%','severe':'25%','validation':'all levels validated on 20 items'},
        {'fault':'structure_validity','control':'100%','mild':None,'moderate':'85% valid, 75% exact match','severe':None,'validation':'moderate validated; extra levels would change malformed-versus-wrong-value mix on this 20-item profile'},
        {'fault':'stale_evidence','control':'current window','mild':'one prior window','moderate':'two prior windows proposed','severe':'three prior windows proposed','validation':'one-window and replay mechanisms validated; longer durations proposal only'},
        {'fault':'rollback_delay','control':'immediate','mild':'5 requests proposed','moderate':'12 requests measured in Phase 6','severe':'20 requests proposed','validation':'12-request level validated in Phase 6; extensions proposal only'},
        {'fault':'routing_realization_mismatch','control':'desired=effective','mild':'desired 0.5/effective 0.4 proposed','moderate':'desired 0.5/effective 0.2 proposed','severe':'desired 0.5/effective 0.1 Phase 6B','validation':'0.1 tested with seed-sensitive sample insufficiency; other levels proposal only'}]
    return {'analysis_schema_version':'1.0','campaign_id':plan['campaign_id'],
        'plan_sha256':hashlib.sha256((CAMPAIGN/'campaign_plan.json').read_bytes()).hexdigest(),
        'integrity':{'campaign_validation':validation,'planned':78,'executed':len(index),
            'valid':sum(r['status']=='VALID' for r in index),'invalid':sum(r['status']=='INVALID' for r in index),
            'historical_source_runs':len(history['entries']),'routing_study_runs':len(routing_rows),
            'error_extension_runs':len(error_mild)+len(error_severe)},
        'matrix':matrix,'attribution':attribution,'cross_epoch_comparisons':comparisons,
        'incremental_saferoll_benefit_count':sum(x['incremental_benefit'] for x in comparisons),
        'routing_study':{'rows':routing_rows,'summary':routing_summary,
            'initial_design_file':'seed_design.json','extension_design_file':'routing_study_extension_design.json',
            'chosen_treatment':'SEEDED_RANDOM_WITH_INCREASED_BUDGET',
            'chosen_evaluation_requests':60,
            'qualification':'0/10 insufficient at 60 requests for the prespecified 0.2 candidate weight; not a universal probability guarantee'},
        'seed_design':seed_design,'latency':latency,'behavior':behavior,
        'error':error_levels,'structured':structured_summary,
        'saferoll_cost':costs,
        'baseline_fairness':{'policy_code_changed':False,'policy_thresholds_changed':False,
            'ai_uses_system_behavior_sufficiency_freshness':True,
            'saferoll_receives_no_effective_state_or_fault_truth':all(not f['visible']['has_hidden_effective_state']
                and not f['visible']['has_fault_truth'] for f in safe),
            'historical_source_preserves_true_epoch_revision':all(x['source_epoch']==x['visible_epoch']==1
                and x['source_revision']==x['visible_revision']=='candidate-v2' for x in attribution)},
        'proposed_final_scenario_taxonomy':taxonomy(),
        'proposed_thresholds':threshold_table,'proposed_severities':severities,
        'repetitions':{'fixed_signal_mock':'3 prespecified seeds for deterministic controls, retained for comparability',
            'routing_sensitive_mock':'10 prespecified seeds using seed_design.json SHA-256 rule; 60 evaluation requests at candidate weight 0.2',
            'real_cpu':'dedicated five-repeat timing pilot before final count'},
        'real_cpu_prefreeze_pilot':{'execute_now':False,'model':'existing pinned llama.cpp GGUF',
            'scenarios':['real_healthy','real_structured_configuration_regression'],
            'repetitions_each':5,'purpose':['timing variance','behavior stability','final real repetition planning']},
        'phase7_verdict':'GO',
        'go_scope':'GO to human-reviewed Phase 7 freeze proposal; no final campaign execution or automatic freeze',
        'limits':['The historical observation rebinds lifecycle/window scope as an explicit controller fault but preserves source epoch/revision and pinned source SHA.',
            'Routing 60-request result is based on ten fixed seeds at 0.2 candidate weight; low-exposure conditions need their own sufficiency planning.',
            'Stale/recovery/routing multi-level severities beyond tested levels are proposals, not validated effects.',
            'No real-CPU timing-variance pilot was run in Phase 6C.']}


def render(report: dict) -> str:
    out=['# Phase 6C pre-freeze analysis','',
         'Descriptive targeted hardening only; Phase 6 and 6B artifacts are unchanged.','',
         f"Campaign `{report['campaign_id']}`; plan SHA-256 `{report['plan_sha256']}`.",
         f"Main campaign: {report['integrity']['valid']}/{report['integrity']['planned']} valid; "
         f"validation `{json.dumps(report['integrity']['campaign_validation'],sort_keys=True)}`.",
         f"Additional validated sources/studies: {report['integrity']['historical_source_runs']} historical, "
         f"{report['integrity']['routing_study_runs']} routing, {report['integrity']['error_extension_runs']} error-calibration runs.",'',
         '## Genuine historical attribution','',
         '| Policy | Rep | Decision | Reason | Truth | Correctness | Critical | Visible epoch/revision | Active epoch/revision | Accuracy visible/current |',
         '| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- |']
    for a in report['attribution']:
        out.append(f"| {a['policy']} | {a['repetition']} | {a['decision']} | {','.join(a['reason_codes'])} | "
                   f"{a['ground_truth']} | {a['correctness']} | {a['critical_error']} | "
                   f"{a['visible_epoch']}/{a['visible_revision']} | {a['active_epoch']}/{a['active_revision']} | "
                   f"{a['visible_accuracy']}/{a['active_accuracy']} |")
    out += ['', 'Each replay source is an independently validated persisted epoch-1 candidate-v2 record, pinned by SHA-256. '
            'The observation changes only lifecycle/window presentation, while retaining the source epoch/revision. '
            'Current epoch-2 candidate-v3 ground truth has 0.75 accuracy. Matched seeds give the same workload item IDs.',
            f"Incremental SafeRoll benefit over AIGate: **{report['incremental_saferoll_benefit_count']}** avoided known-unsafe promotions.",'',
            '## Decision matrix','',
            '| Scenario | Level | Policy | P/H/R | Truth | Correctness | Critical | Stable first decision |',
            '| --- | --- | --- | --- | --- | --- | ---: | --- |']
    for r in report['matrix']:
        out.append(f"| {r['scenario']} | {json.dumps(r['sweep'],sort_keys=True)} | {r['policy']} | "
                   f"{r['promote']}/{r['hold']}/{r['rollback']} | {r['truth']} | "
                   f"{r['correctness']} | {r['critical_errors']} | {r['first_decisions_stable']} |")
    out += ['', '## Routing and seed treatment','',
            '| Evaluation requests | Runs | Candidate samples (min–max) | Insufficient system windows | Insufficient behavior windows | Initial HOLDs |',
            '| ---: | ---: | --- | ---: | ---: | ---: |']
    for n in (20,40,60):
        row=report['routing_study']['summary'][str(n)]
        out.append(f"| {n} | {row['runs']} | {row['candidate_sample_min']}–{row['candidate_sample_max']} | "
                   f"{row['candidate_system_insufficient']} | {row['candidate_behavior_insufficient']} | {row['hold_first_decisions']} |")
    out += ['', 'The 60-request extension was declared after the 20/40 study left one insufficiency; original results were retained. '
            'Choose seeded random routing, 60 evaluation requests at 0.2 candidate weight, and the ten-seed SHA-256 rule in `seed_design.json`. '
            'Exact quota routing was not adopted.','',
            '## Calibration','',
            f"Latency relative deltas by added milliseconds: {report['latency']}.",
            f"Classification accuracy drops by every-Nth item: {report['behavior']}.",
            f"Candidate error-rate levels: {report['error']}.",
            f"Structured output: {report['structured']}.",'',
            '## Benefit, cost, fairness','',
            f"SafeRoll incremental benefit: {report['incremental_saferoll_benefit_count']} paired attribution decisions.",
            f"SafeRoll cost: {report['saferoll_cost']}.",
            f"Baseline fairness: {report['baseline_fairness']}.",'',
            '## Proposed final scenario taxonomy','',
            '| Category | Scenario | Version | Domain | Severity | Truth | RQ | Policies | Seed treatment |',
            '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in report['proposed_final_scenario_taxonomy']:
        out.append(f"| {r['category']} | {r['scenario_id']} | {r['version']} | {r['fault_domain']} | "
                   f"{r['severity']} | {r['ground_truth_semantics']} | {r['primary_rq']} | "
                   f"{','.join(r['applicable_policies'])} | {r['seed_treatment']} |")
    out += ['', '## Proposed threshold table','',
            '| Threshold | Value | Pilot evidence | Exercised | Near boundary |',
            '| --- | --- | --- | --- | --- |']
    for r in report['proposed_thresholds']:
        out.append(f"| {r['name']} | {r['value']} | {r['pilot_evidence']} | {r['exercised']} | {r['near_boundary']} |")
    out += ['', '## Proposed severity table','',
            '| Fault | Control | Mild | Moderate | Severe | Validation status |',
            '| --- | --- | --- | --- | --- | --- |']
    for r in report['proposed_severities']:
        out.append(f"| {r['fault']} | {r['control']} | {r['mild']} | {r['moderate']} | {r['severe']} | {r['validation']} |")
    out += ['', '## Repetitions, real CPU, and verdict','',
            f"Mock repetitions: {report['repetitions']}.",
            f"Real CPU pre-freeze pilot (not executed): {report['real_cpu_prefreeze_pilot']}.",
            f"Verdict: **{report['phase7_verdict']}** — {report['go_scope']}.",
            'Limits:']
    out.extend('- '+item for item in report['limits'])
    out += ['', 'No Phase 7 freeze or final campaign was executed.','']
    return '\n'.join(out)


if __name__=='__main__':
    result=main()
    (CAMPAIGN/'phase6c_prefreeze_analysis.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    (CAMPAIGN/'phase6c_prefreeze_analysis.md').write_text(render(result),encoding='utf-8')
    print(json.dumps({'campaign_id':result['campaign_id'],'valid':result['integrity']['campaign_validation']['valid'],
                      'main_runs':result['integrity']['valid'],'verdict':result['phase7_verdict']},sort_keys=True))
