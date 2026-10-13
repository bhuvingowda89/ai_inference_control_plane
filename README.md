# SafeRoll — Phase 4C

This repository is an independent deterministic research prototype for **SafeRoll:
State-Consistent Progressive Deployment and Automated Rollback for AI Inference
Services**. Phase 4A supplies mock inference, routing, latency injection, identity,
artifacts and validation. Phase 4B adds control-plane lifecycle semantics. Phase 4C adds revision-attributed
system evidence with strict lifecycle windows, sufficiency, freshness and independent
metric validation. System evidence reports operational facts; it does not decide
whether a revision is safe.

Current decision providers are **deterministic test scaffolding only**, not the
SafeRoll research policy or scientific baselines. There are no model-behavior
evaluators, evidence aggregation, statistical policies, real models, plots,
cloud services, databases, notebooks, containers or deployment integrations. No
research performance claims are made. Execution is sequential and CPU-only.

## Setup and commands

Python 3.9+ is supported. PyYAML is the only runtime dependency; pytest is used for
tests. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -m pytest -q

./scripts/run_experiment.sh configs/phase4a-smoke.yaml
./scripts/run_experiment.sh configs/phase4b-promote.yaml
./scripts/run_experiment.sh configs/phase4b-rollback.yaml
./scripts/run_experiment.sh configs/phase4b-hold-promote.yaml

./scripts/run_experiment.sh configs/phase4c-healthy.yaml
./scripts/run_experiment.sh configs/phase4c-latency-regression.yaml
./scripts/run_experiment.sh configs/phase4c-insufficient-evidence.yaml
./scripts/run_experiment.sh configs/phase4c-error-regression.yaml

python3 -m saferoll.cli run --config configs/phase4c-healthy.yaml --output-root runs
python3 -m saferoll.cli validate --run-dir runs/<run-id>
./scripts/validate_run.sh runs/<run-id>
```

Run prints JSON with `run_dir` and `valid` and validates before returning success.
Validate returns `{"valid": true, "issues": []}` and exit 0 for a valid artifact
set; invalid artifacts exit 1. Config/I/O errors exit 2. A valid experiment whose
lifecycle outcome is `FAILED` still exits 0: artifact validity and rollout success
are distinct; campaigns must inspect `summary.json` for the outcome. Validating an
existing directory updates `validation.json`. A nonexistent directory is invalid
and is not created. Both scripts use `set -euo pipefail` and resolve relative
paths from the repository root.

## State, decisions and outcomes

Lifecycle states are `STABLE`, `CANDIDATE_CREATED`, `VALIDATING`, `CANARY`,
`EVALUATING`, `HOLDING`, `PROMOTING`, `ROLLING_BACK`, `RECOVERING`, `COMPLETED`, and
`FAILED`. A central explicit graph rejects every unlisted edge:

```text
STABLE -> CANDIDATE_CREATED -> VALIDATING -> CANARY -> EVALUATING
EVALUATING --PROMOTE--> PROMOTING -> COMPLETED
EVALUATING --HOLD-----> HOLDING -> EVALUATING
EVALUATING --ROLLBACK-> ROLLING_BACK -> RECOVERING -> COMPLETED
any nonterminal state -> FAILED
COMPLETED and FAILED have no outgoing edges
```

`PROMOTE`, `HOLD`, and `ROLLBACK` are a separate decision enum. Decisions are
accepted only in converged `EVALUATING`. Ordinary `transition()` calls cannot
bypass decision application for the three decision edges. Final outcome is also
separate: `PROMOTED`, `ROLLED_BACK`, or `FAILED`. Successful completion is observed
only after promotion or recovery; `FAILED` never claims a successful outcome.

The coordinator holds separate **desired and observed state and candidate
weights**. Frozen snapshots and transition records preserve identity and history.
`transition(..., converge=False)` requests a desired state while leaving observed
state and routing unchanged. `observe_state(...)` records convergence separately.
An outstanding transition must converge before another transition or decision.
The runner uses immediate convergence in this phase; no stale-state fault exists.
The router follows the observed candidate weight and never resets its RNG stream.

An index-0 `INITIAL` record establishes STABLE with zero candidate traffic; it is
not a graph edge. Subsequent records represent actual transitions or explicit
observations. `transition_count` includes this initial snapshot.

## Lifecycle workload and HOLD semantics

The runner creates the candidate, enters validation, serves baseline-only
validation requests, enters canary, then evaluates. Validation is a deterministic
mock lifecycle step, not a model-behavior evaluator. CANARY, EVALUATING and HOLDING
use the configured canary weights. HOLDING is a first-class state: a hold batch
occurs (possibly empty), then a transition releases it back to EVALUATING, and the
provider is queried again after the next evaluation batch.

`max_holds` bounds the number of **entered** hold cycles. The next HOLD decision
after that bound triggers an explicit transition to FAILED with reason
`hold bound exceeded`, without entering another HOLDING state. That attempted
HOLD is persisted in `DECISION_MADE` and counted in `decision_count`; the bound
enforcement transition has null decision because it follows the failure edge.
`max_holds: 0` permits no hold cycles. This is deterministic even with zero request
counts. No infinite loop is possible with the supplied providers.

PROMOTE enters PROMOTING, sets both candidate weights to 1, serves the completion
batch, then completes as PROMOTED. ROLLBACK enters ROLLING_BACK with both candidate
weights 0, enters RECOVERING, serves the baseline-only completion batch as simulated
recovery verification, then completes as ROLLED_BACK. Recovery is assumed to
succeed in this phase; no incomplete rollback behavior is implemented.

## Configuration and migration

All Phase 4A fields remain supported. Unknown fields, missing fields, unsupported
enums and implicit numeric coercion are rejected. Backend revisions must be
nonempty and distinct. Epoch is a positive integer, seed is an integer, counts
are nonnegative integers, latencies are finite and nonnegative, and routing
weights are in [0,1] with sum within absolute tolerance 1e-9 of 1. Candidate latency
plus added fault latency must remain finite. Booleans are rejected as numbers.

Explicit lifecycle configs require both of these sections and every field:

```yaml
lifecycle:
  validation_requests: 5
  canary_requests: 20
  evaluation_requests: 10
  hold_requests: 3
  completion_requests: 5
  max_holds: 2
