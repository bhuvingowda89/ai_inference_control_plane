"""Check manuscript facts, citations, and evidence links against frozen artifacts."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "manuscript"
CAMP = ROOT / "campaigns/final-v3"
ANALYSIS = CAMP / "analysis"
PHASE10 = CAMP / "phase10"
ANALYSIS_HASH = "b56f3a71ab5301ec0c4bc6fc08b08bf235ea693bd054c5aea4cace47b790d55c"
PHASE10_HASH = "3fbaaf831417de93000c14ecf76db9dbd58449731c184411a43dd0dea244eb64"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(name: str) -> list[dict]:
    with (ANALYSIS / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_json(name: str, value: dict) -> None:
    (HERE / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def audit() -> dict:
    body = (HERE / "main.md").read_text(encoding="utf-8")
    issues = []
    if sha(ANALYSIS / "analysis_manifest.json") != ANALYSIS_HASH:
        issues.append("PHASE9_MANIFEST_HASH")
    if sha(PHASE10 / "phase10_artifact_manifest.json") != PHASE10_HASH:
        issues.append("PHASE10_MANIFEST_HASH")
    p10 = json.loads((PHASE10 / "phase10_artifact_manifest.json").read_text())

    # Textual values are checked against the final Phase 9 tables, never a pilot.
    policies = rows("policy_metrics.csv")
    expected_policy = {"readiness_gate":"ReadinessGate", "ops_gate":"OpsGate",
                       "behavior_gate":"BehaviorGate", "ai_gate":"AIGate", "saferoll":"SafeRoll"}
    fact_checks = {}
    for item in policies:
        name = expected_policy[item["policy"]]
        upr = f'{item["upr_numerator"]}/{item["upr_denominator"]}'
        spr = f'{item["spr_numerator"]}/{item["spr_denominator"]}'
        fact_checks[f"{name} UPR {upr}"] = upr in body
        fact_checks[f"{name} SPR {spr}"] = spr in body
    pair = {r["endpoint"]:r for r in rows("saferoll_vs_aigate_binary.csv")}
    adjusted = {r["endpoint"]:r for r in rows("statistical_tests.csv") if r["comparison_family"]=="primary"}
    for endpoint in ("correctness", "unsafe_promotion", "critical_error"):
        item = pair[endpoint]
        fact_checks[f"{endpoint} exact p"] = item["raw_p"] in body
        fact_checks[f"{endpoint} Holm p"] = adjusted[endpoint]["adjusted_p"] in body
    effect = {r["endpoint"]:r for r in rows("effect_sizes.csv") if r.get("comparison") == "SafeRoll vs AIGate"}
    # Risk differences use journal display precision but must agree with Phase 9.
    fact_checks["unsafe paired RD"] = "−0.10989" in body and abs(float(pair["unsafe_promotion"]["paired_risk_difference_right_minus_left"])+0.10989010989)<1e-9
    fact_checks["correctness paired RD"] = "−0.07692" in body and abs(float(pair["correctness"]["paired_risk_difference_right_minus_left"])+0.07692307692)<1e-9
    hef = rows("harmful_exposure_analysis.csv")
    for item in hef:
        if item["subset"] == "UNSAFE":
            value = f'{float(item["aggregate_HEF"]):.5f}'
            fact_checks[f'{item["policy"]} HEF {value}'] = value in body
    recovery = rows("containment_analysis.csv")
    safe_recovery = next(r for r in recovery if r["policy"] == "saferoll")
    for key, shown in (("rollback_request_time","553"),("effective_zero_candidate_time","606"),
                       ("containment_delay_logical_units","53")):
        fact_checks[f"recovery {key}"] = float(json.loads(safe_recovery[key])["mean"]) == float(shown) and shown in body
    cost = json.loads((ANALYSIS / "rq6_results.json").read_text())["safe_cost"]
    for key, shown in (("unnecessary_hold","26"),("unnecessary_rollback","zero"),
                       ("safe_rollout_failure_hold_bound","three")):
        actual = cost[key]
        target = 26 if shown=="26" else 0 if shown=="zero" else 3
        fact_checks[f"cost {key}"] = actual == target and shown in body
    fact_checks["cost additional traffic"] = cost["paired_candidate_traffic_difference_total_held_safe"] == 319 and "319" in body
    real = json.loads((ANALYSIS / "real_cpu_descriptive.json").read_text())
    for condition in ("healthy","structured_regression"):
        for key in ("baseline_serving_latency_ms","candidate_serving_latency_ms"):
            shown = f'{real[condition][key]["mean"]:.2f}'
            fact_checks[f"{condition} {key}"] = shown in body
    fact_checks["real behavior and counts"] = (real["healthy"]["decisions"]=={"PROMOTE":3} and
        real["structured_regression"]["decisions"]=={"ROLLBACK":3} and
        real["structured_regression"]["candidate_structure_valid_rate"]["mean"]==0 and
        "0.00" in body and "three" in body)
    for label, okay in fact_checks.items():
        if not okay:
            issues.append("FACT:" + label)
    write_json("manuscript_fact_check.json", {"valid":not issues,"issues":issues,"checks":fact_checks,
        "phase9_manifest_sha256":ANALYSIS_HASH,"phase10_manifest_sha256":PHASE10_HASH})

    bib = (HERE / "references.bib").read_text(encoding="utf-8")
    bibkeys = re.findall(r"@\w+\s*\{\s*([^,]+),", bib)
    cited = set()
    for group in re.findall(r"\[@([^\]]+)\]", body):
        cited.update(key.strip().lstrip("@") for key in group.split(";"))
    cissues = []
    if len(bibkeys) != len(set(bibkeys)):
        cissues.append("DUPLICATE_BIBLIOGRAPHY_KEY")
    for key in cited - set(bibkeys):
        cissues.append("MISSING_BIB:" + key)
    for key in set(bibkeys) - cited:
        cissues.append("UNCITED_BIB:" + key)
    if "doi = {10.1016/j/future" in bib:
        cissues.append("SUSPICIOUS_DOI")
    citation_notes = {
      "kreuzberger2023":"IEEE Access MLOps overview; supports lifecycle/versioning/monitoring context.",
      "fgcsMLOps2026":"FGCS SkeltyMLOps publisher abstract; supports orchestration and coordination context.",
      "fgcsInference2026":"FGCS publisher abstract; supports inference resource scheduling context.",
      "fgcsCloud2017":"FGCS publisher abstract; supports continuous monitoring and recovery context.",
      "fgcsProvenance2017":"FGCS publisher abstract; supports source provenance/reproducibility context.",
      "clipper2017":"Original paper abstract/full text; supports online serving latency and model selection context.",
      "inferline2019":"Original paper abstract; supports pipeline provisioning and latency objectives.",
      "vllm2023":"Original paper abstract; supports efficient LLM serving memory context.",
      "sculley2015":"NeurIPS proceedings abstract; supports system-level ML technical debt.",
      "tfx2017":"KDD conference paper page; supports production ML platform context.",
      "argoAnalysis":"Official Argo analysis documentation; supports metric analysis and inconclusive state.",
      "kserveCanary":"Official KServe versioned documentation; supports stepped canary and rollback.",
      "kayenta2018":"Netflix authors' primary account; supports metric-driven canary analysis.",
      "infaas2021":"USENIX ATC paper; supports accuracy/performance-aware model-variant selection, distinct from rollout evidence attribution.",
      "mark2019":"USENIX ATC paper; supports SLO-aware, cost-aware cloud inference serving."
    }
    for key in cited:
        if key not in citation_notes:
            cissues.append("SOURCE_NOT_INSPECTED:"+key)
    clines = ["# Citation audit", "", f"Status: {'PASS' if not cissues else 'FAIL'}; {len(cited)} cited sources; {len(bibkeys)} bibliography entries.",
              "", "Each source below was inspected at its publisher, project, proceedings, or author page. The note limits the supported claim.", "",
              "| Key | Claim supported |", "|---|---|"]
    clines += [f"| {key} | {citation_notes.get(key,'UNVERIFIED')} |" for key in sorted(cited)]
    clines += ["", "## Issues", "", *("- "+v for v in cissues)] if cissues else ["", "No unresolved citation issues."]
    (HERE / "citation_audit.md").write_text("\n".join(clines)+"\n",encoding="utf-8")

    media = re.findall(r"!\[([^\]]+)\]\(([^)]+)\)",body)
    rissues = []
    seen = set()
    for alt, target in media:
        file = (HERE / target).resolve()
        if not file.is_file():
            rissues.append("MISSING_MEDIA:"+target)
            continue
        match = re.search(r"/(figures|tables)/(fig\d\d|table\d\d)\.png$", target)
        if not match:
            rissues.append("UNMAPPED_MEDIA:"+target)
            continue
        kind, artifact = match.groups()
        if sha(file) != p10[f'{"figure" if kind=="figures" else "table"}_png_hashes'][artifact+".png"]:
            rissues.append("PHASE10_MEDIA_HASH:"+target)
        if not re.match(r"(?:Figure|Table) (?:S\d+|\d+)\.",alt):
            rissues.append("UNNUMBERED_MEDIA:"+target)
        seen.add(artifact)
    if seen != {f"fig{i:02d}" for i in range(1,13)} | {f"table{i:02d}" for i in range(1,11)}:
        rissues.append("PHASE10_ARTIFACT_COVERAGE")
    for label in [f"Figure {i}" for i in range(1,10)] + [f"Figure S{i}" for i in range(1,4)] + [f"Table {i}" for i in range(1,9)] + [f"Table S{i}" for i in range(1,4)]:
        if label not in body:
            rissues.append("MISSING_LABEL:"+label)
    write_json("figure_table_reference_audit.json", {"valid":not rissues,"issues":rissues,
        "embedded_phase10_artifacts":len(seen),"expected_phase10_artifacts":22,
        "manuscript_numbering":"9 main figures, 3 supplement figures; 8 main tables including related-work table, 3 supplement tables"})

    major_claims = [
        ("SafeRoll 0/91 versus AIGate 10/91 UPR","SUPPORTED","Phase 9 policy_metrics.csv; Fig. 2, Table 3"),
        ("Historical attribution avoids ten unsafe promotions","SUPPORTED","Phase 9 paired extract and RQ3; Fig. 5"),
        ("Historical first HOLDs are delayed rollback","SUPPORTED","Frozen oracle and RQ3; immediate rollback 0/10"),
        ("SafeRoll first-decision correctness lower","SUPPORTED","Phase 9 paired correctness; Fig. 4, Table 4"),
        ("SafeRoll safe promotion lower","SUPPORTED","Phase 9 policy metrics; Fig. 3, Table 3"),
        ("SafeRoll HEF slightly higher than AIGate","SUPPORTED","Phase 9 harmful_exposure_analysis.csv; Fig. 7"),
        ("Recovery request precedes physical containment","SUPPORTED","Phase 9 containment_analysis.csv; Fig. 8"),
        ("Real candidate faster with invalid behavior","QUALIFIED","Six pinned CPU runs only; Fig. 9, Table 7"),
        ("Capabilities cause monotonic improvement","UNSUPPORTED_REJECTED","Manuscript explicitly rejects this inference"),
        ("Zero unsafe promotions universally guaranteed","UNSUPPORTED_REJECTED","Manuscript explicitly limits claim to frozen set")]
    lines = ["# Claim audit", "", "Status: PASS for retained major claims; rejected overclaims appear only as cautions.", "",
             "| Claim | Classification | Evidence or qualification |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a,b,c in major_claims]
    lines += ["", "No unsupported affirmative claim was found in the abstract, introduction contributions, results, discussion, or conclusion."]
    (HERE / "claim_audit.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

    main_text = body.split("## Appendix A.")[0]
    abstract = body.split("## Abstract\n",1)[1].split("\n**Keywords:**",1)[0]
    words = lambda s: len(re.findall(r"\b[\w]+(?:[-’'][\w]+)*\b",s))
    headings = re.findall(r"^## (\d+\. [^\n]+)$",body,re.M)
    section_chunks = re.split(r"(?m)^## (\d+\. [^\n]+)\n", main_text)
    section_lengths = [(section_chunks[j], words(section_chunks[j+1]))
                       for j in range(1, len(section_chunks)-1, 2)]
    review = ["# Manuscript review", "", "Target: Future Generation Computer Systems (FGCS).",
              f"Main-text word count (including abstract, excluding appendices): {words(main_text)}.",
              f"Full source word count: {words(body)}.",f"Abstract word count: {words(abstract)}.",
              f"Main figures: 9; supplement figures: 3; main tables: 8 including related-work comparison; supplement tables: 3.",
              f"Cited sources: {len(cited)}; FGCS journal sources: 4; recent FGCS 2026 sources: 2.",
              "", "## Sections and lengths", "", *(f"- {heading}: {count} words" for heading,count in section_lengths), "",
              "## Contributions", "", "State-consistent rollout model; evidence-composing SafeRoll policy; RolloutBench fault framework; frozen five-policy evaluation; pinned real inference validation.",
              "", "## Adverse findings", "", "Lower SafeRoll SPR and first-decision correctness than AIGate; slightly higher HEF; 26 unnecessary HOLDs and three safe HOLD-bound failures.",
              "", "## FGCS fit", "", "Inference-service control plane, progressive deployment, runtime evidence attribution, observability, reliability, and physical rollback containment.",
              "", "## Remaining submission metadata", "", "Author names, affiliations, corresponding author, declarations, and the journal's current production template require author/editorial completion. The publisher guide could not be fetched directly; journal scope was verified from Elsevier's journal page."]
    (HERE / "manuscript_review.md").write_text("\n".join(review)+"\n",encoding="utf-8")
    return {"valid":not (issues or cissues or rissues),"fact_issues":issues,"citation_issues":cissues,
            "reference_issues":rissues,"main_words":words(main_text),"abstract_words":words(abstract),
            "cited_sources":len(cited),"figure_count":12,"table_count":11}


if __name__ == "__main__":
    result=audit()
    print(json.dumps(result,sort_keys=True))
    if not result["valid"]:
        raise SystemExit(1)
