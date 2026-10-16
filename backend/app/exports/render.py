"""The report renderers: Markdown, one-file HTML, PDF, Word and Excel, each drawing the same report model in the
chosen layout, palette and mascots. Charts are horizontal bars with their labels and values written beside them,
so every figure is readable without colour; every report ends with its sources and the not-advice line."""

from __future__ import annotations

import html as _html
import io
import re
from typing import Any

from . import safe
from .templates import LAYOUTS, MASCOTS, draw_pdf, part_colours, png, set_svg

# ---- shared --------------------------------------------------------------------------------------------------

CSS_REFUSED = re.compile(r"</|<|\\|@import|url\s*\(|image-set\s*\(|image\s*\(|expression\s*\(|javascript:|behavior\s*:|-moz-binding", re.IGNORECASE)


def clean_css(css: str) -> str:
    """The owner's extra CSS for the HTML layout: kept to plain declarations, never a fetch or a script."""
    css = (css or "")[:20000]
    return "" if CSS_REFUSED.search(css) else css


def _layout(layout: str) -> dict:
    return LAYOUTS.get(layout, LAYOUTS["board"])


def _max(bars: list[dict]) -> float:
    return max([b["value"] for b in bars] + [1.0])


def _usd(v: float) -> str:
    return f"${v:,.0f}"


# ---- Markdown ------------------------------------------------------------------------------------------------

ASCII = {
    "kitties": "```\n /\\_/\\     /\\_/\\\n( o.o )   ( -.- )\n > ^ <     > ^ <\n```",
    "forest": "```\n   .-''-.      *  .  *\n  / o  o \\    ( o o )\n |  .--.  |    *  .  *\n  \\ '--' /\n   '-..-'\n```",
    "sprites": "```\n *  ( o o )  *   ( o o )   *\n```",
    "acorns": "```\n  _      _\n (_)    (_)    ( o o )\n /_\\    /_\\\n```",
}


def markdown(report: dict, layout: str = "board", palette: str = "layout", mascot: str | None = None) -> str:
    L = _layout(layout)
    mascot = mascot if mascot is not None else L["mascot"]
    rule = "-" * 40 if layout in ("terminal", "receipt") else "---"
    lines = [f"# {report['title']}", "", f"*{report['as_of_words']} · {report['brand']}*", ""]
    if mascot in ASCII:
        lines += [ASCII[mascot], ""]
    for s in report["sections"]:
        lines += [f"## {s['title']}", ""]
        if s["lead"]:
            lines += [s["lead"], ""]
        for k in s["kpis"]:
            lines.append(f"- **{k['label']}:** {k['value']}" + (f" ({k['note']})" if k.get("note") else ""))
        if s["kpis"]:
            lines.append("")
        if s["bars"] and L["charts"]:
            top = _max(s["bars"])
            width = max(len(b["label"]) for b in s["bars"])
            lines.append("```")
            for b in s["bars"]:
                n = max(1, round(28 * b["value"] / top))
                lines.append(f"{b['label'].ljust(width)}  {'█' * n}{' ' * (28 - n)}  {_usd(b['value'])}")
            lines += ["```", ""]
        t = s.get("table")
        if t and t["rows"]:
            lines.append("| " + " | ".join(t["columns"]) + " |")
            lines.append("|" + "|".join("---:" if a == "r" else "---" for a in t["align"]) + "|")
            for r in t["rows"]:
                lines.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
            lines.append("")
        for n in s["notes"]:
            lines.append(f"- {n}" if not n.endswith(":") else f"**{n}**")
        if s["notes"]:
            lines.append("")
    lines += [rule, "", f"*Sources: {report['sources']}.*", "", f"*{report['not_advice']}*", ""]
    return "\n".join(lines)


# ---- HTML ----------------------------------------------------------------------------------------------------

