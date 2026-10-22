# Focused literature gap audit

Search date: 2026-10-21. Sources were inspected at publisher, proceedings, or official project pages. The search sought directly relevant work on MLOps orchestration, model serving, canary analysis, and rollout evidence identity. It was not a count-driven bibliography expansion.

| Candidate | Source | Disposition | Reason |
|---|---|---|---|
| INFaaS: Automated Model-less Inference Serving (2021) | [USENIX ATC](https://www.usenix.org/conference/atc21/presentation/romero) | **Added** | Important accuracy/performance-aware serving system; prevents implying all serving work is infrastructure-only. It selects serving variants rather than validating rollout-epoch evidence. |
| MArk: Exploiting Cloud Services for Cost-Effective, SLO-Aware Machine Learning Inference Serving (2019) | [USENIX ATC](https://www.usenix.org/conference/atc19/presentation/zhang-chengliang) | **Added** | Clear cloud-inference SLO/cost comparator that sharpens the rollout-control distinction. |
| Online learning and continuous model upgrading through Kafka-ML (2024) | [FGCS](https://www.sciencedirect.com/science/article/pii/S0167739X24002930) | Inspected, **not added** | Directly concerns continuous model upgrading, but inspected abstract describes streaming training/deployment rather than evidence-bound promotion decisions. The existing FGCS MLOps architecture already supports lifecycle context. Could be revisited if authors expand that subsection. |
| BEAVER-EDGE: Edge AI lifecycle management automation (2026) | [FGCS](https://www.sciencedirect.com/science/article/abs/pii/S0167739X26003316) | Inspected, **not added** | Edge lifecycle automation is adjacent; current abstract does not establish relevance to rollout attribution or recovery. |
| Model partitioning and batch scheduling (2026) | [FGCS](https://www.sciencedirect.com/science/article/pii/S0167739X25006326) | Already cited | Resource-efficient inference comparator. |
| MLOps reference architecture (2026) | [FGCS](https://www.sciencedirect.com/science/article/abs/pii/S0167739X26003341) | Already cited | Lifecycle orchestration context. |
| Dynamic DNN edge offloading (2026) | [FGCS](https://www.sciencedirect.com/science/article/pii/S0167739X25002377) | Inspected, **not added** | Offloading and resource allocation rather than progressive revision control. |
| Serverless application scaling (2025) | [FGCS](https://www.sciencedirect.com/science/article/pii/S0167739X25001682) | Inspected, **not added** | Autoscaling scope; not model-behavior attribution. |
| Federated learning across the continuum (2024) | [FGCS](https://www.sciencedirect.com/science/article/pii/S0167739X24003431) | Inspected, **not added** | Federated training rather than inference rollout decisions. |
| AuditOps compliance framework (2026) | [FGCS](https://www.sciencedirect.com/science/article/pii/S0167739X26002839) | Inspected, **not added** | Regulatory audit trail is adjacent provenance, but different decision boundary. |

Original cited references: **13**. Newly inspected candidates in this focused pass: **8** (two added, six rejected); additionally two already-cited recent FGCS papers were rechecked. New references: **2**. Final cited references: **15**. New FGCS references: **0**. The bibliography remains intentionally compact; the manuscript now explicitly recognizes accuracy-aware inference serving.
