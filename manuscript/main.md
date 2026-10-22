# SafeRoll: State-Consistent Progressive Deployment and Automated Rollback for AI Inference Services

**Target journal:** Future Generation Computer Systems (FGCS)  
**Manuscript type:** Research article  
**Author and affiliation metadata:** to be supplied by the authors before submission

## Abstract

Progressive deployment limits the exposure of a new service revision while a controller decides whether to promote or roll it back. For artificial intelligence (AI) inference services, readiness, latency, and error rates can remain acceptable while model behavior degrades. Even behavior measurements can mislead a controller when they are stale or belong to a different revision or rollout epoch. We present SafeRoll, a control-plane policy that combines system and behavior evidence with freshness, revision attribution, and consistency between desired, observed, and effective serving state. SafeRoll makes PROMOTE, HOLD, and ROLLBACK first decisions; HOLD explicitly represents uncertainty and has measurable costs. We evaluate five frozen policies in RolloutBench across 650 validated mock runs and separately conduct six pinned real-CPU inference runs. In 91 unsafe mock evaluations per policy, SafeRoll made no unsafe promotions, whereas the strong system-and-behavior AIGate baseline made 10. In the paired historical-attribution condition, SafeRoll avoided all 10 immediate unsafe AIGate promotions, but its first HOLDs were classified as delayed rollback rather than correct immediate rollback. SafeRoll's safe promotions fell from AIGate's 26/39 to 16/39, and its request-normalized harmful exposure was slightly higher (0.40522 versus 0.40114). Recovery runs showed 53 mock logical units between rollback request and physical containment. The real inference block demonstrated structured-output failure despite faster, error-free serving. The results identify evidence attribution and physical containment as distinct control-plane concerns while quantifying the conservatism they introduce.

**Keywords:** AI inference services; MLOps; progressive deployment; model serving; state consistency; rollback

## Highlights

- Cross-epoch evidence checks avoided 10 unsafe AIGate promotions.
- SafeRoll made 0/91 unsafe promotions, versus 10/91 for AIGate.
- SafeRoll's safe promotions were 16/39, versus 26/39 for AIGate.
- Physical containment followed rollback request by 53 logical units.
- Faster real inference produced invalid structured output in 3/3 runs.

## 1. Introduction

AI inference has become a continuously operated service rather than a one-time model artifact. Operators change model weights, quantization, prompts, runtime binaries, batching, and traffic routing while users continue to send requests. The serving stack must therefore keep a stable baseline available, direct limited traffic to a candidate, and decide whether exposure should increase. Research on serving systems has made latency and throughput central engineering objectives [@clipper2017; @inferline2019; @vllm2023]. MLOps adds lifecycle management, versioning, monitoring, and automation [@kreuzberger2023; @fgcsMLOps2026]. The decision to let a particular revision serve more traffic is an operational control problem at the intersection of these concerns.

Progressive delivery systems expose candidates gradually and evaluate measurements before promotion or rollback. Canary analysis is supported by general deployment controllers and by model-serving infrastructure [@argoAnalysis; @kayenta2018; @kserveCanary]. Such systems provide useful mechanisms for traffic shifts, metric checks, and recovery. Inference services, however, can satisfy readiness and service-level indicators while giving wrong classifications or malformed structured output. A serving revision can also be faster precisely because it has stopped producing the required content. A rollout controller that treats transport success as model success can therefore promote a harmful revision.

Adding model-quality evidence addresses one failure mode but introduces an attribution problem. A valid accuracy observation can describe an earlier candidate, another serving epoch, or requests routed through a different effective state than the controller assumes. If the controller sees a healthy number without its revision and epoch, it may use that number to justify the active candidate. Missing and stale observations also require explicit handling: lack of evidence is not evidence of safety. These situations are especially likely during progressive deployment, when the desired traffic split, observed routing configuration, and requests actually served need not coincide at every instant.

SafeRoll is a state-consistent rollout policy for this decision boundary. It composes system and behavior signals with freshness, sufficiency, revision and epoch identity, and rollout-state consistency. Its output is PROMOTE, HOLD, or ROLLBACK. HOLD is a first-class decision when the controller-visible evidence cannot justify either commitment. SafeRoll does not read experimental ground truth; the oracle exists only for evaluation. Figure 1 summarizes the control-plane architecture and separates desired, observed, and effective serving state.

We evaluate SafeRoll against four prespecified policies that isolate readiness, operational evidence, behavioral evidence, and their combination. AIGate is the strongest non-state-aware baseline: it already checks system and behavior evidence, freshness, and sufficiency. The frozen evaluation contains 13 scenarios, 20 condition or severity cells, 650 validated mock policy runs, and six separate real-CPU runs. Paired comparisons share scenario, severity, and seed. Interventions were frozen before the final-v3 execution; realized error rates are measured results rather than post hoc severity labels.

The principal finding is deliberately two-sided. SafeRoll made no unsafe first-decision promotions in 91 frozen unsafe evaluations, compared with 10 for AIGate. All 10 paired differences arose in genuine historical-attribution runs: AIGate promoted on healthy numerical evidence from a prior revision, while SafeRoll held because the evidence identity did not match the active epoch. Yet these HOLDs were not correct immediate rollbacks under the frozen oracle. SafeRoll's first-decision correctness was lower, its safe promotion rate was 16/39 rather than AIGate's 26/39, and its request-normalized harmful exposure was slightly higher. The controller trades immediate promotion safety for abstention, delayed progress, and some additional traffic. Results on physical rollback containment and real inference strengthen the infrastructure interpretation without removing these costs.

This article makes five contributions. First, it states a rollout model that distinguishes desired, observed, and effective serving state and binds evidence to a rollout identity. Second, it presents a controller that uses operational, behavioral, freshness, and consistency evidence to choose PROMOTE, HOLD, or ROLLBACK. Third, it introduces RolloutBench, a reproducible fault-injection evaluation with immutable oracle truth and controller-visible observations. Fourth, it reports a paired, frozen comparison across five policies, including adverse SafeRoll results. Fifth, it adds pinned CPU inference runs to demonstrate that behavioral failure can coexist with operational health. The contribution is a model-serving control plane; it is not a new inference algorithm or a claim of universal zero-risk deployment.

![Figure 1. SafeRoll architecture. Conceptual figure; desired, observed, and effective state are distinct.](../campaigns/final-v3/phase10/figures/fig01.png)

*Figure 1. SafeRoll architecture. System and behavior evidence are bound to revision and epoch before a rollout decision. The desired, observed, and effective serving planes are distinct.*

## 2. Background and Motivation

### 2.1 Progressive delivery for an inference service

A rollout begins with an incumbent revision serving the workload and a candidate receiving a controlled share. The candidate might change only a configuration, yet that change can alter the model's observable behavior. Traffic shifting creates a measurement window in which the controller collects candidate and baseline requests, compares metrics, and chooses an action. If a policy promotes, candidate exposure may increase; if it rolls back, the system must restore baseline-only serving. A HOLD leaves the candidate at a bounded evaluation state while more evidence is sought or an inconsistency is resolved.

Operational monitoring usually includes request success, latency, readiness, and sample counts. These measurements are important but insufficient for all inference changes. Classification quality, exact match, and structured-output validity answer different questions than HTTP success. A malformed answer can be delivered quickly with a success status. Consequently, the observation schema must distinguish system evidence from behavior evidence and preserve their provenance. A model registry or deployment pipeline may track artifact versions, but the controller still needs to associate each decision-time measurement with the requests and revision that produced it. Existing MLOps descriptions emphasize reproducibility and lifecycle coordination [@kreuzberger2023; @fgcsMLOps2026]; SafeRoll focuses on the narrower runtime decision at the point of progressive release.

