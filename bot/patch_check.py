"""고친 내용(wordpress/patches.json)이 서버 글에 실제로 들어갔는지 배포 때마다 확인 (server/bot-setup.sh, 보정 직후).

감시 세션이 손으로 돌리던 automation/verify_patches.py 를 서버에서 같은 기준으로 (글은 wp-cli 로 한 번에 읽음).
문제가 있으면 봇 기록에 오류로 남기고 state["patch_problems"] → /next → 글쓰기 작업이 그 글을 갱신 파일로 고친다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _refreshed_ids() -> set[int]:
    from .settings import wp_cli
    out = wp_cli("eval", "echo wp_json_encode( array_map( 'intval', get_posts( array( 'post_type' => 'post', 'post_status' => 'any', "
                         "'numberposts' => -1, 'fields' => 'ids', 'meta_key' => '_firstlab_refreshed' ) ) ) );")
    return set(json.loads(out or "[]"))


PLAIN_MARK = "<strong>한 줄 정리"  # 쉬운 말로 다시 쓴 글 표시 (automation/plain_queue.DONE_MARK 와 같음)


def rewritten(content: str) -> bool:
    return PLAIN_MARK in (content or "")


def _plain(text: str) -> str:
    import html
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(text or ""))).strip()


def reintroduced(post_id: int, content_html: str, patches: list[dict] | None = None) -> list[str]:
    """다시 쓴 본문에 예전 패치가 고친 '틀린 문구(find)'가 다시 들어 있으면 그 문구들.

    배포 때마다 bot/fix_posts.apply_patches 가 find 가 든 문단을 예전 고친 문단으로 덮어씀 →
    쉬운 말로 다시 쓴 문단·'한 줄 정리'가 사라지고 같은 내용이 두 번 나옴 (11/10 #1775 등 9편).
    끼워 넣기(insert_after)는 find 가 남는 게 정상이라 뺌."""
    if patches is None:
        patches = json.loads((ROOT / "wordpress" / "patches.json").read_text(encoding="utf-8"))
    body = _plain(content_html)
    out = []
    for p in patches:
        if int(p.get("post", 0)) != int(post_id) or not p.get("find") or "insert_after" in p:
            continue
        if not ("replace_paragraph" in p or p.get("remove_element") or p.get("remove_block")):
            continue
        find = p["find"]
        hit = find in content_html if "<" in find else _plain(find) in body
        if hit:
            out.append(f"예전에 고친 문구가 다시 들어감 (배포 때 예전 패치가 이 문단을 덮어씀) — 뜻은 고친 쪽대로, 말을 바꿔 쓰기: {_plain(find)[:50]}")
    return out


def run() -> str:
    sys.path.insert(0, str(ROOT / "automation"))
    from verify_patches import problems  # noqa: E402

    from . import wpbulk
    from .state import add_log, load_state, save_state
    patches = json.loads((ROOT / "wordpress" / "patches.json").read_text(encoding="utf-8"))
    bulk = wpbulk.posts()
    refreshed = _refreshed_ids()
    # 갱신(refresh)으로 다시 쓴 글은 예전 패치 문구가 없는 게 정상 → 확인에서 뺌 (11/5 검토: 영원히 안 풀리는 항목).
    # 쉬운 말 다듬기는 갱신일을 안 남기므로(사실 확인이 아님, bot/refresh.apply_direct) 본문 표시('한 줄 정리')로 가림 (11/10: 문제 13→31개)
    posts = {int(pid): {"title": p.get("title", ""), "excerpt": p.get("excerpt", ""), "content_html": p.get("content", ""),
                        "status": "trash" if int(pid) in refreshed or rewritten(p.get("content", "")) else p.get("status", "")}
             for pid, p in bulk.items()}
    found = problems(patches, posts)
    state = load_state()
    state["patch_problems"] = found[:20]
    msg = f"고친 내용 서버 반영 확인: 패치 {len(patches)}개 중 문제 {len(found)}개" + (f" — {found[0][:80]}" if found else "")
    if found:
        add_log(state, False, msg)
    else:  # 문제 없으면 하루 한 번만 (배포마다 '문제 0개'가 쌓여 다른 기록을 밀어냄, 11/6) — 패치 수는 빼고 비교
        from .state import add_log_once_a_day
        add_log_once_a_day(state, True, "고친 내용 서버 반영 확인: 문제 0개")
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