def _bars_svg(bars: list[dict], colours: dict, C: dict, font: str) -> str:
    top = _max(bars)
    row = 26
    w = 640
    label_w = 190
    val_w = 90
    track = w - label_w - val_w
    h = row * len(bars) + 4
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{_html.escape(", ".join(f"{b["label"]} {_usd(b["value"])}" for b in bars))}" style="max-width:{w}px;display:block">']
    for i, b in enumerate(bars):
        y = 2 + i * row
        bw = max(2.0, track * b["value"] / top)
        fill = colours.get(b.get("role", ""), C["accent"])
        out.append(f'<text x="0" y="{y + 16}" font-size="12.5" fill="{C["ink"]}" font-family="{_html.escape(font)}">{_html.escape(b["label"][:28])}</text>')
        out.append(f'<rect x="{label_w}" y="{y + 4}" width="{track}" height="16" rx="4" fill="{C["track"]}"/>')
        out.append(f'<rect x="{label_w}" y="{y + 4}" width="{bw:.1f}" height="16" rx="4" fill="{fill}"/>')
        out.append(f'<text x="{w}" y="{y + 16}" font-size="12.5" font-weight="700" text-anchor="end" fill="{C["ink"]}" font-family="{_html.escape(font)}">{_usd(b["value"])}</text>')
    out.append("</svg>")
    return "".join(out)


