"""쉬운 말 다듬기 검사 (11/9 운영자 '기존 글도 전체 쉬운 말로 — 새 글 멈출 테니 니가 직접 해라').

다듬은 본문이 원래 본문과 '사실'이 같은지 코드로 확인한다 — 외부 검수를 다시 하지 않아도 되게.
  - 숫자(금액·비율·날짜·기간 등 숫자가 든 낱말), 법 조항(제N조), 링크 주소, 계산기 짧은 코드는 하나도 빠지거나 새로 생기면 안 됨
  - 소제목·표 개수 같고, 본문 길이 80% 이상
  - 검색어: 제목·소제목 낱말, 본문에 3번 이상 나온 용어는 원래 횟수의 절반 이상 남김 (lost_terms)
  - 첫 문장: 쉬운 답 한 줄 — 70자 이하, 괄호·법 조항 없음
  - 문단마다 문장 2개까지 (긴 문단은 자동으로 끊음, 첫 문단 상자는 <br> 줄마다 2문장까지 — 사람이 나눔)
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

FIRST_MAX = 55  # 11/10 운영자 '90점 넘는 거 확인하제?' — 기준 글 #1419 첫 문장 39자, 69자 글(#1798)은 휴대폰에서 네 줄
AVG_MAX = 40
LONG_SENT, LONG_RATIO = 60, 0.05  # 60자 넘는 문장은 5% 까지 (기준 글 #1419 3/128, 90점 못 미친 #1798 11/92·#1761 8/93)
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


MAX_SENT = 2  # 문단 하나(첫 문단은 줄 하나)에 문장 2개까지 — 11/10 운영자 '줄바꿈은 엿 바꿔 먹었나' (한눈에 보기 상자에 6문장이 한 덩어리)
_SENT_END = re.compile(r"(?<=[.?!])\s+(?=[^<>]*(?:<|$))")


def _balanced(chunk: str) -> bool:
    return all(chunk.count(f"<{t}") == chunk.count(f"</{t}>") for t in ("strong", "a", "em", "b", "span"))


def split_long_paragraphs(html: str, first_too: bool = False) -> str:
    """문장 3개 이상인 문단을 문장 2개씩 끊어 여러 문단으로 (글 내용은 그대로). 꾸밈 태그가 문장을 걸쳐 끊기 어려우면 그대로 둠.
    첫 문단(한눈에 보기 상자)은 사람이 줄(<br>)을 나눠 쓰므로 기본으로 건드리지 않음."""
    first = re.search(r"<p[^>]*>", html or "")

    def cut(m):
        if (m.start() == first.start() and not first_too) or m.group(1) or "<br" in m.group(2):
            return m.group(0)
        parts = _SENT_END.split(m.group(2).strip())
        if len(parts) <= MAX_SENT:
            return m.group(0)
        chunks = [" ".join(parts[i:i + MAX_SENT]) for i in range(0, len(parts), MAX_SENT)]
        if not all(_balanced(c) for c in chunks):
            return m.group(0)
        return "\n".join(f"<p>{c}</p>" for c in chunks)
    return re.sub(r"<p( [^>]*)?>(.*?)</p>", cut, html or "", flags=re.S)


def crowded_paragraphs(html: str) -> list[str]:
    """문장 3개 이상인 문단(첫 문단은 <br> 줄마다) — 공식 안내 목록은 뺌."""
    body = (html or "").split("공식 안내 바로가기")[0]
    out = []
    for inner in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S):
        for line in re.split(r"<br\s*/?>", inner):
            n = len([x for x in re.split(r"(?<=[.?!])\s+", plain(line)) if len(x) > 3])
            if n > MAX_SENT:
                out.append(plain(line)[:30])
    return out


_BOXNUM = re.compile(r"\d[\d,.]*\d|\d")
REPEAT_MAX = 2  # 상자 숫자가 첫 소제목 앞 문단에 3개 이상 또 나오면 되풀이 (11/10 운영자 '반복 줄여라')


TICK_MAX = 22
STALE_WORDS = re.compile(r"지금 같은 \d+월|이번 달|요즘 같은|지금은 잠시|현재 한시")  # 올해·내년은 '올해(2026년)'·'적용 연도가 올해인지'처럼 바른 쓰임이 많아 뺌


# 법에 정의가 있는 용어를 풀 때 꼭 들어가야 하는 말 (11/10 감시 세션 직접 읽기에서 뜻 검수가 놓친 것 — 근로기준법 제2조·시행령 제6조)
DEFINITION_MUST = {
    "단시간근로자": (r"그 사업장|같은 사업장", "그 사업장에서 같은 일을 하는 통상 근로자보다 1주 소정근로시간이 짧은"),
    "단시간 근로자": (r"그 사업장|같은 사업장", "그 사업장에서 같은 일을 하는 통상 근로자보다 1주 소정근로시간이 짧은"),
    "피보험 단위기간": (r"보수 지급의 기초", "보수 지급의 기초가 된 날을 합한 기간 (고용보험법 제41조① — '보수를 받은 날'은 유급휴일 등이 빠져 뜻이 달라짐, 11/10 #1800·#475)"),
    "피보험단위기간": (r"보수 지급의 기초", "보수 지급의 기초가 된 날을 합한 기간 (고용보험법 제41조①)"),
    # 소정근로시간 '계약으로 정한 일하는 시간'은 법정 범위 말이 빠져도 뜻이 틀리지 않아 넣지 않음 (비교 대상이 바뀌는 단시간근로자만)
}


def loose_definitions(html: str) -> list[str]:
    """'○○은/는 …예요' 처럼 용어를 풀어 쓴 문장에 법 정의의 핵심 말이 빠졌으면."""
    out = []
    for s in sentences(html):
        for term, (must, hint) in DEFINITION_MUST.items():
            if re.match(rf"\s*{term}(?:은|는)\s", s) and re.search(r"(이에요|예요|말해요|뜻이에요)\.?$", s) and not re.search(must, s):
                out.append(f"'{term}' 뜻풀이에 법 정의 요소가 빠짐 — '{hint}' (문장: {s[:40]}…)")
    return out


def expired_deadlines(html: str, written, today=None) -> list[str]:
    """11/10 #561 '이 기한은 2026년 11월 9일까지예요' — 마지막 저장(written) 때 앞날이던 기한이 지났는데 그대로 옮기지 않게
    (공개 직전 검사 bot/expiry.passed_rounds 와 같은 판정. 쉬운 말 갱신은 확인한 날을 바꾸지 않으므로 여기서도 막음)."""
    from datetime import date
    from bot import expiry
    return [f"지난 기한을 앞날처럼 씀 — 지난 일로 쓰거나 다음 회차로: {x}"
            for x in expiry.passed_rounds(expiry.blocks(html), written, today or date.today())]


DUP_MIN, DUP_SHARE = 30, 0.8  # 30자 넘는 문단·목록 항목끼리 글자 3개 묶음이 80% 넘게 겹치면 같은 내용 (#1143 0.82·0.89, 다른 내용인 비슷한 표 줄은 0.7대)


def _units(html: str) -> list[str]:
    body = (html or "").split("공식 안내 바로가기")[0]
    body = re.sub(r"<p[^>]*>\s*<strong>이런 건 꼭 확인하세요</strong>\s*</p>\s*<ul[^>]*>.*?</ul>", " ", body, flags=re.S)  # 확인 목록은 본문 요약이라 겹치는 게 정상
    texts = (plain(x) for _, x in re.findall(r"<(p|li|td)[^>]*>(.*?)</\1>", body, re.S))
    return [t for t in texts if len(t) > DUP_MIN and not t.startswith(("근거", "한 줄 정리"))]


def _grams(t: str) -> set[str]:
    t = re.sub(r"\s+", "", t)
    return {t[i:i + 3] for i in range(len(t) - 2)}


def _dup_pairs(html: str) -> list[str]:
    units = [(_grams(t), t) for t in _units(html)]
    out = []
    for i, (a, ta) in enumerate(units):
        for b, tb in units[i + 1:]:
            if a and b and len(a & b) / min(len(a), len(b)) > DUP_SHARE:
                out.append(min(ta, tb, key=len))
    return out


def duplicated_text(old_html: str, new_html: str) -> list[str]:
    """11/10 #1143: 다듬을 때 원래 긴 문단을 안 지우고 나눈 문장을 그 아래에 또 써서 같은 내용이 두 번 나옴
    (최종 읽기 plain_diff 는 바뀐 곳만 보여 줘서 안 바뀐 원래 문단이 남은 걸 못 봄). 원래 글에 없던 겹침만."""
    old_units = [_grams(t) for t in _units(old_html)]
    def in_old(t: str) -> int:  # 원래 글에서 이 내용이 나온 문단 수 (원래도 두 곳이면 다듬기 탓이 아님 — 예: 근로기준법 1년·기간제법 2년)
        g = _grams(t)
        return sum(1 for u in old_units if g and u and len(g & u) / min(len(g), len(u)) > DUP_SHARE)
    return [t for t in dict.fromkeys(_dup_pairs(new_html)) if in_old(t) < 2]


def box_lines(html: str) -> list[str]:
    lead = re.search(r"<p[^>]*>(.*?)</p>", html or "", re.S)
    return [plain(x) for x in re.split(r"<br\s*/?>", lead.group(1))] if lead else []


def box_repeats(html: str) -> list[str]:
    """한눈에 보기 상자(첫 문단)의 숫자 중 바로 아래(첫 소제목 전) 문단에 또 나온 것 (한 자리 1~3 은 뺌)."""
    lead = re.search(r"<p[^>]*>(.*?)</p>", html or "", re.S)
    if not lead:
        return []
    end = html.find("<h2", lead.end())
    box = set(_BOXNUM.findall(plain(lead.group(1)))) - {"1", "2", "3"}
    intro = set(_BOXNUM.findall(plain(html[lead.end():end if end > 0 else None])))
    return sorted(box & intro)


def first_sentence(html: str) -> str:
    m = re.search(r"<p[^>]*>(.*?)</p>", html or "", re.S)
    text = plain(m.group(1)) if m else ""
    return re.split(r"(?<=[.?!])\s", text, maxsplit=1)[0]


_PARTICLE = re.compile(r"(으로|에서|이에요|예요|입니다|이면|에는|에게|까지|부터|은|는|이|가|을|를|의|에|로|도|과|와|만)$")
_VERB_END = re.compile(r"(요|다|면|고|지|기|게|며|서|던|된|한|인|운|든|까|나)$")


def _terms(text: str, min_len: int) -> set[str]:
    out = set()
    for w in re.findall(r"[가-힣]{2,}", text):
        w = _PARTICLE.sub("", w)
        if len(w) >= min_len and not _VERB_END.search(w):
            out.add(w)
    return out


def _headings(html: str) -> str:
    return " ".join(plain(x) for x in re.findall(r"<h[23][^>]*>(.*?)</h[23]>", html or "", re.S))


def lost_terms(old_html: str, new_html: str, title: str = "") -> list[str]:
    """검색에 걸리는 낱말이 쉬운 말로 바뀌며 사라졌는지 (11/9 운영자 '검색에도 걸려야 되니 기존 단어 다 없애면 안 된다').
    제목·소제목 낱말, 본문에 3번 이상 나온 3글자 이상 용어(소정근로시간·개근 등)는 원래 횟수의 절반(올림) 이상 남아야 한다."""
    old_t, new_t = plain(old_html), plain(new_html)
    words = _terms(f"{title} {_headings(old_html)}", 2) | {w for w in _terms(old_t, 3) if old_t.count(w) >= 3}
    out = []
    for w in sorted(words, key=lambda x: -old_t.count(x)):
        before, after = old_t.count(w), new_t.count(w)
        if before and after < -(-before // 2):
            out.append(f"{w} {before}→{after}")
    return out


def problems(old_html: str, new_html: str, title: str = "", allow: set[str] | None = None, written=None,
             post_id: int | None = None) -> list[str]:
    """allow: 법·공식 자료로 확인한 원래 글 오류를 고치며 바뀌는 숫자 (--fix 로만, 이유와 함께 갱신 파일에 남음).
    written: 원래 글을 마지막으로 저장한 날 (그 뒤 지난 기한 검사)."""
    allow = allow or set()
    out = []
    lost = lost_terms(old_html, new_html, title)
    if lost:
        out.append(f"검색어 줄어듦 (원래의 절반 이상 남기기, 쉬운 말은 옆에 덧붙이기): {', '.join(lost[:8])}")
    before, after = facts(old_html), facts(new_html)
    for kind in before:
        lost, added = before[kind] - after[kind], after[kind] - before[kind]
        if kind == "숫자":  # 같은 숫자를 한 번 더 쓰는 것(쉬운 첫 문장에 핵심 숫자 반복)은 괜찮음 — 새 숫자·빠진 숫자만
            added = Counter({k: v for k, v in added.items() if k not in before[kind] and k not in allow})
            lost = Counter({k: v for k, v in lost.items() if k not in allow})
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
    longs = [x for x in sents if not x.startswith(("근거", "한 줄 정리")) and "✔" not in x and len(x) > LONG_SENT]  # 상자 ✔ 줄은 마침표가 없어 한 문장으로 붙음 — 줄 길이는 TICK_MAX 가 봄
    if sents and len(longs) > len(sents) * LONG_RATIO:
        out.append(f"{LONG_SENT}자 넘는 문장 {len(longs)}개 (전체 {len(sents)}개의 {LONG_RATIO:.0%} 까지) — 둘로 나누기 (예: {longs[0][:40]}…)")
    crowded = [x for x in sents if not x.startswith("근거") and x.count("(") > 1]
    if crowded:
        out.append(f"괄호 2개 이상인 문장 {len(crowded)}개 — 한 문장에 괄호 하나까지 (예: {crowded[0][:40]}…)")
    long_ticks = [x for x in box_lines(new_html) if x.startswith("✔") and len(x) > TICK_MAX]
    if long_ticks:
        out.append(f"한눈에 보기 ✔ 줄이 {TICK_MAX}자 넘음 (휴대폰에서 두 줄로 꺾임): {long_ticks[0]}")
    stale = sorted(set(STALE_WORDS.findall(plain(new_html))))
    if stale:
        out.append(f"시간이 지나면 틀리는 말: {', '.join(stale)} — '2026년'처럼 연도·기간으로 (11/10 '지금 같은 11월')")
    out += loose_definitions(new_html)
    dup = duplicated_text(old_html, new_html)
    if dup:
        out.append(f"같은 내용이 두 번 나옴 {len(dup)}곳 — 원래 문단을 지우고 고친 문장만 남기기 (예: {dup[0][:40]}…)")
    out += expired_deadlines(new_html, written) if written else []
    rep = box_repeats(new_html)
    if len(rep) > REPEAT_MAX:
        out.append(f"한눈에 보기 상자 내용이 바로 아래 문단에서 되풀이됨 (숫자 {', '.join(rep[:6])}) — 상자에 있는 건 아래에서 빼고 새 내용만")
    crowded_p = crowded_paragraphs(new_html)
    if crowded_p:
        out.append(f"문장 {MAX_SENT + 1}개 이상인 문단 {len(crowded_p)}개 — 휴대폰에서 한 덩어리로 보임, 문장 {MAX_SENT}개마다 끊기 (예: {crowded_p[0]}…)")
    from bot import condition_gate
    out += condition_gate.known_wrong(new_html)
    out += condition_gate.html_unbalanced(new_html)
    if post_id is not None:
        from bot import patch_check
        out += patch_check.reintroduced(post_id, new_html)
    out += condition_gate.readability(new_html) if hasattr(condition_gate, "readability") else []
    return out


def refresh_file(post_id: int, base_modified: str, new_html: str, fix_reason: str = "") -> Path:
    path = ROOT / "wordpress" / "queue" / f"refresh-plain-{post_id}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"type": "refresh", "kind": "plain", "post_id": post_id, "base_modified": base_modified, "content_html": new_html,
                                "changes": "쉬운 말 다듬기 (숫자·법 조항·링크 그대로, automation/plain_check.py 대조 통과)"
                                           + (f" / 원래 글 오류 정정: {fix_reason}" if fix_reason else "")},
                               ensure_ascii=False), encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    post_id, original, new_file = int(argv[0]), json.loads(Path(argv[1]).read_text(encoding="utf-8")), Path(argv[2])
    new_html = split_long_paragraphs(new_file.read_text(encoding="utf-8"))  # 긴 문단은 자동으로 끊어서 검사·반영
    fix = next((a.split("=", 1)[1] for a in argv if a.startswith("--fix=")), "")  # 예: --fix=11,0.9448:장기요양 요율 2026년 1월분부터(노인장기요양보험법 시행령 제4조)
    nums = fix.split(":", 1)[0]
    allow = set(nums.split("|" if "|" in nums else ",")) if fix else set()  # '1,000' 처럼 쉼표 든 숫자는 '|' 로 나눔 (--fix=1,000|5|185:…)
    if fix and ":" not in fix:
        print("--fix=숫자,숫자:근거 — 바꾸는 이유(법·공식 자료)를 꼭 적기")
        return 2
    base = original.get("base_modified") or ""
    written = datetime.strptime(base[:10], "%Y-%m-%d").date() if base else None
    found = problems(original["content_html"], new_html, original.get("title", ""), allow, written, post_id)
    if found:
        print("통과 못 함:\n  " + "\n  ".join(found))
        return 1
    print("통과")
    if "--write" in argv:
        if not base:
            print("원본 JSON 에 base_modified 가 없음 (서버 /post 응답) — 그 사이 바뀐 글을 덮을 수 있어 갱신 파일을 만들지 않음")
            return 1
        print(refresh_file(post_id, base, new_html, fix.split(":", 1)[1] if fix else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
