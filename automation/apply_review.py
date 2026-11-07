"""외부 검수 결과 반영 (감시 세션용): 패치 검사 → patches.json 저장 → 자주 틀린 유형표 갱신 → 그다음에만 검수 완료 기록.

왜: 패치 저장이 실패했는데 검수 완료(reviewed.txt)만 기록된 일이 두 번 있었다 (11/5 #416, #542 — 명령을 ';' 로 이어
앞 단계 실패에도 다음 단계가 돌았음). 한 도구가 순서대로 하고, 하나라도 틀리면 아무것도 바꾸지 않는다.

사용: python3 automation/apply_review.py <글번호> "<제목>" [패치목록.json]
  패치목록.json: [{"type", "why", "find", "replace_paragraph" | "remove_element", "tag", "prevention"} 또는 {"type", "why", "set_title" | "set_excerpt", "prevention"}]
  prevention (필수, 11/5 운영자 '하나라도 수정했으면 다음부터 그런 오류가 안 생기게 해야지'): 재발 방지 없이는 고치지 않는다.
    "check:<함수 이름>"  이 오류를 다음부터 막는 코드 검사 (CHECK_MODULES 안의 함수여야 함 — condition_gate 의 검사는 고치기 전 글에 실제로 걸려야 통과)
    "review_only:<이유>"        코드로 못 막는 사실 오류(공식 값과 다름 등) — 이유를 남기고 automation/review-only-log.md 에 쌓임 (같은 꼴이 쌓이면 검사로 옮김)
  패치가 없으면(오류 0) 파일 없이 실행 → 검수 완료만 기록.
  본문은 /tmp/firstlab-review/post-<글번호>.json (automation/unreviewed.py 가 받아 둔 것) 과 대조한다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # bot 패키지
from common_errors import main as rebuild_common_errors  # noqa: E402
from unreviewed import OUT_DIR, REVIEWED, ROOT  # noqa: E402

PATCHES = ROOT / "wordpress" / "patches.json"
TYPES = json.loads((ROOT / "automation" / "error_types.json").read_text(encoding="utf-8"))
# 숫자 자체가 틀렸다고 고치는 유형 — 이 밖의 유형(조건 추가·완화)은 공식 페이지에서 확인된 숫자를 지우면 안 됨
NUMBER_FIX_TYPES = {"wrong_fact", "outdated", "calc", "my_mistake", "headline"}


def _plain(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).replace(",", "")


def element_around(body: str, find: str, tag: str) -> str:
    """찾을 문구가 든 문단·목록·표 줄 전체 (고치면 통째로 바뀌는 범위)."""
    i = body.find(find)
    start = body.rfind(f"<{tag}", 0, i)
    end = body.find(f"</{tag}>", i)
    return body[start if start >= 0 else i: end if end >= 0 else i + len(find)]


def verified_numbers(post: dict) -> list[str]:
    """서버 근거 대조에서 공식 페이지에 있다고 확인된 숫자 (source_check found)."""
    found = (post.get("source_check") or {}).get("found") or []
    return sorted({n for item in found for n in item.get("numbers", [])})


PLAIN_REVIEW_ONLY = ("unverified", "no_source")   # 근거 대조가 맞춰 볼 숫자(세 자리 이상·비율)가 없는 문단일 때만 review_only 허용


def _has_claim_number(text: str) -> bool:
    from bot.source_check import key_numbers
    return bool(key_numbers(_plain(text)))


def _numbers_kept(p: dict, element: str) -> bool:
    """확인 못 한 문구(find) 자체엔 숫자가 없고, 문단의 숫자는 고친 문구에 그대로 남음 — 숫자가 아니라 숫자 없는 안내(접수처·절차)를
    완화한 것이라 근거 대조(숫자 검사)로는 못 막음 (11/6 #1150: 확인된 '65개'와 같은 문단의 'HRD-Net 접수'를 완화하는데 막힘)."""
    from bot.source_check import key_numbers
    if _has_claim_number(p.get("find", "")):
        return False
    after = set(key_numbers(_plain(p.get("replace_paragraph", ""))))
    return set(key_numbers(_plain(element))) <= after


PREVENTION_LOG = ROOT / "automation" / "review-only-log.md"
CHECK_MODULES = ("condition_gate.py", "review_gate.py", "source_check.py", "writer.py", "freshness.py", "expiry.py")  # 글을 막거나 표시하는 검사가 있는 곳


def _prevention_problem(post: dict, p: dict) -> str | None:
    """재발 방지 표시가 없거나 거짓이면 이유."""
    prev = str(p.get("prevention", "")).strip()
    if prev.startswith("review_only:"):
        element = element_around(post["content_html"], p.get("find", ""), p.get("tag", "p"))
        if p.get("type") in PLAIN_REVIEW_ONLY and (not _has_claim_number(element) or _numbers_kept(p, element)):
            # 근거 대조(bot/source_check)는 숫자만 페이지와 맞춰 봄 — 숫자 없는 단정(11/6 #874 '공항버스 제외')은 코드로 못 봄
            return None if len(prev) >= len("review_only:") + 10 else "review_only 이유가 너무 짧음 (왜 코드로 못 막는지)"
        if not TYPES.get(p.get("type"), {}).get("review_only"):
            return f"'{p.get('type')}' 유형은 코드 검사가 있는 유형 — review_only 대신 check:<검사 이름> (검사가 못 잡았으면 검사를 고치고 테스트에 이 글 사례 추가)"
        return None if len(prev) >= len("review_only:") + 10 else "review_only 이유가 너무 짧음 (왜 코드로 못 막는지)"
    if prev.startswith("check:"):
        name = prev[len("check:"):].strip()
        # 검사 모듈의 함수만 (11/5 #847: 감시 세션이 상관없는 refresh.title_has_keyword 를 적어 통과 — 이름만 맞으면 되던 구멍)
        code = "".join((ROOT / "bot" / f).read_text(encoding="utf-8") for f in CHECK_MODULES)
        if not re.fullmatch(r"\w+", name) or not re.search(rf"\b(def|class) {name}\b", code):
            return f"check:{name} 를 저장소 코드·테스트에서 못 찾음 — 검사를 먼저 만들기"
        from bot import condition_gate, expiry, source_check
        fn = {"headline_scope": lambda: condition_gate.headline_scope(post["title"], post["excerpt"], post["content_html"]),
              "lead_vs_body": lambda: condition_gate.lead_vs_body(post["content_html"]),
              "lead_vs_exceptions": lambda: condition_gate.lead_vs_exceptions(post["content_html"]),
              "title_length": lambda: condition_gate.title_length(post["title"]),
              "title_numbers": lambda: len(condition_gate.title_numbers(post["title"])) > 1,
              "calc_coverage": lambda: condition_gate.calc_coverage(post["content_html"]),
              "cross_law_exceptions": lambda: condition_gate.cross_law_exceptions(post["content_html"]),
              "excerpt_qualifiers": lambda: condition_gate.excerpt_qualifiers(post["excerpt"], post["content_html"]),
              "excerpt_caveats": lambda: condition_gate.excerpt_caveats(post["excerpt"], post["content_html"]),
              "excerpt_parentheticals": lambda: condition_gate.excerpt_parentheticals(post["excerpt"], post["content_html"]),
              "limited_event_year": lambda: condition_gate.limited_event_year(post["excerpt"], post["content_html"]),
              "deadline_holiday": lambda: condition_gate.deadline_holiday(post["content_html"]),
              "facility_ordinance": lambda: condition_gate.facility_ordinance(post["title"], post.get("review"), post["excerpt"], post["content_html"]),
              "upcoming_amendments": lambda: condition_gate.upcoming_amendments(post.get("review"), post["content_html"]),
              "notice_year_label": lambda: condition_gate.notice_year_label(post["title"], post["excerpt"], post["content_html"]),
              "unresolved_delegation": lambda: condition_gate.unresolved_delegation(post["content_html"]),
              "county_scope": lambda: condition_gate.county_scope(post["content_html"]),
              "readability": lambda: condition_gate.readability(post["content_html"]),
              "average_wage_floor": lambda: condition_gate.average_wage_floor(post["content_html"]),
              "labor_day_exception": lambda: condition_gate.labor_day_exception(post["content_html"]),
              "holiday_attendance": lambda: condition_gate.holiday_attendance(post["excerpt"], post["content_html"]),
              "round_headline": lambda: expiry.round_headline(post["title"], post["excerpt"], " ".join(expiry.blocks(post["content_html"]))),
              "headline_from_price": lambda: condition_gate.headline_from_price(post["title"], post["excerpt"], post["content_html"]),
              "small_workplace_items": lambda: condition_gate.small_workplace_items(post["content_html"]),
              "ordinary_wage_rule": lambda: condition_gate.ordinary_wage_rule(post["content_html"]),
              "approx_rounding": lambda: condition_gate.approx_rounding(post["content_html"]),
              "retention_start": lambda: condition_gate.retention_start(post["content_html"]),
              "ordinance_delegation": lambda: condition_gate.ordinance_delegation(post["title"], post["excerpt"], post["content_html"], post.get("review")),
              "stale_forms": lambda: condition_gate.stale_forms(post.get("review")),
              # 길 화살표('A→B→C 하루 21회') 주장의 숫자를 계산으로 봐서 대조 안 했음 (11/6 #1060) — 이제 대조함
              "is_calc_claim": lambda: [c for c in (str(i.get("claim", "")) for i in post.get("review") or [])
                                              if "→" in c and source_check.key_numbers(c) and not source_check.is_calc_claim(c)],
              "transition_rules": lambda: condition_gate.transition_rules(post.get("review"), post["content_html"]),
              "excerpt_vs_exclusions": lambda: condition_gate.excerpt_vs_exclusions(post["excerpt"], post["content_html"]),
              "lead_thresholds": lambda: condition_gate.lead_thresholds(post["excerpt"], post["content_html"]),
              "title_qualifier": lambda: condition_gate.title_qualifier(post["title"], post["content_html"]),
              "lead_minimums": lambda: condition_gate.lead_minimums(post["excerpt"], post["content_html"]),
              "variant_only": lambda: condition_gate.variant_only(post["content_html"]),
              "law_absence": lambda: condition_gate.law_absence(post["content_html"]),
              "excerpt_apply_date": lambda: condition_gate.excerpt_apply_date(post["excerpt"], post["content_html"])}.get(name)
        if fn and not fn():
            return f"check:{name} 가 고치기 전 글에 안 걸림 — 이 오류를 실제로 잡는 검사가 아님"
        return None
    return "prevention 없음 — 'check:<검사 이름>' 또는 'review_only:<이유>' (재발 방지 없이 고치지 않는다, 11/5 운영자)"


def check(post: dict, items: list[dict]) -> list[str]:
    """저장 전 검사: 유형 등록, 찾을 문구가 지금 본문에 딱 한 번, 고친 문구에 찾을 문구가 남지 않음."""
    errors = []
    body = post["content_html"]
    for i, p in enumerate(items, 1):
        if p.get("type") not in TYPES:
            errors.append(f"{i}번: 오류 유형 '{p.get('type')}' 이 automation/error_types.json 에 없음")
        if not p.get("why"):
            errors.append(f"{i}번: why 없음")
        bad = _prevention_problem(post, p)
        if bad:
            errors.append(f"{i}번: {bad}")
        if "set_title" in p or "set_excerpt" in p:
            continue
        find = p.get("find", "")
        if body.count(find) != 1:
            errors.append(f"{i}번: 찾을 문구가 본문에 {body.count(find)}번 (1번이어야 함): {find[:40]}")
        tag = p.get("tag", "p")
        if re.search(rf"</(?:p|li|{tag}|h\d)>", find):  # 표 칸(</td>)은 줄(tr) 안이라 괜찮음  # 닫는 태그를 넣으면 다음 문단까지 바뀔 수 있음 (11/5 #51·#59·#70)
            errors.append(f"{i}번: 찾을 문구에 닫는 태그(</p> 등)를 넣지 않는다 — 문단 안의 글자만: {find[-40:]}")
        if re.search(rf"<{re.escape(tag)}[\s>]", find):  # 서버(bot/fix_posts)는 <태그…>와 </태그> 사이에서 찾음 — 여는 태그를 넣으면 못 찾아 안 고쳐짐 (11/6 #1028)
            errors.append(f"{i}번: 찾을 문구에 여는 태그(<{tag}>)를 넣지 않는다 — 문단 안의 글자만: {find[:40]}")
        if p.get("replace_paragraph", "").lstrip().startswith(f"<{tag}"):
            errors.append(f"{i}번: 고친 문구는 <{tag}> 안쪽만 (겉 태그까지 넣으면 이중 문단이 됨, 11/5 #578)")
        if "replace_paragraph" in p and tag in ("p", "li"):  # 검수 수정이 조건을 덧붙여 글을 빽빽하게 만들던 것 (11/7 운영자 가독성)
            from bot.condition_gate import readability_element
            errors += [f"{i}번: 읽기 — {x}" for x in readability_element(tag, p["replace_paragraph"])]
        if find and find in p.get("replace_paragraph", ""):
            errors.append(f"{i}번: 고친 문구 안에 찾을 문구가 그대로 있음 (고친 뒤에도 다시 걸림): {find[:40]}")
        # 조건 추가·완화가 공식 페이지에서 확인된 사실까지 지우지 않게 (11/5 #673: 공단 원문 그대로인 200원·230원을 지우고 '확인하세요'로만 바꿈)
        if p.get("type") not in NUMBER_FIX_TYPES and "replace_paragraph" in p and body.count(find) == 1:
            from bot.source_check import has_number
            before, after = _plain(element_around(body, find, tag)), _plain(p["replace_paragraph"])
            lost = [n for n in verified_numbers(post) if has_number(before, n) and not has_number(after, n)]
            if lost:
                errors.append(f"{i}번: 공식 페이지에서 확인된 숫자 {', '.join(lost)} 를 지움 — 조건 추가·완화는 확인된 사실을 남긴다 "
                              "(숫자가 틀렸으면 근거 원문을 직접 확인하고 type 을 wrong_fact·outdated 로)")
    return errors


def with_title_fields(post: dict, p: dict) -> dict:
    """제목·요약 수정은 원래 값(from_…)을 붙여 한 번만 적용되게."""
    if "set_title" in p:
        p.setdefault("from_title", post["title"])
    if "set_excerpt" in p:
        p.setdefault("from_excerpt", post["excerpt"])
    return p


QUALITY_LOG = ROOT / "automation" / "quality-log.csv"   # 외부 검수에서 고친 곳 수 — 품질이 유지·향상되는지 숫자로 (11/6 운영자 '퀄리티는 유지하거나 더 올리고')


def record_quality(post_id: int, items: list[dict], path: Path = QUALITY_LOG, today: str | None = None) -> None:
    """날짜, 글 번호, 고친 곳 수, 유형별 수 (제목·요약 고침은 headline). 같은 글을 다시 고치면 줄이 더 생김 (재검수도 품질 기록)."""
    import collections
    import datetime as dt
    kinds = collections.Counter(str(p.get("type", "")) for p in items)
    date = today or f"{dt.date.today():%Y-%m-%d}"
    new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write("date,post,fixes,types\n")
        f.write(f"{date},{post_id},{len(items)},{' '.join(f'{k}:{v}' for k, v in sorted(kinds.items()))}\n")


def with_review(post: dict, queue_dir: Path | None = None) -> dict:
    """검수 기록(review)을 붙임 — 서버 본문에는 없어서 review 를 보는 검사(transition_rules·is_calc_claim)가 늘 '안 걸림'이었음
    (11/6 #1060). 보류 수정 파일(fixhold-<번호>-*) → 같은 제목의 처음 글 파일 순."""
    if post.get("review"):
        return post
    queue_dir = queue_dir or ROOT / "wordpress" / "queue"
    pid = str(post.get("post_id", ""))
    paths = sorted(queue_dir.glob(f"fixhold-{pid}-*.json"), reverse=True) + sorted(queue_dir.glob("*.json"))
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        own_fix = path.name.startswith(f"fixhold-{pid}-")  # 11/6 #1178: 다른 글의 fixhold 파일 검수 기록이 붙었음
        if isinstance(data, dict) and data.get("review") and (own_fix or (not path.name.startswith("fixhold-")
                                                                          and data.get("title") == post.get("title"))):
            return {**post, "review": data["review"]}
    return post


def current_post(post_id: int, fetch=None) -> dict:
    """서버의 지금 본문으로 검사 (11/6: 예전에 받아 둔 검수 파일로 검사해 제목 길이 검사가 '안 걸림'으로 잘못 나옴 —
    그사이 바뀐 본문이면 찾을 문구·검사 결과가 틀림). 서버를 못 읽으면 받아 둔 파일로."""
    try:
        if fetch is None:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from unreviewed import save_body as fetch
        return json.loads(fetch(post_id).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"서버 본문을 못 읽음({type(e).__name__}) → 받아 둔 파일로 검사")
        return json.loads((OUT_DIR / f"post-{post_id}.json").read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    post_id, title = int(argv[1]), argv[2]
    items = json.loads(Path(argv[3]).read_text(encoding="utf-8")) if len(argv) > 3 else []
    post = with_review(current_post(post_id))
    errors = check(post, items)
    if errors:
        print("반영 안 함 (아무것도 바꾸지 않음):\n  " + "\n  ".join(errors))
        return 1
    if items:
        data = json.loads(PATCHES.read_text(encoding="utf-8"))
        data += [{"post": post_id, **with_title_fields(post, dict(p))} for p in items]
        PATCHES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rebuild_common_errors()
        only = [p for p in items if str(p.get("prevention", "")).startswith("review_only:")]
        if only:  # 코드로 못 막은 것은 쌓아 두고, 같은 꼴이 모이면 검사로 옮긴다
            with PREVENTION_LOG.open("a", encoding="utf-8") as f:
                for p in only:
                    f.write(f"- #{post_id} [{p.get('type')}] {p['prevention'][len('review_only:'):].strip()} — {str(p.get('why', ''))[:80]}\n")
    with REVIEWED.open("a", encoding="utf-8") as f:  # 패치를 저장한 뒤에만
        f.write(f"{post_id} {title}\n")
    record_quality(post_id, items)
    print(f"글 {post_id}: 패치 {len(items)}개 저장, 검수 완료 기록")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
