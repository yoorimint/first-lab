"""외부 검수 결과 반영 (감시 세션용): 패치 검사 → patches.json 저장 → 자주 틀린 유형표 갱신 → 그다음에만 검수 완료 기록.

왜: 패치 저장이 실패했는데 검수 완료(reviewed.txt)만 기록된 일이 두 번 있었다 (11/5 #416, #542 — 명령을 ';' 로 이어
앞 단계 실패에도 다음 단계가 돌았음). 한 도구가 순서대로 하고, 하나라도 틀리면 아무것도 바꾸지 않는다.

사용: python3 automation/apply_review.py <글번호> "<제목>" [패치목록.json]
  패치목록.json: [{"type", "why", "find", "replace_paragraph" | "remove_element", "tag"} 또는 {"type", "why", "set_title" | "set_excerpt"}]
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


def check(post: dict, items: list[dict]) -> list[str]:
    """저장 전 검사: 유형 등록, 찾을 문구가 지금 본문에 딱 한 번, 고친 문구에 찾을 문구가 남지 않음."""
    errors = []
    body = post["content_html"]
    for i, p in enumerate(items, 1):
        if p.get("type") not in TYPES:
            errors.append(f"{i}번: 오류 유형 '{p.get('type')}' 이 automation/error_types.json 에 없음")
        if not p.get("why"):
            errors.append(f"{i}번: why 없음")
        if "set_title" in p or "set_excerpt" in p:
            continue
        find = p.get("find", "")
        if body.count(find) != 1:
            errors.append(f"{i}번: 찾을 문구가 본문에 {body.count(find)}번 (1번이어야 함): {find[:40]}")
        tag = p.get("tag", "p")
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
    with REVIEWED.open("a", encoding="utf-8") as f:  # 패치를 저장한 뒤에만
        f.write(f"{post_id} {title}\n")
    print(f"글 {post_id}: 패치 {len(items)}개 저장, 검수 완료 기록")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
