"""월정액(직접 작성) 방식: 서버가 Claude 예약 작업(claude.ai)을 실행시킨다 → Claude 가 글을 써서 저장소에 올림.

API 요금이 아니라 구독 사용량으로 처리된다. 공식 문서: https://code.claude.com/docs/en/routines (API trigger)
비밀값 (/etc/first-lab/secrets.env, server/set-secrets.sh 로 입력):
  CLAUDE_ROUTINE_FIRE_URL  예약 작업 편집 화면 > Trigger + API 의 Fire URL
  CLAUDE_ROUTINE_TOKEN     같은 화면의 Generate token 값 (이 예약 작업 실행 권한만 있음)
주의: 이 기능은 연구용 미리보기라 형식이 바뀔 수 있다 (BETA 헤더 날짜). 실패하면 봇 기록에 남는다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

BETA = "experimental-cc-routine-2026-04-01"
TIMEOUT = 30
MANUAL_TEXT = "manual-request"     # '지금 1개 생성' 버튼으로 실행됨 (예약 작업 지시문이 이 값만 인식)
LATE_LIMIT = timedelta(hours=3)    # 서버가 멈췄다 켜져도 3시간 넘게 지난 시각은 실행하지 않음


class RoutineError(RuntimeError):
    pass


def fire(fire_url: str, token: str, text: str = "") -> str:
    """예약 작업 실행. 성공하면 세션 주소, 실패하면 RoutineError."""
    if not (fire_url and token):
        raise RoutineError("CLAUDE_ROUTINE_FIRE_URL / CLAUDE_ROUTINE_TOKEN 이 서버에 없음 (server/set-secrets.sh)")
    body = json.dumps({"text": text} if text else {}).encode()
    req = urllib.request.Request(fire_url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": BETA,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RoutineError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RoutineError(f"연결 실패: {e}") from e
    return str(data.get("claude_code_session_url") or data.get("session_url") or data.get("session_id") or "실행됨")


def due_slot(times: list[str], fired: list[str], now: datetime) -> str | None:
    """오늘 실행할 '직접 작성 시각' 중 시각이 됐고 아직 실행 안 한 가장 이른 것 (now: 한국 시간, 시간대 정보 없음)."""
    for t in sorted(times):
        hour, minute = map(int, t.split(":"))
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if t not in fired and at <= now < at + LATE_LIMIT:
            return t
    return None
