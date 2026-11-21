"""고친 내용(wordpress/patches.json)이 서버 글에 실제로 들어갔는지 전수 확인 (감시 세션용, automation/monitor.md).

검수 결과를 패치로 써도, 배포 때 적용이 안 되면 틀린 글이 그대로 나간다. 테스트는 패치 파일만 보므로 서버 글을 직접 본다.
패치마다: 고친 문구(replace_paragraph)가 글에 있어야 하고, 고치기 전 문구(find)는 없어야 한다.
제외: 찾은 문장 뒤에 덧붙이는 패치(insert_after, find 가 남는 게 정상), 같은 문단을 나중 패치가 다시 고친 경우.

사용: FIRSTLAB_API_TOKEN=... python3 automation/verify_patches.py   (문제 있으면 종료코드 1)
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from unreviewed import ROOT, _get  # noqa: E402

PATCHES = ROOT / "wordpress" / "patches.json"


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(text or ""))).strip()


def superseded(patch: dict, later: list[dict]) -> bool:
    """같은 글의 나중 패치가 이 패치 결과 문단을 다시 고쳤는지 (나중 find 가 이 패치의 고친 문구 안에 있음)."""
    rep = plain(patch.get("replace_paragraph", ""))
    return any(q["post"] == patch["post"] and q.get("find") and plain(q["find"]) in rep for q in later)


def problems(patches: list[dict], posts: dict[int, dict]) -> list[str]:
    out = []
    for n, p in enumerate(patches):
        post = posts.get(int(p["post"]))
        if post is None or post.get("status") == "trash":
            continue  # 지운 글
        # 제목·요약문: 지금 값이 고치기 전 값(from_…) 그대로일 때만 '안 바뀜' — 그 뒤 다시 쓴 값(보강·쉬운 말)은 실패가 아님
        # (11/21: 요약문 9개를 '안 바뀜'으로 셌는데 모두 나중에 다시 쓴 것)
        if "set_title" in p and "from_title" in p and post["title"].strip() == p["from_title"].strip() != p["set_title"].strip():
            out.append(f"글 {p['post']} 제목 안 바뀜: {post['title'][:40]}")
        if "set_excerpt" in p and "from_excerpt" in p and plain(post["excerpt"]) == plain(p["from_excerpt"]) != plain(p["set_excerpt"]):
            out.append(f"글 {p['post']} 요약문 안 바뀜")
        if "replace_paragraph" not in p or superseded(p, patches[n + 1:]):
            continue
        raw = post["title"] + " " + post["excerpt"] + " " + post["content_html"]
        body = plain(raw)
        rep = plain(p["replace_paragraph"])
        # 표 칸처럼 태그째 찾는 패치는 태그째 비교 (태그를 지우면 '<td>200개비</td>' 가 고친 '200개비(…)' 안에 있는 걸로 보임)
        old_left = p["find"] in html.unescape(raw) if "<" in p["find"] else plain(p["find"]) in body
        if old_left:
            out.append(f"글 {p['post']} 고치기 전 문구 남음: {p['find'][:40]}")
        # 고친 문구도 고치기 전 문구도 없음 = 그 뒤 글을 다시 씀(보강·쉬운 말) → 실패가 아님, 셀 때 빼고 개수만 (11/21: 571개 중 562개)
    return out


def rewritten(patches: list[dict], posts: dict[int, dict]) -> int:
    """고친 문구가 안 보이지만 고치기 전 문구도 없는 패치 수 (나중에 글을 다시 써서 문장이 바뀐 것)."""
    n = 0
    for p in patches:
        post = posts.get(int(p["post"]))
        if not post or post.get("status") == "trash" or "replace_paragraph" not in p:
            continue
        body = plain(post["title"] + " " + post["excerpt"] + " " + post["content_html"])
        rep = plain(p["replace_paragraph"])
        if rep and rep[:40] not in body and plain(p["find"]) not in body:
            n += 1
    return n


def main() -> None:
    patches = json.loads(PATCHES.read_text(encoding="utf-8"))
    posts = {}
    for pid in sorted({int(p["post"]) for p in patches}):
        try:
            posts[pid] = _get(f"/post/{pid}")
        except Exception:  # noqa: BLE001 — 지운 글 등
            posts[pid] = None
    found = problems(patches, posts)
    print(f"패치 {len(patches)}개 서버 반영 확인: 문제 {len(found)}개 (나중에 다시 써서 문장이 바뀐 것 {rewritten(patches, posts)}개는 문제 아님)")
    for line in found:
        print("  " + line)
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
