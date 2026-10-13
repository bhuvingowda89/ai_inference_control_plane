"""Sequential deterministic scenario execution; policy-neutral lifecycle scaffolding."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from typing import Any
from .artifacts import config_hash, write_artifacts
from .backends.mock import MockBackend
from .config import Config, config_from_dict
from .faults.base import FaultInjector
from .faults.latency import LatencyFault
from .faults.failure import CandidateRequestFailure
from .ids import request_id
from .lifecycle import RolloutCoordinator, RolloutState as S
from .policies import build_test_policy
from .router import WeightedRouter
from .schemas import Event, Request, Response, RunState


def summarize(config: Config, responses: list[Response]) -> dict[str, Any]:
    baseline = [r for r in responses if r.backend_role == "baseline"]
    candidate = [r for r in responses if r.backend_role == "candidate"]
    state = RunState(**asdict(config.identity), completed_requests=len(responses))
    return {**asdict(state), "planned_requests": config.experiment.request_count,
            "successful_requests": sum(r.success for r in responses),
            "failed_requests": sum(not r.success for r in responses),
            "baseline_requests": len(baseline), "candidate_requests": len(candidate),
            "candidate_fraction": len(candidate) / len(responses) if responses else 0.0,
            "fault_affected_requests": sum(r.fault_active for r in responses),
            "mean_baseline_latency_ms": sum(r.latency_ms for r in baseline) / len(baseline) if baseline else None,
            "mean_candidate_latency_ms": sum(r.latency_ms for r in candidate) / len(candidate) if candidate else None}


def simulate(config: Config) -> tuple[list[Response], list[Event], dict[str, Any]]:
    router = WeightedRouter(config.routing, config.experiment.seed)
    backends = {role: MockBackend(role, getattr(config, role)) for role in ("baseline", "candidate")}
    fault: FaultInjector = (LatencyFault(config.fault) if config.fault.type == "latency"
                            else CandidateRequestFailure(config.fault))
    responses: list[Response] = []
    events: list[Event] = []
    identity = config.identity

    def emit(kind: str, request: Request | None = None, response: Response | None = None,
             details: dict[str, Any] | None = None) -> None:
        record = response or request
        events.append(Event(len(events), kind, **asdict(identity),
                            request_id=record.request_id if record else None,
                            intended_revision=record.intended_revision if record else None,
                            served_revision=record.served_revision if record else None,
                            logical_time=len(events), details=details or {}))

    emit("RUN_STARTED", details={"planned_requests": config.experiment.request_count})
    coordinator = RolloutCoordinator(identity, config.routing.candidate_weight,
                                     config.lifecycle.max_holds, logical_time=len(events))
    emit("STATE_TRANSITION", details=asdict(coordinator.history[-1]))
    policy = build_test_policy(config.decision_policy.type, config.decision_policy.holds_before_decision)

    def transition(target: S, reason: str) -> None:
        record = coordinator.transition(target, len(events), reason)
        emit("STATE_TRANSITION", details=asdict(record))

    def serve(count: int) -> None:
        snapshot = coordinator.snapshot
        router.observe_candidate_weight(snapshot.observed_candidate_weight)
        for _ in range(count):
            index = len(responses)
            for kind in fault.before_request(index):
                emit(kind, details={"request_index": index, **asdict(config.fault)})
            role = router.route()
            request = Request(index, request_id(identity, index), identity.rollout_id, identity.epoch,
                              getattr(config, role).revision, None, role, f"request-{index:06d}",
                              snapshot.desired_state.value, snapshot.observed_state.value,
                              snapshot.transition_index)
            context = {"desired_rollout_state": request.desired_rollout_state,
                       "observed_rollout_state": request.observed_rollout_state,
                       "transition_index": request.transition_index}
            emit("REQUEST_CREATED", request, details={"request_index": index, "payload": request.payload, **context})
            emit("REQUEST_ROUTED", request, details={"backend_role": role, **context})
            response = fault.modify_response(backends[role].infer(request))
            responses.append(response)
            emit("REQUEST_COMPLETED", response=response,
                 details={"success": response.success, "latency_ms": response.latency_ms,
                          "fault_active": response.fault_active, "output": response.output, **context})

    transition(S.CANDIDATE_CREATED, "candidate registered")
    transition(S.VALIDATING, "mock candidate validation started")
    serve(config.lifecycle.validation_requests)
    transition(S.CANARY, "validation completed; canary enabled")
    serve(config.lifecycle.canary_requests)
    transition(S.EVALUATING, "canary exposure completed")
    evaluation_index = 0
    while coordinator.snapshot.observed_state == S.EVALUATING:
        serve(config.lifecycle.evaluation_requests)
        decision = policy.decide(coordinator.snapshot, evaluation_index)
        emit("DECISION_MADE", details={"decision": decision.value, "evaluation_index": evaluation_index,
                                      "policy_type": config.decision_policy.type,
                                      "transition_index": coordinator.snapshot.transition_index})
        record = coordinator.apply_decision(decision, len(events), "deterministic test provider decision")
        emit("STATE_TRANSITION", details=asdict(record))
        evaluation_index += 1
        if coordinator.snapshot.observed_state == S.HOLDING:
            serve(config.lifecycle.hold_requests)
            transition(S.EVALUATING, "deterministic hold cycle completed")
        elif coordinator.snapshot.observed_state == S.PROMOTING:
            serve(config.lifecycle.completion_requests)
            transition(S.COMPLETED, "simulated promotion completed")
        elif coordinator.snapshot.observed_state == S.ROLLING_BACK:
            transition(S.RECOVERING, "baseline routing restored; recovery verification started")
            serve(config.lifecycle.completion_requests)
            transition(S.COMPLETED, "simulated recovery verified")
    for kind in fault.finish():
        emit(kind, details={"request_index": len(responses), **asdict(config.fault)})
    emit("RUN_COMPLETED", details={"completed_requests": len(responses),
                                   "final_outcome": coordinator.final_outcome.value})
    summary = summarize(config, responses)
    summary.update(final_lifecycle_state=coordinator.snapshot.observed_state.value,
                   final_outcome=coordinator.final_outcome.value,
                   decision_count=coordinator.decision_count, hold_count=coordinator.hold_count,
                   transition_count=len(coordinator.history))
    return responses, events, summary


def run_experiment(config: Config, output_root: str | Path = "runs") -> Path:
    """Hash names scientific identity; numbered suffix distinguishes equivalent reruns."""
    from .validator import validate_run
    config = config_from_dict(config.to_dict())
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    stem = "run-" + config_hash(config)
    run_dir = root / stem
    suffix = 1
    while run_dir.exists():
        run_dir = root / f"{stem}-{suffix:03d}"
        suffix += 1
    responses, events, summary = simulate(config)
    write_artifacts(run_dir, config, responses, events, summary)
    result = validate_run(run_dir)
    if not result["valid"]:
        raise ValueError(f"generated run failed validation: {result['issues']}")
    return run_dir