The six-run real-CPU block illustrates the distinction without serving as the main causal experiment. In three healthy runs, the candidate produced valid and exact structured output while serving with a mean latency of 326.60 ms. In three structured-regression runs, the candidate served without transport failures and with a lower mean latency of 252.21 ms, but its structure-valid and exact-match rates were both zero. Operational improvement did not imply behavioral acceptability. Section 7.7 reports the block separately and does not infer a population-level performance effect from three repetitions per condition.

### 2.2 A good metric can describe the wrong revision

Inference observations are not timeless scalar values. Consider an epoch-1 candidate-v2 whose measured classification accuracy is 1.00. During a later epoch, candidate-v3 is active and its ground-truth accuracy is 0.75. If the earlier numerical result becomes controller-visible without a binding to its source revision and epoch, a behavior-aware policy can incorrectly promote candidate-v3. The metric itself need not be corrupted. The error lies in using it for a decision about a different candidate. This is why SafeRoll's additional check is distinct from merely setting a stricter accuracy threshold.

An attribution check can also impose a cost. When numerical evidence and rollout identity disagree, HOLD is prudent for immediate promotion safety but may fail to execute the oracle's preferred immediate rollback. Likewise, observed routing can differ from desired routing even when the current candidate is safe. Refusing to promote then reduces progress. The evaluation treats these outcomes as scientific observations rather than exceptions to be filtered away.

### 2.3 Issuing recovery is not completing recovery

Rollback is a command to change serving state; it is not direct evidence that in-flight and subsequent requests no longer reach the candidate. A controller may acknowledge a rollback request while route propagation, in-flight work, or service reconciliation continues. The relevant endpoint for exposure is physical zero-candidate state. This distinction is familiar to self-managing cloud applications that monitor runtime state after issuing management actions [@fgcsCloud2017]. SafeRoll's recovery analysis records decision time, request time, last candidate request, and effective containment separately.

## 3. Problem Formulation

### 3.1 State and observations

For rollout epoch *e* at logical time *t*, let *D(t)* denote the **desired** rollout configuration, *O(t)* the **observed** configuration reported by the control plane, and *E(t)* the **effective serving state** inferred from request-level routing. They may differ during transitions. Desired state records the intended candidate fraction and action. Observed state records controller-visible routing and revision status. Effective state records which revision actually serves requests. None is a substitute for the others.

Let *S(t)* contain system observations such as error rate, latency, and count; let *M(t)* contain behavior observations such as accuracy, exact match, and output validity; and let *C(t)* contain attribution and consistency observations. An evidence item has at least a rollout identifier, epoch, serving revision, role (baseline or candidate), window identifier, and provenance. A policy acts on its available controller-visible record *R_e(t)* and returns *π(R_e(t)) ∈ {PROMOTE, HOLD, ROLLBACK}*. Ground truth is held outside *R_e(t)* and is used by the experimental oracle only after the policy acts.

Evidence is **fresh** when its observation window satisfies the frozen freshness rule for the decision window. It is **sufficient** when enough valid system or behavior samples exist to apply the associated threshold. Evidence is **attributable** when source identity matches the revision and epoch relation required by the active scenario. A historical source may legitimately remain visible with its original identity, but it must not be silently relabeled as the active candidate. Routing is **consistent** when observed and effective state meet the frozen tolerance for the intended comparison. These definitions are operational checks, not guarantees that the observations reveal all latent faults.

### 3.2 Actions and correctness

PROMOTE commits to increasing or accepting candidate exposure. ROLLBACK requests removal of the candidate from service. HOLD abstains from either commitment while keeping the rollout within its policy bound. The frozen oracle labels a candidate SAFE, UNSAFE, or INDETERMINATE and assigns first-decision correctness categories. For a known unsafe candidate, PROMOTE is a critical error. For a known safe candidate, an unnecessary HOLD or rollback is a cost. The oracle can distinguish a HOLD that merely delays a warranted rollback from an immediate correct rollback; this distinction is essential to interpreting the historical-attribution condition.

We measure Unsafe Promotion Rate (UPR) as unsafe first-decision promotions divided by known unsafe evaluations. Safe Promotion Rate (SPR) is safe first-decision promotions divided by known safe evaluations. Harmful Exposure Fraction (HEF) is unsafe-candidate requests divided by relevant rollout requests, both per run and request-normalized across runs. Effective Containment Time is measured from rollback request to physical zero-candidate state, never from request to command acknowledgment. Mock Decision Delay is measured in deterministic logical units; it is not a wall-clock latency in milliseconds. These endpoints address different failure and cost modes, so they are not combined into a synthetic utility score.

### 3.3 Scope of the consistency contract

SafeRoll applies five frozen invariants. **SR-I1:** decision evidence preserves the serving revision that produced it. **SR-I2:** evidence from one epoch cannot justify another epoch absent valid attribution. **SR-I3:** promotion requires the state consistency checks mandated by the policy. **SR-I4:** stale or missing mandatory evidence does not become positive evidence. **SR-I5:** physical containment is assessed from effective serving state rather than rollback acknowledgment. These invariants describe the implemented decision and measurement contract; they do not assert that every conceivable production data source is trustworthy.

The research questions and direct measurements are mapped in Table 1. This mapping keeps first-decision correctness distinct from request-level exposure, an important distinction because policies can change traffic after a HOLD or rollback request.

![Table 1. Research questions and metrics.](../campaigns/final-v3/phase10/tables/table01.png)

*Table 1. Frozen research questions, primary evidence, and metrics. The source table is Phase 10 table_data/table01.csv.*

## 4. SafeRoll Design

### 4.1 Control-plane workflow

The rollout controller instantiates a candidate alongside a baseline and records the active rollout identity. It asks the serving layer for a bounded evaluation window and receives request-level system and behavior observations. Observations retain source revision, role, epoch, and window. The controller calculates frozen summaries and applies sufficiency and freshness checks before any metric can justify promotion. It also compares desired, observed, and effective routing state. A revision or epoch mismatch is visible as a mismatch; the data are not overwritten to make them appear current.

The three evidence classes answer complementary questions. System evidence asks whether the candidate serves requests within operational limits. Behavior evidence asks whether it performs the workload correctly. State and attribution evidence asks whether those observations refer to the candidate and rollout being judged. AIGate uses the first two classes plus freshness and sufficiency; SafeRoll additionally applies the third. This makes AIGate a substantial baseline rather than a readiness-only straw man. Figure S1 and Table 2 record the frozen capability comparison.

![Table 2. Policy definitions.](../campaigns/final-v3/phase10/tables/table02.png)

*Table 2. Frozen policy capabilities. An absent check denotes the stated policy scope, not an intentionally degraded implementation.*

### 4.2 Decision precedence and HOLD

The controller evaluates the frozen policy's evidence in a defined order. Reliable harmful evidence can trigger ROLLBACK where the policy semantics permit it. Evidence whose required revision or epoch identity cannot be established, stale or insufficient mandatory observations, or a required routing-state mismatch leads to HOLD. When all mandatory checks pass and harmful thresholds are not crossed, the policy can PROMOTE. The exact branch order is an implementation contract recorded by the frozen policy version; the prose is a conceptual summary rather than a new policy specification.

