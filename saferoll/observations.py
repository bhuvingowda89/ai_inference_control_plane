"""Controller views over immutable ground truth; no policy decisions."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from .config import Config

_REPOSITORY = Path(__file__).resolve().parent.parent


def _repository_file(name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('historical evidence path must be repository-relative')
    path = (_REPOSITORY / name).resolve()
    if not path.is_relative_to(_REPOSITORY):
        raise ValueError('historical evidence path escapes repository')
    return path


def historical_source(config: Config, fault: Any) -> tuple[dict[str,Any], int, str, str]:
    """Validate and load pinned prior evidence without rewriting source identity."""
    index_path = _repository_file(fault.historical_index_path)
    content = index_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != fault.historical_index_sha256:
        raise ValueError('historical index SHA-256 mismatch')
    index = json.loads(content)
    schema = index.get('schema_version')
    if schema not in {'1.0', '2.0'} or not isinstance(index.get('campaign_id'), str) or not index['campaign_id']:
        raise ValueError('invalid historical source index')
    entries = index.get('entries')
    if not isinstance(entries, dict) or str(config.experiment.seed) not in entries:
        raise ValueError('historical source seed pairing is absent')
    entry = entries[str(config.experiment.seed)]
    if not isinstance(entry, dict):
        raise ValueError('invalid historical source entry')
    run_dir = entry['run_dir']
    source_dir = _repository_file(run_dir)
    if not source_dir.is_dir() or source_dir.name != entry.get('source_run_id', source_dir.name):
        raise ValueError('historical source run identity mismatch')
    from .validator import validate_run
    if not validate_run(source_dir)['valid']:
        raise ValueError('historical source run fails independent validation')
    source_file = _repository_file(run_dir + '/behavior_evidence.jsonl')
    raw = source_file.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry['behavior_evidence_sha256']:
        raise ValueError('historical evidence SHA-256 mismatch')
    historical_config = json.loads(_repository_file(run_dir + '/config.resolved.json').read_text(encoding='utf-8'))
    source_workload_path = _repository_file(run_dir + '/behavior_workload.json')
    source_workload = json.loads(source_workload_path.read_text(encoding='utf-8'))
    from .behavior.workload import build_workload
    current_workload = build_workload(config)
    if (historical_config['experiment']['seed'] != config.experiment.seed
            or historical_config['rollout']['epoch'] >= config.rollout.epoch
            or historical_config['candidate']['revision'] == config.candidate.revision
            or source_workload['workload_id'] != current_workload['workload_id']
            or source_workload['items'] != current_workload['items']):
        raise ValueError('historical source seed, epoch, revision or workload mismatch')
    if schema == '2.0':
        import re
        match = re.search(r'-r(\d+)$', config.experiment.name)
        if match is None:
            raise ValueError('current historical run lacks a repetition pairing key')
        pairing = f'historical_attribution:epoch1_v2_to_epoch2_v3:{match.group(1)}'
        required = {'source_run_id': source_dir.name, 'source_epoch': historical_config['rollout']['epoch'],
                    'source_revision': historical_config['candidate']['revision'],
                    'rollout_id': historical_config['rollout']['rollout_id'],
                    'seed': config.experiment.seed,
                    'source_artifact_path': run_dir + '/behavior_evidence.jsonl',
                    'source_sha256': digest,
                    'source_config_sha256': hashlib.sha256(_repository_file(run_dir + '/config.resolved.json').read_bytes()).hexdigest(),
                    'workload_sha256': hashlib.sha256(source_workload_path.read_bytes()).hexdigest(),
                    'workload_id': source_workload['workload_id'],
                    'pairing_key': pairing, 'identity_relation': 'DISTINCT_SOURCE_CURRENT'}
        if any(entry.get(key) != value for key, value in required.items()):
            raise ValueError('historical source frozen provenance or pairing mismatch')
        if historical_config['rollout']['rollout_id'] == config.rollout.rollout_id:
            raise ValueError('historical source/current rollout identities must differ')
    elif historical_config['rollout']['rollout_id'] != config.rollout.rollout_id:
        # Legacy schema supported same-rollout replay only; new campaigns use v2.
        raise ValueError('legacy historical source rollout identity mismatch')
    records = [json.loads(line) for line in raw.splitlines()]
    matches = [(i,r) for i,r in enumerate(records) if r['window_sequence'] == fault.source_window
               and r['backend_role'] == fault.target]
    if len(matches) != 1:
        raise ValueError('historical evidence source window is missing or ambiguous')
    position, record = matches[0]
    if (record['rollout_id'] != historical_config['rollout']['rollout_id'] or record['epoch'] != historical_config['rollout']['epoch']
            or record['revision'] != historical_config['candidate']['revision']
            or record['workload_id'] != config.behavior_evidence.workload_id):
        raise ValueError('historical evidence identity or workload mismatch')
    return record, position, run_dir, digest


def reference(artifact: str, index: int, record: dict[str,Any]) -> dict[str,Any]:
    return {'artifact':artifact,'record_index':index,'rollout_id':record['rollout_id'],'epoch':record['epoch'],
            'window_id':record.get('window_id'),'revision':record.get('revision')}


def build_observations(config: Config, system: list[dict[str,Any]], behavior: list[dict[str,Any]],
                       routing: list[dict[str,Any]]) -> list[dict[str,Any]]:
    observations=[]
    for source,artifact,records,age_limit in [('system','system_evidence.jsonl',system,config.evidence.max_age_logical_units),
            ('behavior','behavior_evidence.jsonl',behavior,config.behavior_evidence.max_age_logical_units)]:
        for index,current in enumerate(records):
            source_index=index
            historical=None
            value=deepcopy(current)
            now=current['closed_at_logical_time']
            status=current['freshness_status']
            faults=[]
            dropped=False
            for f in config.phase4e.observation_faults:
                if f.source!=source or f.target!=current['backend_role'] or not f.start_window<=current['window_sequence']<f.start_window+f.duration_windows:
                    continue
                if f.type=='observation_drop' and (current['window_sequence']-f.start_window+1)%f.drop_every_nth_record:
                    continue
                faults.append(f)
                if f.type=='stale_evidence':
                    source_index=next((i for i,r in enumerate(records) if r['window_sequence']==f.source_window and r['backend_role']==f.target),-1)
                    if source_index<0:
                        raise ValueError('stale observation source window does not exist')
                    value=deepcopy(records[source_index])
                    status='STALE'
                elif f.type=='observation_delay':
                    now+=f.delay_logical_units
                    status='DELAYED'
                elif f.type=='observation_drop':
                    value=None
                    dropped=True
                    status='DROPPED'
                elif f.type=='misattribution':
                    if value is not None:
                        value['revision']=f.reported_revision or (config.baseline.revision if f.target=='candidate' else config.candidate.revision)
                        value['epoch']=f.reported_epoch or config.rollout.epoch
                    status='MISMATCHED' if value is not None else 'DROPPED'
                elif f.type=='misattribution_replay':
                    # A faulty observer republishes older values under the current
                    # evaluation scope and a false identity. The source reference
                    # remains the immutable older ground-truth record.
                    source_index=next((i for i,r in enumerate(records) if
                        r['window_sequence']==f.source_window and r['backend_role']==f.target),-1)
                    if source_index<0:
                        raise ValueError('misattribution replay source window does not exist')
                    value=deepcopy(records[source_index])
                    for field in ('window_id','window_sequence','lifecycle_state','transition_index'):
                        value[field]=current[field]
                    value['revision']=f.reported_revision or current['revision']
                    value['epoch']=f.reported_epoch or current['epoch']
                    status='MISMATCHED'
                elif f.type=='historical_evidence_replay':
                    historical=historical_source(config,f)
                    value=deepcopy(historical[0])
                    for field in ('window_id','window_sequence','lifecycle_state','transition_index'):
                        value[field]=current[field]
                    status='MISMATCHED'
            if dropped:
                value=None
            ground=historical[0] if historical else records[source_index]
            ground_ref=reference(artifact,historical[1],ground) if historical else reference(artifact,source_index,ground)
            if historical:
                ground_ref.update(source_run_dir=historical[2],artifact_sha256=historical[3])
            age=now-ground['latest_sample_logical_time'] if ground['latest_sample_logical_time'] is not None else None
            if not faults:
                status='NO_SAMPLES' if age is None else ('FRESH' if age<=age_limit else 'STALE')
            if value is None:
                status='DROPPED'
            name=json.dumps([config.rollout.rollout_id,config.rollout.epoch,source,index],separators=(',',':'))
            observations.append({'observation_id':str(uuid5(NAMESPACE_URL,name)),'rollout_id':config.rollout.rollout_id,
                'epoch':config.rollout.epoch,'logical_time':now,'observation_type':source,
                'source_window_id':ground['window_id'],'source_revision':ground['revision'],
                'ground_truth_reference':ground_ref,
                'expected_ground_truth_reference':reference(artifact,index,current),
                'observed_value':value,'observation_status':status,'available':value is not None,
                'observed_age_logical_units':age,'ground_truth_age_at_evaluation':ground['age_at_evaluation'],
                'fault_active':bool(faults),'fault_ids':[f.id for f in faults],'fault_types':[f.type for f in faults]})
    for index,r in enumerate(routing):
        mismatched=not r['observed_effective_match']
        name=json.dumps([config.rollout.rollout_id,config.rollout.epoch,'routing',index],separators=(',',':'))
        observations.append({'observation_id':str(uuid5(NAMESPACE_URL,name)),'rollout_id':config.rollout.rollout_id,
            'epoch':config.rollout.epoch,'logical_time':r['logical_time'],'observation_type':'routing',
            'source_window_id':None,'source_revision':None,'ground_truth_reference':reference('routing_states.jsonl',index,r),
            'expected_ground_truth_reference':reference('routing_states.jsonl',index,r),
            'observed_value':{k:r[k] for k in ('observed_state','observed_baseline_weight','observed_candidate_weight')},
            'observation_status':'MISMATCHED' if mismatched else 'FRESH','available':True,
            'observed_age_logical_units':0,'ground_truth_age_at_evaluation':0,
            'fault_active':r['fault_active'],'fault_ids':r['fault_ids'],'fault_types':r['fault_types']})
    observations.sort(key=lambda r:(r['logical_time'],r['observation_id']))
    for index,r in enumerate(observations):
        r['observation_index']=index
    return observations


def build_fault_events(config: Config, routing: list[dict[str,Any]], system: list[dict[str,Any]],
                       events: list[dict[str,Any]]) -> list[dict[str,Any]]:
    from dataclasses import asdict
    result=[]
    end=events[-1]['logical_time']
    for f in config.phase4e.routing_faults:
        active=[r for r in routing if f.id in r['fault_ids']]
        if not active:
            continue
        start=active[0]['logical_time']
        clear=next((r['logical_time'] for r in routing if r['logical_time']>start and f.id not in r['fault_ids']),end)
        for kind,time in [('FAULT_ACTIVATED',start),('FAULT_CLEARED',clear)]:
            result.append({'event_type':kind,'logical_time':time,'fault_id':f.id,'fault_type':f.type,
                'domain':'routing','target':f.target,'parameters':asdict(f),'termination':'run_end' if time==end else 'boundary'})
    for domain,faults in [('observation',config.phase4e.observation_faults),('behavior',config.phase4e.behavior_faults)]:
        for f in faults:
            starts=[w for w in system[::2] if w['window_sequence']==f.start_window]
            if not starts:
                continue
            start=starts[0]['window_start_logical_time']
            clear=next((w['window_start_logical_time'] for w in system[::2] if w['window_sequence']==f.start_window+f.duration_windows),end)
            for kind,time in [('FAULT_ACTIVATED',start),('FAULT_CLEARED',clear)]:
                result.append({'event_type':kind,'logical_time':time,'fault_id':f.id,'fault_type':f.type,
                    'domain':domain,'target':f.target,'parameters':asdict(f),'termination':'run_end' if time==end else 'boundary'})
    for e in events:
        if e['event_type'] in {'FAULT_ACTIVATED','FAULT_CLEARED'}:
            details=e['details']
            result.append({'event_type':e['event_type'],'logical_time':e['logical_time'],
                'fault_id':details.get('fault_id','legacy-request-fault'),'fault_type':details['type'],
                'domain':'request','target':details['target'],'parameters':details,'termination':'boundary'})
    result.sort(key=lambda r:(r['logical_time'],0 if r['event_type']=='FAULT_CLEARED' else 1,r['fault_id']))
    for index,r in enumerate(result):
        r.update(fault_event_index=index,rollout_id=config.rollout.rollout_id,epoch=config.rollout.epoch)
    return result


def controller_view_at(observations: list[dict[str,Any]], logical_time: int) -> tuple[dict[str,Any], ...]:
    """Return only delivered views; future delayed deliveries cannot leak to the controller."""
    if type(logical_time) is not int or logical_time<0:
        raise ValueError('controller observation time must be a nonnegative integer')
    return tuple(deepcopy(o) for o in observations if o['logical_time']<=logical_time)
