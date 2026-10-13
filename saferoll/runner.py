"""Sequential deterministic scenario execution; no deployment decisions."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from typing import Any
from .artifacts import config_hash, write_artifacts
from .backends.mock import MockBackend
from .config import Config
from .faults.base import FaultInjector
from .faults.latency import LatencyFault
from .ids import request_id
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
    fault: FaultInjector = LatencyFault(config.fault)
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
    for index in range(config.experiment.request_count):
        for kind in fault.before_request(index):
            emit(kind, details={"request_index": index, **asdict(config.fault)})
        role = router.route()
        request = Request(index, request_id(identity, index), identity.rollout_id, identity.epoch,
                          getattr(config, role).revision, None, role, f"request-{index:06d}")
        emit("REQUEST_CREATED", request, details={"request_index": index, "payload": request.payload})
        emit("REQUEST_ROUTED", request, details={"backend_role": role})
        response = fault.modify_response(backends[role].infer(request))
        responses.append(response)
        emit("REQUEST_COMPLETED", response=response,
             details={"success": response.success, "latency_ms": response.latency_ms,
                      "fault_active": response.fault_active, "output": response.output})
    for kind in fault.finish():
        emit(kind, details={"request_index": config.experiment.request_count, **asdict(config.fault)})
    emit("RUN_COMPLETED", details={"completed_requests": len(responses)})
    return responses, events, summarize(config, responses)


def run_experiment(config: Config, output_root: str | Path = "runs") -> Path:
    """Hash names scientific identity; numbered suffix distinguishes equivalent reruns."""
    from .validator import validate_run
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
