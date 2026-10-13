"""Scriptable experiment and validation entrypoints."""
from __future__ import annotations
import argparse
import json
import sys
from .config import load_config
from .runner import run_experiment
from .validator import validate_run
import yaml


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="saferoll")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", required=True)
    run.add_argument("--output-root", default="runs")
    validate = commands.add_parser("validate")
    validate.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            path = run_experiment(load_config(args.config), args.output_root)
            print(json.dumps({"run_dir": str(path), "valid": True}, sort_keys=True))
            return 0
        result = validate_run(args.run_dir)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["valid"] else 1
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"saferoll: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
