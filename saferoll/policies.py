"""Deterministic dummy providers for exercising lifecycle paths, not research policies."""
from __future__ import annotations
from typing import Protocol
from .lifecycle import Decision, RolloutSnapshot


class DecisionProvider(Protocol):
    def decide(self, snapshot: RolloutSnapshot, evaluation_index: int) -> Decision: ...


class AlwaysPromoteTestPolicy:
    def decide(self, snapshot: RolloutSnapshot, evaluation_index: int) -> Decision:
        return Decision.PROMOTE


class AlwaysRollbackTestPolicy:
    def decide(self, snapshot: RolloutSnapshot, evaluation_index: int) -> Decision:
        return Decision.ROLLBACK


class HoldThenPromoteTestPolicy:
    def __init__(self, holds_before_decision: int) -> None:
        self.holds_before_decision = holds_before_decision

    def decide(self, snapshot: RolloutSnapshot, evaluation_index: int) -> Decision:
        return Decision.HOLD if evaluation_index < self.holds_before_decision else Decision.PROMOTE


class AlwaysHoldTestPolicy:
    def decide(self, snapshot: RolloutSnapshot, evaluation_index: int) -> Decision:
        return Decision.HOLD


def build_test_policy(policy_type: str, holds_before_decision: int) -> DecisionProvider:
    if policy_type == "always_promote":
        return AlwaysPromoteTestPolicy()
    if policy_type == "always_rollback":
        return AlwaysRollbackTestPolicy()
    if policy_type == "hold_then_promote":
        return HoldThenPromoteTestPolicy(holds_before_decision)
    if policy_type == "always_hold":
        return AlwaysHoldTestPolicy()
    raise ValueError("unknown test policy")
