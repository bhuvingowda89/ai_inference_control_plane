"""Build the Phase 12A editorial package without touching frozen evidence."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
MAN = ROOT / "manuscript"
OUT = MAN / "submission"
P10 = ROOT / "campaigns/final-v3/phase10"
P9 = ROOT / "campaigns/final-v3/analysis"
DATE = "2026-10-21"


def put(name: str, body: str) -> None:
    (OUT / name).write_text(body.strip() + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    assert digest(P9 / "analysis_manifest.json") == "b56f3a71ab5301ec0c4bc6fc08b08bf235ea693bd054c5aea4cace47b790d55c"
    assert digest(P10 / "phase10_artifact_manifest.json") == "3fbaaf831417de93000c14ecf76db9dbd58449731c184411a43dd0dea244eb64"
    main_text = (MAN / "main.md").read_text()
    abstract = main_text.split("## Abstract\n", 1)[1].split("\n**Keywords:**", 1)[0].strip()
    keywords = [x.strip() for x in main_text.split("**Keywords:**", 1)[1].split("\n", 1)[0].split(";")]
    assert len(keywords) == 6
    put("keywords.txt", "\n".join(keywords))
    highlights = [
        "Cross-epoch evidence checks avoided 10 unsafe AIGate promotions.",
        "SafeRoll made 0/91 unsafe promotions, versus 10/91 for AIGate.",
        "SafeRoll's safe promotions were 16/39, versus 26/39 for AIGate.",
        "Physical containment followed rollback request by 53 logical units.",
        "Faster real inference produced invalid structured output in 3/3 runs.",
    ]
    assert 3 <= len(highlights) <= 5 and all(len(s) <= 85 for s in highlights)
    put("highlights.txt", "\n".join("• " + s for s in highlights))
    put("highlights_audit.md", "# Highlights audit\n\nElsevier general rule: 3–5 bullets, at most 85 characters including spaces. FGCS-specific mandatory status remains unverified.\n\n" + "\n".join(f"- {len(s)} characters: {s}" for s in highlights) + "\n\n[Official Elsevier support](https://www.elsevier.support/publishing/answer/how-do-i-include-highlights-with-my-manuscript), accessed " + DATE + ".")

    sources = {
        "fgcs_scope": "https://shop.elsevier.com/journals/future-generation-computer-systems/0167-739X",
        "fgcs_guide": "https://www.sciencedirect.com/journal/future-generation-computer-systems/publish/guide-for-authors",
        "fgcs_legacy": "https://www.elsevier.com/journals/future-generation-computer-systems/0167-739X/guide-for-authors",
        "highlights": "https://www.elsevier.support/publishing/answer/how-do-i-include-highlights-with-my-manuscript",
        "graphical": "https://www.elsevier.com/researcher/author/tools-and-resources/graphical-abstract",
        "ai_policy": "https://www.elsevier.com/en-au/about/policies-and-standards/generative-ai-policies-for-journals",
        "credit": "https://www.elsevier.support/publishing/answer/how-do-i-enter-author-contributor-roles-in-editorial-manager",
        "artwork": "https://www.prod.webpresence.elsevier.com/about/policies-and-standards/author/artwork-and-media-instructions/artwork-sizing",
        "ethics": "https://www.elsevier.com/about/policies-and-standards/publishing-ethics",
    }
    requirements = [
        ("article_type", "Research article is the intended ordinary article type; exact submission-menu label requires FGCS guide/system check", "JOURNAL_UNVERIFIED"),
        ("abstract", "233 words; journal-specific maximum/structure could not be confirmed", "JOURNAL_UNVERIFIED"),
        ("keywords", "Six selected; exact FGCS maximum could not be confirmed", "JOURNAL_UNVERIFIED"),
        ("highlights", "Five prepared; 3–5 and <=85 characters each meet Elsevier general rules; FGCS mandatory status unknown", "PUBLISHER_VERIFIED_JOURNAL_UNVERIFIED"),
        ("graphical_abstract", "Design specification prepared; FGCS required/optional status unknown", "JOURNAL_UNVERIFIED"),
        ("references", "Consistent author-year keys and verified metadata; exact FGCS citation style unknown", "JOURNAL_UNVERIFIED"),
        ("figures", "Separate PDF vectors and PNG review images available; final FGCS format/upload constraints unknown", "PARTIAL"),
        ("tables", "Source CSV and PDF/PNG renderings available; final FGCS layout constraints unknown", "PARTIAL"),
        ("supplementary", "Package plan prepared; journal-specific upload categories unknown", "JOURNAL_UNVERIFIED"),
        ("source_files", "Markdown source and generated review files available; accepted production-source format needs confirmation", "PARTIAL"),
        ("data_code", "Availability statement drafted truthfully; repository release/access choice pending author", "AUTHOR_PENDING"),
        ("ai_use", "Separate disclosure before references is required for substantive AI manuscript assistance", "PUBLISHER_VERIFIED"),
        ("conflicts_funding_credit", "Templates prepared; author-specific facts pending", "AUTHOR_PENDING"),
        ("corresponding_author", "Name/contact/affiliation pending", "AUTHOR_PENDING"),
        ("cover_letter", "FGCS-specific draft prepared; originality and concurrent-submission assertion pending author", "AUTHOR_PENDING"),
        ("submission_system", "No account or upload attempted; current FGCS workflow must be checked by corresponding author", "JOURNAL_UNVERIFIED"),
    ]
    req = {"access_date": DATE, "journal_guide_access": "403 Forbidden at both official FGCS URLs; indexed snippets do not establish all current journal-specific rules", "sources": sources, "requirements": [{"item": a, "finding": b, "status": c} for a,b,c in requirements]}
    put("fgcs_requirements.json", json.dumps(req, indent=2, sort_keys=True))
    put("fgcs_requirements.md", "# FGCS requirements audit\n\nAccessed " + DATE + ". Both official FGCS guide URLs returned HTTP 403 to automated retrieval. Consequently this is an honest publisher-policy check, **not** a complete verification of journal-specific rules. Do not treat generic Elsevier or another journal's guide as FGCS policy.\n\n| Item | Finding | Status |\n|---|---|---|\n" + "\n".join(f"| {a} | {b} | {c} |" for a,b,c in requirements) + "\n\n## Official sources\n\n" + "\n".join(f"- {k}: {v}" for k,v in sources.items()) + "\n\nBefore upload, corresponding author must inspect the live FGCS guide/submission portal and close every JOURNAL_UNVERIFIED item. Elsevier's AI policy was updated in 2026 and requires a disclosure for substantive writing assistance; AI cannot be an author. General highlights rules are five or fewer bullets of 85 characters or less. Elsevier's graphical-abstract page gives minimum 1328 × 531 pixels and 300 dpi, but explicitly defers to the journal guide.")

    put("fgcs_fit_statement.md", "# Editorial fit\n\n[FGCS's official scope](https://shop.elsevier.com/journals/future-generation-computer-systems/0167-739X) covers computing infrastructure, distributed systems, cloud platforms, and infrastructure development and monitoring. SafeRoll addresses the control plane of continuously operated AI inference services. Its unit of decision is a progressively exposed serving revision, with operational signals, model behavior, revision/epoch attribution, observed routing, and effective traffic as runtime inputs. This places the work in cloud-native AI infrastructure, MLOps orchestration, model-serving reliability, and recovery. The 650-run frozen comparison and six pinned CPU runs evaluate deployment control rather than a new model algorithm or a generic testing tool. The central gap is whether evidence used to promote a revision actually belongs to that revision and to the serving state being decided upon. The manuscript also reports the cost of state-aware abstention and the gap between rollback request and physical containment.")
    put("novelty_statement.md", "# Novelty statement\n\nAmong the inspected MLOps, serving, and canary-deployment sources, SafeRoll studies a specific decision contract: bind operational and model-behavior evidence to rollout revision, epoch, and controller-visible serving state before promotion, HOLD, or rollback. Existing infrastructure may be extensible to implement pieces of this contract. The paper evaluates the contract and its safety costs; it does not claim to be the first or only possible implementation.")
    put("related_work_table_audit.md", "# Related-work Table 8 audit\n\nThe five rows retain their qualified wording. Argo Rollouts, KServe, and Kayenta have inspected first-party documentation; Clipper has an inspected original paper; SafeRoll is this study. “Not established” means the inspected source does not establish the specific decision contract, not that the system cannot be configured or extended. Argo's analysis templates can consume custom behavior metrics; KServe documents revision routing; Clipper considers online accuracy. These capabilities are explicitly acknowledged. INFaaS and MArk were added to narrative positioning, not forced into the table because neither paper evaluates progressive rollout attribution. No binary ‘No’ cell was introduced.")
    with (MAN / "related_work_comparison.csv").open(newline="") as f:
        comparison = list(csv.DictReader(f))
    with (OUT / "related_work_cell_audit.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["work", "column", "cell", "inspected_source", "interpretation_rule"])
        for row in comparison:
            for col in ("operational_signals", "behavior_signals", "progressive_deployment", "revision_epoch_attribution", "explicit_hold", "physical_containment_endpoint"):
                writer.writerow([row["work"], col, row[col], row["source_key"], "Qualified source scope; absence of documentation is not impossibility"])

    put("graphical_abstract_spec.md", "# Graphical abstract specification\n\n**Status:** design only; whether FGCS requires or accepts it remains unverified because its guide returned 403.\n\nCreate an original vector schematic, left to right: candidate revision and progressive traffic; system and behavior evidence tagged with revision/epoch; desired, observed, and effective state; SafeRoll; three possible outputs PROMOTE, HOLD, ROLLBACK. A thin bottom annotation should say ‘safer immediate promotion decisions; measurable HOLD/progress cost’ without embedding selective numeric results. No borrowed icons or figures. Do not use a general-purpose generative image tool for the graphical abstract under [Elsevier's AI policy](https://www.elsevier.com/en-au/about/policies-and-standards/generative-ai-policies-for-journals).\n\n[Elsevier's general graphical-abstract guidance](https://www.elsevier.com/researcher/author/tools-and-resources/graphical-abstract) specifies a separate file, minimum 1328 × 531 pixels, at least 300 dpi, approximately 500:200 ratio, preferred TIFF/EPS/PDF/MS Office, large readable type, and no separate heading or synopsis. Recheck against FGCS guide before production.")
    put("author_metadata_template.md", "# Author metadata — author completion required\n\n| Field | Entry |\n|---|---|\n| Full author names and order | [AUTHOR INPUT] |\n| Degrees, only if portal requests | [AUTHOR INPUT] |\n| Departments/institutions | [AUTHOR INPUT] |\n| City, state/region, country | [AUTHOR INPUT] |\n| Emails | [AUTHOR INPUT] |\n| ORCIDs, if available/requested | [AUTHOR INPUT] |\n| Corresponding author, email, postal address | [AUTHOR INPUT] |\n\nAll authors must approve the final manuscript and submission metadata.")
    put("credit_author_statement_template.md", "# CRediT author statement — assign only after author confirmation\n\n[Author full name]: [select actual roles]. Repeat for every author. Available roles: Conceptualization; Methodology; Software; Validation; Formal analysis; Investigation; Data curation; Writing – original draft; Writing – review & editing; Visualization; Supervision; Project administration; Resources; Funding acquisition. The corresponding author should confirm agreement. [Elsevier support](https://www.elsevier.support/publishing/answer/how-do-i-enter-author-contributor-roles-in-editorial-manager).")
    put("conflict_of_interest_template.md", "# Declaration of competing interest — choose after all authors confirm\n\nOption A: The authors declare that they have no known competing financial interests or personal relationships that could have appeared to influence the work reported in this paper.\n\nOption B: [AUTHOR-SUPPLIED detailed financial/personal relationships].\n\nNo option is selected yet.")
    put("funding_statement_template.md", "# Funding statement — author confirmation required\n\nOption A: This research did not receive any specific grant from funding agencies in the public, commercial, or not-for-profit sectors.\n\nOption B: This work was supported by [FUNDER] [GRANT NUMBER].\n\nNo option is selected yet. Acknowledge institutional resources if required.")
    put("data_code_availability_template.md", "# Data and code availability — author release decision required\n\nThe research workspace contains versioned campaign freezes, intervention configurations, seed design, history index, source code, validation reports, 650 final-v3 mock observations, six real-CPU observations, Phase 9 analysis scripts and manifest, and Phase 10 figure generators and manifest. Final dataset and manifest hashes are recorded in the reproducibility package. The workspace is presently local; **no public repository or DOI is asserted**.\n\nBefore submission choose one truthful route: (A) provide a persistent public archive URL/DOI and release the reviewed files; (B) state a documented access procedure and restrictions; or (C) state why release is unavailable. Do not promise release upon publication without author approval and a release plan.")
    put("ai_use_disclosure.md", "# Required generative-AI disclosure draft\n\n**Author review required.** Codex/OpenAI assisted with manuscript drafting and editing, literature triage, submission metadata, and code/document preparation in this workspace. The authors must verify the exact tools/models and scope from their records, inspect every citation and figure, edit the manuscript to reflect their own analysis, and approve the final text. Suggested section immediately before References:\n\n> **Declaration of generative AI and AI-assisted technologies in the manuscript preparation process.** During preparation of this work, the authors used [EXACT TOOL/MODEL AND VERSION] to assist with [AUTHOR-CONFIRMED TASKS]. The authors reviewed and edited the output, verified the reported evidence and cited sources, and take full responsibility for the content.\n\nDo not list AI as an author. Any AI-assisted explanatory figures require caption-level disclosure; data figures must remain traceable to reproducible code and frozen data. [Official Elsevier policy](https://www.elsevier.com/en-au/about/policies-and-standards/generative-ai-policies-for-journals), accessed " + DATE + ".")
    put("ethics_statement_template.md", "# Ethics / human-subject statement — confirm before selection\n\nThe described artifacts are simulated rollout traces and pinned local model-serving measurements; no human participants, animal subjects, clinical records, or personal-data collection are identified in the documented protocol. If authors confirm this scope, use: ‘Ethics approval and consent to participate: Not applicable.’ If any undisclosed user data or external workload was used, replace this statement after appropriate review.")
    put("cover_letter.md", "# Cover letter — draft for author approval\n\nDear Editors of *Future Generation Computer Systems*,\n\nPlease consider our research article, **‘SafeRoll: State-Consistent Progressive Deployment and Automated Rollback for AI Inference Services.’** It addresses reliable progressive deployment in AI inference infrastructure: a rollout controller needs to know not only what operational and model-behavior evidence says, but whether it belongs to the revision, epoch, and effective serving state being decided upon.\n\nSafeRoll combines system, behavioral, freshness, attribution, and state-consistency checks in a PROMOTE/HOLD/ROLLBACK control plane. We evaluated five policies in 650 frozen mock runs and separately examined six pinned real-CPU inference runs. In the tested unsafe set, SafeRoll made 0/91 unsafe promotions versus 10/91 for an AI-aware baseline. We also report the cost: safe promotions decreased from 26/39 to 16/39, first-decision correctness was lower in the historical-attribution condition, and harmful exposure was slightly higher. Real inference confirmed that a behaviorally invalid candidate can serve successfully and faster. The paper quantifies physical containment after rollback request.\n\nWe believe this systems contribution fits FGCS's interest in future-generation computing infrastructure, cloud-native AI serving, MLOps orchestration, and service reliability. The manuscript includes reproducible frozen configurations, artifact hashes, source data, and qualified comparisons to serving and canary systems.\n\n[CORRESPONDING AUTHOR TO CONFIRM: The work is original, approved by all authors, and is not under consideration elsewhere.] [CONFIRM DATA/CODE AVAILABILITY AND DECLARATIONS.]\n\nSincerely,\n\n[CORRESPONDING AUTHOR NAME, AFFILIATION, EMAIL]")
    put("reviewer_suggestions_template.md", "# Reviewer suggestions — do not populate without author review\n\nIf requested, identify independent researchers with expertise in MLOps, inference serving, cloud-native systems, runtime reliability, progressive deployment, or service orchestration. For each, record current affiliation, institutional email, expertise, recent relevant paper, no recent coauthorship, no shared grant/supervision, no close personal conflict, and geographical diversity. Do not suggest current editorial board members unless the portal asks. Names remain blank.")
    put("opposed_reviewers_template.md", "# Excluded/opposed reviewers\n\nPopulate only if the FGCS portal permits it and authors have a concrete conflict or fairness reason. [NONE PROVIDED; DO NOT INFER NAMES.]")

    figures = sorted((P10 / "figures").glob("fig*.pdf"))
    pngs = sorted((P10 / "figures").glob("fig*.png"))
    assert len(figures) == len(pngs) == 12
    dimensions = []
    for p in pngs:
        data = p.read_bytes()[:24]
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        dimensions.append((p.name, int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")))
    put("figure_production_audit.md", "# Figure production audit\n\nTwelve Phase 10 figures each have PDF vector and PNG review renderings; all source files remain untouched. PDFs should be preferred for submission if accepted. Raster DPI metadata and final journal page-size readability still need a proof view; pixel size alone does not prove production resolution. Elsevier's [artwork sizing](https://www.prod.webpresence.elsevier.com/about/policies-and-standards/author/artwork-and-media-instructions/artwork-sizing) gives 300 dpi for halftones, 500 dpi for combination art, and 1000 dpi for line art when raster is used. Color accessibility and type size need final human visual inspection at print size.\n\n| PNG | Width px | Height px | PDF present |\n|---|---:|---:|---|\n" + "\n".join(f"| {n} | {w} | {h} | yes |" for n,w,h in dimensions))
    table_src = sorted((P10 / "table_data").glob("table*.csv"))
    assert len(table_src) == 10
    with (P10 / "table_data/table04.csv").open(newline="") as f:
        overall = list(csv.DictReader(f))
    for suffix, fields in {
        "safety": ["policy","unsafe_n","unsafe_promotions","UPR","UPR_Wilson_95","safe_n","safe_promotions","SPR","SPR_Wilson_95"],
        "cost": ["policy","critical_errors","HOLD_rate","rollback_rate","harmful_exposure_fraction"],
    }.items():
        with (OUT / f"overall_policy_{suffix}_panel.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows({k:r[k] for k in fields} for r in overall)
    put("table_production_audit.md", "# Table production audit\n\nTen Phase 10 table sources have PDF and PNG outputs. The wide overall-policy table is split into `overall_policy_safety_panel.csv` and `overall_policy_cost_panel.csv`, both copied column-for-column from hashed Phase 10 `table04.csv`; no values are recomputed or omitted across the pair. Journal layout proof remains necessary for width and type size. The comparison table retains qualified cells. Source CSVs should be used for typesetting rather than raster screenshots.")
    put("format_compliance.md", "# Formatting audit\n\nThe manuscript has numbered main sections, title, 233-word abstract, six keywords, figure/table captions, equations in source Markdown, references, and appendices. Figure/table-reference audit passes and Phase 10 assets are hash checked. Separate production files (editable manuscript, bibliography, figures, tables) are available, but the current `review.docx` is text-only and must not be uploaded as a complete illustrated manuscript. A journal-ready Word/LaTeX/PDF build with embedded figures, line numbering and spacing if demanded, and confirmed FGCS citation/figure/supplement rules remains to be produced after the live journal guide can be read. Do not infer FGCS rules from another Elsevier journal.")
    put("supplement_plan.md", "# Supplement plan\n\n**Main manuscript:** nine selected figures; eight main tables including qualified related-work comparison.\n\n**Appendix/supplement:** three additional Phase 10 figures, three tables, full frozen scenario matrix, severity definitions, oracle and statistical details.\n\n**Repository artifact:** campaign freezes and plans, final-v3 data, validation reports, Phase 9/10 scripts/manifests, seed and history indexes.\n\nActual FGCS supplementary upload categories and file limits require portal verification. Do not upload raw or sensitive files until author review.")
    put("reproducibility_package.md", "# Reproducibility package index\n\n- Source: `src/`, `scripts/`, `tests/`; exact source revision/tree hash in frozen campaign manifest.\n- Freezes/plans/configuration: `campaigns/final-v3/campaign_freeze.json`, `campaign_plan.json`, seed design, history index.\n- Final observations: 650 mock rows and six separate REAL_CPU rows.\n- Validation: Phase 8 campaign and per-run reports; no exclusions or reruns.\n- Analysis: `campaigns/final-v3/analysis/` and manifest SHA-256 `b56f3a71ab5301ec0c4bc6fc08b08bf235ea693bd054c5aea4cace47b790d55c`.\n- Figures/tables: `campaigns/final-v3/phase10/` and manifest SHA-256 `3fbaaf831417de93000c14ecf76db9dbd58449731c184411a43dd0dea244eb64`.\n- Final mock dataset CSV SHA-256 `a0de3588d6aaa494f109f564ca37fb67d6d4fca533744e473d8a4a48e62e08f6`; real CSV `ad4bb3bcc096e8911b087828808ec47258a82ba395d14a9eaa581fd6e2fe903d`.\n\nThis is an index, not a public release. Assign a persistent archive URL/DOI only after author approval.")
    put("editorial_risk_audit.md", "# Editorial-risk audit\n\n| Risk | Level | Evidence and action |\n|---|---|---|\n| FGCS scope | LOW | Explicit inference-infrastructure and MLOps control-plane framing. |\n| Novelty clarity | MEDIUM | Keep specific revision/epoch/state decision contract prominent; avoid first-ever claim. |\n| Related-work breadth | MEDIUM | 15 focused sources after two additions; adequate for core comparison, but authors should judge whether more recent deployment-control papers are needed. |\n| Abstract length | MEDIUM | 233 words; FGCS-specific limit still unverified. |\n| Nine main figures | MEDIUM | Consider moving secondary figures to supplement during layout review. |\n| Real-world validation | MEDIUM | Six pinned CPU runs, n=3 per condition; state limits. |\n| Overclaiming | LOW | Adverse correctness, SPR, HEF, and HOLD costs retained and audited. |\n| Missing author declarations | HIGH | Authorship, COI, funding, originality, release decision pending. |\n| Journal guide / file format | HIGH | FGCS guide inaccessible (403); must verify portal and produce complete illustrated submission file. |")

    put("submission_metadata.yaml", """journal: Future Generation Computer Systems
