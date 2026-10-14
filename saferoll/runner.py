"""Sequential deterministic scenario execution; policy-neutral lifecycle scaffolding."""
from __future__ import annotations
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from .artifacts import config_hash, write_artifacts
from .backends.mock import MockBackend
from .behavior.workload import build_workload
from .behavior.probes import generate_probe
from .config import Config, config_from_dict
from .faults.base import FaultInjector
from .faults.latency import LatencyFault
from .faults.failure import CandidateRequestFailure
from .ids import request_id
from .lifecycle import RolloutCoordinator, RolloutState as S
from .policies import build_test_policy
from .research.runtime import make_live_decision
from .router import WeightedRouter
from .planes import ServingPlanes
from .containment import containment_facts
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
    workload = build_workload(config)
    window_sequence = 0
    planes = ServingPlanes(config)
    extra_faults = [(f, LatencyFault(f) if f.type == "latency" else CandidateRequestFailure(f))
                    for f in config.phase4e.request_faults]

    def emit(kind: str, request: Request | None = None, response: Response | None = None,
             details: dict[str, Any] | None = None, probe: dict[str, Any] | None = None) -> None:
        record = response or request
        events.append(Event(len(events), kind, **asdict(identity),
                            request_id=record.request_id if record else probe["request_id"] if probe else None,
                            intended_revision=record.intended_revision if record else probe["intended_revision"] if probe else None,
                            served_revision=record.served_revision if record else probe["served_revision"] if probe else None,
                            logical_time=len(events), details=details or {}))

    emit("RUN_STARTED", details={"planned_requests": config.experiment.request_count})
    coordinator = RolloutCoordinator(identity, config.routing.candidate_weight,
                                     config.lifecycle.max_holds, logical_time=len(events))
    emit("STATE_TRANSITION", details=asdict(coordinator.history[-1]))
    def capture(point: str) -> int:
        index = planes.capture(coordinator.snapshot, len(responses), len(events), point)
        emit("ROUTING_STATE", details=planes.records[index])
        return index

    capture("transition")
    policy = build_test_policy(config.decision_policy.type, config.decision_policy.holds_before_decision)

    def transition(target: S, reason: str) -> None:
        record = coordinator.transition(target, len(events), reason)
        emit("STATE_TRANSITION", details=asdict(record))
        capture("transition")

    def serve(count: int) -> None:
        nonlocal window_sequence
        snapshot = coordinator.snapshot
        for _ in range(count):
            index = len(responses)
            for kind in fault.before_request(index):
                emit(kind, details={"request_index": index, **asdict(config.fault)})
            for f, injector in extra_faults:
                for kind in injector.before_request(index):
                    emit(kind, details={"fault_id": f.id, "request_index": index, **asdict(f)})
            routing_index = capture("request")
            router.observe_candidate_weight(planes.snapshot.effective_candidate_weight)
            role = router.route()
            request = Request(index, request_id(identity, index), identity.rollout_id, identity.epoch,
                              getattr(config, role).revision, None, role, f"request-{index:06d}",
                              snapshot.desired_state.value, snapshot.observed_state.value,
                              snapshot.transition_index, routing_index)
            context = {"desired_rollout_state": request.desired_rollout_state,
                       "observed_rollout_state": request.observed_rollout_state,
                       "transition_index": request.transition_index, "routing_state_index": routing_index}
            emit("REQUEST_CREATED", request, details={"request_index": index, "payload": request.payload, **context})
            emit("REQUEST_ROUTED", request, details={"backend_role": role, **context})
            response = fault.modify_response(backends[role].infer(request))
            for _, injector in extra_faults:
                response = injector.modify_response(response)
            responses.append(response)
            emit("REQUEST_COMPLETED", response=response,
                 details={"success": response.success, "latency_ms": response.latency_ms,
                          "fault_active": response.fault_active, "output": response.output, **context})

        if config.behavior_evidence.enabled:
            b = config.behavior_evidence
            for index, item in enumerate(workload["items"]):
                for role, limit in (("baseline", b.baseline_items_per_window), ("candidate", b.candidate_items_per_window)):
                    if index < limit:
                        probe_config = config
                        for f in config.phase4e.behavior_faults:
                            if f.target == role and f.start_window <= window_sequence < f.start_window + f.duration_windows:
                                probe_config = replace(probe_config, **{role: replace(getattr(probe_config, role),
                                    behavior_profile=f.type, regression_every_nth_item=f.every_nth_item)})
                        probe = generate_probe(probe_config, snapshot, window_sequence, item, role, len(events))
                        emit("BEHAVIOR_PROBE_COMPLETED", details=probe, probe=probe)
        window_sequence += 1

    transition(S.CANDIDATE_CREATED, "candidate registered")
    transition(S.VALIDATING, "mock candidate validation started")
    serve(config.lifecycle.validation_requests)
    transition(S.CANARY, "validation completed; canary enabled")
    serve(config.lifecycle.canary_requests)
    transition(S.EVALUATING, "canary exposure completed")
    evaluation_index = 0
    while coordinator.snapshot.observed_state == S.EVALUATING:
        serve(config.lifecycle.evaluation_requests)
        details = {"evaluation_index": evaluation_index, "transition_index": coordinator.snapshot.transition_index}
        if config.policy.type == "dummy":
            decision = policy.decide(coordinator.snapshot, evaluation_index)
            details.update(decision=decision.value, policy_type=config.decision_policy.type)
        else:
            visible, result = make_live_decision(config, responses, events, planes.records, coordinator.snapshot.transition_index)
            from .lifecycle import Decision
            decision = Decision(result["decision"])
            details.update(decision=decision.value, policy_type=config.policy.type, policy_input=visible, policy_result=result)
        emit("DECISION_MADE", details=details)
        record = coordinator.apply_decision(decision, len(events), "deterministic test provider decision")
        emit("STATE_TRANSITION", details=asdict(record))
        capture("transition")
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
    for f, injector in extra_faults:
        for kind in injector.finish():
            emit(kind, details={"fault_id": f.id, "request_index": len(responses), **asdict(f)})
    emit("RUN_COMPLETED", details={"completed_requests": len(responses),
                                   "final_outcome": coordinator.final_outcome.value})
    summary = summarize(config, responses)
    summary.update(final_lifecycle_state=coordinator.snapshot.observed_state.value,
                   final_outcome=coordinator.final_outcome.value,
                   decision_count=coordinator.decision_count, hold_count=coordinator.hold_count,
                   transition_count=len(coordinator.history),
                   behavior_windows=window_sequence if config.behavior_evidence.enabled else 0,
                   behavior_samples=sum(e.event_type == "BEHAVIOR_PROBE_COMPLETED" for e in events),
                   paired_behavior_samples=(window_sequence * min(config.behavior_evidence.baseline_items_per_window,
                       config.behavior_evidence.candidate_items_per_window) if config.behavior_evidence.enabled else 0))
    if config.policy.type != "dummy":
        summary.update(planned_requests=len(responses), request_budget=config.experiment.request_count,
            promote_count=sum(e.event_type == "DECISION_MADE" and e.details["decision"] == "PROMOTE" for e in events),
            rollback_count=sum(e.event_type == "DECISION_MADE" and e.details["decision"] == "ROLLBACK" for e in events),
            policy_hold_count=sum(e.event_type == "DECISION_MADE" and e.details["decision"] == "HOLD" for e in events))
    summary.update(containment_facts([asdict(e) for e in events], planes.records))
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
