# SafeRoll — Phase 4F

This repository is an independent deterministic research prototype for **SafeRoll:
State-Consistent Progressive Deployment and Automated Rollback for AI Inference
Services**. Phase 4A supplies mock inference, routing, latency injection, identity,
artifacts and validation. Phase 4B adds control-plane lifecycle semantics. Phase 4C adds revision-attributed
system evidence with strict lifecycle windows, sufficiency, freshness and independent
metric validation. Phase 4D adds deterministic ground-truth behavior probes and
independently validated behavior evidence. Phase 4E adds three-plane routing and
controller-observation fault views. Phase 4F adds controller-only rollout policies
and a separate post-hoc known-model oracle. System evidence reports operational facts; it does not decide
whether a revision is safe.

Legacy decision providers remain deterministic lifecycle scaffolding. Research
policies are explicit and threshold-configurable. There are no learned policies,
statistical campaigns, real models or plots,
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
./scripts/run_experiment.sh configs/phase4d-behavior-healthy.yaml
./scripts/run_experiment.sh configs/phase4d-classification-regression.yaml
./scripts/run_experiment.sh configs/phase4d-structured-regression.yaml
./scripts/run_experiment.sh configs/phase4d-insufficient-behavior.yaml
./scripts/run_experiment.sh configs/phase4d-system-healthy-behavior-regressed.yaml

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
schema-6.0 artifacts intentionally differ. Archived schema-1.0/2.0/3.0/4.0/5.0 run artifacts
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

## Artifacts — schema 6.0, state machine 1.0, system/behavior evidence 1.0

Each run directory contains:

| File | Contents |
| --- | --- |
| `manifest.json` | Artifact/state-machine/evidence versions, policy type, initial state, experiment/seed, rollout/epoch/revisions, request count, config/source SHA-256, Python/platform and Git provenance |
| `config.resolved.json` | Complete normalized config, including explicit lifecycle defaults for legacy YAML |
| `requests.csv` | Original Phase 4A response columns plus `desired_rollout_state`, `observed_rollout_state`, `transition_index` |
| `events.jsonl` | Original identity/ordering fields; adds `STATE_TRANSITION` and `DECISION_MADE` event types with structured details |
| `rollout_transitions.jsonl` | Every initial snapshot, desired transition and observed convergence, in append order |
| `system_evidence.jsonl` | Separate baseline/candidate operational evidence for every lifecycle request batch, including empty batches |
| `behavior_workload.json` | Deterministic parity tasks, inputs, item IDs and ground truth |
| `behavior_probes.jsonl` | Separate behavior requests/responses with complete revision, window and workload attribution |
| `behavior_evidence.jsonl` | Per-revision/window objective behavior metrics, sufficiency and freshness |
| `behavior_comparisons.jsonl` | Exact output disagreement and ground-truth metrics on paired items only |
| `routing_states.jsonl` | Explicit desired, observed and effective state/weights, with request routing references |
| `observations.jsonl` | Controller-visible evidence/routing envelopes, source references, ages and fault status |
| `fault_events.jsonl` | Ordered FAULT_ACTIVATED/FAULT_CLEARED records with distinct IDs and configured parameters |
| `policy_inputs.jsonl` | Exact closed controller capability passed to each research policy |
| `decisions.jsonl` | Deterministic decision IDs, snapshot references, enum reasons and explanations |
| `decision_evaluations.jsonl` | Separate post-hoc oracle truth, correctness category and critical-error flag |
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

Phase 4G has not been implemented.


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


## Behavior workloads and ground truth

Phase 4D measures objective correctness on two simple original tasks; these are
mechanism tests, not representative language-model benchmarks. There are no
embeddings, external APIs, semantic judgments or LLM-as-a-judge. Behavior evidence
and system evidence are independent channels. The evaluator never makes rollout
decisions, and dummy policies never read its metrics.

A workload item contains `workload_id`, UUID5 `item_id`, zero-based `item_index`,
`task_type`, `input`, `expected_output`, and task-specific `expected_label` or
`expected_structure`/`expected_values` (inapplicable fields are null). With seed S,
item i uses integer `S+i`:

* **classification:** input `{number: S+i}`; expected label `even` or `odd`.
  Labels match exactly, with no whitespace/case normalization.
* **structured_output:** same input; expected object `{number: S+i, parity: label}`.
  Valid structure requires exactly those keys, integer number (not boolean or
  float), and string parity. Exact match compares the parsed object with ground
  truth; whitespace and JSON key order do not affect correctness. Malformed JSON,
  duplicate keys, missing/extra keys and wrong types are invalid.

