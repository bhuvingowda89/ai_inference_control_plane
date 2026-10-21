#!/usr/bin/env python3
"""Prespecified five by two CPU calibration; never contributes final observations."""
from __future__ import annotations

from dataclasses import replace
import csv
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from saferoll.artifacts import canonical_json
from saferoll.config import load_config
from saferoll.runner import run_experiment
from saferoll.validator import validate_run

OUT = ROOT / "campaigns/final-v1/real_timing_pilot"
CONFIGS = ("phase4g-real-healthy.yaml", "phase4g-real-behavior-regression.yaml")


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = OUT / "pilot_results.json"
    previous = json.loads(index.read_text()) if index.exists() else {"schema_version": "1.0", "calibration_only": True, "runs": []}
    records = previous["runs"]
    for name in CONFIGS:
        condition = "healthy" if "healthy" in name else "structured_regression"
        base = load_config(ROOT / "configs" / name)
        for repetition in range(5):
            if any(r["condition"] == condition and r["repetition"] == repetition for r in records):
                continue
            config = replace(base, experiment=replace(base.experiment, name=f"phase7-real-pilot-{condition}-{repetition}", seed=7400 + repetition),
                             rollout=replace(base.rollout, rollout_id=f"phase7-real-pilot-{condition}-{repetition}"))
            started = time.monotonic()
            run = run_experiment(config, OUT / condition / f"rep-{repetition}")
            validity = validate_run(run)
            metrics = json.loads((run / "metrics.json").read_text()) if (run / "metrics.json").exists() else {}
            decisions = lines(run / "decisions.jsonl")
            backend = json.loads((run / "real_provenance.json").read_text())
            row = {"condition": condition, "repetition": repetition, "seed": config.experiment.seed,
                   "run_dir": run.relative_to(ROOT).as_posix(), "duration_seconds": time.monotonic()-started,
                   "validity": validity, "decision": decisions[0]["decision"] if decisions else None,
                   "metrics": metrics, "backend": backend}
            records.append(row)
            index.write_text(canonical_json(previous)+"\n")
            print(canonical_json({"condition": condition, "repetition": repetition, "valid": validity["valid"], "duration_seconds": row["duration_seconds"]}), flush=True)
    index.write_text(canonical_json(previous)+"\n")


if __name__ == "__main__":
    main()