HOLD matters because uncertainty is not a harmless implementation detail. For a genuinely unsafe candidate, withholding promotion can avoid a critical error. For a safe candidate, HOLD can postpone progress, generate more candidate requests, and reach the policy's finite HOLD bound. A HOLD can also be wrong under a first-decision oracle when reliable harmful evidence warranted immediate rollback. Our analysis counts these categories separately. We do not call every HOLD a success merely because it avoids PROMOTE.

### 4.3 Revision and epoch attribution

The historical case uses legitimate earlier source evidence. The source run exists, validates, matches a frozen index and SHA-256, and retains its original candidate-v2/epoch-1 identity. The current evaluation concerns candidate-v3/epoch-2. Source selection follows a predeclared seed and workload match, not a final outcome. AIGate sees healthy behavior numbers and can promote because its capability set does not enforce revision/epoch attribution. SafeRoll sees the same numbers with the original identity and abstains. This is a difference in what the policies do with controller-visible provenance, not privileged access to the current oracle truth.

### 4.4 Routing and recovery

Progressive deployment exposes only a fraction of requests to a candidate. The frozen routing-sensitive design uses seeded random assignment with a 60-request evaluation budget at 0.20 candidate exposure, where the pre-freeze sufficiency study applies. Lower candidate exposure would require independent sample-budget validation. SafeRoll also distinguishes desired routing from observed routing and from the effective requests served. A safe observed-only routing mismatch can therefore cause HOLD, a deliberate cost-control test of the consistency rule.

When ROLLBACK is requested, the controller records the request and continues to observe physical serving. The experiment considers candidate exposure ended only when the effective serving state reaches zero candidate traffic. This makes rollback a closed-loop control action rather than a declaration of success at command issuance. The 12-request frozen propagation intervention produces a measured containment interval reported in Section 7.5; its observed delay is an outcome, not the severity label.

### 4.5 Evidence lifecycle and failure handling

The controller's decision record should be read as a statement about a particular evidence snapshot, not as a timeless label for the model artifact. A candidate may be unsafe in one evaluation window and safe in a later one after the modeled condition clears. In the historical-attribution case, SafeRoll's first HOLD and later promotion describe different windows. Preserving both decisions avoids a common reporting error: using the final run outcome to overwrite the immediate decision being compared. The mock dataset therefore retains first decision, reason codes, later sequence, and final rollout outcome as separate fields. The paper's UPR and SPR refer to first decisions; containment and traffic metrics use the persisted request history.

The policy also treats failure to observe as an explicit control-plane input. A missing system summary can be caused by collection failure rather than a healthy system; a behavior sample count below the minimum may make a quality estimate unstable; a stale window may accurately describe a previous state while failing to characterize the current one. These cases differ from a measured harmful signal. The frozen policy may HOLD when required evidence is unavailable and ROLLBACK on a reliable harmful observation. This distinction prevents a missing observation from being silently translated into a threshold pass. It also introduces availability costs because an observation pipeline outage can stall a safe rollout. Those costs are not implementation noise: they are measured in the insufficient-evidence and cost-control conditions.

Finally, identity checks require a positive contract. It would be easy to reject every historical observation simply because it came from another campaign directory, but that would confuse storage namespace with scientific provenance. The loader instead checks an indexed, hash-pinned source and its workload/seed relation, then preserves the source's revision and epoch in the controller-visible record. In a genuine cross-epoch replay, those source identifiers are expected to differ from the current candidate. The validator rejects altered identities, wrong workload, wrong pairing, and substituted artifacts. This stricter form of validation is necessary for the central comparison: without it, SafeRoll could appear to benefit from a scenario that is itself assembled inconsistently.

## 5. RolloutBench Experimental Framework

RolloutBench is a fault-injection and evaluation framework for model-serving rollout policies. It generates baseline and candidate request streams, applies frozen interventions, produces controller-visible observations, and stores an immutable ground-truth artifact separately. The same scenario, condition, and seed can be evaluated under all five policies. Run artifacts contain provenance, request traces, decisions, reason codes, routing state, lifecycle transitions, and validation results. A run enters the final dataset only after artifact and campaign validation. The framework supports operational faults, behavior transformations, observation faults, historical attribution, recovery delay, compound faults, and safe controls.

The taxonomy contains CONTROL, OPERATIONAL, BEHAVIORAL, STATE_CONSISTENCY, OBSERVATION, RECOVERY, COMPOUND, and COST_CONTROL families. Operational levels include deterministic latency interventions and candidate-only error-injector cadences. Behavioral levels include deterministic classification-loss transformations and structured-output corruption. Observation interventions create stale behavior or dropped system evidence without modifying the oracle truth. The historical-attribution condition replays legitimate prior-epoch evidence while preserving its source identity. Recovery faults delay physical rollback propagation. The compound condition combines behavioral degradation with routing inconsistency. Cost controls include a safe observed-only routing mismatch and insufficient evidence.

Each intervention is specified before observation. In particular, candidate-error severity is defined by fixed candidate-request cadences—control every 100th in the configured nontriggering window, mild every eighth, moderate every fourth, and severe every second eligible candidate request. Finite seeded routing changes how many eligible requests appear and therefore changes the realized error fraction. The realized fraction is analyzed as a response, never used to reassign a run's severity. Latency +10%, +30%, +60%, and +100% and classification-loss 10%, 20%, and 25% follow deterministic transformations in their frozen workloads. The structured corruption profile is reported by separate structure-valid and exact-match outcomes.

The simulator is purpose-built to distinguish desired, observed, and effective serving state. Its request trace determines physical exposure and containment. It does not model every production scheduling, networking, or feedback delay; the purpose is controlled comparison under known faults. This separation allows both a policy decision and the underlying requests to be examined. A policy may avoid unsafe first promotion yet still expose the candidate during a HOLD or delayed rollback. Thus first-decision UPR and cumulative HEF need not rank policies identically.

### 5.1 Fault realization and measurement boundary

Faults enter at distinct points in the serving workflow. A candidate-only error intervention changes the success status of eligible candidate requests; it does not modify baseline requests. A latency intervention changes candidate serving time relative to the baseline according to a deterministic multiplier. Classification and structured-output interventions alter the candidate's returned behavior under fixed workloads. Observation interventions change what the controller can see while leaving the immutable oracle artifact untouched. Routing interventions alter the relationship between commanded, observed, and request-realized traffic. Recovery intervention changes propagation after the rollback request. Keeping these entry points distinct matters because a policy should be credited only for signals it is allowed to use and because the oracle must not change merely because telemetry becomes unavailable.

The harness stores each request's chosen route, serving revision, result, and logical timing. Policy observations aggregate over a frozen evaluation window, while the oracle uses the original workload and fault specification to classify candidate safety. For historical attribution, source evidence is loaded from a previously validated source run, not synthesized from the current outcome. The source retains its own revision, epoch, and hash. A scenario can therefore present a controller with a true historical measurement and a distinct current truth. This separation is the central experimental mechanism behind the RQ3 attribution result and allows a reviewer to inspect whether an apparent policy win arises from the intended check or from hidden leakage.

Not every fault is expected to produce a distinct decision under every policy. For example, a behavior regression combined with routing inconsistency can already be rejected by a behavior-aware gate. This is useful negative evidence about uniqueness: it shows when SafeRoll's extra state logic adds no observable first-decision benefit. Similarly, a safe routing mismatch is expected to reveal a cost of conservative consistency enforcement rather than a candidate defect. The scenario catalog therefore includes healthy and safe controls, near-threshold and severity levels where validated, and conditions that may favor baselines. The final matrix was frozen before the final-v3 results and does not encode expected decisions in scenario metadata.

