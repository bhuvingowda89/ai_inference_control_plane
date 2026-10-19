"""Byte-stable expansion of a campaign specification into complete run configs."""
from __future__ import annotations

import csv
from dataclasses import replace
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

from ..artifacts import canonical_json, config_hash, write_json
from ..config import Config, config_from_dict, load_config
from ..research.configuration import POLICY_VERSION
from ..research.matrix import with_policy
from .spec import CAMPAIGN_SCHEMA_VERSION, CampaignSpec, Scenario, load_campaign_spec

PLAN_COLUMNS = ("campaign_id", "run_id", "scenario", "scenario_version", "policy", "policy_version",
                "repetition", "seed", "sweep", "config_path", "config_sha256", "expected_backend",
                "expected_faults", "template", "template_sha256", "threshold_signature")


def derived_seed(base: int, scenario: str, policy: str, repetition: int, sweep: dict[str, Any]) -> int:
    """SHA-256-derived 63-bit seed; stable across platforms and Python processes."""
    material = canonical_json([base, scenario, policy, repetition, sweep]).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & ((1 << 63) - 1)


def _set_path(value: dict[str, Any], path: str, replacement: Any) -> None:
    target: Any = value
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    key = parts[-1]
    if isinstance(target, list):
        target[int(key)] = replacement
    else:
        target[key] = replacement


def _faults(config: Config) -> list[str]:
    items = []
    if config.fault.type == "candidate_request_failure" or config.fault.added_latency_ms > 0:
        items.append(config.fault.type)
    phase = config.phase4e
    for name in ("routing_faults", "observation_faults", "behavior_faults", "request_faults"):
        items.extend(f"{fault.id}:{fault.type}" for fault in getattr(phase, name))
    if config.candidate.behavior_profile != "correct":
        items.append(f"candidate_profile:{config.candidate.behavior_profile}")
    return sorted(items)


def expand_campaign(spec: CampaignSpec) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return plan rows and resolved configs without touching the filesystem."""
    rows: list[dict[str, Any]] = []
    configs: dict[str, dict[str, Any]] = {}
    seeds: set[int] = set()
    for scenario in spec.scenarios:
        template = load_config(scenario.template)
        template_sha = hashlib.sha256(scenario.template.read_bytes()).hexdigest()
        dimensions = scenario.sweep
        combinations = itertools.product(*(values for _, values in dimensions)) if dimensions else [()]
        for combination in combinations:
            sweep = dict(zip((name for name, _ in dimensions), combination))
            sweep_label = "base" if not sweep else hashlib.sha256(canonical_json(sweep).encode()).hexdigest()[:10]
            for policy in spec.policies:
                for repetition in range(spec.repetitions):
                    seed = derived_seed(spec.seed_base, scenario.id, policy, repetition, sweep)
                    if seed in seeds:
                        raise ValueError("campaign seed identity collision")
                    seeds.add(seed)
                    raw = template.to_dict()
                    for key, value in sweep.items():
                        _set_path(raw, key, value)
                    raw["policy"]["type"] = policy
                    raw["experiment"].update(name=f"{spec.id}-{scenario.id}-{sweep_label}-{policy}-r{repetition}",
                                             seed=seed)
                    raw["rollout"]["rollout_id"] = f"{spec.id}-{scenario.id}-{sweep_label}-{policy}-r{repetition}"
                    lifecycle = raw["lifecycle"]
                    raw["experiment"]["request_count"] = (
                        lifecycle["validation_requests"] + lifecycle["canary_requests"]
                        + (1 + lifecycle["max_holds"]) * lifecycle["evaluation_requests"]
                        + lifecycle["max_holds"] * lifecycle["hold_requests"]
                        + lifecycle["completion_requests"])
                    base = config_from_dict(raw)
                    config = with_policy(base, policy)
                    digest = config_hash(config)
                    run_id = hashlib.sha256(canonical_json([spec.id, scenario.id, sweep,
                                                               policy, repetition, seed, digest]).encode()).hexdigest()[:24]
                    threshold_signature = hashlib.sha256(canonical_json(config.to_dict()["policy"]).encode()).hexdigest()
                    row = {"campaign_id": spec.id, "run_id": run_id, "scenario": scenario.id,
                           "scenario_version": scenario.version, "policy": policy, "policy_version": POLICY_VERSION,
                           "repetition": repetition, "seed": seed, "sweep": sweep,
                           "config_path": f"configs/{run_id}.json", "config_sha256": digest,
                           "expected_backend": scenario.backend, "expected_faults": _faults(config),
                           "template": scenario.config_path, "template_sha256": template_sha,
                           "threshold_signature": threshold_signature}
                    rows.append(row)
                    configs[run_id] = config.to_dict()
    if len({r["run_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate planned run identity")
    return rows, configs


def _csv_text(rows: list[dict[str, Any]]) -> str:
    from io import StringIO
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=PLAN_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: canonical_json(row[key]) if isinstance(row[key], (list, dict)) else row[key]
                         for key in PLAN_COLUMNS})
    return stream.getvalue()


def write_plan(spec: CampaignSpec, root: str | Path = "campaigns") -> Path:
    """Write a plan once; reject changes to an existing campaign identity."""
    directory = Path(root).resolve() / spec.id
    rows, configs = expand_campaign(spec)
    plan = {"schema_version": CAMPAIGN_SCHEMA_VERSION, "campaign_id": spec.id,
            "campaign_config_sha256": spec.config_sha256, "seed_base": spec.seed_base,
            "repetitions": spec.repetitions, "scenario_order": [s.id for s in spec.scenarios],
            "policy_order": list(spec.policies), "rows": rows}
    path = directory / "campaign_plan.json"
    content = canonical_json(plan) + "\n"
    csv_content = _csv_text(rows)
    if path.exists():
        if path.read_text(encoding="utf-8") != content or (directory / "campaign_plan.csv").read_text(encoding="utf-8") != csv_content:
            raise ValueError("existing campaign plan differs; use a new campaign ID")
        for row in rows:
            config_path = directory / row["config_path"]
            if not config_path.is_file() or config_path.read_text(encoding="utf-8") != canonical_json(configs[row["run_id"]]) + "\n":
                raise ValueError("existing campaign run config differs")
        return directory
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("campaign directory exists without a matching plan")
    (directory / "configs").mkdir(parents=True, exist_ok=True)
    (directory / "runs").mkdir(exist_ok=True)
    (directory / "aggregate").mkdir(exist_ok=True)
    (directory / "validation").mkdir(exist_ok=True)
    path.write_text(content, encoding="utf-8")
    (directory / "campaign_plan.csv").write_text(csv_content, encoding="utf-8")
    for row in rows:
        write_json(directory / row["config_path"], configs[row["run_id"]])
    return directory


def read_plan(directory: str | Path) -> dict[str, Any]:
    path = Path(directory) / "campaign_plan.json"
    return json.loads(path.read_text(encoding="utf-8"))
