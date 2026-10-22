# SafeRoll manuscript package

- `main.md` is the authoritative FGCS-oriented manuscript source.
- `references.bib` holds inspected sources; `related_work_comparison.csv` is the source for manuscript Table 8.
- `review.html` is the illustrated review copy with all Phase 10 figures and tables.
- `review.docx` is a text-and-reference review copy. The macOS `textutil` converter did not embed the linked images; use HTML or the source PNG/PDF files for visual review.
- `phase11_completion_report.txt` is the copyable 31-point report.

Regenerate the HTML and audits:

```bash
python3 manuscript/build_review.py
python3 manuscript/audit.py
```

On macOS, regenerate the text review DOCX with:

```bash
textutil -convert docx -output manuscript/review.docx manuscript/review.html
```

The DOCX conversion needs the macOS helper application. Author metadata, declarations, and journal production formatting are reserved for submission preparation.
