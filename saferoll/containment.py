"""Raw rollback/containment facts; command completion is not physical containment."""
from __future__ import annotations
from typing import Any


def containment_facts(events: list[dict[str,Any]], routing: list[dict[str,Any]]) -> dict[str,Any]:
    decision=next((e['logical_time'] for e in events if e['event_type']=='DECISION_MADE' and e['details']['decision']=='ROLLBACK'),None)
    requested=next((e['logical_time'] for e in events if e['event_type']=='STATE_TRANSITION' and e['details']['to_desired_state']=='ROLLING_BACK'),None)
    candidates=[e['logical_time'] for e in events if e['event_type']=='REQUEST_COMPLETED' and e['served_revision']==e['candidate_revision']]
    post=[t for t in candidates if requested is not None and t>requested]
    states=[r for r in routing if requested is not None and r['logical_time']>=requested]
    nonzero=[i for i,r in enumerate(states) if r['effective_candidate_weight']>0]
    after=states[(nonzero[-1]+1) if nonzero else 0:]
    zero=after[0]['logical_time'] if after else None
    return {'rollback_decision_time':decision,'rollback_requested_time':requested,
            'rollback_requested_logical_time':requested,'effective_zero_candidate_time':zero,
            'effective_candidate_zero_logical_time':zero,
            'last_candidate_served_time':max(candidates,default=None),
            'last_candidate_request_logical_time':max(candidates,default=None),
            'last_post_rollback_candidate_time':max(post,default=None),
            'effective_contained':requested is not None and zero is not None,
            'final_effective_candidate_weight':routing[-1]['effective_candidate_weight'] if routing else None,
            'final_effective_state':routing[-1]['effective_state'] if routing else None}
