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


def run() -> str:
    sys.path.insert(0, str(ROOT / "automation"))
    from verify_patches import problems  # noqa: E402

    from . import wpbulk
    from .state import add_log, load_state, save_state
    patches = json.loads((ROOT / "wordpress" / "patches.json").read_text(encoding="utf-8"))
    bulk = wpbulk.posts()
    refreshed = _refreshed_ids()
    # 갱신(refresh)으로 다시 쓴 글은 예전 패치 문구가 없는 게 정상 → 확인에서 뺌 (11/5 검토: 영원히 안 풀리는 항목)
    posts = {int(pid): {"title": p.get("title", ""), "excerpt": p.get("excerpt", ""), "content_html": p.get("content", ""),
                        "status": "trash" if int(pid) in refreshed else p.get("status", "")} for pid, p in bulk.items()}
    found = problems(patches, posts)
    state = load_state()
    state["patch_problems"] = found[:20]
    msg = f"고친 내용 서버 반영 확인: 패치 {len(patches)}개 중 문제 {len(found)}개" + (f" — {found[0][:80]}" if found else "")
    add_log(state, not found, msg)
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
