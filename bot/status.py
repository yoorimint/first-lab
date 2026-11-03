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
        print("후보 상위 10:", [f"{c['keyword']}({c['total']})" for c in ranked_candidates(conn, settings, 10)])
    print("설정: AI =", settings.provider, "| 키", "있음" if settings.api_key else "없음",
          "| 자동발행", "켜짐" if settings.enabled else "꺼짐", "| 방식", settings.post_status)
    print("오늘 계획:", state.get("plan"), "완료:", state.get("done"))
    for entry in state["log"][:5]:
        print(" ", "✅" if entry["ok"] else "❌", entry["time"], entry["msg"][:200])


if __name__ == "__main__":
    main()