Workload item IDs use canonical JSON `[workload_id, seed, task_type, item_index]`
with the standard UUID5 URL namespace. Items intentionally remain identical
across rollout epochs; probe request IDs include rollout, epoch, window, role and
item ID, preventing cross-epoch evidence reuse. The workload artifact also carries
rollout/epoch, seed, task type and behavior schema version.

## Behavior configuration and profiles

Legacy configs default to `behavior_evidence.enabled: false`. Behavior artifacts
are still required: workload has an empty item list and probe/evidence/comparison
JSONL files are empty. This preserves serving assignments and the accepted logical
event sequence. Existing backend configs resolve to `behavior_profile: correct`
and `regression_every_nth_item: 4`. Earlier run artifacts require regeneration;
no artifact migration edits archived data.

Explicit behavior configuration requires all fields:

```yaml
behavior_evidence:
  enabled: true
  workload_id: deterministic-parity-v1
  task_type: classification
  item_count: 20
  baseline_items_per_window: 20
  candidate_items_per_window: 20
  minimum_samples_per_revision: 3
  minimum_paired_samples: 3
  max_age_logical_units: 1000
```

All counts are integers; workload/minimum counts are positive, per-revision limits
are in `[0,item_count]`, and maximum age is nonnegative. Enabled is a boolean;
unknown fields/types and profile/task mismatches are rejected. Both backend
sections optionally specify these fields (shown for a candidate):

```yaml
candidate:
  revision: candidate-v2
  fixed_latency_ms: 10
  response_prefix: candidate
  behavior_profile: classification_regression
  regression_every_nth_item: 4
```

Profiles are role-neutral and explicitly configured. `correct` emits ground truth.
`classification_regression` flips the expected label for every nth **one-based**
item ordinal. `structured_output_regression` alters the same deterministic subset,
cycling through omitted parity field, wrong numeric value, and malformed JSON.
The evaluator has no knowledge of profiles. Either revision may regress; identical
incorrect profiles can have zero disagreement while both accuracies are poor.
Serving outputs are unchanged by these profiles.

## Separate paired probes and lifecycle windows

At the end of each serving lifecycle batch, enabled runs probe workload items in
ascending order, baseline then candidate for each item. Each role receives the
configured prefix of the same workload. Probes bypass the serving router and the
serving-index latency/error faults. They are explicitly tagged
`request_kind: behavior_probe`, operationally successful mock attempts, and are
excluded from requests.csv, serving summaries and system metrics. In VALIDATING,
these are internal candidate checks. During PROMOTION or RECOVERY, isolated probes
may still target both revisions; they do not represent serving candidate exposure.

Each probe emits one `BEHAVIOR_PROBE_COMPLETED` event containing the persisted
request/response record. All events use the shared logical clock. System-window
closure occurs after probes and before the next transition/decision; thus probe
collection can increase the logical age of earlier system samples. It does not
change their latency or success. When behavior is disabled, no extra events occur.
Behavior windows use the same UUID, state, transition index and boundaries as
system windows, including zero-serving-request batches. VALIDATION, CANARY,
EVALUATION, HOLD, PROMOTION and RECOVERY are supported. Repeated evaluations use
separate windows and unique probe requests; no metrics pool samples across windows.

## Behavior metrics, pairing, sufficiency and freshness

Classification records expose sample/correct/incorrect counts and accuracy.
Structured records expose valid/invalid structure counts and rate, exact-match
count and rate. Inapplicable task metrics are null. Empty applicable counts are
zero, but their rates are null. Metrics are independently recomputable from raw
outputs and the persisted, independently verified ground truth.

Comparison records use the item-ID intersection **within one window**. Unpaired
items are excluded from comparison counts and comparison accuracies. Overall
per-revision evidence still includes all samples. Agreement/disagreement compares
exact raw output strings, including malformed output; it is not semantic drift.
Correctness is a separate ground-truth metric. Paired accuracy/exact-match rates
and candidate-minus-baseline differences are descriptive only. Empty pairing has
null rates, not assumed agreement.

Behavior sufficiency is `sample_count >= minimum_samples_per_revision`; paired
sufficiency uses its own minimum. Both are independent of system sufficiency and
make no health/safety assertion. Freshness uses closure minus latest probe logical
time, FRESH at or below the behavior age limit, otherwise STALE. Empty evidence has
NO_SAMPLES and null latest time/age. Closure and evaluation times coincide in this
phase; no telemetry-delay fault is added.

`behavior_evidence.jsonl` includes schema version, rollout/epoch/revision/role,
window/state/transition/workload/task identity, ordered sample request/item IDs,
window timing, sample/minimum counts, sufficiency, freshness and task metrics.
`behavior_comparisons.jsonl` includes window/rollout/epoch/revisions/workload/task,
paired item/request IDs, sample/minimum counts, paired sufficiency, agreement and
disagreement, and paired ground-truth rates/differences. Both are ordered by window;
per-revision records are baseline then candidate. Summary adds `behavior_windows`,
`behavior_samples`, and `paired_behavior_samples`.

