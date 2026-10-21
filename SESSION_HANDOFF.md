# SafeRoll session handoff

Updated 2026-10-20. Repository: `/Users/nischithacn/ai_inference_control_plane`.

## Research state and boundary

Paper: **SafeRoll: State-Consistent Progressive Deployment and Automated Rollback for AI Inference Services**.

Phases 4A–4H and the Phase 6, 6B, and 6C pilots are complete. Phase 6C recommends **GO to a human-reviewed Phase 7 freeze proposal only**. The final campaign has not been frozen or run. Do not automatically freeze Phase 7, change policy thresholds, or modify completed pilot evidence. The next task should be set by the user.

Git HEAD at this handoff: `751f9f19af65333511c1fa390900fd056faba613` (`Phase 6c`). The working tree was clean before this handoff file was added. This file itself is uncommitted unless committed later.

## Preserved campaigns

| Campaign | Plan SHA-256 | Valid runs | Scientific verdict |
| --- | --- | ---: | --- |
| `phase6-pilot-v1` | `a36838b4c36f90d0171d0210696be9bc65ab3e241ed8b61f949bbf91bf1b6784` | 105/105 | NO-GO for freeze: no incremental SafeRoll decision-correctness benefit over AIGate. |
| `phase6b-refinement-v1` | `57b87caa06ae41b3275c8f71d0f26032791dc8ef45512cb52b1003dddc62f239` | 78/78 | CONDITIONAL GO: earlier attribution replay was not genuine prior-revision evidence. |
| `phase6c-prefreeze-v1` | `fa6007c357396d896de724f577658c193df61ac1391a77df3df2cc362da73058` | 78/78 | GO to a reviewed freeze proposal; campaign validation `{"issues": [], "valid": true}`. |

Campaign run artifacts are under `campaigns/<id>/`, **ignored by Git**. Keep all three directories unchanged. Their analysis files are `phase6_pilot_analysis.{json,md}`, `phase6b_refinement_analysis.{json,md}`, and `phase6c_prefreeze_analysis.{json,md}` within their respective campaign directories. The detailed Phase 6C analysis is the authoritative source for figures and proposals.

## Phase 6C results worth carrying forward

- Historical attribution test: a real persisted epoch-1 candidate-v2 healthy evidence record (accuracy 1.00) was replayed through the controller observation layer during an epoch-2 candidate-v3 rollout (current accuracy 0.75). Source identity remained intact; immutable ground-truth artifacts were unchanged. AIGate made two known unsafe promotions. SafeRoll made two identity-uncertainty HOLD decisions, avoiding those promotions; its first decision was classified `DELAYED_ROLLBACK`, so abstention has a cost. Historical sources and hashes are indexed in `campaigns/phase6c-prefreeze-v1/history_index.json`.
- Cost control: observed-only routing mismatch yielded four SafeRoll unnecessary HOLD decisions and two SAFE runs that reached the HOLD bound and ended FAILED. This is a conservatism control, not a safety-benefit scenario.
- Routing study at candidate weight 0.2, ten prespecified seeds: with 20 evaluation requests, system evidence was insufficient in 3/10; with 40, 1/10; with 60, 0/10. Recommended future treatment: seeded random routing with 60 evaluation requests and ten predeclared seeds for routing-sensitive conditions. This is observed support, not a universal guarantee, especially at lower candidate weights. See `seed_design.json`, `routing_study_results.json`, and `routing_study_extension_results.json` in the Phase 6C campaign directory.
- Latency calibration: baseline p95 10 ms; candidate p95 11/13/16/20 ms gives +10/+30/+60/+100%, separating the healthy control and fault levels from the +20% policy boundary. Behavior losses of 10/20/25% cross the provisional 5% allowance. Selected error-rate levels were control 0%, mild about 9.5–12%, moderate about 21.1–23.8%, severe about 47.4–48% against the 5% threshold. Structured candidate validity 0.85 and exact match 0.75 versus baseline 1.00 exercised the structured threshold.
- No policy classes or policy thresholds were changed in Phase 6C. AIGate retained system and behavior evidence with sufficiency and freshness handling. The historical replay is a controller-visible observation fault; direct ground-truth attribution corruption remains invalid.
- Last reported full test command/result: `python3 -m pytest -q` → `634 passed, 3 deselected in 65.38s`. This was before this documentation-only handoff.

## Next reviewed decisions

Review the proposed final scenario taxonomy, threshold and severity tables, and seed design in `campaigns/phase6c-prefreeze-v1/phase6c_prefreeze_analysis.{json,md}`. The Phase 6C verdict does **not** finalize those proposals. Outstanding limits: additional stale/recovery/routing severities are proposals rather than measured Phase 6C levels; lower candidate routing weights need their own sufficiency check; real CPU timing variance has not been measured. The proposed separate real CPU pre-freeze pilot is five repetitions each of pinned llama.cpp healthy and structured-configuration regression, if authorized later.

Before new work, check `git status --short`, the Phase 6C campaign validation, and the detailed analysis. Do not rerun selected outcomes or overwrite pilot directories. Since `campaigns/*` is ignored, back up those directories separately before moving machines or cleaning the workspace.
