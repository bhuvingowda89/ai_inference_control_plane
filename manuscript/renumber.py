"""Map immutable Phase 10 artifact identifiers to continuous manuscript numbers once."""
import json
from pathlib import Path
import re

root = Path(__file__).resolve().parent
path = root / "main.md"
body = path.read_text(encoding="utf-8")
if "Figure S1" in body:
    raise SystemExit("Manuscript has already been renumbered")
figures = {1:"1", 2:"S1", 3:"2", 4:"3", 5:"4", 6:"5", 7:"6", 8:"7", 9:"8", 10:"S2", 11:"S3", 12:"9"}
tables = {1:"1", 2:"2", 3:"S1", 4:"3", 5:"4", 6:"S2", 7:"5", 8:"6", 9:"7", 10:"S3", 11:"8"}
body = re.sub(r"\bFigure (\d+)\b", lambda m: "Figure " + figures[int(m.group(1))], body)
body = re.sub(r"\bTable (\d+)\b", lambda m: "Table " + tables[int(m.group(1))], body)
path.write_text(body, encoding="utf-8")
(root / "figure_table_numbering.json").write_text(json.dumps({"phase10_figure_to_manuscript":figures,
    "phase10_table_to_manuscript":tables},sort_keys=True,indent=2)+"\n",encoding="utf-8")
