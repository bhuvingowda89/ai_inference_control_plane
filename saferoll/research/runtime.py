"""Live collection/projection before a decision; post-hoc oracle is never imported here."""
from __future__ import annotations
from dataclasses import asdict
from typing import Any
from ..config import Config
from ..schemas import Event,Response
from ..evidence import collect_system_evidence
from ..behavior.evidence import collect_behavior_evidence
from ..behavior.workload import build_workload
from ..observations import build_observations
from .projection import project_input
from .policies import build_policy


def make_live_decision(config: Config, responses: list[Response], events: list[Event],
                       routing: list[dict[str,Any]], transition_index: int) -> tuple[dict[str,Any],dict[str,Any]]:
    now=len(events)
    # Closure is a boundary at the current clock, not a fabricated policy result.
    closing=Event(now,'DECISION_MADE',**asdict(config.identity),request_id=None,intended_revision=None,
                  served_revision=None,logical_time=now,details={})
    system=[asdict(r) for r in collect_system_evidence(config,responses,events+[closing])]
    probes=[e.details for e in events if e.event_type=='BEHAVIOR_PROBE_COMPLETED']
    behavior,_=collect_behavior_evidence(config,build_workload(config),probes,system)
    observations=build_observations(config,system,behavior,routing)
    window=system[-1]['window_id']
    desired=routing[-1]
    visible=project_input(config.identity,config.policy.type,now,window,transition_index,observations,
        desired['desired_state'],desired['desired_candidate_weight'],config.evidence.max_age_logical_units,
        config.behavior_evidence.max_age_logical_units)
    result=build_policy(config.policy).decide(visible)
    return asdict(visible),asdict(result)