The validator independently reconstructs ground truth from the seed/task definition,
checks probe identity/expected/observed fields and event correspondence, recomputes
classification and structured metrics through a separate implementation, checks
paired membership, then recomputes sufficiency, freshness and summary counts.
Deterministic event replay also verifies the configured backend outputs. Corruption
tests include synchronized raw probes/events and deliberately broken runtime
evaluators. Equivalent configs/seeds produce byte-identical workload, probe,
evidence and comparison artifacts, independent of Python hash randomization.

The canonical conflicting-evidence config is
`configs/phase4d-system-healthy-behavior-regressed.yaml`: baseline latency is 12 ms,
candidate latency 10 ms, both have zero serving errors, and the explicit candidate
classification profile reduces accuracy. The artifact reports this conflict;
Phase 4D does not judge safety or alter the dummy rollout decision.


## Phase 4E: three planes and controller observations

The command lifecycle remains the accepted policy-neutral state graph. A separate
`ServingPlanes` model supplies immutable three-plane snapshots:

* desired state/weights: the commanded deployment intent;
* observed state/weights: the observer's report, which may be stale or incorrect;
* effective state/weights: the routing/deployment state actually serving requests.

Each plane has independent baseline/candidate weights that sum to 1. Without
faults all planes converge. Actual serving uses the effective candidate weight
with the existing seeded RNG; changing planes never resets that RNG. Requests add
only `routing_state_index`, pointing to the full routing snapshot. Existing desired/
observed lifecycle columns retain their **command-window context** for evidence
attribution. They do not claim to measure physical state under faults: use the
routing reference for actual three-plane state. The dummy command coordinator
still drives the scripted lifecycle; future SafeRoll policies will consume the
faultable observer view. Dummy policies have not changed.

Routing snapshots are emitted as ROUTING_STATE events at each command transition
and before each request. They record all plane states/weights, rollout/epoch and
revisions, transition/request indices, active fault IDs/types, and independently
checked desired-observed, desired-effective and observed-effective match flags.
Lifecycle windows still close at the next decision/transition and retain strict
request attribution even when the physical plane differs from command intent.

## Phase 4E configuration

Existing configs default to an empty, disabled `phase4e` section. The new artifacts
are emitted for all schema-5.0 runs. Previous artifact schemas must be regenerated;
archived files are never edited. A concise runnable example is:

```yaml
phase4e:
  enabled: true
  evaluation_candidate_weight: 0.50
  routing_faults:
    - id: route-delay-1
      type: routing_propagation_delay
      target: candidate
      start_state: EVALUATING
      duration_requests: 10
```

In this example configure normal canary weight 0.20: EVALUATING requests 0.50, the
observer acknowledges 0.50, and effective routing retains 0.20 for ten serving
requests. The optional evaluation weight also applies during HOLDING. It does not
change the lifecycle graph. Fault durations count **serving attempts**, not probes.
Activation happens on the first matching state; the interval is half-open from
activation request index through `start + duration_requests`. An unmet activation
condition remains inactive. At expiry the relevant plane converges to current
intent; state changes during a delay do not erase the fault.

Every fault has a unique nonempty ID, type, target, start condition, duration/bounds
and parameters. IDs are unique across every list; `legacy-request-fault` is reserved
for the accepted single request fault. Missing optional fields resolve explicitly;
unknown fields/types, invalid durations/weights/sources and duplicate IDs fail.
Per-window fault starts must reference an existing planned window. The supported
lists and operations are:

| List/type | Semantics |
| --- | --- |
| `routing_faults`: `routing_propagation_delay` | Hold previous effective state/weight |
| `stale_observed_routing` | Hold previous observed state/weight; effective routing changes normally |
| `incorrect_observed_routing` | Report configured `candidate_weight` and optional `reported_state`; no history lookup |
| `partial_rollout` | Use configured effective fraction, at most requested exposure |
| `rollback_propagation_delay` | Hold pre-rollback effective exposure despite desired zero |
| `partial_rollback` | Retain configured residual exposure, at most pre-rollback effective exposure |
| `rollback_observation_mismatch` | Restore effective baseline routing while observer retains prior exposure |
| `observation_faults`: `observation_delay` | Deliver evidence at closure plus `delay_logical_units` |
| `observation_drop` | Explicit unavailable envelope; every `drop_every_nth_record` scoped window is dropped |
| `stale_evidence` | Use `source_window` from earlier history of the same channel/role |
| `misattribution` | Alter reported revision/epoch in a copied payload only |
| `behavior_faults` | Window-scoped classification/structured-output profile override |
| `request_faults` | Additional latency/failure intervals using the accepted generic response interface |

