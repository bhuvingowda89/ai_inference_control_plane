"""Strict campaign specification and scenario metadata."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from ..config import load_config
from ..research.configuration import POLICY_NAMES

CAMPAIGN_SCHEMA_VERSION = "1.0"
_ID = re.compile(r"[a-z][a-z0-9_-]*\Z")
_DOMAINS = {"NONE", "SYSTEM", "BEHAVIOR", "ROUTING", "OBSERVATION", "RECOVERY"}


@dataclass(frozen=True)
class Scenario:
    id: str
    version: str
    template: Path
    config_path: str
    fault_domains: tuple[str, ...]
    ground_truth_intent: str
    expected_condition: str
    backend: str
    sweep: tuple[tuple[str, tuple[int | float, ...]], ...]


@dataclass(frozen=True)
class CampaignSpec:
    id: str
    seed_base: int
    repetitions: int
    scenarios: tuple[Scenario, ...]
    policies: tuple[str, ...]
    config_path: Path
    config_sha256: str


def _keys(value: Any, allowed: set[str], required: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= set(value) or set(value) - allowed:
        raise ValueError(f"{context}: missing or unknown fields")
    return value


def load_campaign_spec(path: str | Path) -> CampaignSpec:
    """Resolve templates relative to the repository containing configs/campaigns."""
    import hashlib

    source = Path(path).resolve()
    raw_bytes = source.read_bytes()
    raw = _keys(yaml.safe_load(raw_bytes), {"campaign", "scenarios", "policies"},
                {"campaign", "scenarios", "policies"}, "campaign spec")
    campaign = _keys(raw["campaign"], {"id", "seed_base", "repetitions"},
                     {"id", "seed_base", "repetitions"}, "campaign")
    identifier, seed_base, repetitions = (campaign[k] for k in ("id", "seed_base", "repetitions"))
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError("campaign.id must be a safe lowercase identifier")
    if type(seed_base) is not int or seed_base < 0 or type(repetitions) is not int or repetitions < 1:
        raise ValueError("campaign seed_base must be nonnegative and repetitions positive")
    policies = raw["policies"]
    if not isinstance(policies, list) or not policies or any(p not in POLICY_NAMES for p in policies) or len(set(policies)) != len(policies):
        raise ValueError("policies must be unique supported research policies")
    items = raw["scenarios"]
    if not isinstance(items, list) or not items:
        raise ValueError("scenarios must be a nonempty list")
    scenarios = []
    repository = source.parent.parent.parent
    for item in items:
        value = _keys(item, {"id", "version", "template", "fault_domains", "ground_truth_intent",
                             "expected_condition", "backend", "sweep"},
                      {"id", "version", "template", "fault_domains", "ground_truth_intent",
                       "expected_condition", "backend"}, "scenario")
        sid = value["id"]
        if not isinstance(sid, str) or not _ID.fullmatch(sid) or sid in {s.id for s in scenarios}:
            raise ValueError("scenario IDs must be unique safe identifiers")
        version = value["version"]
        if not isinstance(version, str) or not version.strip():
            raise ValueError("scenario version is required")
        config_path = value["template"]
        if not isinstance(config_path, str) or Path(config_path).is_absolute() or ".." in Path(config_path).parts:
            raise ValueError("scenario template must be a repository-relative path")
        template = (repository / config_path).resolve()
        if not template.is_relative_to(repository) or not template.is_file():
            raise ValueError(f"scenario template missing or outside repository: {config_path}")
        resolved = load_config(template)
        backend = value["backend"]
        if backend not in {"MOCK", "REAL_CPU"} or (resolved.baseline.backend_type == "mock") != (backend == "MOCK"):
            raise ValueError(f"scenario {sid}: backend disagrees with template")
        domains = value["fault_domains"]
        if not isinstance(domains, list) or not domains or len(set(domains)) != len(domains) or set(domains) - _DOMAINS:
            raise ValueError(f"scenario {sid}: invalid fault domains")
        intent = value["ground_truth_intent"]
        if intent not in {"SAFE", "UNSAFE", "INDETERMINATE"}:
            raise ValueError(f"scenario {sid}: invalid ground-truth intent")
        condition = value["expected_condition"]
        if not isinstance(condition, str) or not condition.strip():
            raise ValueError(f"scenario {sid}: expected condition is required")
        sweep_raw = value.get("sweep", {})
        if not isinstance(sweep_raw, dict):
            raise ValueError("sweep must map an allowlisted field to numeric values")
        allowed_sweeps = {"fault.added_latency_ms", "candidate.regression_every_nth_item",
                          "phase4e.observation_faults.0.duration_windows",
                          "phase4e.routing_faults.0.duration_requests"}
        sweep = []
        for key in sorted(sweep_raw):
            values = sweep_raw[key]
            if key not in allowed_sweeps or not isinstance(values, list) or not values or any(
                    type(x) not in (int, float) or x < 0 for x in values) or len(set(values)) != len(values):
                raise ValueError(f"scenario {sid}: invalid sweep {key}")
            sweep.append((key, tuple(values)))
        scenarios.append(Scenario(sid, version, template, config_path, tuple(domains), intent,
                                  condition, backend, tuple(sweep)))
    return CampaignSpec(identifier, seed_base, repetitions, tuple(scenarios), tuple(policies),
                        source, hashlib.sha256(raw_bytes).hexdigest())
