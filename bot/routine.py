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

BETA = "experimental-cc-routine-2026-04-01"
TIMEOUT = 30
MANUAL_TEXT = "manual-request"     # '지금 1개 생성' 버튼으로 실행됨
MAINT_TEXT = "maintenance-check"   # 매일 점검으로 실행됨 → 예약 작업이 글 대신 automation/maintenance.md 를 따름


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

