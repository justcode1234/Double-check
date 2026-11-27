import argparse
import base64
import html
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROJECT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MARKDOWN = ROOT / "COMPETITION_SUBMISSION_DRAFT.md"
RESULTS = OUT / "submission_assets" / "submission_results.json"


def rewrite(path):
    if path.startswith(("http://", "https://", "#", "mailto:")):
        return path
    return os.path.relpath((ROOT / path).resolve(), OUT.resolve())


def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)

    def link(match):
        label, url = match.group(1), match.group(2)
        href = html.escape(rewrite(url), quote=True)
        external = ' target="_blank" rel="noopener noreferrer"' if url.startswith(("http://", "https://")) else ""
        return f'<a href="{href}"{external}>{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def image_block(line):
    match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line.strip())
    if not match:
        return None
    alt, path = match.group(1), match.group(2)
    source = (ROOT / path).resolve()
    if source.suffix == ".svg":
        svg = re.sub(r"<\?xml[^>]+>", "", source.read_text(encoding="utf-8")).strip()
        return f'<figure class="chart"><figcaption>{html.escape(alt)}</figcaption><div class="chart-frame">{svg}</div></figure>'
    data = base64.b64encode(source.read_bytes()).decode()
    mime = "image/png" if source.suffix == ".png" else "image/jpeg"
    return f'<figure><figcaption>{html.escape(alt)}</figcaption><img src="data:{mime};base64,{data}" alt="{html.escape(alt)}"></figure>'


