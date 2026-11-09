"""공개 글 화면 점검 (11/9 운영자 '발행하기 전에도 확인한다며 — 이딴 식으로 할래?').

공개 직전 확인(bot/freshness.py)은 글의 사실(근거 조문·공식 숫자)만 보고, 화면은 안 봤다.
그래서 ITQ 글이 공개됐는데 본문 'license.kpc.or.kr에서 해요' 가 링크가 아니었다 (화면 링크는 봇이 그 주소를 열어 본 기록이 있어야 붙음).

1) 공개하는 순간 그 글의 공식 주소 확인을 바로 한다 (official_links.run_for — 10분 차례를 기다리지 않음).
2) 최근 공개 글의 실제 화면을 열어, 본문에 글자로만 남은 공식 주소가 있으면 봇 기록에 ❌ 로 남기고 다음 차례에 다시 확인한다
   (주소가 안 열려서 링크를 못 붙인 경우도 기록 — 매일 점검·감시가 봄). 같은 글·같은 문제는 하루 한 번만 기록.
"""
from __future__ import annotations

import re
import urllib.request

from . import official_links

SITE = "https://first-lab.kr"
RECENT_DAYS = 3
SECTION = "공식 안내 바로가기"
_CONTENT = re.compile(r'<div class="entry-content[^"]*wp-block-post-content[^"]*"[^>]*>', re.S)


def body_of(page_html: str) -> str:
    """화면 HTML 중 글 본문 (글 끝 '공식 안내 바로가기' 앞까지)."""
    m = _CONTENT.search(page_html or "")
    body = page_html[m.end():] if m else (page_html or "")
    cut = body.find(SECTION)
    return body[:cut] if cut >= 0 else body


def unlinked_hosts(page_html: str) -> list[str]:
    """본문에 글자로만 있고 링크가 안 된 공식 주소 (링크 안의 글자는 hosts_in 이 뺌)."""
    return official_links.hosts_in(body_of(page_html))


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "first-lab screen check"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


_RECENT_PHP = r"""
$out = array();
foreach ( get_posts( array( 'post_type' => 'post', 'post_status' => 'publish', 'numberposts' => 20, 'date_query' => array( array( 'after' => 'DAYS days ago' ) ) ) ) as $p ) {
  $out[] = array( 'id' => $p->ID, 'url' => get_permalink( $p ) );
}
echo wp_json_encode( $out );
"""


def run(state: dict, today: str, wp=None, fetch=_fetch) -> list[str]:
    """최근 공개 글 화면 점검. 새로 찾은 문제 줄 목록 (봇 기록에 ❌ 로)."""
    import json
    if wp is None:
        from .settings import wp_cli as wp
    seen = state.setdefault("screen_check", {})
    if seen.get("date") != today:
        seen.clear()
        seen["date"] = today
    out = []
    for row in json.loads(wp("eval", _RECENT_PHP.replace("DAYS", str(RECENT_DAYS))) or "[]"):
        try:
            missing = unlinked_hosts(fetch(row["url"]))
        except Exception:  # noqa: BLE001 - 화면을 못 열면 다음 차례에
            continue
        if not missing:
            continue
        official_links.run_for(int(row["id"]), wp=wp)  # 기록이 없거나 낡았으면 바로 다시 확인 (다음 화면에서 링크)
        key = f"{row['id']}:{','.join(missing)}"
        if key not in seen:
            seen[key] = True
            out.append(f"화면 점검: #{row['id']} 본문 공식 주소가 링크 안 됨 {', '.join(missing)} — 주소 확인 다시 함 (안 열리는 주소면 링크 못 붙임)")
    return out
