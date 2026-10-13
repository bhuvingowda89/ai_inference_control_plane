"""Revision-attributed operational facts, independent of decision providers."""
from __future__ import annotations
from dataclasses import dataclass, replace
import json
import math
from uuid import NAMESPACE_URL, uuid5
from .config import Config, EvidenceConfig
from .ids import RolloutIdentity
from .schemas import Event, Response

EVIDENCE_SCHEMA_VERSION = "1.0"
WINDOW_TYPES = {"VALIDATING": "VALIDATION", "CANARY": "CANARY", "EVALUATING": "EVALUATION",
                "HOLDING": "HOLD", "PROMOTING": "PROMOTION", "RECOVERING": "RECOVERY"}


def window_id(identity: RolloutIdentity, transition_index: int, window_sequence: int) -> str:
    name = json.dumps([identity.rollout_id, identity.epoch, "system-window", transition_index, window_sequence],
                      separators=(",", ":"), ensure_ascii=False)
    return str(uuid5(NAMESPACE_URL, name))


def percentile(values: list[float], percent: float) -> float | None:
    """Linear interpolation at (n-1)*percent/100 in sorted values; empty -> null."""
    if not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError("percentile must be in [0,100]")
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("latencies must be finite and nonnegative")
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


@dataclass(frozen=True)
class EvidenceWindow:
    window_id: str
    rollout_id: str
    epoch: int
    lifecycle_state: str
    transition_index: int
    window_sequence: int
    window_type: str
    window_start_logical_time: int
    planned_request_count: int


@dataclass(frozen=True)
class SystemEvidence:
    schema_version: str
    window_id: str
    rollout_id: str
    epoch: int
    revision: str
    backend_role: str
    lifecycle_state: str
    transition_index: int
    window_sequence: int
    window_type: str
    window_start_logical_time: int
    window_end_logical_time: int
    closed_at_logical_time: int
    evaluated_at_logical_time: int
    planned_window_request_count: int
    sample_request_ids: tuple[str, ...]
    sample_count: int
    minimum_required_samples: int
    sufficient: bool
    latest_sample_logical_time: int | None
    age_at_evaluation: int | None
    freshness_status: str
    request_count: int
    success_count: int
    failure_count: int
    success_rate: float | None
    error_rate: float | None
    latency_mean_ms: float | None
    latency_min_ms: float | None
    latency_max_ms: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None


