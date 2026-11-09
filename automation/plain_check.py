"""쉬운 말 다듬기 검사 (11/9 운영자 '기존 글도 전체 쉬운 말로 — 새 글 멈출 테니 니가 직접 해라').

다듬은 본문이 원래 본문과 '사실'이 같은지 코드로 확인한다 — 외부 검수를 다시 하지 않아도 되게.
  - 숫자(금액·비율·날짜·기간 등 숫자가 든 낱말), 법 조항(제N조), 링크 주소, 계산기 짧은 코드는 하나도 빠지거나 새로 생기면 안 됨
  - 소제목·표 개수 같고, 본문 길이 80% 이상
  - 첫 문장: 쉬운 답 한 줄 — 70자 이하, 괄호·법 조항 없음
  - 읽기 기준(문장 100자·문단 300자·목록 200자, bot/condition_gate.readability)
통과하면 글 갱신 파일(wordpress/queue/refresh-…json, type refresh)을 만든다 → 서버 bot/refresh.apply_direct 가 반영.

사용: python3 automation/plain_check.py <글번호> <원본 JSON(unreviewed 저장본)> <다듬은 본문 HTML 파일> [--write]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIRST_MAX = 70
AVG_MAX = 40
_TAG = re.compile(r"<[^>]+>")
_NUM = re.compile(r"\d[\d,.]*")
_ARTICLE = re.compile(r"제\s?\d+조(?:의\s?\d+)?(?:\s?제?\d+항)?")
_HREF = re.compile(r'href="([^"]+)"')
_CALC = re.compile(r"\[firstlab_calc[^\]]*\]")


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html or "")).strip()


def facts(html: str) -> dict[str, Counter]:
    text = plain(html)
    return {
        "숫자": Counter(n.rstrip(".,") for n in _NUM.findall(text)),
        "법 조항": Counter(re.sub(r"\s", "", a) for a in _ARTICLE.findall(text)),
        "링크": Counter(_HREF.findall(html or "")),
        "계산기": Counter(_CALC.findall(html or "")),
        "소제목": Counter({"h2": len(re.findall(r"<h2", html or "")), "h3": len(re.findall(r"<h3", html or ""))}),
        "표": Counter({"table": len(re.findall(r"<table", html or "")), "tr": len(re.findall(r"<tr", html or ""))}),
    }


def sentences(html: str) -> list[str]:
    """문장 목록 — 요소(문단·목록 항목·소제목·표 칸)마다 따로 나눔. 글 끝 '공식 안내 바로가기' 목록은 뺌
    (11/9: 마침표 없는 링크 목록이 한 문장으로 붙어 괄호 수 검사에 걸렸음)."""
    body = (html or "").split("공식 안내 바로가기")[0]
    out = []
    for _, inner in re.findall(r"<(p|li|h2|h3|td|th)[^>]*>(.*?)</\1>", body, re.S):
        out += [x for x in re.split(r"(?<=[.?!])\s+", plain(inner)) if len(x) > 5]
    return out


def first_sentence(html: str) -> str:
    m = re.search(r"<p[^>]*>(.*?)</p>", html or "", re.S)
    text = plain(m.group(1)) if m else ""
    return re.split(r"(?<=[.?!])\s", text, maxsplit=1)[0]


def problems(old_html: str, new_html: str) -> list[str]:
    out = []
    before, after = facts(old_html), facts(new_html)
    for kind in before:
        lost, added = before[kind] - after[kind], after[kind] - before[kind]
        if kind == "숫자":  # 같은 숫자를 한 번 더 쓰는 것(쉬운 첫 문장에 핵심 숫자 반복)은 괜찮음 — 새 숫자·빠진 숫자만
            added = Counter({k: v for k, v in added.items() if k not in before[kind]})
        if lost:
            out.append(f"{kind} 빠짐: {', '.join(list(lost)[:8])}")
        if added:
            out.append(f"{kind} 새로 생김: {', '.join(list(added)[:8])}")
    if len(plain(new_html)) < len(plain(old_html)) * 0.8:
        out.append("본문이 원래의 80%보다 짧음")
    first = first_sentence(new_html)
    if len(first) > FIRST_MAX:
        out.append(f"첫 문장 {len(first)}자 — {FIRST_MAX}자 이하 쉬운 답 한 줄로")
    if re.search(r"[(（]|제\s?\d+조", first):
        out.append("첫 문장에 괄호·법 조항 — 쉬운 말만")
    sents = sentences(new_html)
    avg = sum(map(len, sents)) / len(sents) if sents else 0
    if avg > AVG_MAX:
        out.append(f"문장 평균 {avg:.0f}자 — {AVG_MAX}자 이하로 (중학교 1학년이 한 번에 읽게, 운영자 11/9)")
    crowded = [x for x in sents if not x.startswith("근거") and x.count("(") > 1]
    if crowded:
        out.append(f"괄호 2개 이상인 문장 {len(crowded)}개 — 한 문장에 괄호 하나까지 (예: {crowded[0][:40]}…)")
    from bot import condition_gate
    out += condition_gate.readability(new_html) if hasattr(condition_gate, "readability") else []
    return out


def refresh_file(post_id: int, base_modified: str, new_html: str) -> Path:
    path = ROOT / "wordpress" / "queue" / f"refresh-plain-{post_id}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"type": "refresh", "post_id": post_id, "base_modified": base_modified, "content_html": new_html,
                                "changes": "쉬운 말 다듬기 (숫자·법 조항·링크 그대로, automation/plain_check.py 대조 통과)"},
                               ensure_ascii=False), encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    post_id, original, new_file = int(argv[0]), json.loads(Path(argv[1]).read_text(encoding="utf-8")), Path(argv[2])
    new_html = new_file.read_text(encoding="utf-8")
    found = problems(original["content_html"], new_html)
    if found:
        print("통과 못 함:\n  " + "\n  ".join(found))
        return 1
    print("통과")
    if "--write" in argv:
        base = original.get("base_modified") or ""
        if not base:
            print("원본 JSON 에 base_modified 가 없음 (서버 /post 응답) — 그 사이 바뀐 글을 덮을 수 있어 갱신 파일을 만들지 않음")
            return 1
        print(refresh_file(post_id, base, new_html))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