article_type: research article  # provisional; confirm FGCS portal label
title: 'SafeRoll: State-Consistent Progressive Deployment and Automated Rollback for AI Inference Services'
authors: []  # AUTHOR INPUT
affiliations: []  # AUTHOR INPUT
corresponding_author: null  # AUTHOR INPUT
abstract: |-
""" + "\n".join("  " + line for line in abstract.splitlines()) + "\nkeywords:\n" + "\n".join("  - '" + k + "'" for k in keywords) + "\nhighlights:\n" + "\n".join("  - '" + h.replace("'", "''") + "'" for h in highlights) + "\nfunding: null  # AUTHOR CONFIRMATION\nconflicts: null  # AUTHOR CONFIRMATION\ndata_availability: null  # AUTHOR RELEASE DECISION\ncode_availability: null  # AUTHOR RELEASE DECISION\nAI_use: manuscript/submission/ai_use_disclosure.md  # final text requires author review\nethics: null  # AUTHOR CONFIRMATION\ncover_letter_path: manuscript/submission/cover_letter.md\ngraphical_abstract_path: null  # guide status and author choice pending\nsupplementary_files: []  # confirm portal categories\n")

    bib = (MAN / "references.bib").read_text()
    entries = re.findall(r"@(\w+)\{([^,]+),(.*?)(?=\n@|\Z)", bib, re.S)
    with (OUT / "reference_verification.csv").open("w", newline="") as f:
        writer = csv.writer(f); writer.writerow(["citation_key","title","verified","DOI","publisher_or_source","notes"])
        for typ,key,body in entries:
            field = lambda n: (re.search(r"\b"+n+r"\s*=\s*\{([^}]*)\}", body) or [None, ""])[1]
            title = field("title")
            doi = field("doi")
            url = field("url")
            writer.writerow([key,title,"publisher metadata matched title and DOI" if doi else "original source inspected",doi,url,"Verified against indexed IEEE/Elsevier publisher record on 2026-10-21; direct DOI resolution not tested" if doi else "No DOI asserted"])

    put("phase12a_review.md", "# Phase 12A review\n\n- FGCS-specific guide access: **not verified** (403); publisher-wide highlights, graphical-abstract, AI, CRediT and artwork rules checked.\n- Literature: 13 original cited sources; two inspected USENIX systems papers added (INFaaS and MArk); 15 final. No new FGCS citation added; four retained.\n- Abstract: 233 words, balanced; journal maximum unverified.\n- Keywords: six; journal maximum unverified. Highlights: five, each <=85 characters; FGCS mandatory status unverified.\n- Graphical abstract: specification prepared; journal requirement unverified.\n- Author metadata and declarations: placeholders; author confirmation required.\n- Figures/tables: original Phase 10 assets untouched; wide overall-policy table split into two source panels.\n- Main editorial risks: live journal rules, complete illustrated submission build, author declarations, real-CPU external-validity limits.\n- Science: no Phase 9/10 files modified.\n\nPhase 12A acceptance gate remains open until the live FGCS guide is verified. Author-specific placeholders are allowed in Phase 12A but must be resolved before upload.")

    checks = {"phase9_hash": True, "phase10_hash": True, "keywords_six": len(keywords)==6, "highlight_count": 3<=len(highlights)<=5, "highlight_char_limit": all(len(x)<=85 for x in highlights), "phase10_figure_pairing": len(figures)==len(pngs)==12, "phase10_table_sources": len(table_src)==10, "declaration_templates_ready": True, "journal_specific_guide_verified": False}
    put("phase12a_validation.json", json.dumps({"phase12a_gate_passed": all(checks.values()), "ready_to_upload": False, "checks": checks, "issues": [k for k,v in checks.items() if not v], "author_specific_items_pending": ["authors", "CRediT", "conflicts", "funding", "data/code release", "AI use verification", "ethics", "originality/concurrent submission"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
