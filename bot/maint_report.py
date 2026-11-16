"""전체 글 보정(server/maint.sh) 결과 한 줄을 봇 기록(/maintenance log)에 — 단계마다 걸린 시간 (11/16 '왜 부하 생기냐').
사용: python -m bot.maint_report "기존 글 보정 끝 (1 번째): fix_posts 120초 patch_check 30초 ..."
"""
from __future__ import annotations

import sys


def main(line: str) -> None:
    from .state import add_log, load_state, save_state
    state = load_state()
    add_log(state, "실패" not in line, line[:300])
    save_state(state)


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]))