Recovery routing faults must start at ROLLING_BACK. Routing targets are candidate;
observation/behavior targets can be baseline or candidate. Observation source is
system or behavior. Window scopes use `start_window` plus `duration_windows`.
Behavior faults add `every_nth_item`. Request faults use inclusive request bounds
and `added_latency_ms`. Only existing behavior tasks and profiles are supported.
No metric threshold or evidence-driven decision is added.

## Ground truth versus controller views

System/behavior evidence remains ground truth. The adapter deep-copies source
records and emits a separate controller envelope with observation ID/index,
rollout/epoch, logical delivery time, source window/revision, actual and expected
current ground-truth references, observed value, availability, status, independent
view age and active fault IDs/types. References identify artifact, record index,
rollout/epoch/window/revision. Routing observations report the observed plane.

Statuses are FRESH, STALE, DELAYED, DROPPED, MISMATCHED and NO_SAMPLES. Stale views
explicitly reference an earlier same-epoch source while preserving a reference to
the current expected record. Delay changes delivery time and view age, not the
original record. Delayed deliveries can extend beyond serving-run completion;
`controller_view_at(observations, t)` filters out future deliveries. Dropped views
retain valid source metadata with null payload and `available: false`.
Misattribution envelopes intentionally carry misleading payload identity, while
their actual source references and envelope rollout/epoch remain valid. The
validator accepts configured misleading views and rejects changed ground-truth
identity. Observation-only faults do not add events to the serving clock and leave
scientific ground-truth artifacts byte-identical.

## Compound order and fault event history

All fault lists normalize to ascending ID before hashing/execution. Response
faults apply after the legacy request fault, in ID order. Routing transforms apply
in ID order, independently updating effective or observed planes; later overlapping
transforms override earlier values. Window behavior overrides apply in ID order.
Observer transforms apply in ID order; delays add, stale source selection and
misattribution compose, and drop is absorbing so later transforms cannot resurrect
an unavailable record. No order depends on dictionary insertion or registration.

`fault_events.jsonl` combines all domains, including the legacy request fault.
Activation/clearing uses explicit event types, identities, parameters and logical
points. Overlay events share the corresponding command/window clock point without
changing ground truth. Ties are ordered by `(logical_time, clear-before-activate,
fault_id)` and a contiguous fault-event index. Observation records use
`(logical_time, observation_id)` with a contiguous observation index. These are
explicit deterministic views of the same logical operations. Faults still active
at termination get a run-end clear record, which **does not assert physical
convergence** or add fictitious requests.

## Physical containment facts

Summary records `rollback_decision_time`, `rollback_requested_time` (also its
`_logical_time` alias), `effective_zero_candidate_time` (also
`effective_candidate_zero_logical_time`), global last candidate serving time,
`last_post_rollback_candidate_time`, final effective state/weight, and
`effective_contained`. Probe requests are excluded from serving/containment facts.
Effective zero is the first zero-routing snapshot after the last nonzero snapshot
in recorded post-rollback history; it therefore means containment throughout the
remaining **recorded horizon**, not a guarantee about unobserved future time.
Without convergence the zero time is null and effective_contained is false.
Nominal ROLLED_BACK command outcome can coexist with residual effective exposure;
it is not a physical-containment claim. Post-rollback requests and their routing
references preserve the raw facts for later harmful-exposure analysis.

## Phase 4E smoke campaigns and validation

```bash
./scripts/run_experiment.sh configs/phase4e-routing-delay.yaml
./scripts/run_experiment.sh configs/phase4e-stale-behavior-observation.yaml
./scripts/run_experiment.sh configs/phase4e-dropped-system-observation.yaml
./scripts/run_experiment.sh configs/phase4e-rollback-delay.yaml
./scripts/run_experiment.sh configs/phase4e-compound-behavior-stale.yaml
./scripts/run_experiment.sh configs/phase4e-compound-routing-latency.yaml
./scripts/run_experiment.sh configs/phase4e-compound-behavior-rollback-delay.yaml
python3 -m pytest -q
```

The additional seventh smoke exercises behavior regression plus rollback delay.
The stale scenarios use an earlier perfect candidate window and a later .75
accuracy window; the controller view remains the earlier healthy result. Dropped
system observations coexist with complete latency-regression ground truth. No
scenario changes the dummy decisions or claims SafeRoll effectiveness.