An important experimental boundary is between *fault intervention* and *observed response*. For an error-injector condition, the experimenter controls the cadence applied to eligible candidate requests. The number of eligible requests arises from seeded routing, and the empirical failure fraction is calculated only afterward. A finite sample can therefore yield 6.67% under the mild cadence without becoming a different severity. This design avoids selecting or discarding final observations according to their realized value. A run is invalid only if its injector rule, seed, eligible population, budget, fault events, or provenance violate the freeze. The same principle applies to rollback: the configured propagation rule is the intervention, and effective containment time is measured from requests. It also keeps the interpretation of figures honest; the severity label belongs to the treatment, not to a post hoc grouping of outcomes.

## 6. Experimental Methodology

### 6.1 Frozen design and policy set

The final-v3 mock plan comprises 13 scenarios, 20 condition/severity cells, and 650 policy-evaluation runs: 130 per policy. Each condition uses ReadinessGate, OpsGate, BehaviorGate, AIGate, and SafeRoll. ReadinessGate reflects minimal readiness gating. OpsGate evaluates system indicators. BehaviorGate evaluates behavior indicators. AIGate combines system and behavior evidence with freshness and sufficiency. SafeRoll adds state, routing, and revision/epoch attribution. Every policy is evaluated on its prespecified capability set and thresholds; no policy is retuned after seeing final outcomes. Table 2 shows the capability matrix; the full experiment matrix appears in Appendix A.

The frozen thresholds include a maximum candidate error rate of 0.05, maximum latency regression of 0.20, maximum accuracy and exact-match drops of 0.05, and minimum structure-valid rate of 0.95. Mandatory system and behavior sample counts are three, freshness is required for both, and the routing-consistency tolerance is 10⁻⁹. These values are held fixed across the final experiment. The 650 mock runs use three prespecified repetitions for deterministic fixed-signal conditions and ten prespecified seeds for routing-sensitive conditions. Three repetitions check execution reproducibility for zero-variance signals; this is not a power analysis. The ten routing seeds use the frozen SHA-256-derived 63-bit rule from base 7300. All five policies share the relevant scenario/condition/seed pairing key.

The final-v3 real block contains three healthy and three structured-regression runs. It uses Qwen2.5-0.5B-Instruct Q4_K_M with model SHA-256 `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db` and llama.cpp 0.5.0, build 11146, commit `7fe450e19`. Runs are CPU-only with four threads, context 512, batch 128, temperature zero, and maximum output 48 tokens. The real block uses the frozen 0.20 latency-regression threshold. It is external-validity evidence, not a pooled extension of mock observations.

The five-policy design is a capability comparison rather than a claim that each policy is an exact predecessor of SafeRoll in a deployed product. ReadinessGate answers what an operationally minimal readiness decision does under the same faults. OpsGate and BehaviorGate isolate two classes of evidence. AIGate combines them and represents the main incremental baseline because it has freshness and minimum-sample handling. SafeRoll's extra state checks are assessed against that strong baseline on matched keys. The benchmark does not intentionally remove AIGate's access to behavior observations, and the historical source exposes the same numerical quality value to both AIGate and SafeRoll. Differences therefore concern provenance interpretation and consistency rules, not a secret signal handed only to the proposed method.

### 6.2 Outcomes and statistical plan

The known-safe and known-unsafe denominators are 39 and 91 per policy. UPR, SPR, critical errors, HOLD rate, and rollback rate use first decisions. Decision sequences and final outcomes are retained separately. For paired AIGate–SafeRoll and OpsGate–SafeRoll contrasts, the unit is a scenario/condition/seed key, not an independent policy row. The primary incremental SafeRoll endpoint is an AIGate unsafe PROMOTE paired with a SafeRoll decision other than PROMOTE; the reverse discordance is reported as well. HEF is reported both as aggregate unsafe candidate requests divided by relevant requests and as a per-run distribution so larger runs do not silently determine the presentation.

The frozen analysis uses Wilson 95% intervals for binary rates, exact McNemar tests for applicable paired binary comparisons, paired risk differences with deterministic bootstrap 95% intervals, and Wilcoxon signed-rank tests for appropriate non-normal paired continuous outcomes. Holm adjustment is applied within prespecified primary and secondary comparison families. Deterministic zero-variance results are described directly rather than given manufactured uncertainty. Capability contrasts outside the frozen confirmatory family are exploratory. All inferential values in this article come from the Phase 9 analysis outputs; plotting code imports them without recomputing tests.

For a paired binary endpoint, a policy pair can agree positively, agree negatively, or disagree in either direction. The exact McNemar calculation is driven by the two discordant counts, not by treating the 260 policy rows as independent observations. On the principal unsafe-promotion comparison, there are ten AIGate-only unsafe promotions and zero SafeRoll-only unsafe promotions. The paired risk difference reports the signed change in event probability on the shared unsafe keys. Confidence intervals are imported from the frozen paired bootstrap procedure, which resamples keys and keeps the two policies together. This approach respects the matched exposure and workload design. The correction family and Holm ordering are published in the Phase 9 multiple-comparison artifact, so a nominal p-value is never detached from its family.

The statistical interpretation is intentionally endpoint-specific. A reduction in critical error can coexist with a reduction in first-decision correctness because HOLD occupies a third action state. An exact test of unsafe promotion therefore cannot stand in for a test of oracle correctness. Similarly, a Wilson interval for 0/91 unsafe promotions gives uncertainty about the observed binary rate under the frozen set; it does not establish a universal safety invariant. Continuous and count summaries are reported as distributions, medians and IQRs, with means and standard deviations when informative. When every recovery seed has the same containment delay, reporting the identical value is more transparent than implying stochastic spread from a deterministic intervention.

### 6.3 Integrity and reproducibility

The final experiment froze policy versions, scenario definitions, interventions, seeds, repetition counts, oracle, metrics, exclusions, and analysis methods. Run validators checked artifact schema, source identity, thresholds, seed and scenario coverage, and the historical-source index. The final mock campaign had 650/650 valid runs with no exclusions or reruns; the real block had 6/6 valid runs. Earlier stopped campaigns and calibration observations were excluded. The final mock and real CSV SHA-256 hashes are `a0de3588d6aaa494f109f564ca37fb67d6d4fca533744e473d8a4a48e62e08f6` and `ad4bb3bcc096e8911b087828808ec47258a82ba395d14a9eaa581fd6e2fe903d`. The Phase 9 analysis manifest and Phase 10 visualization manifest are separately hashed and validated. Appendix D gives the provenance chain without crowding the results narrative.

The history-index validation deserves separate emphasis because attribution is the central contribution. Before current-run execution, each source artifact was generated as setup evidence, validated as a run, and pinned by SHA-256 and workload/seed match. The current run could consume that source but could not choose a source according to a policy outcome. The loader preserved the historical epoch and revision even when the campaign namespace differed. At campaign level, validators checked that all 50 planned historical references loaded and that source and current identities satisfied the cross-epoch relation. Source-generation artifacts did not inflate the 650 policy-evaluation count. This workflow prevents a seemingly favorable historical comparison from being assembled by selecting only source runs that happen to make one policy look good.

