"""내부 링크용 관련 글 후보.

새 글을 쓸 때 이미 '발행된' 글 중 관련 있어 보이는 후보를 골라 글쓰기 AI 에 넘긴다.
실제로 링크를 걸지는 AI 가 본문 내용을 보고 판단한다 (관련 없으면 안 건다).
"""
from __future__ import annotations

import json
import urllib.parse

from .keyword_db import norm
from .settings import wp_cli

MAX_CANDIDATES = 8

_PHP = r"""
$out = array();
foreach ( get_posts( array( 'post_status' => 'publish', 'numberposts' => 300 ) ) as $p ) {
    $out[] = array(
        'title' => $p->post_title,
        'slug'  => urldecode( $p->post_name ),
        'url'   => get_permalink( $p ),
        'cats'  => wp_get_post_categories( $p->ID, array( 'fields' => 'names' ) ),
    );
}
// 계산기 단독 페이지(슬러그 ...-calculator)도 링크 후보 — 그 계산기를 넣은 공개 글이 있으면 그 글이 이미 후보라 뺌 (11/6, 넘어가는 주소)
foreach ( get_posts( array( 'post_type' => 'page', 'post_status' => 'publish', 'numberposts' => 100 ) ) as $p ) {
    if ( str_ends_with( $p->post_name, '-calculator' ) && ! ( function_exists( 'firstlab_calc_page_target' ) && firstlab_calc_page_target( $p ) ) ) {
        $out[] = array( 'title' => $p->post_title, 'slug' => $p->post_name, 'url' => get_permalink( $p ), 'cats' => array() );
    }
}
echo wp_json_encode( $out );
"""


def _bigrams(text: str) -> set[str]:
    t = norm(text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def published_posts() -> list[dict]:
    try:
        return json.loads(wp_cli("eval", _PHP) or "[]")
    except Exception:  # noqa: BLE001 - 링크 후보 실패가 글쓰기를 막지 않게
        return []


def candidates(keyword: str, posts: list[dict] | None = None) -> list[dict]:
    """키워드와 제목·주소가 많이 겹치는 순 + 최신 글 일부 (AI 가 최종 판단)."""
    posts = published_posts() if posts is None else posts
    kw = _bigrams(keyword)
    scored = sorted(posts, key=lambda p: -len(kw & (_bigrams(p["title"]) | _bigrams(p["slug"]))))
    picked = [p for p in scored if kw & (_bigrams(p["title"]) | _bigrams(p["slug"]))][:MAX_CANDIDATES - 3]
    for p in posts[:10]:  # 최신 글도 몇 개 (주제가 달라 보여도 연결될 수 있음)
        if p not in picked and len(picked) < MAX_CANDIDATES:
            picked.append(p)
    return picked


def prompt_section(cands: list[dict]) -> str:
    if not cands:
        return ""
    lines = "\n".join(f"- {c['title']} : {c['url']}" for c in cands)
    return ("[내부 링크]\n아래는 우리 블로그의 기존 글이다. 본문 내용과 실제로 관련 있는 글이 있으면 "
            "관련 문장 안에 <a href=\"주소\">자연스러운 문구</a> 형태로 1~3개 링크한다. "
            "관련 없으면 링크하지 않는다. 목록에 없는 주소는 절대 만들지 않는다.\n" + lines + "\n")


def _url_key(url: str) -> str:
    """한글 주소가 인코딩돼 있든 아니든, 끝 '/' 가 있든 없든 같은 주소로 본다."""
    return urllib.parse.unquote(url.strip()).rstrip("/")


def allowed_urls(posts: list[dict] | None = None) -> set[str]:
    """링크를 남겨도 되는 주소: 우리 사이트의 발행 글·계산기 페이지."""
    return {p["url"] for p in (published_posts() if posts is None else posts)}


def clean_links(html: str, allowed: set[str]) -> tuple[str, int]:
    """AI 가 넣은 링크 중 우리 글 목록에 있는 주소만 남김 (지어낸 주소·외부 링크 제거). (본문, 남은 링크 수)"""
    import re

    kept = 0
    allowed_keys = {_url_key(u) for u in allowed}

    def repl(m: "re.Match") -> str:
        nonlocal kept
        if _url_key(m.group(1)) in allowed_keys:
            kept += 1
            return m.group(0)
        return m.group(2)  # 링크만 풀고 문구는 유지

    html = re.sub(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', repl, html, flags=re.S | re.I)
    return html, kept