Validation retains all independent system/behavior metric checks. It additionally
checks plane weights/enums/identities and match flags, seeded effective assignments,
request routing references, source history and cross-epoch isolation, configured
observer transformations, deterministic fault event ordering, and independently
reconciles containment facts from raw serving requests and routing history.
Negative tests corrupt references, plane weights, observation identity/status,
fault events, containment facts and immutable ground truth. All valid observer
faults remain valid scientific runs.

The Phase 4E substrate remains available independently. Phase 4F policy semantics
are documented below; Phase 4G is not started.


## Phase 4F information boundary

**SafeRoll policies operate only on controller-visible observations; ground truth
is used only for post-hoc experimental evaluation.**

The trusted adapter projects delivered observation payloads into closed, frozen
capabilities. It passes reported identity, scoped observation IDs, metrics,
sufficiency and freshness plus controller-owned rollout identity, command-window
identity and lifecycle context. It does not pass effective routing, fault flags,
fault IDs/configuration, ground-truth references, raw labels/probes, oracle results,
future observations or expected decisions. Internal source references are used
only to route a delivered response to its controller-owned query subscription;
these references never enter a policy snapshot. Routing projection reads only the
reported observed state/weight and the controller's desired command. In particular,
it does **not** pass a MISMATCHED status derived from hidden effective routing.

| Policy | Capability and semantics |
| --- | --- |
| ReadinessGate | Minimal candidate availability/success; promotes a ready service, rolls back catastrophic readiness failure, holds if unavailable |
| OpsGate | System error/p95 comparisons with mandatory configured sufficiency/freshness; no behavior capability |
| BehaviorGate | Classification or structured quality with sufficiency/freshness; no system capability |
| AIGate | Strong combined system and behavior checks, including the same sufficiency/freshness checks; no routing capability |
| SafeRoll | AIGate monitoring plus strict reported attribution, matched behavior item populations and desired/observed state/routing checks |

All policies receive only a frozen threshold configuration, not the experiment
Config. Each requires its exact input type, so supplying SafeRoll's broader input
to AIGate or OpsGate fails. Baselines trust their role-scoped monitoring queries;
SafeRoll additionally verifies reported rollout/epoch/revision/role attribution.
The boundary prevents accidental leakage in this implementation; it is not a
sandbox for arbitrary hostile Python plugins.

## Frozen configurable comparisons

Every Phase 4F smoke config explicitly supplies the following values. Defaults
resolve identically; no run learns or tunes them. Pilot/campaign freeze will fix
these separately from this phase.

```yaml
policy:
  type: saferoll
  numeric_tolerance: 1.0e-9
  system:
    max_error_rate: 0.05
    max_latency_regression_fraction: 0.20
    min_readiness_success_rate: 0.50
  behavior:
    max_accuracy_drop: 0.05
    max_exact_match_drop: 0.05
    min_structure_valid_rate: 0.95
  evidence:
    require_system_sufficient: true
    require_behavior_sufficient: true
    require_fresh_system: true
    require_fresh_behavior: true
  consistency:
    require_observed_routing_match: true
```

Candidate error rate must be at most the configured maximum. Candidate p95 must
be at most `baseline_p95 * (1 + max_latency_regression_fraction)`; no division by
baseline latency occurs, including when baseline latency is zero. Candidate
accuracy/exact-match must be at least baseline minus allowed drop. Structure-valid
rate has its own absolute lower bound. Readiness uses its configured lower success
bound independently of QoS/behavior monitoring.

An upper-bound violation means `value > bound + numeric_tolerance`; a lower-bound
violation means `bound > value + numeric_tolerance`. Equality passes. Tolerance is
absolute in the compared quantity (ms for latency, fraction for rates). Accuracy
drop .05 passes with allowance .05; .0500001 fails at the default tolerance.
Mandatory switches control promotion sufficiency/freshness. Rollback from metrics
always requires sufficient, fresh, available evidence in the harmful channel,
even if promotion requirements were explicitly relaxed.

## Explicit SafeRoll precedence and HOLD

1. Non-EVALUATING lifecycle context abstains. Any available mandatory evidence with
   ambiguous reported rollout/epoch/revision/role attribution abstains before harm
   interpretation. Fresh reported window/lifecycle attribution must also match.
2. Assess each channel independently for missing observations, sufficiency,
   freshness, defined metrics and harmful comparisons. Behavior comparisons under
   SafeRoll also require equal item populations when both are sufficient/fresh.
3. Reliable harmful evidence yields ROLLBACK. It dominates routing/state mismatch,
   and missing/insufficient/stale evidence in the other channel. Stale apparent
   harm cannot justify rollback. System harm precedes behavior harm in explanations.
4. Without reliable harm, mandatory evidence uncertainty yields HOLD.
5. With acceptable evidence, desired/observed state mismatch or configured material
   routing mismatch yields HOLD. No hidden effective state is consulted.
