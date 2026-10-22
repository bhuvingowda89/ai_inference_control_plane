# Cover letter — draft for author approval

Dear Editors of *Future Generation Computer Systems*,

Please consider our research article, **‘SafeRoll: State-Consistent Progressive Deployment and Automated Rollback for AI Inference Services.’** It addresses reliable progressive deployment in AI inference infrastructure: a rollout controller needs to know not only what operational and model-behavior evidence says, but whether it belongs to the revision, epoch, and effective serving state being decided upon.

SafeRoll combines system, behavioral, freshness, attribution, and state-consistency checks in a PROMOTE/HOLD/ROLLBACK control plane. We evaluated five policies in 650 frozen mock runs and separately examined six pinned real-CPU inference runs. In the tested unsafe set, SafeRoll made 0/91 unsafe promotions versus 10/91 for an AI-aware baseline. We also report the cost: safe promotions decreased from 26/39 to 16/39, first-decision correctness was lower in the historical-attribution condition, and harmful exposure was slightly higher. Real inference confirmed that a behaviorally invalid candidate can serve successfully and faster. The paper quantifies physical containment after rollback request.

We believe this systems contribution fits FGCS's interest in future-generation computing infrastructure, cloud-native AI serving, MLOps orchestration, and service reliability. The manuscript includes reproducible frozen configurations, artifact hashes, source data, and qualified comparisons to serving and canary systems.

[CORRESPONDING AUTHOR TO CONFIRM: The work is original, approved by all authors, and is not under consideration elsewhere.] [CONFIRM DATA/CODE AVAILABILITY AND DECLARATIONS.]

Sincerely,

[CORRESPONDING AUTHOR NAME, AFFILIATION, EMAIL]
