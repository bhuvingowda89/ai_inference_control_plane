# SafeRoll — Phase 4A

This repository implements a deterministic experimental substrate for **SafeRoll:
State-Consistent Progressive Deployment and Automated Rollback for AI Inference
Services**. It contains configuration validation, immutable rollout identities,
mock inference, weighted routing, one latency fault, logical events, structured
artifacts, validation, and a CLI. It makes no research claims.

Behavior-quality evaluation, deployment decision policies, HOLD/PROMOTE/ROLLBACK
logic, real LLM inference, statistical analysis, and plotting are intentionally
outside Phase 4A. Execution is sequential, CPU-only, and notebook-free. No cloud,
external database, containers, or ML framework is required.

## Setup

Python 3.9 or later is supported. PyYAML is the only runtime dependency; pytest is
the test dependency. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
```

## Run and validate

```bash
./scripts/run_experiment.sh configs/phase4a-smoke.yaml
# Equivalent, with an optional output directory:
python3 -m saferoll.cli run --config configs/phase4a-smoke.yaml --output-root runs
python3 -m saferoll.cli validate --run-dir runs/<run-id>
./scripts/validate_run.sh runs/<run-id>
python3 -m pytest -q
```

The run command prints a JSON object containing `run_dir` and `valid`, writes all
six artifacts, and validates the run before returning success. Validation prints
`{"valid": true, "issues": []}` for a valid run and exits 1 for an invalid run.
Configuration or I/O errors exit 2. Validation of an existing directory always
updates `validation.json`; a nonexistent directory returns invalid without
creating it. Scripts use `set -euo pipefail` and resolve relative paths from the
repository root.

## Configuration

The complete example is [configs/phase4a-smoke.yaml](configs/phase4a-smoke.yaml).
All sections and fields are required; unknown fields are rejected.

| Section | Fields and constraints |
| --- | --- |
| `experiment` | Nonempty `name`, integer `seed`, nonnegative integer `request_count` |
| `rollout` | Nonempty `rollout_id`, positive integer `epoch` |
| `baseline`, `candidate` | Distinct nonempty `revision`, finite nonnegative `fixed_latency_ms`, nonempty `response_prefix` |
| `routing` | `baseline_weight`, `candidate_weight` in [0,1], sum within absolute tolerance 1e-9 of 1 |
| `fault` | `type: latency`, `target: candidate`, integer `start_request` and `end_request`, finite nonnegative `added_latency_ms` |

Request indices are **zero-based**. Fault bounds are inclusive and must satisfy
`0 <= start_request <= end_request < request_count`. For an empty run, the single
accepted inert interval is `[0,0]`; no requests or fault transitions are emitted.
Boolean values are rejected for numeric configuration fields. Candidate latency
plus added fault latency must remain finite. Numeric latency and weight fields
are normalized to floats in the resolved config.

## Execution and determinism

Routing owns a local `random.Random(seed)` instance. In ascending request index
order, it takes one `random()` draw per request and selects candidate if the draw
is strictly below `candidate_weight`; otherwise it selects baseline. Both weights
are validated; candidate weight determines the threshold. This is reproducible
Bernoulli sampling, not an exact quota. No Python hash or global RNG is involved.

Request IDs use UUID5 with the standard URL namespace and the canonical JSON name
`[rollout_id, epoch, "request", request_index]`. Epoch changes therefore change
every request ID. Frozen dataclasses carry request/response/state identity. A
created or routed request has `served_revision: null` until completion; the served
revision is present in every completed response and CSV row. Global lifecycle and
fault events have null request-specific identity and explicitly carry both
backend revisions, rollout ID, and epoch.

Backend outputs are `<response_prefix>:request-<six-digit-index>`. Latency is
simulated telemetry; backends never sleep. The fault activates before the start
request, including when that request routes to baseline. Only candidate responses
within the interval receive added latency and `fault_active: true`; outputs remain
unchanged. It clears before request `end_request + 1`, or immediately after the
last response when its interval reaches run end. Activation and clearing occur
even if no candidate request falls in the interval.

Events are append-only, ordered by zero-based `event_index` and identical integer
`logical_time`. Request creation, routing, and completion are emitted in order.
Scientific artifacts contain no wall-clock timestamps or filesystem paths.

The run directory is `runs/run-<full-resolved-config-SHA256>`, with a numbered
filesystem suffix for equivalent reruns. Existing runs are never overwritten.
Equivalent runs in the same software environment have byte-identical config,
CSV, JSONL, summary, and validation files. Python/platform/Git provenance can vary
across environments; reproducing the source and interpreter versions is advised
for automated campaigns. The source digest deliberately identifies package Python
sources, not every file in the repository.

## Artifacts (schema version 1.0)

| Artifact | Contents |
| --- | --- |
| `manifest.json` | Schema version, experiment name, seed, rollout/epoch/revisions, request count, canonical config SHA-256, package source SHA-256, Python/platform provenance, optional Git revision and dirty status |
| `config.resolved.json` | Complete validated configuration serialized as stable UTF-8 JSON |
| `requests.csv` | One completed response per request: `request_index`, `request_id`, `rollout_id`, `epoch`, `intended_revision`, `served_revision`, `backend_role`, `success`, `latency_ms`, `fault_active`, `output` |
| `events.jsonl` | `event_index`, `event_type`, `rollout_id`, `epoch`, `baseline_revision`, `candidate_revision`, `request_id`, `intended_revision`, `served_revision`, `logical_time`, `details` |
| `summary.json` | Rollout/epoch/revisions, planned/completed/successful/failed counts, baseline/candidate counts, actual candidate fraction, affected request count, mean latency per backend |
| `validation.json` | Boolean `valid` and list of diagnostic `issues` |

CSV booleans serialize as `True`/`False`. A backend mean is null if no request used
that backend; an empty run has candidate fraction 0. Config hashing uses sorted
keys, compact separators, UTF-8, and no nonfinite JSON values. Source hashing uses
sorted relative filenames and source bytes. If Git metadata is unavailable, Git
fields are null and `git_metadata_status` explains why; execution continues.

The validator checks required artifacts and schemas, config hash and manifest
identity, counts, unique IDs, epoch attribution, served revision membership,
summary metrics, configured fault attribution, lifecycle boundaries, and logical
ordering. It also replays the resolved configuration and compares all request and
event records and summary fields, detecting altered assignments, missing records,
outputs, and fault transitions. It never rewrites scientific artifacts. Replay
uses the same simulator, so unit tests also independently verify routing, fault
boundaries, and accounting. Digests are reproducibility metadata, not signatures.

The tests include intentional corruption, repeat runs, changes to
`PYTHONHASHSEED`, cross-epoch contamination, CLI success and failure, and empty-run
handling. Phase 4B has not been implemented.