6. Otherwise PROMOTE with ALL_REQUIRED_EVIDENCE_ACCEPTABLE.

AIGate uses the same monitoring reliability and harm precedence as SafeRoll,
without its state/routing or stricter attribution checks. This is a strong baseline,
not a deliberately stale-blind monitor. HOLD is abstention: preserve exposure,
serve the bounded hold cycle, reevaluate, and fail deterministically if the
accepted lifecycle hold bound is exceeded. Missing evidence is not assumed healthy.
No observation delay is bypassed by reading the underlying evidence store.

Reason codes are versioned enums: IDENTITY_UNCERTAINTY, STATE_NOT_EVALUATING,
MISSING_SYSTEM_OBSERVATION, MISSING_BEHAVIOR_OBSERVATION,
INSUFFICIENT_SYSTEM_EVIDENCE, INSUFFICIENT_BEHAVIOR_EVIDENCE,
STALE_SYSTEM_EVIDENCE, STALE_BEHAVIOR_EVIDENCE, ROLLOUT_STATE_MISMATCH,
ROUTING_STATE_MISMATCH, SYSTEM_UNAVAILABLE, SYSTEM_REGRESSION,
BEHAVIOR_REGRESSION and ALL_REQUIRED_EVIDENCE_ACCEPTABLE. Ordered reason details
accompany the codes; snapshots and resolved thresholds expose the exact comparisons.

## Live execution, bounded workloads and artifacts

Research policies are queried during the live EVALUATING phase. At its current
logical boundary, the runner closes that request batch using a temporary closure
marker with **no decision**, collects evidence, applies the observation adapter,
projects the delivered view, then makes the decision. The actual DECISION_MADE
event occupies that same boundary afterward. No future window or oracle is used.
Policy snapshots and results are persisted in the decision event and their own
artifacts. Content SHA-256 identifies snapshots; decision IDs include snapshot ID,
policy name, version and frozen-threshold SHA-256. Decisions record that threshold
digest, so changing parameters cannot alias a different decision under the same
visible snapshot. Policy version is 1.0, overall artifact schema 6.0.

For research policies `experiment.request_count` is a checked **maximum workload
budget**: validation + canary + `(max_holds+1)*evaluation` + `max_holds*hold` +
completion. Actual execution can stop early after promotion/rollback or omit the
completion batch after exhausted HOLD. Summary records actual scheduled
`planned_requests` and separate `request_budget`. Legacy dummy runs retain their
exact Phase 4A–4E request-count semantics. New research artifacts are empty for
legacy dummy runs; prior artifact schemas require regeneration.

The existing top-level `hold_count` counts entered lifecycle HOLDING cycles.
`decision_metrics.hold_count` counts all HOLD decisions, including the one that
exceeds the bound. Decision metrics also include promote/rollback/decision counts,
unsafe promotions, unnecessary rollbacks and unnecessary holds. No final campaign
statistics or publication plots are generated.

## Separate post-hoc oracle and correctness

The oracle is invoked after the episode, outside policy execution. It uses both
channels and hidden realization for **every** policy, ignoring policy explanations.
Operational criteria use current-window actual serving records; when a revision
has no serving records, the oracle explicitly labels a configured deterministic
backend counterfactual basis rather than fabricating requests. Behavior criteria
use the complete configured deterministic workload/model profile for that window,
including timed behavior faults; this remains known even if only two probes were
observed. These modeled oracle values never enter controller evidence or inputs.

UNSAFE means a known operational/behavior regression exceeds the same frozen
comparative thresholds, or an invalid serving revision appears. SAFE means these
criteria pass and effective deployment matches intent. INDETERMINATE means behavior
ground truth is undefined (e.g. behavior workload disabled), or realized deployment
is inconsistent without established harm. SAFE is a defined experiment criterion,
not a universal claim about model quality: relative classification monitoring alone
cannot detect common-mode poor baseline/candidate accuracy.

| Truth | PROMOTE | HOLD | ROLLBACK |
| --- | --- | --- | --- |
| SAFE | CORRECT_PROMOTION | UNNECESSARY_HOLD | UNNECESSARY_ROLLBACK |
| UNSAFE | UNSAFE_PROMOTION | DELAYED_ROLLBACK | CORRECT_ROLLBACK |
| INDETERMINATE | UNSUPPORTED_PROMOTION | CORRECT_HOLD | UNSUPPORTED_ROLLBACK |