Reproducibility is also a boundary on interpretation. The freeze hash identifies the scientific inputs, while the dataset, analysis, and figure hashes identify successive artifacts. A reviewer can regenerate a visualization from the published source CSV without manually copying a number from manuscript prose. A run excluded for an artifact or backend failure would have remained in an exclusion audit, and a valid run could not be rerun for an inconvenient decision. The actual final-v3 dataset had zero exclusions and zero replacements. This chain does not make the simulated scenarios representative of production incident frequencies; it makes the stated conditional comparisons auditable and prevents unnoticed drift between collection, analysis, and presentation.

## 7. Results

### 7.1 RQ1 — Which harmful revisions pass infrastructure-oriented gates?

Table 3 and Figure 2 report UPR. ReadinessGate promoted 91/91 frozen unsafe candidates, OpsGate 52/91, BehaviorGate 49/91, AIGate 10/91, and SafeRoll 0/91. Their observed rates are 100%, 57.1%, 53.8%, 11.0%, and 0%, respectively. Wilson intervals are shown in the figure and table. The readiness result describes the tested unsafe conditions, not all conceivable deployments. Operational checks detect some latency and error interventions but miss behavioral revisions that preserve request-level health. Behavior checks catch classification and structured-output faults but cannot by themselves identify every operational fault. Combining evidence reduces the observed UPR, while the remaining AIGate failures isolate a state-attribution gap.

The structured-output condition gives a concrete blind spot. The frozen mock baseline has structure validity and exact match of 1.00, versus candidate values of 0.85 and 0.75. ReadinessGate and OpsGate promoted in all three fixed repetitions, while BehaviorGate, AIGate, and SafeRoll rolled back in all three. Transport and latency evidence alone did not indicate the failed output contract. The complete condition-by-policy counts are retained in the Phase 9 scenario table; Figure S3 is recommended for the supplement. No policy's aggregate UPR should be interpreted as a production failure probability because the condition mix was deliberately constructed rather than sampled from a deployment population.

![Table 3. Overall policy outcomes.](../campaigns/final-v3/phase10/tables/table04.png)

*Table 3. First-decision outcomes across the frozen mock campaign. UPR and SPR intervals are Wilson 95% intervals imported from Phase 9.*

![Figure 2. Unsafe Promotion Rate by policy.](../campaigns/final-v3/phase10/figures/fig03.png)

*Figure 2. Unsafe first-decision promotions among 91 known unsafe evaluations per policy. Whiskers are Wilson 95% intervals. The scale begins at zero.*

### 7.2 RQ2 — How does evidence composition change decisions?

The five policies expose different combinations of coverage and abstention. AIGate is materially stronger than an infrastructure-only gate because it combines system and behavior checks, including freshness and sufficiency. Its UPR is 10/91, compared with OpsGate's 52/91. Safe-promotion counts are ReadinessGate 29/39, OpsGate 29/39, BehaviorGate 36/39, AIGate 26/39, and SafeRoll 16/39. BehaviorGate's higher safe-promotion count than AIGate illustrates that adding checks is not monotonically favorable on that endpoint. These are policy comparisons under the frozen design, not independent causal estimates of a single software feature. Non-prespecified intermediate capability contrasts remain exploratory.

SafeRoll's incremental state checks eliminated the observed AIGate unsafe promotions but made it less willing to promote known-safe candidates: 16/39 versus 26/39. Figure 3 places each policy's SPR against its UPR without treating either axis as a utility score. Table 3 reports 0/91 UPR and 16/39 SPR for SafeRoll; the benefit and cost are visible simultaneously. Figure 4 further shows paired first-decision correctness: of 130 AIGate–SafeRoll pairs, both were correct in 87, AIGate alone was correct in 10, SafeRoll alone was correct in zero, and both were incorrect in 33. The paired correctness risk difference (SafeRoll minus AIGate) was −0.07692 with Phase 9 paired bootstrap 95% interval [−0.123, −0.038]; exact McNemar p = 0.001953125 and within-primary-family Holm p = 0.005859375. This result is adverse to SafeRoll under the frozen first-decision oracle.

The paired table clarifies what the aggregate policy rates cannot. If the 130 decisions had been analyzed as two independent samples, shared scenario and seed structure would have been lost. Instead, the 10 disagreement cases can be traced to a single, prespecified fault mechanism: historical evidence from a prior epoch. The apparent contradiction between improved unsafe-promotion safety and worse first-decision correctness is therefore not a statistical artifact. The two endpoints ask different questions of the same HOLD action. One asks whether a harmful candidate was promoted; the other asks whether the immediate action matched the oracle's prescribed rollback. The manuscript retains both answers in Figure 4 and Table 4. This also limits causal interpretation of the policy matrix: the broad five-policy progression reveals behavior, while the AIGate–SafeRoll paired contrast isolates the specific incremental state-aware policy under the frozen workloads.

![Table 4. SafeRoll versus AIGate paired statistics.](../campaigns/final-v3/phase10/tables/table05.png)

*Table 4. Prespecified paired correctness, unsafe promotion, and critical-error comparisons. Risk differences are SafeRoll minus AIGate; p-values and intervals are imported from Phase 9.*

![Figure 3. Unsafe versus safe promotion.](../campaigns/final-v3/phase10/figures/fig04.png)

*Figure 3. UPR versus SPR across the five frozen policies. Each policy has 91 unsafe and 39 safe evaluations. Both axes cover 0–100%; points are descriptive.*

![Figure 4. Paired AIGate–SafeRoll decisions.](../campaigns/final-v3/phase10/figures/fig05.png)

*Figure 4. Paired first-decision correctness for 130 scenario/condition/seed keys and unsafe promotion for the 91 unsafe keys. The same ten attribution cases produce avoided promotion and adverse first-decision correctness.*

### 7.3 RQ3 — What happens when observations are unreliable or misattributed?

The observation family includes a one-window stale behavior observation and dropped system evidence; the cost-control family includes insufficient behavior evidence. These conditions test how policies treat missing or temporally invalid information without changing underlying oracle truth. The full PROMOTE/HOLD/ROLLBACK distribution and eventual outcomes for all five policies are in Table S2. When mandatory evidence is stale or insufficient, a HOLD can avoid making an unsupported immediate decision, but it can also delay action. We therefore report HOLD and correctness as separate fields.

The historical-attribution condition is the clearest distinction between SafeRoll and AIGate. A validated epoch-1 source run for candidate-v2 supplied healthy controller-visible behavior evidence with accuracy 1.00. The epoch-2 active candidate-v3 had ground-truth accuracy 0.75. Source revision and epoch remained visible, and the source workload and seed matched the frozen history index. AIGate PROMOTEd in all 10 paired seeds because the numerical behavior signal was acceptable. SafeRoll HOLDed in all 10 because the evidence belonged to a different revision and epoch. SafeRoll therefore avoided 10 immediate unsafe promotions, with zero reverse SafeRoll-only unsafe promotions. Among 91 unsafe pairs overall, the paired unsafe-promotion risk difference (SafeRoll minus AIGate) was −0.10989; Phase 9 paired bootstrap 95% interval [−0.176, −0.055], exact McNemar p = 0.001953125, and Holm-adjusted p = 0.005859375.

Historical replay is a deliberately hard case for an evidence-composing controller. System observations need not be abnormal, and the supplied behavior value looks better than the current candidate's ground truth. An operational-only gate can promote for lack of a visible fault; a behavior-aware gate can promote because it trusts a seemingly healthy quality number. The state-aware policy has an orthogonal reason to refuse promotion: the provenance says that the number belongs to another epoch and revision. The result does not demonstrate that SafeRoll inferred candidate-v3's true accuracy from stale evidence. It demonstrates a narrower and operationally useful property: the policy declined to use evidence whose identity could not justify a decision about candidate-v3.

