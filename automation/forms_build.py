"""빈 양식 파일 만들기 (11/12 운영자 '알바이력서양식 같은 글은 실제 양식 첨부하면 좋을까?' → '그래 해라').

정해진 법정 서식이 없는 양식(이력서·해촉증명서·급여명세서 등)은 글에 적은 칸 그대로 우리가 직접 만든 빈 양식을 붙인다.
법정 서식(혼인신고서 등)은 개정되면 옛 서식이 되므로 파일을 올리지 않고 spec 의 "official" 링크로만 안내한다.

spec: wordpress/themes/first-lab/forms/<id>.json
  {"post": 글 번호, "title": "이력서", "intro": "내려받기 상자에만 보이는 한 줄 안내(선택)",
   "blocks": [{"heading": "인적사항", "rows": ["이름", "생년월일"]},          # 칸 이름 | 빈칸
              {"heading": "계약 내용", "text": "1. … 한다.", "rows": [...]},  # text: 계약 조항처럼 서류에 실제로 찍힐 문장
              {"heading": "경력", "table": ["매장명", "기간", "맡은 일"], "lines": 3},  # 여러 줄 표
              {"heading": "자기소개", "lines": 5}],                          # 빈 줄만
   "statement": "위와 같이 … 증명합니다.",                                   # 서명 줄 위 문장 (선택)
   "sign": "작성일:      년    월    일        지원자:              (서명)",   # 맨 아래 (선택)
   "official": [{"name": "공식 서식 이름", "url": "https://..."}]}           # 함께 안내할 공식 서식 (선택)
→ 같은 폴더에 <id>.docx (Word, 한글에서도 열림) 와 <id>.pdf (인쇄용) 를 만든다.
화면에는 mu-plugins/first-lab-forms.php 가 글 첫 소제목 앞에 '빈 양식 내려받기' 상자로 붙인다 (본문은 안 건드림).

서류에 찍히는 글자(title·heading·rows·table)는 진짜 양식처럼 칸 이름만 — 작성 요령·괄호 설명·'알바' 같은 글 주제어는 넣지 않는다
(11/12 운영자 '실제 자료에 알바 이력서라고 적으면 안 되지 그냥 이력서', '다른 증명서도 단어 이상하게 적은 거 아냐?').
요령은 글 본문에 있다. label_problems() 가 막고 tests 가 확인한다.

사용: python3 automation/forms_build.py [id ...]   (id 를 안 주면 전부)
필요: python-docx, reportlab, 나눔고딕 글꼴 (/usr/share/fonts/truetype/nanum)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "wordpress" / "themes" / "first-lab" / "forms"
FONT_DIR = Path("/usr/share/fonts/truetype/nanum")
FONT, FONT_BOLD = "NanumGothic", "NanumGothicBold"
ROW = 0.8  # 칸 높이(cm) — 이력서 한 장(A4)에 서명 줄까지 들어가게


def labels(spec: dict) -> list[str]:
    """파일에 꼭 들어가야 하는 글자 (테스트가 docx 안에 다 있는지 봄)."""
    out = [spec["title"]]
    for b in spec["blocks"]:
        out.append(b["heading"])
        out += b.get("rows", []) + b.get("table", [])
    return out


LABEL_MAX = 14  # 칸 이름·소제목 글자 수 상한 — 넘으면 설명이 섞인 것
_TIP = re.compile(r"[()（）]|있을 때|하면|정확히|생략|예:|것부터|알바")


def label_problems(spec: dict) -> list[str]:
    """서류에 찍히는 칸 이름·소제목·제목에 작성 요령·괄호 설명·글 주제어('알바')가 섞였는지."""
    out = []
    for text in labels(spec):
        if len(text) > LABEL_MAX or _TIP.search(text):
            out.append(f"칸 이름에 설명이 섞임: '{text}' — 진짜 양식처럼 칸 이름만 (요령은 글 본문에)")
    return out


def build_docx(spec: dict, path: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    doc = Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)  # A4
    sec.left_margin = sec.right_margin = Cm(2)
    sec.top_margin = sec.bottom_margin = Cm(1.8)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = "맑은 고딕", Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(spec["title"])
    run.bold, run.font.size = True, Pt(20)

    for b in spec["blocks"]:
        head = doc.add_paragraph()
        r = head.add_run(b["heading"])
        r.bold, r.font.size = True, Pt(12)
        if b.get("text"):
            doc.add_paragraph(b["text"])
        if b.get("rows"):
            t = doc.add_table(rows=len(b["rows"]), cols=2, style="Table Grid")
            for i, name in enumerate(b["rows"]):
                t.cell(i, 0).text = name
                t.cell(i, 0).width, t.cell(i, 1).width = Cm(4), Cm(13)
        elif b.get("table"):
            cols = b["table"]
            t = doc.add_table(rows=1 + b.get("lines", 3), cols=len(cols), style="Table Grid")
            for j, name in enumerate(cols):
                t.cell(0, j).text = name
        else:
            t = doc.add_table(rows=b.get("lines", 4), cols=1, style="Table Grid")
        for row in t.rows:
            row.height = Cm(ROW)
    if spec.get("statement"):
        st = doc.add_paragraph(spec["statement"])
        st.alignment = WD_ALIGN_PARAGRAPH.CENTER
        st.paragraph_format.space_before = Pt(14)
    if spec.get("sign"):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(18)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.add_run(spec["sign"])
    doc.save(path)


def build_pdf(spec: dict, path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    for name in (FONT, FONT_BOLD):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / f"{name}.ttf")))
    title = ParagraphStyle("t", fontName=FONT_BOLD, fontSize=20, leading=26, alignment=1, spaceAfter=10)
    head = ParagraphStyle("h", fontName=FONT_BOLD, fontSize=12, leading=16, spaceBefore=8, spaceAfter=3)
    sign = ParagraphStyle("s", fontName=FONT, fontSize=10.5, leading=14, alignment=2, spaceBefore=18)
    body = ParagraphStyle("b", fontName=FONT, fontSize=10, leading=14, spaceAfter=4)
    center = ParagraphStyle("c", parent=body, alignment=1, spaceBefore=12)
    grid = TableStyle([("FONTNAME", (0, 0), (-1, -1), FONT), ("FONTSIZE", (0, 0), (-1, -1), 10),
                       ("GRID", (0, 0), (-1, -1), 0.6, colors.black), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")])
    width = A4[0] - 4 * cm
    story = [Paragraph(spec["title"], title)]
    for b in spec["blocks"]:
        story.append(Paragraph(b["heading"], head))
        if b.get("text"):
            story.append(Paragraph(b["text"].replace("\n", "<br/>"), body))
        if b.get("rows"):
            t = Table([[n, ""] for n in b["rows"]], colWidths=[4 * cm, width - 4 * cm], rowHeights=ROW * cm)
            t.setStyle(grid)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f2f2"))]))
        elif b.get("table"):
            cols = b["table"]
            t = Table([cols] + [[""] * len(cols) for _ in range(b.get("lines", 3))],
                      colWidths=[width / len(cols)] * len(cols), rowHeights=ROW * cm)
            t.setStyle(grid)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2"))]))
        else:
            t = Table([[""] for _ in range(b.get("lines", 4))], colWidths=[width], rowHeights=ROW * cm)
            t.setStyle(grid)
        story.append(t)
    if spec.get("statement"):
        story.append(Paragraph(spec["statement"], center))
    if spec.get("sign"):
        story.append(Spacer(1, 6))
        story.append(Paragraph(spec["sign"].replace(" ", "&nbsp;"), sign))
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                      bottomMargin=1.8 * cm, title=spec["title"], invariant=1).build(story)


def main(ids: list[str]) -> int:
    specs = sorted(FORMS.glob("*.json"))
    for f in specs:
        if ids and f.stem not in ids:
            continue
        spec = json.loads(f.read_text(encoding="utf-8"))
        bad = label_problems(spec)
        if bad:
            print(f"{f.stem}: 안 만듦 — " + " / ".join(bad))
            continue
        build_docx(spec, f.with_suffix(".docx"))
        build_pdf(spec, f.with_suffix(".pdf"))
        print(f"{f.stem}: docx·pdf 만듦 (글 {spec['post']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