Only known UNSAFE + PROMOTE is critical_error true; unsupported promotion is a
separate category. Truth is not tailored to favor SafeRoll. Healthy-but-stale
routing reports can make SafeRoll HOLD while AIGate correctly promotes. Stale
healthy evidence hiding known current harm can make SafeRoll HOLD, scored as
DELAYED_ROLLBACK. Hidden effective propagation lag with an apparently consistent
observer can make SafeRoll promote: the boundary forbids cheating, and the oracle
records UNSUPPORTED_PROMOTION. These outcomes are preserved, not optimized away.

## Policy matrices and validation

```bash
./scripts/run_experiment.sh configs/phase4f-healthy.yaml
./scripts/run_experiment.sh configs/phase4f-silent-behavior-regression.yaml --policy ops_gate
./scripts/run_policy_matrix.sh configs/phase4f-silent-behavior-regression.yaml
python3 -m saferoll.cli policy-matrix --config configs/phase4f-routing-mismatch.yaml --output-root runs
```

Policies are readiness_gate, ops_gate, behavior_gate, ai_gate and saferoll. A matrix
runs the same scenario and thresholds against each in this fixed order and writes
`policy_matrix.json`. Rows cover **every decision**, with decision index, policy,
truth, correctness, critical flag, reasons, run reference and final lifecycle outcome.
Compare decision_index 0 for initial-response smoke comparisons; later HOLD cycles
remain visible. The eight Phase 4F configs cover healthy, silent behavior regression,
latency regression, insufficient behavior, stale behavior, routing mismatch,
behavior/routing compound and dropped observation. This is smoke testing, not the
full scientific campaign.

Validation reconstructs the exact allowed snapshot projection and checks all
observation references and delivery times, boundary schemas, snapshot hashes,
identity/window attribution, policy versions, reason enums and deterministic policy
recomputation from persisted visible inputs. It separately recomputes oracle truth,
correctness classifications and metrics. Existing independent system/behavior and
three-plane validation remain intact. Tests cover exact threshold boundaries,
capability isolation, oracle exclusion during execution, future-data filtering,
byte determinism and deliberate snapshot/decision/oracle corruption.

## Phase 4G: measured CPU inference

The mock backend remains the controlled, exactly replayable experiment backend.
Phase 4G adds a local `llama-server` adapter. It uses the same rollout lifecycle,
routing, system evidence, behavior evidence, controller observations, and policy
inputs. The policy classes do not inspect the backend type. Real latency is measured
with `time.monotonic()` and marked `MEASURED`; mock latency remains `SIMULATED`.
Logical event indices still order lifecycle actions. A measured run is validated
from its persisted requests, probes, and events without rerunning model inference.
The artifact schema is 7.0; older runs require regeneration for strict validation.