SafeRoll did **not** immediately roll back in these 10 cases. The frozen oracle classified its first HOLDs as DELAYED_ROLLBACK, not correct rollback; immediate SafeRoll rollback was 0/10. Each SafeRoll run later promoted after the current condition cleared, so eventual promotion does not retroactively make the first decision correct. This is the mechanism behind the adverse paired correctness result in Section 7.2. Figure 5 keeps the historical evidence, current oracle truth, first decisions, and later outcome distinct. The case shows that a numerically acceptable metric can be unusable when it belongs to another rollout identity.

![Figure 5. Genuine historical attribution.](../campaigns/final-v3/phase10/figures/fig06.png)

*Figure 5. Healthy epoch-1 candidate-v2 evidence is replayed with preserved identity during an unsafe epoch-2 candidate-v3 evaluation (10 paired seeds). AIGate promotes; SafeRoll holds on the mismatch. The first HOLD is a delayed rollback under the frozen oracle, and later promotion occurs after the condition clears.*

### 7.4 RQ4 — How do policies handle compound faults?

The frozen compound condition combines a 25% classification-accuracy loss with routing inconsistency. Across its 10 paired seeds, ReadinessGate and OpsGate each promoted all 10 unsafe candidates. BehaviorGate, AIGate, and SafeRoll each rolled back all 10. Critical errors were 10 for each of the two non-behavior baselines and zero for the other three. This result does not show a unique SafeRoll advantage: behavioral evidence already dominates the decision in this compound case. Table S3 reports the condition separately rather than pooling it with single-fault families. The result also cautions against claiming that every observed SafeRoll success is attributable to state-consistency logic.

### 7.5 RQ5 — Does rollback physically contain exposure?

The frozen recovery condition delays physical propagation after rollback. In each of 10 SafeRoll seeds, the rollback decision occurred at mock logical time 552, the request at 553, and the effective zero-candidate state at 606. Effective Containment Time from request to physical containment was 53 logical units in every seed; the deterministic result has no across-seed timing variance. Candidate completions after the rollback request totaled 21, with per-seed counts ranging from zero to four. Figure 8 and Table 5 distinguish the request event from physical containment. Reporting command acceptance as recovery completion would omit the interval in which the candidate continued to serve.

First-decision safety does not settle cumulative exposure. Aggregate request-normalized HEF was 0.45656 for ReadinessGate, 0.43372 for OpsGate, 0.42338 for BehaviorGate, 0.40114 for AIGate, and 0.40522 for SafeRoll. SafeRoll was slightly **higher** than AIGate on this descriptive aggregate. Figure 7 also displays per-run medians and ranges so that request weighting is visible. HOLD, recovery, and subsequent routing can add candidate requests after an initially safer decision; the frozen comparison does not support a claim that SafeRoll minimizes harmful exposure. The HEF difference is descriptive here and should not be promoted into an unplanned confirmatory hypothesis.

![Table 5. Recovery containment.](../campaigns/final-v3/phase10/tables/table07.png)

*Table 5. SafeRoll rollback request, physical containment, elapsed mock logical time, and post-request candidate completions across 10 seeds.*

![Figure 7. Harmful exposure fraction.](../campaigns/final-v3/phase10/figures/fig08.png)

*Figure 7. Request-normalized harmful exposure on unsafe evaluations, with per-run median and range. SafeRoll's aggregate value is slightly higher than AIGate's; the bars and per-run marks use different weighting.*

![Figure 8. Rollback request and containment.](../campaigns/final-v3/phase10/figures/fig09.png)

*Figure 8. SafeRoll recovery across 10 frozen seeds. Mock logical time 553 is the rollback request; time 606 is physical zero-candidate state. Bars show post-request candidate completions by seed.*

### 7.6 RQ6 — What does stronger validation cost?

SafeRoll's conservative behavior imposed measurable costs on the 39 known-safe first-decision evaluations. It produced 26 unnecessary HOLD decisions and zero unnecessary rollbacks. Twenty-three safe runs contained a cost event, and three safe rollouts failed at the HOLD bound. Across those 23 held-safe pairs, SafeRoll completed 319 more candidate requests than AIGate. Its safe-promotion count was 16/39, compared with AIGate's 26/39. AIGate also had abstention costs: Table 6 records 16 unnecessary HOLD decisions and three HOLD-bound failures for that policy. The difference is therefore incremental, not a claim that AIGate never holds.

![Table 6. Safety costs.](../campaigns/final-v3/phase10/tables/table08.png)

*Table 6. Safe-evaluation costs for AIGate and SafeRoll. Counts and candidate traffic remain separate; the latter is paired against AIGate on held-safe keys.*

First-decision mock Decision Delay was identical across policies in this design (mean 206.97 logical units per policy), because each policy received the same evaluation-window schedule before its first decision. The consequential delay appears after HOLD: among the 23 held-safe pairs, SafeRoll's final decision-time difference relative to AIGate had mean 146.96 logical units and median zero. Across all 39 safe pairs, the mean difference was 86.67 logical units. These differences are distributions of a discrete control process, not milliseconds and not a uniform delay imposed on every safe rollout. Figure 6 places avoided unsafe promotion, unnecessary HOLD, HOLD-bound failure, and additional candidate traffic in separate panels; no synthetic score conceals their different units.

![Figure 6. Safety benefit and cost.](../campaigns/final-v3/phase10/figures/fig07.png)

*Figure 6. SafeRoll's frozen incremental benefit and measured safe-rollout costs. Counts and traffic have distinct units and are presented separately; this is not a net utility calculation.*

### 7.7 REAL_CPU external-validity block

The pinned real inference block uses a separate dataset. In three healthy runs, both baseline and candidate produced structure-valid and exact outputs at rate 1.00. Baseline mean serving latency was 340.44 ms and candidate mean was 326.60 ms; all three candidates were SAFE and all three first decisions were PROMOTE. In three structured-regression runs, baseline mean latency was 345.81 ms and candidate mean was 252.21 ms. Candidate structure validity and exact match were both 0.00 despite zero serving failures; all three candidates were UNSAFE and all three decisions were ROLLBACK. Figure 9 and Table 7 present the block without pooling its milliseconds with mock logical time. The candidate was operationally successful and faster while behavior was invalid. With n = 3 per condition, these observations illustrate the failure mode and controller response under the pinned configuration; they are not a broad statistical demonstration across models, hardware, or production traffic.

![Table 7. Real CPU evidence.](../campaigns/final-v3/phase10/tables/table09.png)

*Table 7. Separate REAL_CPU external-validity block. Latencies are descriptive mean and SD in milliseconds; n = 3 per condition.*

![Figure 9. REAL_CPU external validity.](../campaigns/final-v3/phase10/figures/fig12.png)

*Figure 9. Descriptive REAL_CPU healthy and structured-regression blocks, n = 3 each. Latency is in milliseconds; behavior validity and decisions are reported separately. No significance inference is made from the block.*

## 8. Discussion

### 8.1 Evidence correctness includes identity

A metric's numerical value is only part of its meaning. For a rollout decision, the controller needs the revision, role, epoch, and observation window from which that value arose. The historical-attribution result demonstrates a concrete failure of using a healthy numerical signal across an identity boundary. This is a control-plane form of provenance: a true observation can be wrong evidence for the current action. Work on workflow provenance shows the general value of preserving source traces for reproducibility and reuse [@fgcsProvenance2017]. SafeRoll applies the principle at decision time to model-serving state. The finding is limited to the tested cross-epoch setup; it does not establish the frequency of such mismatches in production.

