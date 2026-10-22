"""Build an illustrated, self-contained HTML review copy and DOCX when available."""
from __future__ import annotations
import html
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
source = (HERE / "main.md").read_text(encoding="utf-8")
bib = (HERE / "references.bib").read_text(encoding="utf-8")
entries = {}
for match in re.finditer(r"@\w+\s*\{\s*([^,]+),", bib):
    start = match.end()
    end = bib.find("\n@", start)
    block = bib[start:] if end < 0 else bib[start:end]
    fields = dict(re.findall(r"(?m)^\s*(\w+)\s*=\s*\{([^}]*)\}", block))
    entries[match.group(1)] = fields
citation_order = []
for group in re.findall(r"\[@([^\]]+)\]", source):
    for key in (part.strip().lstrip("@") for part in group.split(";")):
        if key not in citation_order:
            citation_order.append(key)


def inline(value: str) -> str:
    value = html.escape(value)
    value = re.sub(r"\[@([^\]]+)\]", lambda m: "[" + ", ".join(
        str(citation_order.index(k.strip().lstrip("@"))+1) for k in m.group(1).split(";")) + "]", value)
    value = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"\*(.*?)\*", r"<em>\1</em>", value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m:
        f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', value)
    return value


out = ["<!doctype html><html><head><meta charset='utf-8'><title>SafeRoll manuscript review</title>",
       "<style>body{font-family:Georgia,serif;color:#223148;max-width:850px;margin:40px auto;line-height:1.5}",
       "h1,h2,h3{font-family:Arial,sans-serif;color:#223148}h1{font-size:27px}h2{font-size:21px;margin-top:35px}",
       "h3{font-size:17px;margin-top:27px}p{text-align:justify;margin:11px 0}img{max-width:100%;height:auto;display:block;margin:18px auto}",
       "table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #a8b1bc;padding:5px;vertical-align:top}",
       "th{background:#e9eef3}code{font-family:monospace;font-size:0.9em}li{margin:5px 0}",
       "@media print{body{max-width:none;margin:18mm}img{page-break-inside:avoid}h2,h3{page-break-after:avoid}}</style></head><body>"]
lines = source.splitlines()
index = 0
while index < len(lines):
    line = lines[index].strip()
    if not line:
        index += 1
        continue
    if line == "## References":
        break
    if line.startswith("#"):
        level = min(len(line)-len(line.lstrip("#")), 3)
        out.append(f"<h{level}>{inline(line[level:].strip())}</h{level}>")
        index += 1
        continue
    image = re.fullmatch(r"!\[([^]]+)\]\(([^)]+)\)",line)
    if image:
        path = (HERE / image.group(2)).resolve()
        out.append(f'<img src="{path.as_uri()}" alt="{html.escape(image.group(1))}">')
        index += 1
        continue
    if line.startswith("|"):
        table_lines = []
        while index < len(lines) and lines[index].strip().startswith("|"):
            table_lines.append(lines[index].strip())
            index += 1
        out.append("<table>")
        for row_index, raw in enumerate(table_lines):
            cells = [s.strip() for s in raw.strip("|").split("|")]
            if all(re.fullmatch(r"[-: ]+", c) for c in cells):
                continue
            tag = "th" if row_index == 0 else "td"
            out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
        out.append("</table>")
        continue
    if line.startswith("- "):
        out.append("<ul>")
        while index < len(lines) and lines[index].strip().startswith("- "):
            out.append(f"<li>{inline(lines[index].strip()[2:])}</li>")
            index += 1
        out.append("</ul>")
        continue
    block = [line]
    index += 1
    while index < len(lines) and lines[index].strip() and not lines[index].startswith(("#", "![", "|", "- ")):
        block.append(lines[index].strip())
        index += 1
    out.append("<p>" + inline(" ".join(block)) + "</p>")
out.append("<h2>References</h2><ol>")
for key in citation_order:
    entry = entries[key]
    fields = [entry.get("author", ""), entry.get("title", ""), entry.get("journal", entry.get("booktitle", "")),
              entry.get("year", ""), entry.get("doi", "")]
    label = ". ".join(part for part in fields if part)
    url = entry.get("url", "")
    out.append(f'<li>{html.escape(label)}. <a href="{html.escape(url)}">Source</a></li>')
out.append("</ol></body></html>")
html_path = HERE / "review.html"
html_path.write_text("\n".join(out)+"\n",encoding="utf-8")
print(f"HTML: {html_path}\nCitations: {len(citation_order)}")