def html(report: dict, layout: str = "board", palette: str = "layout", mascot: str | None = None, css: str = "") -> str:
    L = _layout(layout)
    C = L["colours"]
    F = L["fonts"]
    colours = part_colours(L, palette)
    mascot = mascot if mascot is not None else L["mascot"]
    e = _html.escape
    decor = {
        "grid": "background-image:linear-gradient(rgba(255,255,255,.06) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.06) 1px,transparent 1px);background-size:16px 16px;",
        "scan": "background-image:repeating-linear-gradient(0deg,rgba(0,0,0,.28) 0 1px,transparent 1px 3px);",
        "horizon": "background:linear-gradient(#160830,#2a0f4a 55%,#3a1260);",
    }.get(L.get("decor", ""), "")
    width = "440px" if layout == "receipt" else "880px"
    style = f"""
:root{{--bg:{C['bg']};--card:{C['card']};--ink:{C['ink']};--muted:{C['muted']};--accent:{C['accent']};--rule:{C['rule']};--head-bg:{C['head_bg']};--head-ink:{C['head_ink']};--track:{C['track']}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 {F['body']};{decor}}}
.page{{max-width:{width};margin:0 auto;padding:28px 20px 40px}}
header{{display:flex;align-items:flex-end;gap:16px;justify-content:space-between;background:var(--head-bg);color:var(--head-ink);padding:22px 22px 18px;border-radius:{'0' if layout in ('ledger','dossier','receipt','terminal','blueprint') else '14px'};border-bottom:{'3px double var(--rule)' if layout=='ledger' else '0'}}}
h1{{font-family:{F['display']};font-size:{'30px' if layout in ('forest','kitties','meadow') else '24px'};margin:0;line-height:1.15}}
.sub{{font-size:13px;opacity:.85;margin-top:4px}}
.mascots{{display:flex;align-items:flex-end;gap:6px;flex-shrink:0}}
section{{margin-top:26px;padding-top:14px;border-top:1px {'dashed' if layout=='receipt' else 'solid'} var(--rule)}}
h2{{font-family:{F['display']};font-size:{'22px' if layout in ('forest','kitties','meadow') else '18px'};margin:0 0 6px;color:var(--accent)}}
.lead{{margin:0 0 12px;max-width:70ch}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 14px}}
.kpi{{background:var(--card);border:1px solid var(--rule);border-radius:{'0' if layout in ('ledger','dossier','receipt','terminal','blueprint') else '12px'};padding:10px 12px}}
.kpi .l{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}}
.kpi .v{{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums}}
.kpi .n{{font-size:12px;color:var(--muted)}}
.tablewrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}}
th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);border-bottom:{'2px solid var(--rule)' if layout=='ledger' else '1px solid var(--rule)'};padding:6px 8px}}
td{{padding:6px 8px;border-bottom:1px {'dotted' if layout in ('kitties','receipt') else 'solid'} var(--rule)}}
td.r,th.r{{text-align:right}}
ul.notes{{padding-left:18px;margin:8px 0 0;font-size:13px}}
{"ul.notes li::marker{content:'🐾 '}" if layout == 'kitties' else ''}
footer{{margin-top:30px;font-size:12px;color:var(--muted);border-top:1px solid var(--rule);padding-top:10px}}
{"body{text-align:left} .page{font-family:" + F['mono'] + "} header{flex-direction:column;align-items:center;text-align:center} h1{letter-spacing:.2em;text-transform:uppercase}" if layout == 'receipt' else ''}
{"h1,h2{text-transform:uppercase;letter-spacing:.08em} h1::before{content:'> '}" if layout == 'terminal' else ''}
{"h1{text-shadow:0 0 12px var(--accent)} h2{text-shadow:0 0 8px var(--accent)}" if layout == 'horizon' else ''}
{"header{border:1px solid var(--rule)} h1{text-transform:uppercase;letter-spacing:.1em}" if layout == 'blueprint' else ''}
@media print{{body{{background:var(--bg)}} section{{break-inside:avoid}}}}
{clean_css(css)}
"""
    parts = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(report["title"])}</title><style>{style}</style></head><body><div class="page">']
    parts.append(f'<header><div><h1>{e(report["title"])}</h1><div class="sub">{e(report["as_of_words"])} · {e(report["brand"])}</div></div><div class="mascots">{set_svg(mascot)}</div></header>')
    for s in report["sections"]:
        parts.append(f'<section><h2>{e(s["title"])}</h2>')
        if s["lead"]:
            parts.append(f'<p class="lead">{e(s["lead"])}</p>')
        if s["kpis"]:
            parts.append('<div class="kpis">' + "".join(f'<div class="kpi"><div class="l">{e(k["label"])}</div><div class="v">{e(k["value"])}</div><div class="n">{e(k.get("note", ""))}</div></div>' for k in s["kpis"]) + "</div>")
        if s["bars"] and L["charts"]:
            parts.append(_bars_svg(s["bars"], colours, C, F["body"]))
        t = s.get("table")
        if t and t["rows"]:
            head = "".join(f'<th class="{"r" if a == "r" else ""}">{e(c)}</th>' for c, a in zip(t["columns"], t["align"]))
            body = "".join("<tr>" + "".join(f'<td class="{"r" if a == "r" else ""}">{e(str(c))}</td>' for c, a in zip(r, t["align"])) + "</tr>" for r in t["rows"])
            parts.append(f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
        if s["notes"]:
            parts.append('<ul class="notes">' + "".join(f"<li>{e(n)}</li>" for n in s["notes"] if not n.endswith(":")) + "</ul>")
        parts.append("</section>")
    parts.append(f'<footer>Sources: {e(report["sources"])}.<br>{e(report["not_advice"])}</footer></div></body></html>')
    return "".join(parts)


# ---- PDF -----------------------------------------------------------------------------------------------------

_PDF_SUBS = {"−": "-", "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "→": "->", "≤": "<=", "≥": ">=", "•": "*", " ": " "}


def _latin(text: str) -> str:
    text = "".join(_PDF_SUBS.get(ch, ch) for ch in str(text))
    return text.encode("latin-1", "replace").decode("latin-1")


def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def pdf(report: dict, layout: str = "board", palette: str = "layout", mascot: str | None = None) -> bytes:
    from fpdf import FPDF, XPos, YPos
    from fpdf.fonts import FontFace

    L = _layout(layout)
    C = L["colours"]
    colours = part_colours(L, palette)
    mascot = mascot if mascot is not None else L["mascot"]
    display, body = L["pdf"]["display"], L["pdf"]["body"]
    not_advice = _latin(report["not_advice"])

    class Doc(FPDF):
        def header(self) -> None:
            if L["dark"] or C["bg"].lower() not in ("#ffffff",):
                self.set_fill_color(*_rgb(C["bg"]))
                self.rect(0, 0, self.w, self.h, style="F")

        def footer(self) -> None:
            self.set_y(-34)
            self.set_font(body, size=7.5)
            self.set_text_color(*_rgb(C["muted"]))
            self.cell(0, 10, f"{not_advice}   Page {self.page_no()}", align="C")

    doc = Doc(orientation="P", unit="pt", format="Letter")
    doc.set_margins(42, 40, 42)
    doc.set_auto_page_break(auto=True, margin=50)
    doc.add_page()
    W = doc.w - 84
    # The title band.
    doc.set_fill_color(*_rgb(C["head_bg"]))
    doc.rect(0, 0, doc.w, 92, style="F")
    figures = MASCOTS.get(mascot, MASCOTS["none"])["figures"]
    x = doc.w - 42
    for fig, size, mirror in reversed(figures):
        h = size * fig["box"][1] / fig["box"][0]
        x -= size
        draw_pdf(doc, fig, x, 84 - h, size, mirror)
        x -= 4
    doc.set_xy(42, 28)
    doc.set_text_color(*_rgb(C["head_ink"]))
    doc.set_font(display, style="B", size=20)
    doc.multi_cell(W - 150, 22, _latin(report["title"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    doc.set_font(body, size=9.5)
    doc.cell(0, 14, _latin(f"{report['as_of_words']} - {report['brand']}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    doc.set_y(110)
    for s in report["sections"]:
        if doc.get_y() > doc.h - 160:
            doc.add_page()
        doc.set_draw_color(*_rgb(C["rule"]))
        doc.line(42, doc.get_y(), doc.w - 42, doc.get_y())
        doc.ln(8)
        doc.set_text_color(*_rgb(C["accent"]))
        doc.set_font(display, style="B", size=14)
        doc.cell(0, 18, _latin(s["title"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        doc.set_text_color(*_rgb(C["ink"]))
        if s["lead"]:
            doc.set_font(body, size=9.5)
            doc.multi_cell(W, 13, _latin(s["lead"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            doc.ln(4)
        if s["kpis"]:
            n = min(4, len(s["kpis"]))
            bw = (W - 8 * (n - 1)) / n
            y0 = doc.get_y()
            for i, k in enumerate(s["kpis"][:4]):
                bx = 42 + i * (bw + 8)
                doc.set_fill_color(*_rgb(C["card"]))
                doc.set_draw_color(*_rgb(C["rule"]))
                doc.rect(bx, y0, bw, 46, style="DF")
                doc.set_xy(bx + 7, y0 + 5)
                doc.set_font(body, size=7)
                doc.set_text_color(*_rgb(C["muted"]))
                doc.cell(bw - 14, 9, _latin(k["label"].upper()))
                doc.set_xy(bx + 7, y0 + 15)
                doc.set_font(body, style="B", size=14)
                doc.set_text_color(*_rgb(C["ink"]))
                doc.cell(bw - 14, 16, _latin(k["value"]))
                doc.set_xy(bx + 7, y0 + 32)
                doc.set_font(body, size=7)
                doc.set_text_color(*_rgb(C["muted"]))
                doc.cell(bw - 14, 9, _latin(k.get("note", ""))[:48])
            doc.set_y(y0 + 54)
            doc.set_text_color(*_rgb(C["ink"]))
        if s["bars"] and L["charts"]:
            top = _max(s["bars"])
            for b in s["bars"]:
                if doc.get_y() > doc.h - 70:
                    doc.add_page()
                y = doc.get_y()
                doc.set_font(body, size=8.5)
                doc.set_xy(42, y)
                doc.cell(140, 14, _latin(b["label"][:30]))
                doc.set_fill_color(*_rgb(C["track"]))
                doc.rect(186, y + 3, W - 214, 9, style="F")
                doc.set_fill_color(*_rgb(colours.get(b.get("role", ""), C["accent"])))
                doc.rect(186, y + 3, max(1.5, (W - 214) * b["value"] / top), 9, style="F")
                doc.set_xy(doc.w - 42 - 70, y)
                doc.set_font(body, style="B", size=8.5)
                doc.cell(70, 14, _usd(b["value"]), align="R")
                doc.set_y(y + 15)
            doc.ln(4)
        t = s.get("table")
        if t and t["rows"]:
            doc.set_font(body, size=8)
            heads = FontFace(emphasis="BOLD", color=_rgb(C["head_ink"] if C["head_bg"] != C["bg"] else C["ink"]), fill_color=_rgb(C["head_bg"] if C["head_bg"] != C["bg"] else C["track"]))
            doc.set_fill_color(*_rgb(C["bg"]))
            doc.set_text_color(*_rgb(C["ink"]))
            with doc.table(width=W, text_align=tuple("RIGHT" if a == "r" else "LEFT" for a in t["align"]), headings_style=heads, line_height=11, borders_layout="HORIZONTAL_LINES", padding=3,
                           cell_fill_color=_rgb(C["card"] if C["card"] != C["bg"] else C["track"]), cell_fill_mode="ROWS") as table:
                head = table.row()
                for c in t["columns"]:
                    head.cell(_latin(c))
                for r in t["rows"]:
                    row = table.row()
                    for c in r:
                        row.cell(_latin(str(c)))
            doc.ln(6)
        for note in s["notes"]:
            doc.set_font(body, style="B" if note.endswith(":") else "", size=8.5)
            doc.multi_cell(W, 11, _latin(("" if note.endswith(":") else "- ") + note), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        doc.ln(8)
    doc.set_font(body, size=8)
    doc.set_text_color(*_rgb(C["muted"]))
    doc.multi_cell(W, 11, _latin(f"Sources: {report['sources']}."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(doc.output())


# ---- Word ----------------------------------------------------------------------------------------------------

def _bar_png(bars: list[dict], colours: dict, C: dict) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    row = 34
    w = 1200
    label_w, val_w = 360, 170
    h = row * len(bars) + 10
    bg = "#ffffff"
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:
        font = ImageFont.load_default()
    top = _max(bars)
    track_w = w - label_w - val_w
    ink = C["ink"] if not C.get("dark_ink") else C["dark_ink"]
    for i, b in enumerate(bars):
        y = 5 + i * row
        d.text((0, y + 4), b["label"][:30], fill=ink if _is_dark_on_white(ink) else "#1a1a1a", font=font)
        d.rounded_rectangle([label_w, y + 6, label_w + track_w, y + 26], radius=6, fill="#eeeeee")
        d.rounded_rectangle([label_w, y + 6, label_w + max(4, track_w * b["value"] / top), y + 26], radius=6, fill=colours.get(b.get("role", ""), C["accent"]))
        d.text((w - 6, y + 4), _usd(b["value"]), fill="#1a1a1a", font=font, anchor="ra")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _is_dark_on_white(hex_: str) -> bool:
    r, g, b = _rgb(hex_)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 140


def docx(report: dict, layout: str = "board", palette: str = "layout", mascot: str | None = None) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    L = _layout(layout)
    C = L["colours"]
    colours = part_colours(L, palette)
    mascot = mascot if mascot is not None else L["mascot"]
    # Word uses light paper; a dark layout keeps its accents on it.
    accent = C["accent"] if _is_dark_on_white(C["accent"]) else ("#2a78d6" if layout == "blueprint" else "#8a2a6a" if layout == "horizon" else "#7a5300")
    ink = C["ink"] if _is_dark_on_white(C["ink"]) else "#1a1a1a"
    family = {"Times": "Georgia", "Courier": "Consolas", "Helvetica": "Segoe UI"}[L["pdf"]["body"]]
    if layout in ("forest", "kitties", "meadow"):
        family = "Nunito"
    doc = Document()
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Inches(0.8)
        sec.top_margin = sec.bottom_margin = Inches(0.7)
    normal = doc.styles["Normal"]
    normal.font.name = family
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), family)
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor.from_string(ink.lstrip("#").upper())

    def colour(run, hex_: str) -> None:
        run.font.color.rgb = RGBColor.from_string(hex_.lstrip("#").upper())

    title = doc.add_paragraph()
    r = title.add_run(report["title"])
    r.bold = True
    r.font.size = Pt(22)
    colour(r, accent)
    sub = doc.add_paragraph()
    colour(sub.add_run(f"{report['as_of_words']} · {report['brand']}"), C["muted"] if _is_dark_on_white(C["muted"]) else "#555555")
    pic = png(mascot, height=140)
    if pic:
        doc.add_picture(io.BytesIO(pic), height=Inches(0.9))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.RIGHT

    def shade(cell, hex_: str) -> None:
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), hex_.lstrip("#").upper())
        tcPr.append(shd)

    for s in report["sections"]:
        h = doc.add_paragraph()
        hr = h.add_run(s["title"])
        hr.bold = True
        hr.font.size = Pt(15)
        colour(hr, accent)
        if s["lead"]:
            doc.add_paragraph(s["lead"])
        if s["kpis"]:
            t = doc.add_table(rows=2, cols=len(s["kpis"]))
            t.style = "Table Grid"
            for i, k in enumerate(s["kpis"]):
                lab = t.rows[0].cells[i].paragraphs[0].add_run(k["label"].upper())
                lab.font.size = Pt(8)
                val = t.rows[1].cells[i].paragraphs[0].add_run(k["value"])
                val.bold = True
                val.font.size = Pt(14)
                if k.get("note"):
                    note = t.rows[1].cells[i].add_paragraph().add_run(k["note"])
                    note.font.size = Pt(8)
            doc.add_paragraph()
        if s["bars"] and L["charts"]:
            doc.add_picture(io.BytesIO(_bar_png(s["bars"], colours, C)), width=Inches(6.4))
        t = s.get("table")
        if t and t["rows"]:
            table = doc.add_table(rows=1 + len(t["rows"]), cols=len(t["columns"]))
            table.style = "Table Grid"
            for i, (c, a) in enumerate(zip(t["columns"], t["align"])):
                cell = table.rows[0].cells[i]
                shade(cell, accent)
                run = cell.paragraphs[0].add_run(c)
                run.bold = True
                run.font.size = Pt(8.5)
                colour(run, "#ffffff")
                if a == "r":
                    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
            for ri, row in enumerate(t["rows"], start=1):
                for i, (c, a) in enumerate(zip(row, t["align"])):
                    p = table.rows[ri].cells[i].paragraphs[0]
                    run = p.add_run(str(c))
                    run.font.size = Pt(9)
                    if a == "r":
                        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            doc.add_paragraph()
        for note in s["notes"]:
            if note.endswith(":"):
                doc.add_paragraph().add_run(note).bold = True
            else:
                doc.add_paragraph(note, style="List Bullet")
    foot = doc.add_paragraph()
    fr = foot.add_run(f"Sources: {report['sources']}. {report['not_advice']}")
    fr.font.size = Pt(8)
    colour(fr, "#666666")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# ---- Excel ---------------------------------------------------------------------------------------------------

def xlsx(report: dict, regs: dict[str, list[dict]], layout: str = "board", palette: str = "layout", mascot: str | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.chart.series import DataPoint
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    L = _layout(layout)
    C = L["colours"]
    colours = part_colours(L, palette)
    mascot = mascot if mascot is not None else L["mascot"]
    accent = (C["accent"] if _is_dark_on_white(C["accent"]) else "#2a78d6").lstrip("#").upper()
    money = '"$"#,##0.00'
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor=accent)
    wb = Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws["A1"] = safe.cell(report["title"])
    ws["A1"].font = Font(bold=True, size=16, color=accent)
    ws["A2"] = f"{report['as_of_words']} · {report['brand']}"
    row = 4
    for s in report["sections"]:
        if s["key"] == "summary":
            for k in s["kpis"]:
                safe.put(ws, row=row, column=1, value=k["label"]).font = Font(bold=True)
                safe.put(ws, row=row, column=2, value=k["value"])
                safe.put(ws, row=row, column=3, value=k.get("note", ""))
                row += 1
            row += 1
    check = next((s for s in report["sections"] if s["key"] == "this_check"), None)
    if check and check["bars"]:
        safe.put(ws, row=row, column=1, value=check["title"]).font = Font(bold=True, size=13, color=accent)
        row += 1
        first = row
        for i, h in enumerate(["Part", "Amount"], start=1):
            c = safe.put(ws, row=row, column=i, value=h)
            c.font, c.fill = head_font, head_fill
        for b in check["bars"]:
            row += 1
            safe.put(ws, row=row, column=1, value=b["label"])
            ws.cell(row=row, column=2, value=round(b["value"], 2)).number_format = money
        last = row
        row += 1
        ws.cell(row=row, column=1, value="Total").font = Font(bold=True)
        total = ws.cell(row=row, column=2, value=f"=SUM(B{first + 1}:B{last})")
        total.number_format = money
        total.font = Font(bold=True)
        chart = BarChart()
        chart.type = "bar"
        chart.title = check["title"]
        chart.legend = None
        chart.add_data(Reference(ws, min_col=2, min_row=first, max_row=last), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=first + 1, max_row=last))
        series = chart.series[0]
        for i, b in enumerate(check["bars"]):
            pt = DataPoint(idx=i)
            pt.graphicalProperties.solidFill = colours.get(b.get("role", ""), "#" + accent).lstrip("#").upper()
            series.dPt.append(pt)
        chart.height, chart.width = 7.5, 15
        ws.add_chart(chart, "E4")
        row += 2
    safe.put(ws, row=row, column=1, value=report["not_advice"]).font = Font(italic=True, color="666666")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 28
    pic = png(mascot, height=120)
    if pic:
        img = XLImage(io.BytesIO(pic))
        ws.add_image(img, "H1")

    money_fields = {"amount", "balance", "installment", "total", "fee", "gross", "net", "target", "funded", "minimum", "credit_limit", "pace"}
    sheet_names = {"transactions": "Transactions", "bills": "Bills", "debts": "Debts", "accounts": "Accounts", "plans": "Pay-later plans", "advances": "Advances", "paystubs": "Paystubs", "goals": "Goals", "payments": "Payments"}
    for key, title in sheet_names.items():
        rows = regs.get(key) or []
        sh = wb.create_sheet(title[:31])
        if not rows:
            sh["A1"] = "Nothing on this register yet."
            continue
        cols = [c for c in rows[0].keys() if c not in ("account_id",) or key != "transactions"]
        for i, c in enumerate(cols, start=1):
            cell = sh.cell(row=1, column=i, value=c.replace("_", " ").capitalize())
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(horizontal="right" if c in money_fields else "left")
            sh.column_dimensions[get_column_letter(i)].width = max(10, min(40, len(c) + 4))
        for r_i, r in enumerate(rows, start=2):
            for i, c in enumerate(cols, start=1):
                v = r.get(c)
                cell = safe.put(sh, row=r_i, column=i, value=v)
                if c in money_fields and isinstance(v, (int, float)):
                    cell.number_format = money
        for i, c in enumerate(cols, start=1):
            if c in money_fields and key in ("bills", "debts", "plans", "advances", "goals"):
                letter = get_column_letter(i)
                tot = sh.cell(row=len(rows) + 2, column=i, value=f"=SUM({letter}2:{letter}{len(rows) + 1})")
                tot.number_format = money
                tot.font = Font(bold=True)
        sh.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


RENDERERS: dict[str, Any] = {"md": markdown, "html": html, "pdf": pdf, "docx": docx}
