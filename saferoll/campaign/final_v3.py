"""Final-v3 frozen preflight with runtime-path historical-source parity."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import config_from_dict
from ..observations import historical_source
from .final_freeze import preflight as common_preflight, file_hash
from .final_v2 import validate_error_intervention

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "campaigns/final-v3"


def preflight(directory: Path = DIRECTORY, *, require_frozen: bool = False,
              check_clean: bool = True) -> dict[str, Any]:
    result = common_preflight(directory, require_frozen=require_frozen, check_clean=check_clean)
    issues = set(result["issues"])
    freeze = json.loads((directory / "campaign_freeze.json").read_text())
    plan = json.loads((directory / "campaign_plan.json").read_text())
    history = json.loads((directory / "history_index.json").read_text())
    if freeze.get("campaign_id") != "final-v3" or plan.get("campaign_id") != "final-v3":
        issues.add("CAMPAIGN_VERSION_MISMATCH")
    if history.get("campaign_id") != "final-v3" or history.get("schema_version") != "2.0":
        issues.add("HISTORY_INDEX_CONTRACT_VERSION_MISMATCH")
    if freeze.get("history_contract_version") != "2.0":
        issues.add("HISTORY_CONTRACT_MISMATCH")
    calibration = ROOT / "campaigns/phase7b-error-calibration-v1/calibration.json"
    if not calibration.is_file() or file_hash(calibration) != freeze.get("error_calibration_sha256"):
        issues.add("ERROR_CALIBRATION_MISMATCH")
    checked_history_seeds: set[int] = set()
    for row in plan["rows"]:
        raw = json.loads((directory / row["config_path"]).read_text())
        if row["scenario"] == "candidate_error":
            issues.update(validate_error_intervention(row, raw)["issues"])
        if row["scenario"] == "historical_attribution":
            try:
                config = config_from_dict(raw)
                faults = [f for f in config.phase4e.observation_faults if f.type == "historical_evidence_replay"]
                if len(faults) != 1:
                    raise ValueError("historical replay fault must be unique")
                record, _, source_dir, digest = historical_source(config, faults[0])
                entry = history["entries"][str(row["seed"])]
                if (source_dir != entry["run_dir"] or digest != entry["behavior_evidence_sha256"]
                        or record["epoch"] != entry["source_epoch"]
                        or record["revision"] != entry["source_revision"]
                        or record["rollout_id"] != entry["rollout_id"]):
                    raise ValueError("runtime source differs from frozen history index")
                checked_history_seeds.add(row["seed"])
            except (ValueError, KeyError, OSError) as error:
                issues.add(f"HISTORY_RUNTIME_CONTRACT_MISMATCH:{type(error).__name__}:{error}")
    if len(checked_history_seeds) != len(history.get("entries", {})):
        issues.add("HISTORY_SEED_COVERAGE_MISMATCH")
    result["issues"] = sorted(issues)
    result["valid"] = not issues
    result["historical_runtime_seeds_checked"] = len(checked_history_seeds)
    return result