decision_policy:
  type: hold_then_promote
  holds_before_decision: 1
```

Policy types are `always_promote`, `always_rollback`, `hold_then_promote`, and
`always_hold`. `holds_before_decision` is nonnegative and must be 0 for all types
except `hold_then_promote`. See the complete runnable YAML files in `configs/`.

`experiment.request_count` must equal the deterministic total workload. If H is
the number of entered holds, V/C/E/B/F are validation/canary/evaluation/hold/
completion batch sizes, the total is `V + C + (H+1)*E + H*B + F`. For hold-bound
failure, omit F. H is the smaller of requested holds and max_holds; `always_hold`
always exceeds the bound. The three smoke configs therefore plan 40, 40 and 53
requests. This checked budget prevents silent truncation or overserving.

**Legacy YAML migration:** When both new sections are absent, resolution supplies
zero validation/evaluation/hold/completion requests, `canary_requests` equal to
`experiment.request_count`, `max_holds: 1`, and `always_promote` with zero holds.
This preserves every Phase 4A scientific request ID, route, output, latency and
fault assignment while adding lifecycle context. New resolved config hashes and
schema-3.0 artifacts intentionally differ. Archived schema-1.0/2.0 run artifacts
are not upgraded in place; regenerate from their YAML config to use this validator.

## Routing, identity, faults and logical ordering

Routing uses a local `random.Random(seed)` and one draw per served request in
ascending zero-based request index order. A draw strictly below the observed
candidate weight selects candidate; otherwise it selects baseline. This is
reproducible Bernoulli sampling, not an exact quota. No Python hash or global RNG
is involved. Different lifecycle schedules can intentionally change assignments.

Request IDs are UUID5 with the standard URL namespace and canonical JSON name
`[rollout_id, epoch, "request", request_index]`. Different epochs produce disjoint
identities. Mock output is `<prefix>:request-<six-digit-index>`. Latency is recorded
telemetry; the backend never sleeps.

The candidate latency fault uses a global inclusive request interval
`0 <= start_request <= end_request < request_count`, spanning lifecycle phases.
It activates before the start request even if that request is baseline-only. Only
candidate responses in the interval receive extra latency and `fault_active: true`.
Outputs are unchanged. It clears before the next request past the interval, or at
run end if no such request exists. For zero requests, only the inert interval
`[0,0]` is accepted and no fault events occur.

Events share one contiguous integer clock (`event_index == logical_time`).
Lifecycle records carry the time of their corresponding STATE_TRANSITION event;
these are two persisted views of the same operation, not separate simultaneous
operations. Decisions have their own earlier event time. Every request records
its current lifecycle transition index. Scientific artifacts contain no wall-clock
timestamps or filesystem paths.

## Artifacts — schema 3.0, state machine 1.0, system evidence 1.0

Each run directory contains:

| File | Contents |
| --- | --- |
| `manifest.json` | Artifact/state-machine/evidence versions, policy type, initial state, experiment/seed, rollout/epoch/revisions, request count, config/source SHA-256, Python/platform and Git provenance |
| `config.resolved.json` | Complete normalized config, including explicit lifecycle defaults for legacy YAML |
| `requests.csv` | Original Phase 4A response columns plus `desired_rollout_state`, `observed_rollout_state`, `transition_index` |
| `events.jsonl` | Original identity/ordering fields; adds `STATE_TRANSITION` and `DECISION_MADE` event types with structured details |
| `rollout_transitions.jsonl` | Every initial snapshot, desired transition and observed convergence, in append order |
| `system_evidence.jsonl` | Separate baseline/candidate operational evidence for every lifecycle request batch, including empty batches |
| `summary.json` | Phase 4A metrics and identity plus `final_lifecycle_state`, `final_outcome`, `decision_count`, `hold_count`, `transition_count` |
| `validation.json` | Boolean `valid` and list of diagnostic `issues` |

A lifecycle record has `transition_index`, rollout/epoch/baseline/candidate
identity, `from_desired_state`, `to_desired_state`, `from_observed_state`,
`to_observed_state`, nullable `decision`, `logical_time`, `reason`, `kind`, and
from/to desired/observed candidate weights. Kind is INITIAL, TRANSITION or
OBSERVATION. Snapshots expose `desired_state` and `observed_state` directly.

Requests retain intended and actually served revisions. Before completion,
request-specific events use null served revision. Global events carry both
backend revisions with null request-specific fields. CSV booleans are `True`/
`False`; unused backend means are null; empty-run candidate fraction is 0.

Run names use the full resolved-config SHA-256, with a numbered filesystem suffix
for equivalent reruns; existing runs are never overwritten. Stable serialization
uses sorted keys, compact separators, UTF-8 and finite JSON values. Source SHA-256
covers sorted package Python filenames and bytes. Git revision/dirty status are
diagnostic; missing Git metadata does not fail execution.

## Validation and reproducibility

Validation retains Phase 4A file, schema, hash, count, identity, revision, fault,
ordering and deterministic replay checks. It additionally reconstructs lifecycle
history through the central graph, checks initial state, transition indices,
desired/observed continuity, decision origin, hold bounds, routing convergence,
terminal outcomes and summary counts, compares lifecycle records to event records,
and checks request references and cross-epoch attribution. Scientific artifacts
are never rewritten by validation.

Replay uses the simulator; independent coordinator and graph tests verify allowed
and forbidden edges, delayed observation, failure bounds, phase traffic, and
outcome semantics. Intentional corruption tests synchronize both lifecycle files
and events where appropriate so validation cannot rely solely on their equality.

Equivalent configs/seeds in the same source/interpreter environment produce
byte-identical scientific request, event, lifecycle, evidence, summary, resolved-config and
validation files. Python/platform/Git diagnostic fields may differ. Source digest
changes identify implementation changes; hashes are not authentication signatures.

Phase 4D has not been implemented.


## System evidence and windows

System evidence describes **availability and simulated latency** for one revision
in one rollout epoch and one lifecycle request batch. It has no healthy/unhealthy
label, no behavior-quality metric and no decision-policy threshold. The dummy
providers do not consume evidence; even operational regressions can produce a
PROMOTED outcome under the always-promote test provider.

A collector opens a window at the lifecycle-entry event and closes it at the next
state transition, or the decision event for EVALUATING. Closure requires exactly
the configured lifecycle batch request count. Request completion times are
strictly inside the window's logical interval. No window spans multiple lifecycle
states or transitions; repeated evaluations after HOLD receive different windows.
Empty batches still produce evidence. Window types map as follows:

| Observed lifecycle state | Window type |
| --- | --- |
| VALIDATING | VALIDATION |
| CANARY | CANARY |
| EVALUATING | EVALUATION |
| HOLDING | HOLD |
| PROMOTING | PROMOTION |
| RECOVERING | RECOVERY |

A window ID is UUID5 over canonical JSON
`[rollout_id, epoch, "system-window", transition_index, window_sequence]`, using
the standard URL namespace. Window sequence counts all lifecycle batches from 0.
Every window emits baseline then candidate evidence in deterministic order, with
separate revision IDs, sample lists, metrics and sufficiency. Sample IDs link each
record to requests.csv; a request contributes exactly once. No comparison artifact
is added: downstream work can compare the revision-attributed raw evidence.

New configuration is optional for legacy YAML and fully resolved in artifacts:

```yaml
evidence:
  minimum_samples_per_revision: 3
  max_age_logical_units: 10
