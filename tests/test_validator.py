import csv
import json
import pytest
from saferoll.validator import validate_run


def rewrite_rows(run_dir, modify):
    path = run_dir / "requests.csv"
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        rows = list(reader)
    modify(rows)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader()
        writer.writerows(rows)


def test_valid_run(run_dir):
    assert validate_run(run_dir) == {"valid": True, "issues": []}


@pytest.mark.parametrize("corruption", ["missing_row", "duplicate", "epoch", "revision", "summary",
    "malformed_event", "missing_completed", "event_order", "event_epoch", "fault", "output",
    "intended", "missing_column", "manifest_hash", "event_fields", "latency"])
def test_corruption_rejected(run_dir, corruption):
    if corruption == "missing_row":
        rewrite_rows(run_dir, lambda rows: rows.pop())
    elif corruption == "duplicate":
        rewrite_rows(run_dir, lambda rows: rows[1].update(request_id=rows[0]["request_id"]))
    elif corruption in {"epoch", "revision", "fault", "output", "intended", "latency"}:
        column, value = {"epoch": ("epoch", "2"), "revision": ("served_revision", "unknown"),
                         "fault": ("fault_active", "True"), "output": ("output", "tampered"),
                         "intended": ("intended_revision", "unknown"), "latency": ("latency_ms", "nan")}[corruption]
        rewrite_rows(run_dir, lambda rows: rows[0].update({column: value}))
    elif corruption == "missing_column":
        path = run_dir / "requests.csv"
        path.write_text(path.read_text().replace("request_id,", "other_id,", 1))
    elif corruption in {"summary", "manifest_hash"}:
        path = run_dir / ("summary.json" if corruption == "summary" else "manifest.json")
        data = json.loads(path.read_text())
        data["completed_requests" if corruption == "summary" else "config_sha256"] = "wrong"
        path.write_text(json.dumps(data))
    else:
        path = run_dir / "events.jsonl"
        lines = path.read_text().splitlines()
        if corruption == "malformed_event":
            lines[2] = "{broken"
        elif corruption == "missing_completed":
            lines.pop()
        else:
            event = json.loads(lines[2])
            if corruption == "event_order":
                event["logical_time"] = 0
            elif corruption == "event_epoch":
                event["epoch"] = 2
            else:
                del event["details"]
            lines[2] = json.dumps(event)
        path.write_text("\n".join(lines) + "\n")
    result = validate_run(run_dir)
    assert not result["valid"] and result["issues"]
    assert json.loads((run_dir / "validation.json").read_text()) == result


@pytest.mark.parametrize("artifact", ["manifest.json", "config.resolved.json", "requests.csv", "events.jsonl", "summary.json"])
def test_missing_artifact(run_dir, artifact):
    (run_dir / artifact).unlink()
    assert not validate_run(run_dir)["valid"]


@pytest.mark.parametrize("artifact", ["manifest.json", "config.resolved.json", "summary.json"])
def test_malformed_json(run_dir, artifact):
    (run_dir / artifact).write_text("{bad")
    assert not validate_run(run_dir)["valid"]
