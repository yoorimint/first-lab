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
import urllib.error
import urllib.request

from . import sources

META = "_firstlab_official_hosts"
VERSION = 2  # 2: 조사 붙은 주소 인식(11/9) — 예전 기록은 다시 확인
BATCH = 3
MAX_HOSTS = 6
# 글자 속 도메인 (앞에 주소·메일·경로가 붙은 것은 뺌 — 그건 이미 링크이거나 다른 말)
# 앞뒤 경계는 영문·숫자만 — \w 는 한글도 쳐서 'license.kpc.or.kr에서'(조사)가 주소로 안 잡혔음 (11/9 ITQ 글)
HOST_RE = re.compile(r"(?<![A-Za-z0-9_.@/-])((?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\.kr)?)(?![A-Za-z0-9_-]|\.[a-z])", re.I)


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


DEAD_RETRY_TIMEOUT = 25
DEAD_PREFIX = "본문 공식 주소가 연결되지 않음: "


def _exists(url: str) -> bool:
    """주소가 실제로 있는지 (응답이 오면 403·404 여도 있음 — 봇을 막는 공식 사이트도 있어서). 연결 자체가 안 되면 없음."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (first-lab link check)"})
    for timeout in (sources.TIMEOUT, DEAD_RETRY_TIMEOUT):  # 한 번 느린 것으로 '없는 주소' 판정하지 않게 한 번 더 (11/10 ols.semas.or.kr 보류)
        try:
            with urllib.request.urlopen(req, timeout=timeout):
                return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue
    return False


def _resolves(host: str) -> bool:
    """도메인 이름이 등록돼 있는지 (DNS). 11/10: nip.kdca.go.kr·semas.or.kr 은 실제 있는 공식 주소인데 해외 접속을 늦게 받거나 막아
    서버에서 연결이 안 됨 → '없는 주소'로 보류됐음. 주소를 잘못 적은 경우는 보통 이름 자체가 없음."""
    import socket
    try:
        socket.getaddrinfo(host, 443)
        return True
    except (socket.gaierror, UnicodeError, OSError):
        return False


def dead_hosts(hosts: list[str], exists=_exists, resolves=None) -> list[str]:
    """없는 주소 — 이름(DNS)도 없고 연결도 안 되는 것만. 본문에 적힌 주소가 틀렸을 가능성 (공개하지 않고 보류, 11/9 운영자 '초안 때 들어가 보고 맞게')."""
    resolves = resolves or _resolves
    return [h for h in hosts if not (resolves(h) or resolves(f"www.{h}"))
            and not any(exists(u) for u in (f"https://{h}", f"https://www.{h}", f"http://{h}"))]


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
  if ( is_array( $m ) && ( $m['hash'] ?? '' ) === substr( md5( $c ), 0, 12 ) && (int) ( $m['v'] ?? 1 ) >= VERSION ) { continue; }
  $out[] = array( 'id' => $id, 'content' => $c, 'status' => get_post_status( $id ) );
  if ( count( $out ) >= BATCH ) { break; }
}
echo wp_json_encode( $out );
"""


def run(batch: int = BATCH, wp=None, check=sources.reachable, today: str | None = None, exists=_exists) -> str | None:
    """기록이 없거나 본문이 바뀐 글 batch 편을 확인해 기록. 봇 기록 한 줄 (한 편도 안 했으면 None)."""
    if wp is None:
        from .settings import wp_cli as wp
    todo = json.loads(wp("eval", _TODO_PHP.replace("BATCH", str(int(batch))).replace("VERSION", str(VERSION))) or "[]")
    done = []
    for row in todo if isinstance(todo, list) else []:
        record = _record(row.get("content", ""), check, today, exists)
        wp("post", "meta", "update", str(row["id"]), META, json.dumps(record, ensure_ascii=False))
        if record["dead"] and row.get("status") == "draft":
            hold_dead(int(row["id"]), record["dead"], wp)
        done.append(f"{row['id']}({len(record['hosts'])})")
    return f"공식 사이트 링크 확인: {', '.join(done)}" if done else None


def _record(content: str, check=sources.reachable, today: str | None = None, exists=_exists) -> dict:
    """{'hash', 'v', 'hosts': 링크로 쓸 열린 주소, 'dead': 연결조차 안 되는 주소, 'checked'}."""
    hosts = hosts_in(content)
    found = verify(hosts, check)
    dead = dead_hosts([h for h in hosts if h not in {f["host"] for f in found}], exists)
    return {"hash": content_hash(content), "v": VERSION, "hosts": found, "dead": dead,
            "checked": today or f"{dt.date.today():%Y-%m-%d}"}


def run_for(post_id: int, wp=None, check=sources.reachable, today: str | None = None, exists=_exists) -> dict:
    """한 글을 지금 바로 확인해 기록 (공개 직전·화면 점검에서 — 10분 차례를 기다리지 않게, 11/9 ITQ 글). 기록을 돌려줌."""
    if wp is None:
        from .settings import wp_cli as wp
    record = _record(wp("post", "get", str(int(post_id)), "--field=post_content"), check, today, exists)
    wp("post", "meta", "update", str(int(post_id)), META, json.dumps(record, ensure_ascii=False))
    return record


def dead_reason(dead: list[str]) -> str:
    return f"{DEAD_PREFIX}{', '.join(dead)} — 주소가 맞는지 공식 기관 누리집으로 확인해 고치기"


def recheck_dead(wp=None, exists=_exists, release=None) -> list[str]:
    """'공식 주소 연결 안 됨'으로 보류된 초안의 주소를 다시 열어 봄 — 이제 열리면 보류를 풀고 공개 대기로 (11/10: ols.semas.or.kr 이
    서버에서 한 번 안 열려 보류됐는데 실제로는 열리는 주소 — 글쓰기 작업이 멈춘 동안 평생 보류가 되지 않게). 봇이 한 시간마다."""
    if wp is None:
        from .settings import wp_cli as wp
    from .review_gate import HOLD_META
    if release is None:
        from .schedule import mark_ready

        def release(pid):
            wp("post", "meta", "delete", str(pid), HOLD_META)
            wp("post", "meta", "delete", str(pid), META)  # 본문 링크 기록도 다시 (열린 주소를 링크로)
            mark_ready(pid)
    rows = json.loads(wp("post", "list", "--post_type=post", "--post_status=draft", f"--meta_key={HOLD_META}",
                         "--fields=ID", "--format=json", "--posts_per_page=-1") or "[]")
    out = []
    for row in rows:
        pid = int(row["ID"])
        reason = wp("post", "meta", "get", str(pid), HOLD_META) or ""
        if not reason.startswith(DEAD_PREFIX):
            continue
        hosts = [h.strip() for h in reason[len(DEAD_PREFIX):].split(" — ")[0].split(",") if h.strip()]
        if hosts and not dead_hosts(hosts, exists):
            release(pid)
            out.append(f"글 {pid}: {', '.join(hosts)} 이제 열림 → 보류 풀고 공개 대기")
    return out


def hold_dead(post_id: int, dead: list[str], wp) -> None:
    """초안을 공개 대기에서 빼고 보류 (글쓰기 작업의 보류 수정이 고침 — 고치면 본문이 바뀌어 다시 확인됨). 11/9 운영자 '초안에서 해결'."""
    from .review_gate import HOLD_META
    from .schedule import QUEUE_META
    wp("post", "meta", "update", str(post_id), HOLD_META, dead_reason(dead)[:300])
    try:
        wp("post", "meta", "delete", str(post_id), QUEUE_META)
    except Exception:  # noqa: BLE001 - 대기 표시가 원래 없으면 오류로 끝남
        pass
