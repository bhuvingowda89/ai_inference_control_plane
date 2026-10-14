"""Transparent controller-only policies: no Config, faults, effective planes or oracle."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import json
from .configuration import PolicyConfig, POLICY_VERSION
from .inputs import PolicyInput, ReadinessInput, OpsInput, BehaviorInput, AIInput, SafeRollInput, INPUT_TYPES
from ..lifecycle import Decision


class Reason(str,Enum):
    IDENTITY_UNCERTAINTY='IDENTITY_UNCERTAINTY'
    STATE_NOT_EVALUATING='STATE_NOT_EVALUATING'
    MISSING_SYSTEM_OBSERVATION='MISSING_SYSTEM_OBSERVATION'
    MISSING_BEHAVIOR_OBSERVATION='MISSING_BEHAVIOR_OBSERVATION'
    INSUFFICIENT_SYSTEM_EVIDENCE='INSUFFICIENT_SYSTEM_EVIDENCE'
    INSUFFICIENT_BEHAVIOR_EVIDENCE='INSUFFICIENT_BEHAVIOR_EVIDENCE'
    STALE_SYSTEM_EVIDENCE='STALE_SYSTEM_EVIDENCE'
    STALE_BEHAVIOR_EVIDENCE='STALE_BEHAVIOR_EVIDENCE'
    ROLLOUT_STATE_MISMATCH='ROLLOUT_STATE_MISMATCH'
    ROUTING_STATE_MISMATCH='ROUTING_STATE_MISMATCH'
    SYSTEM_UNAVAILABLE='SYSTEM_UNAVAILABLE'
    SYSTEM_REGRESSION='SYSTEM_REGRESSION'
    BEHAVIOR_REGRESSION='BEHAVIOR_REGRESSION'
    ALL_REQUIRED_EVIDENCE_ACCEPTABLE='ALL_REQUIRED_EVIDENCE_ACCEPTABLE'


@dataclass(frozen=True)
class DecisionResult:
    decision_id: str
    rollout_id: str
    epoch: int
    logical_time: int
    policy_name: str
    policy_version: str
    thresholds_sha256: str
    input_snapshot_id: str
    decision: Decision
    reason_codes: tuple[Reason,...]
    reason_details: tuple[str,...]


def exceeds(value: float, bound: float, tolerance: float) -> bool:
    return value>bound+tolerance


def channel_assessment(records: tuple, channel: str, cfg: PolicyConfig) -> tuple[list[Reason],list[Reason]]:
    """Shared conventional monitoring checks; no state/attribution inspection here."""
    req=cfg.evidence
    missing=Reason.MISSING_SYSTEM_OBSERVATION if channel=='system' else Reason.MISSING_BEHAVIOR_OBSERVATION
    insufficient=Reason.INSUFFICIENT_SYSTEM_EVIDENCE if channel=='system' else Reason.INSUFFICIENT_BEHAVIOR_EVIDENCE
    stale=Reason.STALE_SYSTEM_EVIDENCE if channel=='system' else Reason.STALE_BEHAVIOR_EVIDENCE
    if len(records)!=2 or any(not r.available for r in records):
        return [missing],[]
    uncertainty=[]
    if getattr(req,'require_'+channel+'_sufficient') and any(not r.sufficient for r in records):
        uncertainty.append(insufficient)
    if getattr(req,'require_fresh_'+channel) and any(not r.fresh for r in records):
        uncertainty.append(stale)
    a,b=records
    if channel=='system':
        if any(r.error_rate is None or r.latency_p95_ms is None for r in records):
            return list(dict.fromkeys(uncertainty+[insufficient])),[]
        harm=exceeds(b.error_rate,cfg.system.max_error_rate,cfg.numeric_tolerance) or exceeds(
            b.latency_p95_ms,a.latency_p95_ms*(1+cfg.system.max_latency_regression_fraction),cfg.numeric_tolerance)
    else:
        if a.task_type!=b.task_type or a.workload_id!=b.workload_id:
            return list(dict.fromkeys(uncertainty+[insufficient])),[]
        if b.task_type=='classification':
            if a.accuracy is None or b.accuracy is None:
                return list(dict.fromkeys(uncertainty+[insufficient])),[]
            harm=exceeds(a.accuracy-cfg.behavior.max_accuracy_drop,b.accuracy,cfg.numeric_tolerance)
        elif b.task_type=='structured_output':
            if a.exact_match_rate is None or b.exact_match_rate is None or b.structure_valid_rate is None:
                return list(dict.fromkeys(uncertainty+[insufficient])),[]
            harm=exceeds(a.exact_match_rate-cfg.behavior.max_exact_match_drop,b.exact_match_rate,cfg.numeric_tolerance) or exceeds(
                cfg.behavior.min_structure_valid_rate,b.structure_valid_rate,cfg.numeric_tolerance)
        else:
            return list(dict.fromkeys(uncertainty+[insufficient])),[]
    # Harm must always be sufficient and fresh even if promotion requirements are relaxed.
    reliable=all(r.sufficient and r.fresh and r.sample_count>0 for r in records)
    return uncertainty,[Reason.SYSTEM_REGRESSION if channel=='system' else Reason.BEHAVIOR_REGRESSION] if harm and reliable else []


class ResearchPolicy:
    def __init__(self, thresholds: PolicyConfig) -> None:
        if type(thresholds) is not PolicyConfig:
            raise TypeError("policies accept only frozen threshold configuration")
        if getattr(self, "name", thresholds.type) != thresholds.type:
            raise ValueError("policy class does not match configured policy type")
        self.thresholds=thresholds

    def decide(self, visible: PolicyInput) -> DecisionResult:
        c=self.thresholds
        if type(visible) is not INPUT_TYPES[c.type] or visible.policy_name != c.type:
            raise TypeError('policy received a broader or incorrect information capability')
        decision,reasons=self._evaluate(visible)
        reasons=tuple(dict.fromkeys(reasons))
        threshold_hash=hashlib.sha256(json.dumps(asdict(c),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        identity=hashlib.sha256((visible.input_snapshot_id+':decision:'+c.type+':'+POLICY_VERSION+':'+threshold_hash).encode()).hexdigest()
        return DecisionResult(identity,visible.rollout_id,visible.epoch,visible.logical_time,c.type,POLICY_VERSION,threshold_hash,
            visible.input_snapshot_id,decision,reasons,tuple(r.value.replace('_',' ').lower() for r in reasons))

    def _evaluate(self, v: PolicyInput) -> tuple[Decision,list[Reason]]:
        c=self.thresholds
        if v.lifecycle_state!='EVALUATING':
            return Decision.HOLD,[Reason.STATE_NOT_EVALUATING]
        if isinstance(v,ReadinessInput):
            if not v.candidate_available or v.candidate_success_rate is None or v.candidate_sample_count==0:
                return Decision.HOLD,[Reason.MISSING_SYSTEM_OBSERVATION]
            if exceeds(c.system.min_readiness_success_rate,v.candidate_success_rate,c.numeric_tolerance):
                return Decision.ROLLBACK,[Reason.SYSTEM_UNAVAILABLE]
            return Decision.PROMOTE,[Reason.ALL_REQUIRED_EVIDENCE_ACCEPTABLE]
        if isinstance(v,SafeRollInput):
            for records in (v.system,v.behavior):
                for r in records:
                    if r.available and (r.rollout_id!=v.rollout_id or r.epoch!=v.epoch or r.backend_role!=r.scope_role
                            or r.revision!=(v.baseline_revision if r.scope_role=='baseline' else v.candidate_revision)
                            or (r.fresh and (r.window_id!=v.expected_window_id or r.transition_index!=v.transition_index
                                             or r.lifecycle_state!=v.lifecycle_state))):
                        return Decision.HOLD,[Reason.IDENTITY_UNCERTAINTY]
            if len(v.behavior)==2 and all(r.available for r in v.behavior) and v.behavior[0].fresh and v.behavior[1].fresh and v.behavior[0].sufficient and v.behavior[1].sufficient:
                if (v.behavior[0].workload_id!=v.behavior[1].workload_id or v.behavior[0].task_type!=v.behavior[1].task_type
                        or v.behavior[0].sample_item_ids!=v.behavior[1].sample_item_ids):
                    return Decision.HOLD,[Reason.IDENTITY_UNCERTAINTY]
        uncertainty,harm=[],[]
        if isinstance(v,(OpsInput,AIInput)):
            u,h=channel_assessment(v.system,'system',c); uncertainty+=u; harm+=h
        if isinstance(v,(BehaviorInput,AIInput)):
            u,h=channel_assessment(v.behavior,'behavior',c); uncertainty+=u; harm+=h
        mismatches=[]
        if isinstance(v,SafeRollInput):
            if v.observed_state!=v.desired_state:
                mismatches.append(Reason.ROLLOUT_STATE_MISMATCH)
            if c.consistency.require_observed_routing_match and (v.observed_candidate_weight is None or
                    abs(v.desired_candidate_weight-v.observed_candidate_weight)>c.numeric_tolerance):
                mismatches.append(Reason.ROUTING_STATE_MISMATCH)
        # Explicit precedence: identity -> reliable harm -> uncertainty -> consistency -> promotion.
        if harm:
            return Decision.ROLLBACK,harm+uncertainty+mismatches
        if uncertainty or mismatches:
            return Decision.HOLD,uncertainty+mismatches
        return Decision.PROMOTE,[Reason.ALL_REQUIRED_EVIDENCE_ACCEPTABLE]


class ReadinessGate(ResearchPolicy):
    """Minimal service readiness capability only."""
    name="readiness_gate"


class OpsGate(ResearchPolicy):
    """Operational monitoring capability only."""
    name="ops_gate"


class BehaviorGate(ResearchPolicy):
    """Behavior monitoring capability only."""
    name="behavior_gate"


class AIGate(ResearchPolicy):
    """Strong operational and behavioral monitoring, without rollout consistency."""
    name="ai_gate"


class SafeRoll(ResearchPolicy):
    """Strict attribution, state-aware abstention and reliable-harm dominance."""
    name="saferoll"


def build_policy(config: PolicyConfig) -> ResearchPolicy:
    classes={'readiness_gate':ReadinessGate,'ops_gate':OpsGate,'behavior_gate':BehaviorGate,'ai_gate':AIGate,'saferoll':SafeRoll}
    if config.type not in classes:
        raise ValueError('unsupported research policy')
    return classes[config.type](config)