### 8.2 HOLD is an explicit but costly response to uncertainty

HOLD made SafeRoll safer on immediate unsafe promotion in the historical case. The same decision reduced first-decision correctness under the frozen oracle and contributed to a lower SPR and more safe-run traffic. This tension cannot be resolved by calling the abstention an automatic success. Operational adoption needs a policy for how long HOLD may persist, what additional evidence is gathered, and whether an application prefers immediate rollback when uncertainty is high. Argo Rollouts, for example, documents an inconclusive analysis state that can require human judgment [@argoAnalysis]; SafeRoll formalizes a controller decision in a specific inference-service state model. The two are related but do not have identical semantics.

### 8.3 First-decision safety and cumulative traffic answer different questions

UPR asks whether a policy committed to promoting a known unsafe revision at its first decision. HEF asks how much unsafe candidate traffic served during the rollout. A controller can improve UPR yet hold or recover slowly enough to generate additional candidate requests. The observed SafeRoll–AIGate HEF ordering illustrates this non-equivalence. It argues for reporting decision outcomes and request-level exposure together. An operator with strict exposure limits cannot infer containment from a favorable first-decision table alone.

### 8.4 Physical recovery closes the loop

The 53-logical-unit interval and 21 post-request candidate completions show why rollback acknowledgment is an incomplete recovery endpoint. In a production control plane, the effective serving state may need confirmation from routing and request traces before declaring containment. The test uses a fixed propagation intervention, so its numerical delay is not a latency prediction for a particular service mesh. Its methodological implication is that recovery metrics should end at physical candidate removal, not at command issuance.

### 8.5 Where the extra checks matter

State attribution is most valuable when multiple revisions or rollout epochs coexist, observation pipelines can lag, and promotion of a harmful candidate has a high cost. In a system whose telemetry is already strongly bound to the active revision and whose routing state is atomically verifiable, the incremental benefit may be smaller than in the frozen historical scenario. Behavior-aware policies are already effective for faults that visibly degrade behavior, as the compound condition shows. Simpler gates may be adequate for low-risk changes with reliable provenance and a strong rollback path. The appropriate policy therefore depends on the relative cost of unsafe promotion, HOLD, and delayed progress, rather than on an unconditional ranking of the five policies.

### 8.6 Implications for inference infrastructure

Inference platforms increasingly separate gateway, routing, model runtime, and monitoring components. Research on serving emphasizes resource allocation and latency, while deployment tools supply traffic shifting and health analysis [@inferline2019; @fgcsInference2026; @kserveCanary]. SafeRoll adds a decision contract over these components: evidence must be attributable to the candidate and effective serving state, and rollback completion must be observed. The controller can sit above a particular serving engine because it depends on observations and provenance rather than model internals. This architecture does not remove the engineering need to collect trustworthy behavior metrics, nor does it solve model-quality evaluation itself.

## 9. Threats to Validity

**Internal validity.** The mock harness is deterministic for many interventions. This improves repeatability and paired comparison but simplifies scheduling, network delay, user traffic, and feedback availability. Faults are injected according to frozen rules rather than arising from a production incident process. Validators confirmed configured interventions and provenance, yet unmodeled interactions could change real control-plane behavior.

**Construct validity.** The oracle labels SAFE, UNSAFE, and INDETERMINATE according to prespecified workload and correctness definitions. A different application's tolerance for behavioral loss or HOLD may imply different operational preferences. The historical SafeRoll HOLD is DELAYED_ROLLBACK under this oracle; an organization that values abstention over immediate rollback may assess it differently. This paper preserves the frozen classification instead of changing it to favor a policy. UPR and SPR use first decisions, whereas HEF spans requests across the rollout; neither alone represents all safety costs.

**Statistical conclusion validity.** There are 130 paired keys, but many fixed-signal mock conditions have zero outcome variance and are not independent samples from a wider fault population. The 10 routing-sensitive seeds characterize the frozen randomized routing conditions rather than support universal probability claims. Exact McNemar and paired bootstrap intervals describe the chosen comparisons and design. Multiplicity correction is within prespecified families; exploratory capability contrasts do not become confirmatory merely because they look large. Zero observed SafeRoll unsafe promotions should not be interpreted as a guarantee of zero risk.

**External and ecological validity.** The real block uses one small quantized Qwen model, one llama.cpp build, CPU-only serving, a structured-output workload, and three repetitions per condition. It establishes that the tested behavioral failure can coexist with faster successful serving, but not how often that occurs across modern accelerators or diverse models. Synthetic workload transformations may not match production error modes, traffic bursts, feedback delays, or multi-tenant interference. The 60-request sufficiency result was validated at 0.20 candidate exposure; applying it at a lower exposure would require another sample-budget study.

**Control-plane and provenance assumptions.** SafeRoll relies on accurate identity fields and interpretable effective routing observations. A malicious or systematically incorrect provenance source could defeat the checks. The historical source was validated and hash-pinned in this experiment. The study does not test arbitrary tampering, coordinated monitoring outages, or Byzantine sources. It also does not quantify the overhead of deploying the controller in a production cluster.

**Transfer of cost thresholds.** The chosen HOLD bound, sample minimums, and routing budget are part of the frozen experiment, not generally optimal control parameters. In particular, a service with a costly false rollback may prefer a longer HOLD, while a service with severe harm from any candidate request may prefer an earlier rollback. The real CPU block exercises only the final policy settings and one output contract, so it cannot calibrate those preferences for other applications. Future deployments should define the cost of each action before selecting thresholds, preserve the identity of all evidence used for that selection, and validate their own exposure and sample budgets. The reported 0.20 candidate exposure and 60-request evaluation budget are a measured design point, not a scaling law for arbitrary traffic shares.

## 10. Related Work

**MLOps and lifecycle orchestration.** MLOps work describes the tooling and organizational process needed to move from model development to maintained services [@kreuzberger2023]. TFX shows a production-scale machine-learning platform, and the technical-debt literature argues that the surrounding system contributes significantly to ML reliability [@tfx2017; @sculley2015]. A recent FGCS reference architecture emphasizes orchestration and coordination across MLOps activities [@fgcsMLOps2026]. SafeRoll addresses one narrower lifecycle transition: a candidate serving revision is already available, and the control plane must decide whether to increase traffic given potentially inconsistent runtime observations.

**Progressive deployment and canary analysis.** Argo Rollouts and Kayenta support controlled traffic exposure and automated metric analysis [@argoAnalysis; @kayenta2018]. KServe documents stepped canary rollout and rollback for inference services [@kserveCanary]. These systems demonstrate that rollout control is operationally important, and they may accept user-specified behavior measurements. We do not claim they are limited to infrastructure metrics. SafeRoll's particular contribution is a prespecified policy and evaluated contract for binding system and behavior evidence to revision/epoch identity and for distinguishing desired, observed, and effective serving state. Table 8 uses qualified entries where a general platform could be configured to implement part of that contract.

