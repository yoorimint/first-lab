"""법무사 사무실 상담 문의 → 텔레그램 (11/27 운영자 '배너 눌러 문의 적으면 알림 가게').

문의는 워드프레스가 비공개 글(fl_consult)로 저장한다 (wordpress/mu-plugins/first-lab-consult.php).
매분 run.main 이 run() 을 부른다:
- 아직 안 보낸 문의(메타 _fl_notified 없음)를 텔레그램으로 보내고 표시. 실패하면 표시하지 않아 다음 분에 다시 보낸다.
- 키(FIRSTLAB_TELEGRAM_TOKEN, FIRSTLAB_TELEGRAM_CHAT)는 서버 /etc/first-lab/secrets.env 에만 (server/set-secrets.sh).
  키가 없으면 문의는 그대로 쌓이고, 1시간에 한 번 봇 기록에 '키 없음'을 남긴다 (키를 넣으면 다음 분에 밀린 것부터 보냄).
- 보관 기간(RETAIN_DAYS)이 지난 문의는 지운다 — 동의문에 적은 기간과 같다 (tests 가 PHP 상수와 맞는지 확인).
"""
from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .settings import wp_cli

POST_TYPE = "fl_consult"
SENT_META = "_fl_notified"
RETAIN_DAYS = 30          # first-lab-consult.php FIRSTLAB_CONSULT_RETAIN_DAYS 와 같아야 함
BATCH = 20                # 한 번에 보낼 최대 (밀렸을 때 1분 안에 끝나게)
TOKEN_KEY, CHAT_KEY = "FIRSTLAB_TELEGRAM_TOKEN", "FIRSTLAB_TELEGRAM_CHAT"


def pending(wp=wp_cli) -> list[dict]:
    """아직 안 보낸 문의 (오래된 것부터)."""
    out = wp("post", "list", f"--post_type={POST_TYPE}", "--post_status=private", f"--meta_key={SENT_META}",
             "--meta_compare=NOT EXISTS", "--orderby=date", "--order=ASC", f"--posts_per_page={BATCH}",
             "--fields=ID,post_title,post_content,post_date", "--format=json")
    rows = json.loads(out or "[]")
    return [r for r in rows if isinstance(r, dict) and "ID" in r] if isinstance(rows, list) else []


def message(row: dict) -> str:
    """텔레그램 문구 (글자만 — 손님이 쓴 내용에 꾸밈 문자가 있어도 깨지지 않게)."""
    return f"[퍼스트랩 상담 문의] {row.get('post_title', '')}\n접수 {row.get('post_date', '')}\n\n{row.get('post_content', '')}".strip()


def send(token: str, chat: str, text: str, opener=urllib.request.urlopen) -> bool:
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
    try:
        with opener(req, timeout=10) as r:
            return bool(json.loads(r.read().decode() or "{}").get("ok"))
    except Exception:  # noqa: BLE001 — 실패는 다음 분에 다시
        return False


def purge(now: datetime, wp=wp_cli) -> int:
    """보관 기간이 지난 문의를 지움 → 지운 수. 보낸 것·못 보낸 것 모두 (개인정보 보관 기간 약속)."""
    rows = json.loads(wp("post", "list", f"--post_type={POST_TYPE}", "--post_status=any", "--posts_per_page=-1",
                         "--fields=ID,post_date", "--format=json") or "[]")
    cut = now.replace(tzinfo=None) - timedelta(days=RETAIN_DAYS)
    n = 0
    for r in rows if isinstance(rows, list) else []:
        try:
            old = datetime.strptime(str(r["post_date"]), "%Y-%m-%d %H:%M:%S") < cut
        except (KeyError, ValueError, TypeError):
            continue   # 날짜를 못 읽으면 지우지 않음 (확실하지 않으면 지우지 않는다)
        if old:
            wp("post", "delete", str(r["ID"]), "--force")
            n += 1
    return n


def _secrets() -> dict[str, str]:
    from .config import load_secrets
    try:
        return load_secrets()
    except OSError:
        return {}


def run(now: datetime, secrets: dict[str, str] | None = None, wp=wp_cli, opener=urllib.request.urlopen) -> list[tuple[bool, str]]:
    """새 문의 보내기 + 10분마다 오래된 문의 지우기 → 봇 기록 [(성공 여부, 문구)]."""
    logs: list[tuple[bool, str]] = []
    if now.minute % 10 == 2:
        try:
            gone = purge(now, wp)
            if gone:
                logs.append((True, f"상담 문의 {gone}건 삭제 (보관 {RETAIN_DAYS}일 지남)"))
        except (subprocess.CalledProcessError, ValueError) as e:
            logs.append((False, f"상담 문의 정리 실패: {str(e)[:120]}"))
    rows = pending(wp)
    if not rows:
        return logs
    keys = _secrets() if secrets is None else secrets
    token, chat = keys.get(TOKEN_KEY, ""), keys.get(CHAT_KEY, "")
    if not token or not chat:
        if now.minute == 0:   # 1시간에 한 번만 (매분 같은 줄이 쌓이지 않게)
            logs.append((False, f"상담 문의 {len(rows)}건 대기: 텔레그램 키 없음 (server/set-secrets.sh {TOKEN_KEY} {CHAT_KEY})"))
        return logs
    sent = 0
    for r in rows:
        if not send(token, chat, message(r), opener):
            logs.append((False, f"상담 문의 텔레그램 전송 실패 — {len(rows) - sent}건 다음 분에 다시"))
            break   # 텔레그램이 안 되면 나머지도 다음에
        wp("post", "meta", "update", str(r["ID"]), SENT_META, now.strftime("%Y-%m-%d %H:%M"))
        sent += 1
    if sent:
        logs.append((True, f"상담 문의 {sent}건 텔레그램 전송"))
    return logs