class EvidenceCollector:
    """One open lifecycle window at a time; samples cannot be reused across windows."""
    def __init__(self, identity: RolloutIdentity, config: EvidenceConfig) -> None:
        if config.minimum_samples_per_revision < 1 or config.max_age_logical_units < 0:
            raise ValueError("invalid evidence configuration")
        self.identity = identity
        self.config = config
        self._window: EvidenceWindow | None = None
        self._samples: list[tuple[Response, int]] = []
        self._seen: set[str] = set()
        self._records: list[SystemEvidence] = []
        self._window_count = 0
        self._last_close = -1

    def open_window(self, lifecycle_state: str, transition_index: int,
                    start_logical_time: int, planned_request_count: int) -> EvidenceWindow:
        if self._window is not None:
            raise ValueError("close the current evidence window first")
        if lifecycle_state not in WINDOW_TYPES:
            raise ValueError("unsupported evidence lifecycle state")
        if any(type(v) is not int or v < 0 for v in
               (transition_index, start_logical_time, planned_request_count)) or start_logical_time < self._last_close:
            raise ValueError("invalid evidence window bounds")
        self._window = EvidenceWindow(window_id(self.identity, transition_index, self._window_count),
                                      self.identity.rollout_id, self.identity.epoch, lifecycle_state,
                                      transition_index, self._window_count, WINDOW_TYPES[lifecycle_state],
                                      start_logical_time, planned_request_count)
        self._samples = []
        return self._window

    def add_request(self, response: Response, logical_time: int) -> None:
        window = self._window
        if window is None:
            raise ValueError("no open evidence window")
        expected_revision = {"baseline": self.identity.baseline_revision, "candidate": self.identity.candidate_revision}
        if (response.rollout_id != self.identity.rollout_id or response.epoch != self.identity.epoch
                or response.observed_rollout_state != window.lifecycle_state
                or response.transition_index != window.transition_index
                or expected_revision.get(response.backend_role) != response.served_revision):
            raise ValueError("sample identity/revision/lifecycle does not match window")
        if response.request_id in self._seen:
            raise ValueError("sample already contributed to an evidence window")
        if (type(logical_time) is not int or logical_time <= window.window_start_logical_time
                or (self._samples and logical_time <= self._samples[-1][1])):
            raise ValueError("sample logical time must increase within window")
        if type(response.success) is not bool or not math.isfinite(response.latency_ms) or response.latency_ms < 0:
            raise ValueError("invalid operational sample")
        if len(self._samples) >= window.planned_request_count:
            raise ValueError("window request boundary exceeded")
        self._samples.append((response, logical_time))
        self._seen.add(response.request_id)

    def close_window(self, closed_at_logical_time: int,
                     evaluated_at_logical_time: int | None = None) -> tuple[SystemEvidence, ...]:
        w = self._window
        if w is None:
            raise ValueError("no open evidence window")
        evaluated = closed_at_logical_time if evaluated_at_logical_time is None else evaluated_at_logical_time
        if (type(closed_at_logical_time) is not int or type(evaluated) is not int
                or closed_at_logical_time < w.window_start_logical_time or evaluated < closed_at_logical_time
                or (self._samples and closed_at_logical_time <= self._samples[-1][1])):
            raise ValueError("window closure/evaluation time is invalid")
        if len(self._samples) != w.planned_request_count:
            raise ValueError("window must close at its configured request-count boundary")
        records = []
        for role, revision in (("baseline", self.identity.baseline_revision), ("candidate", self.identity.candidate_revision)):
            samples = [(r, t) for r, t in self._samples if r.backend_role == role]
            latencies = [r.latency_ms for r, _ in samples]
            count = len(samples)
            successes = sum(r.success for r, _ in samples)
            latest = samples[-1][1] if samples else None
            age = evaluated - latest if latest is not None else None
            records.append(SystemEvidence(
                EVIDENCE_SCHEMA_VERSION, w.window_id, w.rollout_id, w.epoch, revision, role,
                w.lifecycle_state, w.transition_index, w.window_sequence, w.window_type,
                w.window_start_logical_time, closed_at_logical_time, closed_at_logical_time, evaluated,
                w.planned_request_count, tuple(r.request_id for r, _ in samples), count,
                self.config.minimum_samples_per_revision, count >= self.config.minimum_samples_per_revision,
                latest, age, "NO_SAMPLES" if age is None else (
                    "FRESH" if age <= self.config.max_age_logical_units else "STALE"),
                count, successes, count - successes, successes / count if count else None,
                (count - successes) / count if count else None,
                math.fsum(v / count for v in latencies) if count else None,
                min(latencies) if count else None, max(latencies) if count else None,
                percentile(latencies, 50), percentile(latencies, 95)))
        self._records.extend(records)
        self._window = None
        self._window_count += 1
        self._last_close = closed_at_logical_time
        return tuple(records)

    def get_evidence(self, evaluated_at_logical_time: int | None = None) -> tuple[SystemEvidence, ...]:
        """Optionally obtain new immutable freshness views at a later logical time."""
        if evaluated_at_logical_time is None:
            return tuple(self._records)
        if type(evaluated_at_logical_time) is not int or any(
                evaluated_at_logical_time < r.closed_at_logical_time for r in self._records):
            raise ValueError("evaluation cannot precede closure")
        records = []
        for r in self._records:
            age = (evaluated_at_logical_time - r.latest_sample_logical_time
                   if r.latest_sample_logical_time is not None else None)
            records.append(replace(r, evaluated_at_logical_time=evaluated_at_logical_time,
                                   age_at_evaluation=age, freshness_status="NO_SAMPLES" if age is None else (
                                       "FRESH" if age <= self.config.max_age_logical_units else "STALE")))
        return tuple(records)


def collect_system_evidence(config: Config, responses: list[Response], events: list[Event]) -> tuple[SystemEvidence, ...]:
    """Collect from completed request records; lifecycle events define count-bounded windows."""
    collector = EvidenceCollector(config.identity, config.evidence)
    by_id = {r.request_id: r for r in responses}
    batch_sizes = {"VALIDATING": config.lifecycle.validation_requests, "CANARY": config.lifecycle.canary_requests,
                   "EVALUATING": config.lifecycle.evaluation_requests, "HOLDING": config.lifecycle.hold_requests,
                   "PROMOTING": config.lifecycle.completion_requests, "RECOVERING": config.lifecycle.completion_requests}
    active = False
    for event in events:
        if event.event_type in {"STATE_TRANSITION", "DECISION_MADE"} and active:
            collector.close_window(event.logical_time)
            active = False
        if event.event_type == "STATE_TRANSITION":
            t = event.details
            state = t["to_observed_state"]
            if state in WINDOW_TYPES:
                collector.open_window(state, t["transition_index"], event.logical_time, batch_sizes[state])
                active = True
        elif event.event_type == "REQUEST_COMPLETED":
            if not active:
                raise ValueError("request completed outside an evidence window")
            collector.add_request(by_id[event.request_id], event.logical_time)
    if active:
        raise ValueError("run ended with unclosed evidence window")
    return collector.get_evidence()
