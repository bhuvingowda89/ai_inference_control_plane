#!/usr/bin/env python3
"""Write the prespecified, separate Phase 6B refinement inputs once."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
CAMPAIGN = CONFIGS / "campaigns" / "phase6b-refinement-v1.yaml"


def source(name: str) -> dict:
    return yaml.safe_load((CONFIGS / name).read_text(encoding="utf-8"))


def save(name: str, value: dict) -> str:
    path = CONFIGS / f"phase6b-{name}.yaml"
    payload = yaml.safe_dump(value, sort_keys=False)
    if path.exists() and path.read_text(encoding="utf-8") != payload:
        raise ValueError(f"existing refinement input differs: {path}")
    path.write_text(payload, encoding="utf-8")
    return f"configs/{path.name}"


def main() -> None:
    base = source("phase4f-healthy.yaml")
    base["candidate"]["fixed_latency_ms"] = 11
    base["fault"]["start_request"] = 45
    base["fault"]["end_request"] = 64
    scenarios = []

    def add(name: str, config: dict, *, sweep: dict | None = None,
            domains: list[str], truth: str, condition: str) -> None:
        config = deepcopy(config)
        config["experiment"]["name"] = f"phase6b-{name}"
        config["rollout"]["rollout_id"] = f"phase6b-{name}-rollout"
        template = save(name, config)
        scenarios.append(dict(id=name, version="1.0", template=template,
                              fault_domains=domains, ground_truth_intent=truth,
                              expected_condition=condition, backend="MOCK",
                              **({"sweep": sweep} if sweep else {})))

    attr = deepcopy(base)
    attr["phase4e"] = dict(enabled=True,
        behavior_faults=[dict(id="candidate-regression", type="classification_regression",
                              target="candidate", start_window=2, duration_windows=1,
                              every_nth_item=4)],
        observation_faults=[dict(id="replayed-attribution", type="misattribution_replay",
                                 source="behavior", target="candidate", start_window=2,
                                 duration_windows=1, source_window=0,
                                 reported_revision="candidate-A")])
    add("revision_attribution_mismatch", attr, domains=["BEHAVIOR", "OBSERVATION"],
        truth="UNSAFE", condition="current candidate regression hidden by healthy prior-window values under a false revision")

    realization = deepcopy(base)
    realization["phase4e"] = dict(enabled=True, evaluation_candidate_weight=0.5,
        routing_faults=[dict(id="partial-effective-rollout", type="partial_rollout",
                             start_state="EVALUATING", duration_requests=25, candidate_weight=0.1),
                        dict(id="stale-visible-routing", type="stale_observed_routing",
                             start_state="EVALUATING", duration_requests=25)])
    add("rollout_realization_mismatch", realization, domains=["ROUTING"],
        truth="INDETERMINATE", condition="desired 0.5, observed 0.2, effective 0.1 candidate exposure")

    control = deepcopy(source("phase4f-routing-mismatch.yaml"))
    control["candidate"]["fixed_latency_ms"] = 11
    add("observed_only_cost_control", control, domains=["ROUTING"], truth="SAFE",
        condition="observed-only mismatch with healthy candidate and realized desired exposure")

    latency = deepcopy(base)
    add("latency_severity", latency, sweep={"fault.added_latency_ms": [0, 2, 5, 9]},
        domains=["SYSTEM"], truth="INDETERMINATE",
        condition="prespecified 10, 30, 60, 100 percent relative candidate latency levels")

    behavior = deepcopy(base)
    behavior["candidate"]["behavior_profile"] = "classification_regression"
    add("behavior_severity", behavior,
        sweep={"candidate.regression_every_nth_item": [10, 5, 4]},
        domains=["BEHAVIOR"], truth="UNSAFE",
        condition="prespecified 10, 20, 25 percent classification accuracy losses")

    errors = deepcopy(base)
    errors["fault"]["type"] = "candidate_request_failure"
    add("error_threshold", errors, domains=["SYSTEM"], truth="UNSAFE",
        condition="candidate request failures exercise error-rate gate")

    structured = deepcopy(source("phase4d-structured-regression.yaml"))
    structured["candidate"]["fixed_latency_ms"] = 11
    structured["fault"]["start_request"] = 45
    structured["fault"]["end_request"] = 64
    add("structured_threshold", structured, domains=["BEHAVIOR"], truth="UNSAFE",
        condition="candidate structured-output regression exercises exact-match and schema validity")

    insufficient = deepcopy(source("phase4f-insufficient-behavior.yaml"))
    insufficient["candidate"]["fixed_latency_ms"] = 11
    add("behavior_sufficiency", insufficient, domains=["NONE"], truth="SAFE",
        condition="two candidate behavior samples with minimum three")

    spec = {"campaign": {"id": "phase6b-refinement-v1", "seed_base": 6200, "repetitions": 2},
            "scenarios": scenarios,
            "policies": ["ops_gate", "ai_gate", "saferoll"]}
    # The focused common set includes operational, strong AI, and proposed gates.
    # 13 scenario/severity cells x 3 policies x 2 repetitions = 78 runs.
    payload = yaml.safe_dump(spec, sort_keys=False)
    if CAMPAIGN.exists() and CAMPAIGN.read_text(encoding="utf-8") != payload:
        raise ValueError("existing refinement campaign differs")
    CAMPAIGN.write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
