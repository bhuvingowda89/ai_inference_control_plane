"""Final-v2 intervention integrity and freeze preflight."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .final_freeze import canonical_freeze_hash, preflight as common_preflight, file_hash

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "campaigns/final-v2"
CADENCES = {"control_every_100th_nontriggering": 100,
            "mild_every_8th_candidate": 8,
            "moderate_every_4th_candidate": 4,
            "severe_every_2nd_candidate": 2}


def validate_error_intervention(row: dict[str, Any], config: dict[str, Any],
                                *, candidate_requests: int | None = None,
                                fault_events: int | None = None,
                                realized_error_rate: float | None = None) -> dict[str, Any]:
    """The observed rate is recorded, never compared with a pilot range."""
    issues: list[str] = []
    if row.get("scenario") != "candidate_error" or row.get("severity") not in CADENCES:
        issues.append("UNRECOGNIZED_ERROR_CONDITION")
        return {"valid": False, "issues": issues, "realized_error_rate": realized_error_rate}
    expected = CADENCES[row["severity"]]
    faults = config.get("phase4e", {}).get("request_faults", [])
    fault = faults[0] if len(faults) == 1 else {}
    if (not config.get("phase4e", {}).get("enabled") or len(faults) != 1 or
        fault.get("type") != "candidate_request_failure" or fault.get("target") != "candidate" or
        fault.get("every_nth_candidate") != expected or
        fault.get("start_request") != 45 or fault.get("end_request") != 84):
        issues.append("ERROR_INJECTOR_MISMATCH")
    if (config.get("experiment", {}).get("seed") != row.get("seed") or
        config.get("routing", {}).get("candidate_weight") != 0.5 or
        config.get("lifecycle", {}).get("evaluation_requests") != 40):
        issues.append("ERROR_SEED_ROUTING_OR_BUDGET_MISMATCH")
    if candidate_requests is not None:
        if candidate_requests < 0 or candidate_requests > 40:
            issues.append("ERROR_ELIGIBLE_POPULATION_INVALID")
        expected_events = candidate_requests // expected
        if fault_events is None or fault_events != expected_events:
            issues.append("ERROR_FAULT_EVENT_COUNT_MISMATCH")
        if candidate_requests and realized_error_rate is not None and fault_events is not None:
            if abs(realized_error_rate - fault_events / candidate_requests) > 1e-12:
                issues.append("ERROR_RATE_ARITHMETIC_MISMATCH")
    return {"valid": not issues, "issues": issues, "configured_every_nth_candidate": expected,
            "eligible_candidate_requests": candidate_requests, "fault_events": fault_events,
            "realized_error_rate": realized_error_rate}


def preflight(directory: Path = DIRECTORY, *, require_frozen: bool = False,
              check_clean: bool = True) -> dict[str, Any]:
    result = common_preflight(directory, require_frozen=require_frozen, check_clean=check_clean)
    issues = set(result["issues"])
    freeze = json.loads((directory / "campaign_freeze.json").read_text())
    plan = json.loads((directory / "campaign_plan.json").read_text())
    if freeze.get("campaign_id") != "final-v2" or plan.get("campaign_id") != "final-v2":
        issues.add("CAMPAIGN_VERSION_MISMATCH")
    if freeze.get("error_severity_rule") != "Intervention cadence and eligibility define severity; realized rate is measured, never an inclusion gate.":
        issues.add("ERROR_SEVERITY_RULE_MISMATCH")
    calibration = ROOT / "campaigns/phase7b-error-calibration-v1/calibration.json"
    if not calibration.is_file() or file_hash(calibration) != freeze.get("error_calibration_sha256"):
        issues.add("ERROR_CALIBRATION_MISMATCH")
    for row in plan["rows"]:
        if row["scenario"] == "candidate_error":
            config = json.loads((directory / row["config_path"]).read_text())
            issues.update(validate_error_intervention(row, config)["issues"])
    result["issues"] = sorted(issues)
    result["valid"] = not issues
    return result
