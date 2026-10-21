#!/usr/bin/env python3
"""Prespecified simulator-only calibration; never contributes final observations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saferoll.artifacts import canonical_json, write_json
from saferoll.campaign.aggregate import descriptive
from saferoll.campaign.plan import derived_seed
from saferoll.config import config_from_dict
from saferoll.runner import simulate

OUT = ROOT / "campaigns/phase7b-error-calibration-v1"
LEVELS = {"CONTROL": 100, "MILD": 8, "MODERATE": 4, "SEVERE": 2}
SEED_BASE = 9300
REPETITIONS = 20
SOURCE = ROOT / "campaigns/final-v1/configs/d6746a5f1fdc8be975853fb1.json"


def calibrate() -> dict:
    base = json.loads(SOURCE.read_text())
    assert base["phase4e"]["request_faults"][0]["start_request"] == 45
    assert base["phase4e"]["request_faults"][0]["end_request"] == 84
    assert base["lifecycle"]["evaluation_requests"] == 40
    assert base["routing"]["candidate_weight"] == 0.5
    rows = []
    for level, cadence in LEVELS.items():
        for repetition in range(REPETITIONS):
            seed = derived_seed(SEED_BASE, "error_calibration", "simulator", repetition, {"cadence": cadence})
            raw = json.loads(canonical_json(base))
            raw["experiment"]["name"] = f"phase7b-{level.lower()}-{repetition}"
            raw["experiment"]["seed"] = seed
            raw["rollout"]["rollout_id"] = raw["experiment"]["name"]
            raw["phase4e"]["request_faults"][0]["every_nth_candidate"] = cadence
            responses, _, _ = simulate(config_from_dict(raw))
            eligible = [r for r in responses if 45 <= r.request_index <= 84 and r.backend_role == "candidate"]
            failures = [r for r in eligible if not r.success]
            expected = len(eligible) // cadence
            if len(failures) != expected:
                raise AssertionError(f"cadence mismatch: {level}, seed={seed}")
            row = {"level": level, "every_nth_candidate": cadence, "repetition": repetition,
                   "seed": seed, "eligible_candidate_requests": len(eligible),
                   "fault_events": len(failures), "realized_candidate_error_rate": len(failures) / len(eligible) if eligible else None,
                   "config_sha256": hashlib.sha256(canonical_json(raw).encode()).hexdigest(),
                   "response_trace_sha256": hashlib.sha256(canonical_json([
                       [r.request_index, r.backend_role, r.success] for r in responses]).encode()).hexdigest()}
            rows.append(row)
    summary = {}
    for level, cadence in LEVELS.items():
        subset = [r for r in rows if r["level"] == level]
        summary[level] = {"every_nth_candidate": cadence, "seed_count": len(subset),
                          "candidate_request_count": descriptive([r["eligible_candidate_requests"] for r in subset]),
                          "error_count": descriptive([r["fault_events"] for r in subset]),
                          "realized_error_rate": descriptive([r["realized_candidate_error_rate"] for r in subset])}
    result = {"campaign_id": "phase7b-error-calibration-v1", "purpose": "pre-freeze simulator calibration only",
              "seed_rule": "SHA-256 63-bit derivation from base 9300, scenario error_calibration, stream simulator, repetition 0..19, cadence setting",
              "request_window": [45, 84], "routing_candidate_weight": 0.5,
              "evaluation_requests": 40, "levels": LEVELS, "rows": rows, "summary": summary}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "calibration.json"
    if path.exists() and json.loads(path.read_text()) != result:
        raise ValueError("calibration artifact already exists with different observations")
    write_json(path, result)
    return result


if __name__ == "__main__":
    result = calibrate()
    print(json.dumps(result["summary"], sort_keys=True))
