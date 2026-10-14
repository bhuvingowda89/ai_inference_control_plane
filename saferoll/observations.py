"""Controller views over immutable ground truth; no policy decisions."""
from __future__ import annotations
from copy import deepcopy
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from .config import Config


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
            if dropped:
                value=None
            ground=records[source_index]
            age=now-ground['latest_sample_logical_time'] if ground['latest_sample_logical_time'] is not None else None
            if not faults:
                status='NO_SAMPLES' if age is None else ('FRESH' if age<=age_limit else 'STALE')
            if value is None:
                status='DROPPED'
            name=json.dumps([config.rollout.rollout_id,config.rollout.epoch,source,index],separators=(',',':'))
            observations.append({'observation_id':str(uuid5(NAMESPACE_URL,name)),'rollout_id':config.rollout.rollout_id,
                'epoch':config.rollout.epoch,'logical_time':now,'observation_type':source,
                'source_window_id':ground['window_id'],'source_revision':ground['revision'],
                'ground_truth_reference':reference(artifact,source_index,ground),
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