The pinned model is [Qwen2.5 0.5B Instruct GGUF Q4_K_M](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/blob/main/qwen2.5-0.5b-instruct-q4_k_m.gguf),
published under Apache 2.0 by Qwen. Its small 491 MB GGUF is practical for a CPU
integration run. Required SHA-256:
`74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`.
The source URL can move, but setup refuses any bytes with a different hash. The
model file is ignored by Git. `llama.cpp` provides the local
[server and health endpoints](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

```bash
./scripts/setup_llama_cpp.sh
./scripts/setup_real_model.sh
./scripts/run_real_experiment.sh configs/phase4g-real-healthy.yaml
./scripts/run_real_experiment.sh configs/phase4g-real-behavior-regression.yaml
./scripts/run_real_experiment.sh configs/phase4g-real-classification.yaml
python3 -m pytest -q
python3 -m pytest -q -m real_backend
```

Execution never downloads or substitutes a model. Preflight checks the runtime,
file hash, and port before starting. The adapter owns one loopback server; it waits
for `/health`, sends local chat-completion requests, terminates its owned process
session, and polls for listener closure. Preflight probes for an active listener;
neither check requires immediately rebinding a recently closed TCP port. Server
output is saved in `llama-server.log`.
`preflight.json` records checks even when a run cannot start.

The original `real-workload-v1` generator makes 20 seed-derived records per task
family. A classification record states an integer's parity; a structured record
requires a JSON object with integer `number` and string `parity`. Items have stable
UUID5 IDs, expected results, and a persisted workload hash. Each lifecycle window
probes all 20 paired items per revision; the full generated dataset remains
fixed. The standard prompt requires one exact label or exactly the two JSON keys.
The candidate's optional `weakened_json` serving revision requests a short sentence
instead. The configured prompt applies to serving requests and paired probes; the
serving prompt is recorded in `REQUEST_CREATED`. This is an explicit prompt/config
revision with the same pinned model and runtime. It is an integration scenario,
not an assumed result.

Classification normalization trims whitespace, folds case, and removes simple
outer punctuation; only an entire `even` or `odd` label is accepted. Structured
normalization trims whitespace, optionally removes one whole JSON code fence, and
canonicalizes strictly parsed JSON. Duplicate keys remain invalid. Extra or wrong
keys fail structure validity; exact match requires the expected values. Every real
probe retains its raw output, normalized output, prompt, evaluation result, and
measured timing. The validator recomputes normalization, task grading, window
aggregates, paired comparisons, and operational metrics independently.

`backend_runs.jsonl` records startup/shutdown, CPU arguments, version/revision,
model file size/hash, request counts, host details, and log reference.
`real_provenance.json` records model, workload, prompt-template, source/config
hashes and runtime metadata. Unavailable model metadata is explicit `null`.
The `--n-gpu-layers 0` server argument supplies the CPU-only path. Request
timeouts are recorded as failed measured requests; model-load, server-exit,
protocol, and cleanup failures invalidate the run.

Real latency and model tokens can vary across hardware, llama.cpp versions, and
runtime scheduling even at temperature zero. No byte-identical real output claim
is made. The deterministic mock runs remain the causal fault substrate. Phase 4G
does not calibrate thresholds or make comparative research claims.

## Phase 4H: pilot campaign automation

Campaign specs in `configs/campaigns/` list versioned scenarios, template configs,
fault domains, backend type, policies, seed base, and repetitions. The default
`phase6-pilot-v1.yaml` expands to 7 scenarios × 5 policies × 3 repetitions =
105 mock runs. `phase4h-smoke.yaml` exercises 8 mock runs. The optional
`phase4h-real-pilot-optional.yaml` plans four sequential CPU real-model runs;
the default pilot never starts llama.cpp.

```bash
python3 -m saferoll.cli campaign-plan --config configs/campaigns/phase6-pilot-v1.yaml
./scripts/run_campaign.sh configs/campaigns/phase4h-smoke.yaml
./scripts/run_campaign.sh configs/campaigns/phase4h-smoke.yaml --resume
python3 -m saferoll.cli campaign-validate --campaign-dir campaigns/phase4h-smoke-v3
./scripts/propose_campaign_freeze.sh campaigns/phase4h-smoke-v3
python3 -m pytest -q
```

`campaign-plan` writes `campaign_plan.json`, `campaign_plan.csv`, and one fully
resolved config per run before execution. Expansion order is scenario, sorted
sweep combination, policy, repetition. A 63-bit seed is derived from SHA-256 of
the campaign seed base, scenario, policy, repetition, and sweep settings. The
plan and resolved configs are byte-stable; an existing campaign ID cannot be
silently replanned with changed inputs. Resumption also rejects a changed
SafeRoll Python source tree or artifact schema. Optional allowlisted severity sweeps
expand into explicit additional rows. Thresholds remain fixed within a plan.

`campaign-run` executes sequentially. Each planned identity owns a slot under
`campaigns/<id>/runs/<run-id>/`; each attempt gets its own run directory. On
`--resume`, every candidate run is validated before reuse. Incomplete or invalid
attempts remain on disk, and the next attempt receives a new suffix. Failures
are recorded in `failures.jsonl`, and the canonical `run_index.jsonl` has one
record per planned run with `VALID`, `INVALID`, `EXECUTION_FAILED`, or
`PRECHECK_FAILED` status. The executor continues after failures by default;
`--fail-fast` stops after the first. Campaign integrity remains invalid until
all planned runs validate. Real runs use the same sequential execution path,
including per-run preflight and cleanup from Phase 4G.

The campaign root contains provenance in `campaign_manifest.json`, resume
attempts in `resume_history.jsonl`, and `pilot_report.json`/`.md`. Under
`aggregate/`, `run_summary.csv` holds per-run system, behavior, rollout, and
containment facts; `decision_summary.csv` contains all decisions;
`policy_scenario_summary.csv` contains counts and rates per scenario/policy;
`fault_summary.csv` records configured faults and activity; and
`campaign_summary.json` holds coverage and descriptive repetition summaries
(mean, median, sample standard deviation, IQR, minimum, maximum). Per-run p95
values are labeled as such and are never represented as a pooled percentile.
The report displays P/H/R counts without hiding mixed decisions. Engineering
durations are provenance, not serving latency.

`campaign-validate` checks plan/config hashes, deterministic seeds, unique
identities, complete scenario/policy/repetition coverage, absence of unplanned
slots, every individual valid run, run-index agreement, failure reporting, and
byte-for-byte regeneration of aggregate tables. Invalid runs are excluded from
scientific aggregates by predeclared validator status only. The freeze proposal
command requires a valid pilot and writes
`proposed_campaign_freeze.json` with status `PROPOSED_NOT_FROZEN` for human
review. Phase 4H produces pilot-ready experiment automation. It does not freeze
thresholds or produce final scientific conclusions.
