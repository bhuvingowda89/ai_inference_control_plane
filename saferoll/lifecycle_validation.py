"""Lifecycle checks independent of scenario replay, using the central graph."""
from __future__ import annotations
from dataclasses import asdict, fields
from typing import Any
from .artifacts import canonical_json
from .config import Config
from .lifecycle import Decision, RolloutCoordinator, RolloutState, RolloutTransition, Outcome


def validate_lifecycle(config: Config, transitions: list[Any], events: list[Any],
                       rows: list[dict[str, str]], summary: Any) -> list[str]:
    issues: list[str] = []
    required = {f.name for f in fields(RolloutTransition)}
    coordinator = None
    pending_decision = None
    last_record = None
    event_records = []
    try:
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("lifecycle event must be an object")
            if event.get("event_type") == "DECISION_MADE":
                if coordinator is None or coordinator.snapshot.desired_state != RolloutState.EVALUATING:
                    raise ValueError("decision outside EVALUATING")
                if pending_decision is not None:
                    raise ValueError("decision not followed by its transition")
                pending_decision = Decision(event["details"]["decision"])
            elif event.get("event_type") == "STATE_TRANSITION":
                record = event["details"]
                if not isinstance(record, dict) or set(record) != required:
                    raise ValueError("lifecycle record schema mismatch")
                if record["logical_time"] != event["logical_time"]:
                    raise ValueError("lifecycle/event clock mismatch")
                if type(record["transition_index"]) is not int or record["transition_index"] != len(event_records):
                    raise ValueError("transition indices must be contiguous")
                for key, value in asdict(config.identity).items():
                    if record[key] != value:
                        raise ValueError(f"transition identity mismatch: {key}")
                for key in ("from_desired_state", "to_desired_state", "from_observed_state", "to_observed_state"):
                    RolloutState(record[key])
                if record["decision"] is not None:
                    Decision(record["decision"])
                if coordinator is None:
                    coordinator = RolloutCoordinator(config.identity, config.routing.candidate_weight,
                                                     config.lifecycle.max_holds, event["logical_time"])
                    expected = coordinator.history[0]
                elif record["kind"] == "OBSERVATION":
                    if pending_decision is not None:
                        raise ValueError("decision cannot be consumed by observation")
                    expected = coordinator.observe_state(record["to_observed_state"], event["logical_time"], record["reason"])
                elif pending_decision is not None:
                    expected = coordinator.apply_decision(pending_decision, event["logical_time"], record["reason"],
                                                          converge=record["to_observed_state"] == record["to_desired_state"])
                    pending_decision = None
                else:
                    expected = coordinator.transition(record["to_desired_state"], event["logical_time"], record["reason"],
                                                      converge=record["to_observed_state"] == record["to_desired_state"])
                if canonical_json(record) != canonical_json(asdict(expected)):
                    raise ValueError("transition chain/state/decision/traffic mismatch")
                event_records.append(record)
                last_record = record
            elif event.get("event_type") == "REQUEST_CREATED":
                details = event["details"]
                if last_record is None or details["transition_index"] != last_record["transition_index"]:
                    raise ValueError("request does not reference latest lifecycle transition")
                if (details["desired_rollout_state"] != last_record["to_desired_state"] or
                        details["observed_rollout_state"] != last_record["to_observed_state"]):
                    raise ValueError("request/event lifecycle attribution mismatch")
        if pending_decision is not None or coordinator is None:
            raise ValueError("incomplete lifecycle or unconsumed decision")
        if canonical_json(transitions) != canonical_json(event_records):
            raise ValueError("lifecycle artifact differs from ordered lifecycle events")
        for row in rows:
            index = int(row["transition_index"])
            if index < 0 or index >= len(transitions):
                raise ValueError("request transition reference out of bounds")
            record = transitions[index]
            if (row["desired_rollout_state"] != record["to_desired_state"] or
                    row["observed_rollout_state"] != record["to_observed_state"]):
                raise ValueError("request lifecycle state does not match transition")
        state = coordinator.snapshot
        if state.desired_state != state.observed_state or state.observed_state not in {
                RolloutState.COMPLETED, RolloutState.FAILED}:
            raise ValueError("run ends without converged terminal state")
        if state.observed_state == RolloutState.FAILED and coordinator.final_outcome != Outcome.FAILED:
            raise ValueError("FAILED rollout cannot claim success")
        expected_summary = {"final_lifecycle_state": state.observed_state.value,
                            "final_outcome": coordinator.final_outcome.value,
                            "hold_count": coordinator.hold_count, "decision_count": coordinator.decision_count,
                            "transition_count": len(coordinator.history)}
        for key, value in expected_summary.items():
            if canonical_json(summary.get(key)) != canonical_json(value):
                raise ValueError(f"lifecycle summary mismatch: {key}")
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        issues.append(f"invalid lifecycle: {error}")
    return issues
