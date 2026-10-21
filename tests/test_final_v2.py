"""Regression checks for intervention-defined final-v2 severity."""
from __future__ import annotations

from copy import deepcopy
import json

from saferoll.campaign.final_v2 import CADENCES, DIRECTORY, preflight, validate_error_intervention
from saferoll.campaign.plan import derived_seed


def _case(severity: str, count: int, failures: int, rate: float):
    row = {"scenario": "candidate_error", "severity": severity, "seed": 123}
    config = {"experiment": {"seed": 123}, "routing": {"candidate_weight": 0.5},
              "lifecycle": {"evaluation_requests": 40},
              "phase4e": {"enabled": True, "request_faults": [{"type": "candidate_request_failure",
                  "target": "candidate", "start_request": 45, "end_request": 84,
                  "every_nth_candidate": CADENCES[severity]}]}}
    return row, config, validate_error_intervention(row, config, candidate_requests=count,
                                                     fault_events=failures, realized_error_rate=rate)


def test_final_v1_edge_rates_are_valid_under_matching_interventions():
    for severity, count, failures, rate in [
        ("mild_every_8th_candidate", 15, 1, 1/15),
        ("moderate_every_4th_candidate", 16, 4, 0.25),
        ("severe_every_2nd_candidate", 15, 7, 7/15),
    ]:
        _, _, result = _case(severity, count, failures, rate)
        assert result["valid"], result


def test_config_and_fault_count_mutations_are_rejected():
    row, config, result = _case("mild_every_8th_candidate", 15, 1, 1/15)
    assert result["valid"]
    broken = deepcopy(config)
    broken["phase4e"]["request_faults"][0]["every_nth_candidate"] = 10
    assert "ERROR_INJECTOR_MISMATCH" in validate_error_intervention(row, broken)["issues"]
    assert "ERROR_FAULT_EVENT_COUNT_MISMATCH" in validate_error_intervention(row, config,
        candidate_requests=15, fault_events=2, realized_error_rate=2/15)["issues"]


def test_final_v2_plan_is_paired_and_deterministic_if_proposed():
    if not (DIRECTORY / "campaign_plan.json").is_file():
        return
    plan = json.loads((DIRECTORY / "campaign_plan.json").read_text())
    assert len(plan["rows"]) == 650
    pairs = {}
    for row in plan["rows"]:
        assert row["seed"] == derived_seed(7300, row["scenario"], "paired", row["repetition"],
                                           {"severity": row["severity"]})
        pairs.setdefault(row["pairing_key"], []).append(row)
    assert all(len(rows) == 5 and len({r["seed"] for r in rows}) == 1 for rows in pairs.values())
    # A later scientific campaign must use a new source revision. The frozen
    # final-v2 plan remains valid, while its source preflight must detect drift.
    result = preflight(require_frozen=False, check_clean=False)
    assert set(result["issues"]) <= {"SOURCE_REVISION_MISMATCH", "SOURCE_TREE_MISMATCH"}
