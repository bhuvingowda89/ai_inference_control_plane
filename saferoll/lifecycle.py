"""Independent, policy-neutral lifecycle graph and immutable history."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from .ids import RolloutIdentity

STATE_MACHINE_VERSION = "1.0"


class RolloutState(str, Enum):
    STABLE = "STABLE"
    CANDIDATE_CREATED = "CANDIDATE_CREATED"
    VALIDATING = "VALIDATING"
    CANARY = "CANARY"
    EVALUATING = "EVALUATING"
    HOLDING = "HOLDING"
    PROMOTING = "PROMOTING"
    ROLLING_BACK = "ROLLING_BACK"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Decision(str, Enum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    ROLLBACK = "ROLLBACK"


class Outcome(str, Enum):
    PROMOTED = "PROMOTED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"


S = RolloutState
# Terminal states have no outgoing edges. Failure is explicit from nonterminals.
ALLOWED_TRANSITIONS = {
    S.STABLE: frozenset({S.CANDIDATE_CREATED, S.FAILED}),
    S.CANDIDATE_CREATED: frozenset({S.VALIDATING, S.FAILED}),
    S.VALIDATING: frozenset({S.CANARY, S.FAILED}),
    S.CANARY: frozenset({S.EVALUATING, S.FAILED}),
    S.EVALUATING: frozenset({S.HOLDING, S.PROMOTING, S.ROLLING_BACK, S.FAILED}),
    S.HOLDING: frozenset({S.EVALUATING, S.FAILED}),
    S.PROMOTING: frozenset({S.COMPLETED, S.FAILED}),
    S.ROLLING_BACK: frozenset({S.RECOVERING, S.FAILED}),
    S.RECOVERING: frozenset({S.COMPLETED, S.FAILED}),
    S.COMPLETED: frozenset(), S.FAILED: frozenset(),
}
DECISION_TARGETS = {Decision.PROMOTE: S.PROMOTING, Decision.HOLD: S.HOLDING,
                    Decision.ROLLBACK: S.ROLLING_BACK}


def validate_edge(source: RolloutState, target: RolloutState,
                  decision: Decision | None = None) -> None:
    """One central graph check for coordinator and artifact validation."""
    if target not in ALLOWED_TRANSITIONS[source]:
        raise ValueError(f"forbidden lifecycle transition: {source.value} -> {target.value}")
    if target in DECISION_TARGETS.values():
        if source != S.EVALUATING or decision is None or DECISION_TARGETS[decision] != target:
            raise ValueError("transition requires its matching evaluation decision")
    elif decision is not None:
        raise ValueError("decision cannot cause this transition")


@dataclass(frozen=True)
class RolloutSnapshot:
    rollout_id: str
    epoch: int
    baseline_revision: str
    candidate_revision: str
    desired_state: RolloutState
    observed_state: RolloutState
    decision: Decision | None
    logical_time: int
    transition_index: int
    reason: str
    desired_candidate_weight: float
    observed_candidate_weight: float


@dataclass(frozen=True)
class RolloutTransition:
    transition_index: int
    rollout_id: str
    epoch: int
    baseline_revision: str
    candidate_revision: str
    from_desired_state: RolloutState
    to_desired_state: RolloutState
    from_observed_state: RolloutState
    to_observed_state: RolloutState
    decision: Decision | None
    logical_time: int
    reason: str
    kind: str
    from_desired_candidate_weight: float
    to_desired_candidate_weight: float
    from_observed_candidate_weight: float
    to_observed_candidate_weight: float


class RolloutCoordinator:
    """Record desired changes and observations separately, or converge atomically."""
    def __init__(self, identity: RolloutIdentity, candidate_weight: float,
                 max_holds: int, logical_time: int = 0) -> None:
        if not 0 <= candidate_weight <= 1 or type(max_holds) is not int or max_holds < 0:
            raise ValueError("invalid coordinator routing weight or hold bound")
        self.identity = identity
        self.canary_weight = candidate_weight
        self.max_holds = max_holds
        self._history: list[RolloutTransition] = []
        self._desired = self._observed = S.STABLE
        self._desired_weight = self._observed_weight = 0.0
        self._decision_count = self._hold_count = 0
        self._outcome: Outcome | None = None
        self._record(S.STABLE, S.STABLE, 0.0, 0.0, None, logical_time, "rollout initialized", "INITIAL")

    @property
    def history(self) -> tuple[RolloutTransition, ...]:
        return tuple(self._history)

    @property
    def snapshot(self) -> RolloutSnapshot:
        t = self._history[-1]
        return RolloutSnapshot(self.identity.rollout_id, self.identity.epoch,
                               self.identity.baseline_revision, self.identity.candidate_revision,
                               self._desired, self._observed, t.decision, t.logical_time,
                               t.transition_index, t.reason, self._desired_weight, self._observed_weight)

    @property
    def final_outcome(self) -> Outcome | None:
        return self._outcome

    @property
    def decision_count(self) -> int:
        return self._decision_count

    @property
    def hold_count(self) -> int:
        return self._hold_count

    def _record(self, desired: RolloutState, observed: RolloutState, desired_weight: float,
                observed_weight: float, decision: Decision | None, time: int,
                reason: str, kind: str) -> RolloutTransition:
        if type(time) is not int or time < 0 or (self._history and time <= self._history[-1].logical_time):
            raise ValueError("lifecycle logical time must increase strictly")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("transition reason must be nonempty")
        t = RolloutTransition(len(self._history), self.identity.rollout_id, self.identity.epoch,
                              self.identity.baseline_revision, self.identity.candidate_revision,
                              self._desired, desired, self._observed, observed, decision, time, reason, kind,
                              self._desired_weight, desired_weight, self._observed_weight, observed_weight)
        self._history.append(t)
        self._desired, self._observed = desired, observed
        self._desired_weight, self._observed_weight = desired_weight, observed_weight
        if desired == observed == S.FAILED:
            self._outcome = Outcome.FAILED
        elif desired == observed == S.COMPLETED:
            origin = next(r.from_desired_state for r in reversed(self._history)
                          if r.to_desired_state == S.COMPLETED and r.from_desired_state != S.COMPLETED)
            self._outcome = Outcome.PROMOTED if origin == S.PROMOTING else Outcome.ROLLED_BACK
        return t

    def _weight(self, target: RolloutState) -> float:
        if target == S.PROMOTING:
            return 1.0
        if target in {S.ROLLING_BACK, S.RECOVERING, S.FAILED}:
            return 0.0
        if target in {S.CANARY, S.EVALUATING, S.HOLDING}:
            return self.canary_weight
        return self._desired_weight

    def transition(self, target: RolloutState, logical_time: int, reason: str,
                   converge: bool = True, decision: Decision | None = None) -> RolloutTransition:
        target = S(target)
        if decision is not None:
            raise ValueError("use apply_decision for decision-driven transitions")
        return self._transition(target, logical_time, reason, converge, None)

    def _transition(self, target: RolloutState, time: int, reason: str,
                    converge: bool, decision: Decision | None) -> RolloutTransition:
        if self._desired != self._observed or self._desired_weight != self._observed_weight:
            raise ValueError("observe pending convergence before another transition")
        validate_edge(self._desired, target, decision)
        weight = self._weight(target)
        return self._record(target, target if converge else self._observed, weight,
                            weight if converge else self._observed_weight, decision, time, reason, "TRANSITION")

    def observe_state(self, state: RolloutState, logical_time: int, reason: str) -> RolloutTransition:
        state = S(state)
        if state != self._desired or state == self._observed:
            raise ValueError("observation must converge to the pending desired state")
        validate_edge(self._observed, state, self._history[-1].decision)
        return self._record(self._desired, state, self._desired_weight, self._desired_weight,
                            None, logical_time, reason, "OBSERVATION")

    def apply_decision(self, decision: Decision, logical_time: int, reason: str,
                       converge: bool = True) -> RolloutTransition:
        decision = Decision(decision)
        if self._desired != S.EVALUATING or self._observed != S.EVALUATING:
            raise ValueError("decisions require converged EVALUATING state")
        exceeded = decision == Decision.HOLD and self._hold_count >= self.max_holds
        target = S.FAILED if exceeded else DECISION_TARGETS[decision]
        t = self._transition(target, logical_time, "hold bound exceeded" if exceeded else reason,
                             converge, None if exceeded else decision)
        self._decision_count += 1
        if decision == Decision.HOLD and not exceeded:
            self._hold_count += 1
        return t
