"""Scriptable experiment and validation entrypoints."""
from __future__ import annotations
import argparse
import json
import sys
from .config import load_config
from .runner import run_experiment
from .research.configuration import POLICY_NAMES
from .research.matrix import with_policy, run_policy_matrix
from .validator import validate_run
import yaml


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="saferoll")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", required=True)
    run.add_argument("--output-root", default="runs")
    run.add_argument("--policy", choices=POLICY_NAMES)
    matrix = commands.add_parser("policy-matrix")
    matrix.add_argument("--config", required=True)
    matrix.add_argument("--output-root", default="runs")
    validate = commands.add_parser("validate")
    validate.add_argument("--run-dir", required=True)
    campaign_plan = commands.add_parser("campaign-plan")
    campaign_plan.add_argument("--config", required=True)
    campaign_plan.add_argument("--output-root", default="campaigns")
    campaign_run = commands.add_parser("campaign-run")
    campaign_run.add_argument("--config", required=True)
    campaign_run.add_argument("--output-root", default="campaigns")
    campaign_run.add_argument("--resume", action="store_true")
    campaign_run.add_argument("--fail-fast", action="store_true")
    campaign_validate = commands.add_parser("campaign-validate")
    campaign_validate.add_argument("--campaign-dir", required=True)
    freeze = commands.add_parser("campaign-freeze-proposal")
    freeze.add_argument("--campaign-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            config = load_config(args.config)
            if args.policy:
                config = with_policy(config, args.policy)
            path = run_experiment(config, args.output_root)
            print(json.dumps({"run_dir": str(path), "valid": True}, sort_keys=True))
            return 0
        if args.command == "policy-matrix":
            path = run_policy_matrix(load_config(args.config), args.output_root)
            print(json.dumps({"matrix_file": str(path), "valid": True}, sort_keys=True))
            return 0
        if args.command == "campaign-plan":
            from .campaign.spec import load_campaign_spec
            from .campaign.plan import read_plan, write_plan
            path = write_plan(load_campaign_spec(args.config), args.output_root)
            print(json.dumps({"campaign_dir": str(path), "planned_runs": len(read_plan(path)["rows"])}, sort_keys=True))
            return 0
        if args.command == "campaign-run":
            from .campaign.spec import load_campaign_spec
            from .campaign.runtime import run_campaign
            path, result = run_campaign(load_campaign_spec(args.config), args.output_root,
                                        resume=args.resume, fail_fast=args.fail_fast)
            print(json.dumps({"campaign_dir": str(path), **result}, sort_keys=True))
            return 0 if result["valid"] else 1
        if args.command == "campaign-validate":
            from .campaign.validation import validate_campaign
            result = validate_campaign(args.campaign_dir)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["valid"] else 1
        if args.command == "campaign-freeze-proposal":
            from .campaign.report import propose_freeze
            path = propose_freeze(args.campaign_dir)
            print(json.dumps({"proposal": str(path), "status": "PROPOSED_NOT_FROZEN"}, sort_keys=True))
            return 0
        result = validate_run(args.run_dir)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["valid"] else 1
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as error:
        print(f"saferoll: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
