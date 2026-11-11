"""+@ 보강 검사 (11/11 운영자 '+@ 가 중요하다 — 쉬운 말 작업 다 하면 바로 초안 전부 +@ 작업해라').

대기 초안마다 네이버 상위 글(automation/naver_top.py)을 보고, 상위 글이 다루는데 우리 글에 없는 것(+)과
상위 글에도 없는 우리만의 것(@: 계산 예시·상황별 판단·최신 개정·확인 방법)을 더한 본문을 검사한다.
쉬운 말 다듬기(plain_check)와 달리 새 사실이 들어가므로:
  - 원래 숫자·법 조항·링크·계산기는 하나도 빠지면 안 됨 (더하기만, 고칠 땐 --fix 와 근거)
  - 새로 생긴 숫자·조항은 모두 검수 기록(review)의 '맞음' 주장 + 공식 기관 주소로 확인돼야 함
  - 쉬운 말 기준·틀린 문구·옛 패치 문구·요약 어긋남은 plain_check 와 같게
  - serp(상위 글 3곳·질문·더한 것)를 본문이 다 다루고, 상위 글 문장을 옮기지 않아야 함 (bot/serp_gate.py)
통과하면 wordpress/queue/refresh-plus-<번호>-….json (kind "plus") → 서버 bot/refresh.apply_direct 가 serp 를 다시 보고 반영.

사용: python3 automation/plus_check.py <글번호> <원본 JSON> <보강 본문 HTML> <보강 기록 JSON> [--write] [--fix=숫자|…:근거] [--excerpt=파일]
  보강 기록 JSON = {"serp": {...}, "review": [{"claim", "urls", "verdict", "action"}, ...],
                   "added": [{"type": "+질문|@계산예시|@상황판단|@최신개정|@조회방법", "for": "누구의 어떤 궁금증", "text": "더한 것 한 줄"}, ...]}
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "automation"))

import plain_check as pc  # noqa: E402

GROW_MAX = 1.6    # 이보다 길어지면 꼭 필요한 것만 넣었는지 의심 (11/11 운영자 '+@ 도 아무거나 넣으면 안 되는 거 알제')
# 더한 것마다 종류를 밝힘 — 분량 채우기·주제 밖 배경 설명·홍보는 종류가 없어 못 들어옴
ADD_TYPES = {
    "+질문": "상위 글이 답하는데 우리 글에 없던 질문 (for 에 serp.questions 의 그 질문)",
    "@계산예시": "실제 숫자로 해 보는 계산 (공식 기준값으로)",
    "@상황판단": "이런 경우엔 이렇게 — 조건별로 갈리는 판단",
    "@최신개정": "시행일이 정해진 개정·바뀐 기준 (확정 여부·시행일 함께)",
    "@조회방법": "내 경우를 직접 확인하는 곳 (기관·메뉴·전화)",
}
GROW_MIN_CHARS = 800   # 짧은 글에 문단 하나 더한 건 비율이 커도 괜찮음
MAX_AT = 3        # @ 는 1~3개 — 많이 넣는 게 목표가 아님


def _verified_text(review: list[dict]) -> str:
    from bot.sources import is_official
    return " ".join(str(c.get("claim", "")) for c in review or []
                    if c.get("verdict") == "맞음" and any(is_official(u) for u in c.get("urls") or []))


def added_problems(record: dict, old_html: str, new_html: str) -> list[str]:
    """더한 것이 검색한 사람에게 필요한 것인지 (분량 채우기 막기). 길이를 늘리는 게 목표가 아님 — 늘지 않으면 +@ 가 없는 것,
    너무 늘면 꼭 필요한 것만 넣었는지 다시 보게."""
    out = []
    added = record.get("added") or []
    if not added:
        return ["더한 내용(added) 목록이 없음 — 더한 것마다 {type, for, text}"]
    questions = [str(q) for q in (record.get("serp") or {}).get("questions") or []]
    for a in added:
        kind = a.get("type") if isinstance(a, dict) else None
        if kind not in ADD_TYPES:
            out.append(f"더한 것의 종류가 없거나 정해진 종류가 아님: {str(a)[:40]} — {', '.join(ADD_TYPES)} 중 하나만 (배경 설명·분량 채우기는 넣지 않음)")
        elif kind == "+질문" and str(a.get("for", "")) not in questions:
            out.append(f"'+질문' 은 serp.questions 의 질문에 답하는 것만: {str(a.get('for', ''))[:40]}")
        elif not str(a.get("for", "")).strip():
            out.append(f"'{kind}' 를 왜 넣는지(for: 이 키워드로 찾는 사람의 어떤 궁금증) 적기")
    if sum(1 for a in added if isinstance(a, dict) and str(a.get("type", "")).startswith("@")) > MAX_AT:
        out.append(f"@ 는 {MAX_AT}개까지 — 꼭 필요한 것만")
    old_len, new_len = len(pc.plain(old_html)), len(pc.plain(new_html))
    if new_len <= old_len:
        out.append("본문이 늘지 않음 — 더한 내용이 없음")
    elif new_len > old_len * GROW_MAX and new_len - old_len > GROW_MIN_CHARS:
        out.append(f"본문이 {int((GROW_MAX - 1) * 100)}% 넘게 늘어남 — 검색한 사람에게 꼭 필요한 것만 남기기")
    return out


from bot.condition_gate import REFERENT, _units, body_self_question, referent_moved  # noqa: E402,F401 (apply_review 의 check: 이름으로도 쓰려고 bot 쪽에 둠)


_WAGE = re.compile(r"(?:통상)?시급(?:이|은|을|으로|를)?\s*(?:(\d{1,3}(?:,\d{3})+|\d{4,6})원|(\d+)만(?:\s*(\d)천)?\s*원)")
_EXAMPLE = re.compile(r"예를 들어|라고 해|라고 할게|라고 하면|라면")


def min_wage() -> int:
    """올해 최저시급 — 계산기 자료(해마다 calc_watch 가 고시와 맞춤)에서 읽음."""
    path = ROOT / "wordpress" / "themes" / "first-lab" / "calculators" / "data" / "overtime-pay.json"
    return int(json.loads(path.read_text(encoding="utf-8"))["min_wage"])


def example_below_min_wage(sentences: list[str], floor: int) -> list[str]:
    """예시로 든 시급이 올해 최저시급보다 낮으면 법에 어긋나는 예시 (11/11 #1576 '통상시급 1만 원' — 2026년 10,320원)."""
    out = []
    for s in sentences:
        if not _EXAMPLE.search(s):
            continue
        for m in _WAGE.finditer(s):
            won = int(m.group(1).replace(",", "")) if m.group(1) else int(m.group(2)) * 10000 + int(m.group(3) or 0) * 1000
            if won < floor:
                out.append(f"예시 시급 {won:,}원이 올해 최저시급 {floor:,}원보다 낮음: '{s[:40]}…' — 최저시급 이상으로")
    return out


def problems(old_html: str, new_html: str, record: dict, title: str = "", allow: set[str] | None = None,
             written=None, post_id: int | None = None, check_copy: bool = True) -> list[str]:
    allow = allow or set()
    out = []
    review = record.get("review") or []
    verified = pc.facts(_verified_text(review))
    before, after = pc.facts(old_html), pc.facts(new_html)
    for kind in before:
        lost = [k for k in before[kind] if k not in after[kind] and k not in allow]
        if lost:
            out.append(f"{kind} 빠짐 (보강은 더하기만): {', '.join(lost[:8])}")
        added = [k for k in after[kind] if k not in before[kind] and k not in allow]
        if kind in ("숫자", "법 조항"):
            unverified = [k for k in added if k not in verified[kind]]
            if unverified:
                out.append(f"새 {kind}가 검수 기록의 '맞음'(공식 주소) 주장에 없음: {', '.join(unverified[:8])}")
    bad = [c.get("claim", "")[:40] for c in review if c.get("verdict") != "맞음" and c.get("action") in ("유지", "수정")]
    if bad:
        out.append(f"'맞음'이 아닌데 본문에 남긴 주장 {len(bad)}개: {' / '.join(bad[:3])} — 빼거나 완화")
    out += added_problems(record, old_html, new_html)
    out += referent_moved(old_html, new_html)
    out += body_self_question(old_html, new_html)
    lost_terms = pc.lost_terms(old_html, new_html, title)
    if lost_terms:
        out.append(f"검색어 줄어듦: {', '.join(lost_terms[:8])}")
    dup = pc.duplicated_text(old_html, new_html)
    if dup:
        out.append(f"같은 내용이 두 번 나옴 {len(dup)}곳 (예: {dup[0][:40]}…)")
    out += pc.expired_deadlines(new_html, written) if written else []
    out += pc.style_problems(new_html)
    from bot import condition_gate, serp_gate
    out += condition_gate.known_wrong(new_html)
    old_sents = set(condition_gate._sentences(old_html))
    new_sents = [x for x in condition_gate._sentences(new_html) if x not in old_sents]
    out += condition_gate.source_absence(new_sents)
    out += example_below_min_wage(new_sents, min_wage())
    out += condition_gate.html_unbalanced(new_html)
    out += condition_gate.faq_answer_flip(new_html)
    out += condition_gate.readability(new_html) if hasattr(condition_gate, "readability") else []
    if post_id is not None:
        from bot import patch_check
        out += patch_check.reintroduced(post_id, new_html)
    out += serp_gate.problems(record.get("serp"), new_html)
    if check_copy:
        out += serp_gate.copied(record.get("serp"), new_html)
    return out


def refresh_file(post_id: int, base_modified: str, new_html: str, record: dict, fix_reason: str = "",
                 title: str = "", excerpt: str = "") -> Path:
    path = ROOT / "wordpress" / "queue" / f"refresh-plus-{post_id}-{datetime.now():%Y%m%d-%H%M%S}.json"
    upto = len(json.loads((ROOT / "wordpress" / "patches.json").read_text(encoding="utf-8")))
    added = "; ".join(f"{a.get('type')} {a.get('text', '')}" if isinstance(a, dict) else str(a) for a in record.get("added") or [])[:300]
    path.write_text(json.dumps({"type": "refresh", "kind": "plus", "post_id": post_id, "base_modified": base_modified,
                                "content_html": new_html, "patches_upto": upto,
                                "serp": record.get("serp"), "review": record.get("review"),
                                **({"title": title, "excerpt": excerpt} if excerpt else {}),
                                "changes": f"+@ 보강: {added}" + (f" / 원래 글 오류 정정: {fix_reason}" if fix_reason else "")},
                               ensure_ascii=False), encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    post_id = int(argv[0])
    original = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    new_html = pc.split_long_paragraphs(Path(argv[2]).read_text(encoding="utf-8"))
    record = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
    fix = next((a.split("=", 1)[1] for a in argv if a.startswith("--fix=")), "")
    if fix and ":" not in fix:
        print("--fix=숫자|숫자:근거 — 바꾸는 이유(법·공식 자료)를 꼭 적기")
        return 2
    nums = fix.split(":", 1)[0]
    allow = set(nums.split("|" if "|" in nums else ",")) if fix else set()
    base = original.get("base_modified") or ""
    written = datetime.strptime(base[:10], "%Y-%m-%d").date() if base else None
    found = problems(original["content_html"], new_html, record, original.get("title", ""), allow, written, post_id)
    excerpt_arg = next((a.split("=", 1)[1] for a in argv if a.startswith("--excerpt=")), "")
    new_excerpt = Path(excerpt_arg).read_text(encoding="utf-8").strip() if excerpt_arg else ""
    found += pc.excerpt_problems(original.get("excerpt", ""), original["content_html"], new_html, new_excerpt)
    if found:
        print("통과 못 함:\n  " + "\n  ".join(found))
        return 1
    print("통과")
    if "--write" in argv:
        if not base:
            print("원본 JSON 에 base_modified 가 없음 — 그 사이 바뀐 글을 덮을 수 있어 갱신 파일을 만들지 않음")
            return 1
        print(refresh_file(post_id, base, new_html, record, fix.split(":", 1)[1] if fix else "",
                           original.get("title", "") if new_excerpt else "", new_excerpt))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
