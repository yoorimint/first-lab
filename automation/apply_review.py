"""외부 검수 결과 반영 (감시 세션용): 패치 검사 → patches.json 저장 → 자주 틀린 유형표 갱신 → 그다음에만 검수 완료 기록.

왜: 패치 저장이 실패했는데 검수 완료(reviewed.txt)만 기록된 일이 두 번 있었다 (11/5 #416, #542 — 명령을 ';' 로 이어
앞 단계 실패에도 다음 단계가 돌았음). 한 도구가 순서대로 하고, 하나라도 틀리면 아무것도 바꾸지 않는다.

사용: python3 automation/apply_review.py <글번호> "<제목>" [패치목록.json]
  패치목록.json: [{"type", "why", "find", "replace_paragraph" | "remove_element", "tag", "prevention"} 또는 {"type", "why", "set_title" | "set_excerpt", "prevention"}]
  prevention (필수, 11/5 운영자 '하나라도 수정했으면 다음부터 그런 오류가 안 생기게 해야지'): 재발 방지 없이는 고치지 않는다.
    "check:<함수·테스트 이름>"  이 오류를 다음부터 막는 코드 검사 (저장소에 있어야 함 — condition_gate 의 검사는 고치기 전 글에 실제로 걸려야 통과)
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


PREVENTION_LOG = ROOT / "automation" / "review-only-log.md"


def _prevention_problem(post: dict, p: dict) -> str | None:
    """재발 방지 표시가 없거나 거짓이면 이유."""
    prev = str(p.get("prevention", "")).strip()
    if prev.startswith("review_only:"):
        if not TYPES.get(p.get("type"), {}).get("review_only"):
            return f"'{p.get('type')}' 유형은 코드 검사가 있는 유형 — review_only 대신 check:<검사 이름> (검사가 못 잡았으면 검사를 고치고 테스트에 이 글 사례 추가)"
        return None if len(prev) >= len("review_only:") + 10 else "review_only 이유가 너무 짧음 (왜 코드로 못 막는지)"
    if prev.startswith("check:"):
        name = prev[len("check:"):].strip()
        code = "".join(f.read_text(encoding="utf-8") for f in list((ROOT / "bot").glob("*.py")) + list((ROOT / "tests").glob("*.py")))
        if not re.fullmatch(r"\w+", name) or not re.search(rf"\b(def|class) {name}\b", code):
            return f"check:{name} 를 저장소 코드·테스트에서 못 찾음 — 검사를 먼저 만들기"
        from bot import condition_gate
        fn = {"headline_scope": lambda: condition_gate.headline_scope(post["title"], post["excerpt"], post["content_html"]),
              "lead_vs_body": lambda: condition_gate.lead_vs_body(post["content_html"]),
              "excerpt_vs_exclusions": lambda: condition_gate.excerpt_vs_exclusions(post["excerpt"], post["content_html"]),
              "lead_thresholds": lambda: condition_gate.lead_thresholds(post["excerpt"], post["content_html"]),
              "title_qualifier": lambda: condition_gate.title_qualifier(post["title"], post["content_html"]),
              "lead_minimums": lambda: condition_gate.lead_minimums(post["excerpt"], post["content_html"]),
              "variant_only": lambda: condition_gate.variant_only(post["content_html"]),
              "law_absence": lambda: condition_gate.law_absence(post["content_html"])}.get(name)
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
        if p.get("replace_paragraph", "").lstrip().startswith(f"<{tag}"):
            errors.append(f"{i}번: 고친 문구는 <{tag}> 안쪽만 (겉 태그까지 넣으면 이중 문단이 됨, 11/5 #578)")
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


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    post_id, title = int(argv[1]), argv[2]
    items = json.loads(Path(argv[3]).read_text(encoding="utf-8")) if len(argv) > 3 else []
    post = json.loads((OUT_DIR / f"post-{post_id}.json").read_text(encoding="utf-8"))
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
    print(f"글 {post_id}: 패치 {len(items)}개 저장, 검수 완료 기록")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