```

These are the defaults when the section is absent. An explicit section must contain
both fields with no unknown fields. The minimum is a positive integer, maximum age
is a nonnegative integer, and booleans are rejected. Percentiles are fixed at 50
and 95; there are no configurable policy thresholds.

`sample_count` and `request_count` count all completed attempts, including failures.
Success/failure counts reconcile to that count; rates divide by count. Latencies
include **both successful and failed attempts**. Metrics are arithmetic mean,
minimum, maximum, p50 and p95. Percentiles sort n latencies ascending and linearly
interpolate at zero-based position `(n-1)*p/100`. For `[0,10,20,30]`, p50 is 15 and
p95 is 28.5. A singleton returns its latency. Empty samples have null rates and
latency metrics, not fabricated zeroes.

`sufficient` means only `sample_count >= minimum_required_samples` for that
revision. It does not assert health or safety. Zero or scarce candidate samples
are explicitly insufficient, regardless of baseline traffic. Sufficiency and
freshness are independent properties.

Normal collection evaluates immediately at closure. `closed_at_logical_time` and
`evaluated_at_logical_time` equal the closing boundary; `latest_sample_logical_time`
is the latest completed request for that revision. `age_at_evaluation` is evaluation
time minus latest sample time. FRESH means age is at most the configured maximum;
otherwise it is STALE. Empty samples have null latest time and age and status
NO_SAMPLES. Sparse revision traffic can produce older evidence even without a
telemetry-delay fault. Smoke configs use a 1000-unit age limit. The collector can
return new immutable freshness views at later evaluation times; no delay fault is
implemented, and this phase's run validator expects the normal closure-time view.

Evidence records contain `schema_version`, window ID/type/sequence, rollout/epoch,
revision/backend role, lifecycle state/transition index, start/end/closure/evaluation
logical times, planned window count, ordered `sample_request_ids`, sample/minimum
counts, sufficiency, latest sample time, age, freshness, request/success/failure
counts and rates, and latency mean/min/max/p50/p95. Undefined numeric fields are
JSON null.

The validator independently reads raw CSV records and completion-event times to
recompute sample membership, counts, rates, latencies and percentiles. It does not
call the collector's aggregation or percentile implementation. Floating metrics
use absolute/relative tolerance 1e-12 for independent recomputation; identity,
counts, flags, ordering and null semantics are exact. It rejects unknown/duplicate
sample IDs, wrong revisions/roles, cross-epoch samples, mixed lifecycle windows,
missing evidence, incorrect sufficiency/freshness, and altered metrics.

**No throughput is reported.** Logical event ordering and configured simulated
latency are not physical execution time and cannot establish real requests/sec.

## Minimal candidate request failure fault

The existing latency fault is unchanged. One additional deterministic fault uses
the same interval activation/clearing protocol:

```yaml
fault:
  type: candidate_request_failure
  target: candidate
  start_request: 5
  end_request: 44
  added_latency_ms: 0
```

Only candidate attempts in the inclusive interval become `success: false` and
`fault_active: true`. Output and simulated latency remain unchanged. The zero
latency field is required for this type to avoid ambiguous combined faults. Only
one configured fault is supported per run. Failure rates emerge from request
records; the collector contains no fault-specific aggregation path.