def parse_markdown(md):
    lines = md.splitlines()
    body, headings = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            body.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            i += 1
            continue
        image = image_block(line)
        if image:
            body.append(image)
            i += 1
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            if level == 1 and not body:
                i += 1
                continue
            hid = slug(text)
            headings.append((level, text, hid))
            body.append(f'<h{level} id="{hid}">{inline(text)}</h{level}>')
            if text.startswith("7. What worked"):
                data = base64.b64encode((OUT / "submission_assets" / "dashboard-viewport.png").read_bytes()).decode()
                body.append('<section class="proof"><h3>Current dashboard evidence</h3><p>Read-only competition dashboard captured 26 November 2026. The runner remains the sole order authority.</p><img src="data:image/png;base64,' + data + '" alt="Current Moo-Moo competition dashboard"></section>')
            i += 1
            continue
        if line.strip() == "---":
            body.append("<hr>")
            i += 1
            continue
        if line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i][1:].lstrip())
                i += 1
            body.append("<blockquote>" + inline(" ".join(quote)) + "</blockquote>")
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-+", lines[i + 1]):
            headers = [cell.strip() for cell in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([cell.strip() for cell in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{inline(cell)}</th>" for cell in headers)
            trs = "".join("<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>" for row in rows)
            body.append(f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>')
            continue
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i]))
                i += 1
            body.append("<ol>" + "".join(f"<li>{inline(item)}</li>" for item in items) + "</ol>")
            continue
        if line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:])
                i += 1
            body.append("<ul>" + "".join(f"<li>{inline(item)}</li>" for item in items) + "</ul>")
            continue
        paragraph = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", ">", "```", "|", "- ")) and not re.match(r"^\d+\.\s+", lines[i]) and not image_block(lines[i]):
            paragraph.append(lines[i].strip())
            i += 1
        body.append("<p>" + inline(" ".join(paragraph)) + "</p>")
    return body, headings


CSS = """
:root{--bg:#0B1220;--panel:#111C2E;--text:#F8FAFC;--muted:#94A3B8;--line:#334155;--teal:#2DD4BF;--amber:#F59E0B;--red:#F87171}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.65}
a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}code{background:#020617;border:1px solid var(--line);border-radius:6px;padding:.12rem .35rem;color:#BAE6FD;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.9em}pre{background:#020617;border:1px solid var(--line);border-radius:14px;padding:18px;overflow:auto}pre code{border:0;background:none;padding:0;color:#BAE6FD}
.layout{display:grid;grid-template-columns:270px minmax(0,1fr);max-width:1440px;margin:auto;gap:34px;padding:34px}nav{position:sticky;top:24px;align-self:start;max-height:calc(100vh - 48px);overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px}nav .brand{font-weight:800;letter-spacing:.08em;color:var(--teal);margin-bottom:14px}nav a{display:block;color:var(--muted);font-size:13px;padding:5px 0}nav a:hover{color:var(--text)}nav .toc-l2{font-weight:700;color:#CBD5E1;margin-top:8px}nav .toc-l3{padding-left:10px;font-size:12px}
main{min-width:0}h1{font-size:44px;line-height:1.08;margin:10px 0 18px}h2{font-size:29px;line-height:1.2;margin:54px 0 18px;padding-top:16px;border-top:1px solid var(--line)}h3{font-size:21px;margin:30px 0 12px}h4{font-size:17px}p{margin:12px 0}ul,ol{padding-left:22px}li{margin:7px 0}blockquote{margin:22px 0;background:rgba(45,212,191,.08);border-left:4px solid var(--teal);padding:16px 18px;border-radius:0 12px 12px 0;color:#CBD5E1}hr{border:0;border-top:1px solid var(--line);margin:34px 0}
.hero{background:linear-gradient(135deg,#111C2E,#0F172A);border:1px solid var(--line);border-radius:22px;padding:30px;margin-bottom:26px}.eyebrow{color:var(--teal);font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.badge{display:inline-block;background:rgba(245,158,11,.12);border:1px solid var(--amber);color:var(--amber);border-radius:999px;padding:4px 10px;font-size:12px;font-weight:700;margin-bottom:12px}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:24px}.kpi{background:#0B1220;border:1px solid var(--line);border-radius:14px;padding:15px}.kpi span{display:block;color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.05em}.kpi strong{display:block;font-size:24px;margin-top:5px}.negative{color:var(--red)}.positive{color:var(--teal)}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:14px;margin:18px 0}table{border-collapse:collapse;width:100%;min-width:640px;background:var(--panel)}th,td{text-align:left;padding:11px 13px;border-bottom:1px solid var(--line);vertical-align:top}th{background:#0F172A;color:#CBD5E1;font-size:12px;letter-spacing:.03em}tr:last-child td{border-bottom:0}
figure{margin:24px 0;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px}figcaption{color:var(--muted);font-size:12px;margin-bottom:10px}.chart-frame svg{display:block;width:100%;height:auto}figure img,.proof img{display:block;width:100%;height:auto;border-radius:10px}.proof{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;margin:24px 0}.proof p{color:var(--muted)}
.footer{margin-top:60px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
.downloads{margin:26px 0;padding:22px;background:var(--panel);border:1px solid var(--line);border-radius:16px}.downloads h2{margin:0 0 8px;padding:0;border:0;font-size:24px}.downloads p{color:var(--muted);margin:0 0 16px}.download-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.download-grid a{display:block;padding:12px 14px;background:#0B1220;border:1px solid var(--line);border-radius:10px;color:var(--teal);font-weight:700}.download-grid a:hover{border-color:var(--teal);text-decoration:none}@media(max-width:640px){.download-grid{grid-template-columns:1fr}}
@media(max-width:980px){.layout{grid-template-columns:1fr;padding:20px}nav{position:static;max-height:260px;overflow:auto}.kpis{grid-template-columns:repeat(2,1fr)}h1{font-size:36px}}
@media print{body{background:white;color:#111827}.layout{display:block;padding:0}nav{display:none}.hero,figure,.proof,blockquote,.table-wrap{break-inside:avoid}.hero,figure,.proof,blockquote{background:white;border-color:#CBD5E1}a{color:#0F766E}.kpi{background:white;border-color:#CBD5EE}.chart-frame svg{background:#0B1220}h2{break-after:avoid}}
"""


def render(body, headings, results, final=False):
    nav = "".join(f'<a class="toc-l{level}" href="#{hid}">{html.escape(text)}</a>' for level, text, hid in headings if level <= 3)
    account = results["account"]
    badge = "Final · 27 Nov 2026 session reconciled" if final else "Draft v0.3 · final 27 Nov session pending"
    footer = "Generated with Qoder Desktop · broker-reconciled paper-trading evidence · no live capital · final submission." if final else "Generated with Qoder Desktop · broker-reconciled paper-trading evidence · no live capital · report and package remain draft until the 27 November 2026 final session is reconciled."
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Moo-Moo Competition Submission Report</title><style>{CSS}</style></head><body><div class="layout"><nav><div class="brand">MOO-MOO / QODER</div>{nav}</nav><main><section class="hero" id="moo-moo-adaptive-two-vote-etf-agent"><div class="eyebrow">Moomoo × Qoder AI Trading Agent Challenge</div><h1>Moo-Moo<br>Adaptive Two-Vote ETF Agent</h1><div class="badge">{badge}</div><p>Ten technical rules. One deterministic decision. Every order reconciled before the next.</p><div class="kpis"><div class="kpi"><span>CURRENT NAV</span><strong>${account['nav']:,.2f}</strong></div><div class="kpi"><span>NET CHANGE</span><strong class="negative">−${abs(account['net_change_usd']):,.2f}</strong></div><div class="kpi"><span>DRAWDOWN</span><strong>{account['drawdown_pct']:.4f}%</strong></div><div class="kpi"><span>ORDERS MATCHED</span><strong>30 / 30</strong></div></div></section>{''.join(body)}<div class="footer">{footer}</div></main></div></body></html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    body, headings = parse_markdown(MARKDOWN.read_text(encoding="utf-8"))
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    page = render(body, headings, results, final=args.final)
    (OUT / "Moo-Moo-Submission-Report.html").write_text(page, encoding="utf-8")
    hosted = re.sub(r'<a href="(?!https?:|#)[^"]+"[^>]*>(.*?)</a>', r"\1", page)
    package_mb = (OUT / "Moo-Moo-Runnable-Package.zip").stat().st_size / 1_000_000
    downloads = f'''<section class="downloads"><h2>Submission downloads</h2><p>Editable deck, rendered PDF, lightweight runnable source package and public repository. The lightweight package omits the largest historical cache; the full cache remains in GitHub.</p><div class="download-grid"><a href="./Moo-Moo-Submission-Draft.pptx" download>Editable PowerPoint deck</a><a href="./Moo-Moo-Submission-Draft.pdf" download>Rendered PDF deck</a><a href="./Moo-Moo-Runnable-Package.zip" download>Runnable source package · {package_mb:.1f} MB</a><a href="./Moo-Moo-Runnable-Package-Manifest.json" download>Package manifest</a><a href="./Moo-Moo-Runnable-Package.sha256" download>Package SHA-256</a><a href="https://github.com/justcode1234/Moo-Moo" target="_blank" rel="noopener noreferrer">GitHub repository + full cache</a></div></section>'''
    hosted = hosted.replace("</section>", "</section>" + downloads, 1)
    (OUT / "Moo-Moo-Submission-Report-Hosted.html").write_text(hosted, encoding="utf-8")
    (OUT / "dist").mkdir(exist_ok=True)
    (OUT / "dist" / "index.html").write_text(hosted, encoding="utf-8")
    for name in ("Moo-Moo-Submission-Draft.pptx", "Moo-Moo-Submission-Draft.pdf", "Moo-Moo-Runnable-Package.zip", "Moo-Moo-Runnable-Package-Manifest.json", "Moo-Moo-Runnable-Package.sha256"):
        shutil.copy2(OUT / name, OUT / "dist" / name)
    print(json.dumps({"headings": len(headings), "figures": page.count("<figure"), "html_bytes": len(page.encode()), "hosted_bytes": len(hosted.encode())}, indent=2))


if __name__ == "__main__":
    main()
