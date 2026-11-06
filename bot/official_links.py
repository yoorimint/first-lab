"""본문에 글자로 적힌 공식 사이트 주소를 서버가 열어 보고, 열리는 것만 그 글의 '링크 가능 주소'로 기록 (11/6 운영자 '그렇게 만들어라').

화면(wordpress/mu-plugins/first-lab-official-links.php)이 이 기록(META)에 있는 주소만 링크로 바꾼다 — 지어낸·틀린 주소는 안 열려서 링크가 안 됨.
공식 판단은 글 끝 '공식 안내 바로가기'와 같은 기준(bot/sources.is_official: .go.kr·.or.kr·korea.kr 등).
봇이 10분마다 BATCH 편씩 (본문이 바뀐 글은 다시) — 새 글·예전 글 모두 저절로.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

from . import sources

META = "_firstlab_official_hosts"
BATCH = 3
MAX_HOSTS = 6
# 글자 속 도메인 (앞에 주소·메일·경로가 붙은 것은 뺌 — 그건 이미 링크이거나 다른 말)
HOST_RE = re.compile(r"(?<![\w.@/-])((?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\.kr)?)(?![\w-]|\.[a-z])", re.I)


def content_hash(html: str) -> str:
    return hashlib.md5((html or "").encode("utf-8")).hexdigest()[:12]


def hosts_in(html: str) -> list[str]:
    """본문 글자(태그·링크 주소 제외)에 적힌 공식 도메인 (www. 뺀 형태, 나온 순서)."""
    text = re.sub(r"<a\b[^>]*>.*?</a>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    out = []
    for m in HOST_RE.finditer(text):
        host = m.group(1).lower()
        host = host[4:] if host.startswith("www.") else host
        if sources.is_official("https://" + host) and host not in out:
            out.append(host)
    return out[:MAX_HOSTS]


def verify(hosts: list[str], check=sources.reachable) -> list[dict]:
    """열리는 주소만 [{'host', 'url'}] (www. 없이 먼저, 안 되면 www.)."""
    out = []
    for host in hosts:
        for url in (f"https://{host}", f"https://www.{host}"):
            if check(url):
                out.append({"host": host, "url": url})
                break
    return out


_TODO_PHP = r"""
$out = array();
$ids = get_posts( array( 'post_type' => 'post', 'post_status' => array( 'publish', 'draft' ), 'numberposts' => -1, 'fields' => 'ids', 'orderby' => 'date', 'order' => 'DESC' ) );
foreach ( $ids as $id ) {
  $c = (string) get_post_field( 'post_content', $id );
  $m = json_decode( (string) get_post_meta( $id, '_firstlab_official_hosts', true ), true );
  if ( is_array( $m ) && ( $m['hash'] ?? '' ) === substr( md5( $c ), 0, 12 ) ) { continue; }
  $out[] = array( 'id' => $id, 'content' => $c );
  if ( count( $out ) >= BATCH ) { break; }
}
echo wp_json_encode( $out );
"""


def run(batch: int = BATCH, wp=None, check=sources.reachable, today: str | None = None) -> str | None:
    """기록이 없거나 본문이 바뀐 글 batch 편을 확인해 기록. 봇 기록 한 줄 (한 편도 안 했으면 None)."""
    if wp is None:
        from .settings import wp_cli as wp
    todo = json.loads(wp("eval", _TODO_PHP.replace("BATCH", str(int(batch)))) or "[]")
    done = []
    for row in todo if isinstance(todo, list) else []:
        found = verify(hosts_in(row.get("content", "")), check)
        record = {"hash": content_hash(row.get("content", "")), "hosts": found, "checked": today or f"{dt.date.today():%Y-%m-%d}"}
        wp("post", "meta", "update", str(row["id"]), META, json.dumps(record, ensure_ascii=False))
        done.append(f"{row['id']}({len(found)})")
    return f"공식 사이트 링크 확인: {', '.join(done)}" if done else None