**Model-serving systems.** Clipper provides a low-latency prediction-serving abstraction with batching and model selection [@clipper2017]. InferLine provisions prediction pipelines against latency objectives [@inferline2019]. MArk studies latency objectives and cloud provisioning cost [@mark2019]. INFaaS explicitly uses both accuracy and performance requirements when selecting model variants, so serving research is not limited to operational metrics [@infaas2021]. vLLM improves large-language-model serving efficiency through memory management [@vllm2023]. A recent FGCS serverless-inference study addresses model partitioning and batch scheduling across device and cloud resources [@fgcsInference2026]. SafeRoll studies a different decision boundary: whether a candidate revision should continue to receive rollout traffic when evidence may belong to another revision or epoch. These layers are complementary; efficient serving and accuracy-aware variant selection do not themselves establish the historical-attribution contract evaluated here.

**Monitoring, provenance, and recovery.** Cloud-native management research treats monitoring and recovery as continuous control functions [@fgcsCloud2017]. Provenance research in FGCS demonstrates the value of retaining origin and transformation records [@fgcsProvenance2017]. SafeRoll applies identity and effective-state checks to rollout evidence, including a physical containment endpoint after rollback. Our comparison table limits claims to features directly documented or evaluated by each source; an unreported feature is marked “not established” rather than “impossible.”

### Table 8. Scope of related systems and SafeRoll

| Work | Operational signals | Behavior signals | Progressive deployment | Revision/epoch attribution for decision | Explicit HOLD | Physical containment endpoint |
|---|---|---|---|---|---|---|
| Argo Rollouts analysis [@argoAnalysis] | configurable | configurable through analysis templates | yes | not established as a built-in inference contract | inconclusive/manual judgment | not established in cited documentation |
| KServe canary [@kserveCanary] | serving status | configurable outside cited rollout guide | yes | revision routing is documented; decision-time cross-epoch validation not established | not established in cited guide | rollback supported; request-level endpoint not established |
| Kayenta [@kayenta2018] | canary metrics | configurable metric analysis | yes | not established in cited description | not established in cited description | not established in cited description |
| Clipper [@clipper2017] | latency/throughput | online prediction accuracy/selection | not its studied objective | not its studied objective | not its studied objective | not its studied objective |
| SafeRoll (this work) | yes | yes | yes | yes, under frozen identity rules | yes | yes, measured from requests |

*Table 8. A qualified scope comparison from inspected sources. “Not established” means the cited source did not establish the feature as the decision contract evaluated here; it is not a claim that an implementation cannot be extended.*

## 11. Conclusion

Safe progressive delivery for AI inference services requires checking both what evidence says and whether that evidence belongs to the rollout state under decision. In the frozen evaluation, SafeRoll's revision and epoch checks avoided 10 immediate unsafe AIGate promotions, while its HOLD behavior reduced safe promotion and first-decision correctness and did not lower request-normalized harmful exposure relative to AIGate. Physical rollback containment lagged the rollback request, and the separate real-CPU block showed that faster, error-free serving can still violate a behavioral contract. State-consistent control is therefore a useful additional safeguard when provenance can be checked, but its abstention and exposure costs must be designed and reported explicitly.

## Appendix A. Frozen experiment matrix and severity semantics

The complete 20-cell scenario-by-condition matrix and routing-sensitive flags are supplied as Phase 10 Table S1, linked below. The 650 rows are not reproduced in print. Control conditions include healthy rollout and structured healthy output. Operational conditions cover four latency levels and four error cadences. Behavioral conditions cover classification loss and one validated structured-output regression. Observation and state conditions cover one-window stale behavior, dropped system evidence, and genuine cross-epoch attribution. Recovery uses a fixed propagation intervention. Compound and cost controls test behavior plus routing, observed-only routing mismatch, and insufficient behavior evidence. The matrix preserves three fixed-signal repetitions or ten seeded routing repetitions as frozen; no final observation is used to add a condition or adjust a seed.

![Table S1. Final experiment matrix.](../campaigns/final-v3/phase10/tables/table03.png)

*Table S1. Frozen condition/severity cells, repetitions, routing sensitivity, and five-policy coverage. The machine-readable source is Phase 10 table_data/table03.csv.*

## Appendix B. Additional evidence

Figure S1 gives the policy capability matrix. Figure S2 shows decisions by frozen latency, candidate-error cadence, and classification-loss severity; its measured realized error-rate ranges are descriptive. Figure S3 separates structure validity and exact match. Table S2 records evidence-reliability conditions, and Table S3 records the compound behavior-plus-routing condition. These are supplemental details behind the main RQ summaries, not new analyses.

![Figure S1. Policy capabilities.](../campaigns/final-v3/phase10/figures/fig02.png)

*Figure S1. Frozen policy capabilities. The baselines have distinct purposes; AIGate includes system and behavior evidence with freshness and sufficiency.*

![Figure S2. Severity response.](../campaigns/final-v3/phase10/figures/fig10.png)

*Figure S2. First-decision response under frozen operational and behavioral severity interventions. Realized candidate-error rates are outcomes, not severity definitions.*

![Figure S3. Mock structured-output failure.](../campaigns/final-v3/phase10/figures/fig11.png)

*Figure S3. Separate mock structure-valid and exact-match outcomes with policy decisions, three fixed repetitions per policy.*

![Table S2. Evidence reliability.](../campaigns/final-v3/phase10/tables/table06.png)

*Table S2. Stale behavior, dropped system observation, historical attribution, and insufficient evidence by policy, including first decisions and eventual outcomes.*

![Table S3. Compound behavior and routing.](../campaigns/final-v3/phase10/tables/table10.png)

*Table S3. First-decision counts for the frozen behavior-plus-routing condition over ten paired seeds per policy.*

## Appendix C. Statistical and oracle detail

The frozen oracle distinguishes first-decision correctness, critical error, unnecessary HOLD, unnecessary rollback, and eventual run outcome. Historical attribution produces a HOLD that avoids unsafe promotion but is labeled DELAYED_ROLLBACK because immediate rollback was warranted. UPR and SPR denominators exclude INDETERMINATE cases by definition; all final policy denominators are independently validated. Wilson intervals are used at zero and all-event boundaries. Paired binary tests use exact McNemar with explicit discordant counts. Bootstrap resampling uses shared pairing units and a deterministic seed as recorded in the Phase 9 analysis manifest. Risk ratios with undefined denominators are reported as undefined in the source analysis rather than forced to zero. The full statistical-tests, intervals, effects, and Holm-family tables remain in campaigns/final-v3/analysis.

## Appendix D. Reproducibility and provenance

The final-v3 freeze SHA-256 is `94e336772d64002561467a9cf231019c41a3c7f1e58005ab40a7dd44c87a3015`, and the frozen plan SHA-256 is `67bae1209a6c09b9d269618ea452dbee1f7b45800218285eb1c9bb301ff9a80d`. The Phase 9 analysis manifest SHA-256 is `b56f3a71ab5301ec0c4bc6fc08b08bf235ea693bd054c5aea4cace47b790d55c`; the Phase 10 artifact-manifest SHA-256 is `3fbaaf831417de93000c14ecf76db9dbd58449731c184411a43dd0dea244eb64`. Final-v1 and final-v2 are permanently stopped protocol versions and contribute no observations to final-v3 results. The final-v3 history index pins legitimate earlier source evidence by artifact hash, workload, and seed; source artifacts are setup evidence, not additional policy-evaluation rows. All Phase 10 figures have source CSVs and vector PDF plus PNG renderings. The analysis and visualization workflows are deterministic on unchanged inputs.

## References

The structured bibliography is in [references.bib](references.bib). Citation keys in this source are intended for the final typesetting workflow; each source was inspected and mapped to the claim it supports in citation_audit.md.
