"""봇 상태 요약 (점검용): python3 -m bot.status"""
from .keyword_db import connect, summary
from .select_keyword import ranked_candidates
from .settings import load_settings
from .state import load_state


def main() -> None:
    settings = load_settings()
    state = load_state()
    with connect() as conn:
        print("키워드 창고:", summary(conn))
        print("후보 상위 15:", [f"{c['keyword']}({c['total']}{', ←' + c['head'] if c['head'] else ''})" for c in ranked_candidates(conn, settings, 15)])
    print("설정: AI =", settings.provider, "| 키", "있음" if settings.api_key else "없음",
          "| API 봇", "켜짐" if settings.enabled else "꺼짐", "| 방식", settings.post_status)
    print("예약 공개 시각:", settings.schedule_times, "±", settings.jitter_minutes, "분")
    print("작성 방식:", settings.write_mode, "| 글 작성 시각:", settings.direct_times if settings.direct_enabled else "꺼짐",
          "| Claude 예약 작업 토큰", "있음" if settings.routine_token else "없음",
          "| 실행 주소", "있음" if settings.routine_fire_url else "없음", "| 직접 작성 실행:", state.get("direct_fired", {}))
    print("오늘 공개 계획:", state.get("plan"), "공개 완료:", state.get("done"))
    for entry in state["log"][:5]:
        print(" ", "✅" if entry["ok"] else "❌", entry["time"], entry["msg"][:200])


if __name__ == "__main__":
    main()
