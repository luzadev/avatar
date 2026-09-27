"""Documenti PDF: impagina in A4 un testo in Markdown (titoli, elenchi, grassetto, corsivo, tabelle, immagini),
lo salva in data/documenti/ e lo apre in Anteprima. Il file arriva anche al telefono.
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import DATA_DIR  # noqa: E402

OUT_DIR = DATA_DIR / "documenti"
FONT_DIR = Path("/System/Library/Fonts/Supplemental")


def _fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.fonts import addMapping
    if "Arial" in pdfmetrics.getRegisteredFontNames():
        return "Arial"
    try:
        pdfmetrics.registerFont(TTFont("Arial", str(FONT_DIR / "Arial.ttf")))
        pdfmetrics.registerFont(TTFont("Arial-Bold", str(FONT_DIR / "Arial Bold.ttf")))
        pdfmetrics.registerFont(TTFont("Arial-Italic", str(FONT_DIR / "Arial Italic.ttf")))
        pdfmetrics.registerFont(TTFont("Arial-BoldItalic", str(FONT_DIR / "Arial Bold Italic.ttf")))
        addMapping("Arial", 0, 0, "Arial"); addMapping("Arial", 1, 0, "Arial-Bold")
        addMapping("Arial", 0, 1, "Arial-Italic"); addMapping("Arial", 1, 1, "Arial-BoldItalic")
        return "Arial"
    except Exception:
        return "Helvetica"


def _inline(text: str) -> str:
    """Markdown in linea → mini-HTML di reportlab (grassetto, corsivo, codice, link)."""
    t = html.escape(text, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", t)
    t = re.sub(r"\[(.+?)\]\((https?://[^\s)]+)\)", r"<link href='\2' color='#0645ad'>\1</link>", t)
    return t


def costruisci(titolo: str, markdown: str, path: Path, autore: str = "", immagini: list[str] | None = None) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem, Table, TableStyle, Image, PageBreak, HRFlowable
    font = _fonts()
    st = {
        "title": ParagraphStyle("t", fontName=font, fontSize=22, leading=27, spaceAfter=10, textColor=colors.HexColor("#123")),
        "h1": ParagraphStyle("h1", fontName=font, fontSize=16, leading=20, spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#123")),
        "h2": ParagraphStyle("h2", fontName=font, fontSize=13, leading=17, spaceBefore=10, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontName=font, fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=3),
        "p": ParagraphStyle("p", fontName=font, fontSize=10.5, leading=15, spaceAfter=6),
        "quote": ParagraphStyle("q", fontName=font, fontSize=10.5, leading=15, leftIndent=14, textColor=colors.HexColor("#444"), spaceAfter=6),
        "small": ParagraphStyle("s", fontName=font, fontSize=8.5, leading=11, textColor=colors.HexColor("#777")),
        "code": ParagraphStyle("c", fontName="Courier", fontSize=9, leading=12, backColor=colors.HexColor("#f4f4f4"), leftIndent=6, spaceAfter=6),
    }
    story = []
    if titolo:
        story += [Paragraph(_inline(titolo), st["title"]), HRFlowable(width="100%", color=colors.HexColor("#123"), thickness=0.8, spaceAfter=8)]
    if autore:
        story.append(Paragraph(html.escape(autore), st["small"])); story.append(Spacer(1, 6))
    lines = (markdown or "").replace("\r", "").split("\n")
    i, para, lst, table, code = 0, [], None, None, None

    def flush_para():
        nonlocal para
        if para:
            story.append(Paragraph(_inline(" ".join(para)), st["p"])); para = []

    def flush_list():
        nonlocal lst
        if lst:
            kind, items = lst
            story.append(ListFlowable([ListItem(Paragraph(_inline(x), st["p"]), leftIndent=12) for x in items],
                                      bulletType="1" if kind == "ol" else "bullet", start="1" if kind == "ol" else None, bulletFormat="%s." if kind == "ol" else None, leftIndent=14, bulletFontName=font, bulletFontSize=9))
            lst = None

    def flush_table():
        nonlocal table
        if table:
            rows = [[Paragraph(_inline(c), st["p"]) for c in r] for r in table]
            t = Table(rows, hAlign="LEFT", repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef3")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
            story.append(t); story.append(Spacer(1, 8)); table = None

    while i < len(lines):
        ln = lines[i].rstrip()
        if code is not None:
            if ln.strip().startswith("```"):
                story.append(Paragraph(html.escape("\n".join(code)).replace("\n", "<br/>"), st["code"])); code = None
            else:
                code.append(ln)
            i += 1; continue
        if ln.strip().startswith("```"):
            flush_para(); flush_list(); flush_table(); code = []; i += 1; continue
        if not ln.strip():
            flush_para(); flush_list(); flush_table(); i += 1; continue
        if ln.strip() in ("---", "***", "___"):
            flush_para(); flush_list(); flush_table(); story.append(HRFlowable(width="100%", color=colors.HexColor("#bbb"), spaceBefore=4, spaceAfter=8)); i += 1; continue
        if ln.strip().lower() in ("\\pagebreak", "<pagebreak>", "[pagina]"):
            flush_para(); flush_list(); flush_table(); story.append(PageBreak()); i += 1; continue
        m = re.match(r"^(#{1,3})\s+(.*)$", ln)
        if m:
            flush_para(); flush_list(); flush_table()
            story.append(Paragraph(_inline(m.group(2)), st[f"h{len(m.group(1))}"])); i += 1; continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)", ln.strip())
        if m:
            flush_para(); flush_list(); flush_table()
            p = Path(m.group(2)).expanduser()
            if p.exists():
                img = Image(str(p)); ratio = img.imageHeight / img.imageWidth; w = min(160 * mm, img.imageWidth * 0.264 * mm)
                img.drawWidth, img.drawHeight = w, w * ratio; story.append(img)
                if m.group(1):
                    story.append(Paragraph(html.escape(m.group(1)), st["small"]))
                story.append(Spacer(1, 8))
            i += 1; continue
        if ln.strip().startswith("|"):
            flush_para(); flush_list()
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                table = (table or []) + [cells]
            i += 1; continue
        m = re.match(r"^\s*([-*•]|\d+[.)])\s+(.*)$", ln)
        if m:
            flush_para(); flush_table()
            kind = "ol" if m.group(1)[0].isdigit() else "ul"
            if lst and lst[0] != kind:
                flush_list()
            lst = (kind, (lst[1] if lst else []) + [m.group(2)]); i += 1; continue
        if ln.startswith(">"):
            flush_para(); flush_list(); flush_table()
            story.append(Paragraph(_inline(ln.lstrip("> ")), st["quote"])); i += 1; continue
        flush_list(); flush_table(); para.append(ln.strip()); i += 1
    flush_para(); flush_list(); flush_table()
    for p in immagini or []:
        pp = Path(p).expanduser()
        if pp.exists():
            img = Image(str(pp)); ratio = img.imageHeight / img.imageWidth; w = min(160 * mm, img.imageWidth * 0.264 * mm)
            img.drawWidth, img.drawHeight = w, w * ratio; story += [Spacer(1, 8), img]

    def footer(canvas, doc):
        canvas.saveState(); canvas.setFont(font, 8); canvas.setFillColor(colors.HexColor("#888"))
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"{doc.page}")
        if titolo:
            canvas.drawString(20 * mm, 12 * mm, titolo[:80])
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
                            title=titolo or path.stem, author=autore or "LuZa")
    doc.build(story or [Paragraph("(documento vuoto)", st["p"])], onFirstPage=footer, onLaterPages=footer)


def crea(params: dict, ctx: dict) -> str:
    titolo = str(params.get("titolo") or "").strip()
    contenuto = str(params.get("contenuto") or "").strip()
    if not contenuto and not titolo:
        return "Errore: serve il contenuto del documento (in Markdown)."
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    nome = re.sub(r"[^a-z0-9]+", "-", str(params.get("nome_file") or titolo or "documento").lower()).strip("-")[:60] or "documento"
    path = OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{nome}.pdf"
    immagini = params.get("immagini") or []
    if isinstance(immagini, str):
        immagini = [immagini]
    try:
        costruisci(titolo, contenuto, path, str(params.get("autore") or ""), [str(x) for x in immagini])
    except Exception as err:
        return f"Impaginazione fallita: {err}"
    if params.get("mostra", True) not in (False, "false", "no"):
        subprocess.Popen(["open", str(path)])
    pagine = ""
    try:
        from pypdf import PdfReader
        pagine = f", {len(PdfReader(str(path)).pages)} pagine"
    except Exception:
        pass
    return f"PDF creato: {path}{pagine}. Aperto in Anteprima.\nFILE: {path}"


def ultimi(params: dict, ctx: dict) -> str:
    files = sorted(OUT_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True) if OUT_DIR.exists() else []
    if not files:
        return "Nessun documento creato finora."
    if str(params.get("apri", "")).lower() in ("true", "1", "sì", "si"):
        subprocess.Popen(["open", str(files[0])])
    n = max(1, min(int(params.get("numero") or 5), 20))
    return "Ultimi documenti:\n" + "\n".join(f"- {p.name}" for p in files[:n]) + f"\nFILE: {files[0]}"


TOOLS = [
    {"name": "pdf_crea", "description": "Crea un documento PDF in A4 da un testo in Markdown: usa # ## ### per i titoli, - o 1. per gli elenchi, **grassetto**, *corsivo*, tabelle con |, ![didascalia](percorso) per le immagini, --- per una riga, [pagina] per il salto pagina. Scrivi tu il contenuto completo e ben strutturato (lettere, relazioni, elenchi, ricette, verbali…). Il PDF viene salvato, aperto in Anteprima e inviato al telefono.",
     "parameters": {"type": "object", "properties": {"titolo": {"type": "string"}, "contenuto": {"type": "string", "description": "testo in Markdown"}, "nome_file": {"type": "string"}, "autore": {"type": "string"},
                    "immagini": {"type": "array", "items": {"type": "string"}, "description": "percorsi di immagini da accodare"}, "mostra": {"type": "boolean"}}, "required": ["contenuto"]}, "run": crea},
    {"name": "pdf_ultimi", "description": "Elenca gli ultimi PDF creati (cartella data/documenti); con apri=true apre il più recente.",
     "parameters": {"type": "object", "properties": {"numero": {"type": "integer"}, "apri": {"type": "boolean"}}}, "run": ultimi},
]
