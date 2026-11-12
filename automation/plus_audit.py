"""+@ 보강 누락 점검 (운영자 11/11 '동시에 돌린다고 누락하지 말고 똑바로').

묶음마다 감시 세션이 돌린다:
  1. 올린 보강(wordpress/queue/refresh-plus-*.json)이 서버 글에 반영됐나 (/post 의 plus_at)
  2. 시작했는데 안 끝난 글: /tmp/firstlab-review/plus-N.json 은 있는데 refresh-plus 파일이 없음
사용: python3 automation/plus_audit.py   (문제 없으면 종료코드 0)
"""
from __future__ import annotations

import calendar
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "automation"))
import tick  # noqa: E402

QUEUE = ROOT / "wordpress" / "queue"
OUT = Path("/tmp/firstlab-review")
DEPLOY_GRACE = 30 * 60   # 올린 지 30분 안이면 아직 배포 전일 수 있음


def pushed(queue: Path = QUEUE) -> dict[int, Path]:
    """글 번호 → 가장 최근 refresh-plus 파일."""
    found: dict[int, Path] = {}
    for f in sorted(queue.glob("refresh-plus-*.json")):
        m = re.match(r"refresh-plus-(\d+)-", f.name)
        if m:
            found[int(m.group(1))] = f
    return found


def made_at(f: Path) -> float:
    """파일 이름의 시각(UTC, refresh-plus-N-YYYYmmdd-HHMMSS.json). git pull 은 mtime 을 바꾸므로 이름을 본다."""
    m = re.search(r"-(\d{8}-\d{6})\.json$", f.name)
    return calendar.timegm(time.strptime(m.group(1), "%Y%m%d-%H%M%S")) if m else f.stat().st_mtime


def started(out: Path = OUT) -> set[int]:
    """보강을 시작한 글 (plus_queue 가 plus-N.json 을 만든 글)."""
    return {int(m.group(1)) for f in out.glob("plus-*.json")
            if (m := re.fullmatch(r"plus-(\d+)\.json", f.name))}


def unfinished(started_ids: set[int], pushed_ids: set[int]) -> list[int]:
    return sorted(started_ids - pushed_ids)


def main() -> int:
    files = pushed()
    now = time.time()
    missing, waiting, unread = [], [], []
    for pid, f in sorted(files.items()):
        try:
            post = tick.get(f"{tick.SITE}/wp-json/firstlab/v1/post/{pid}", token=True)
        except OSError:  # 11/13 서버 부하 중 연결 끊김 한 번에 점검 전체가 멈춤 → 그 글만 '못 읽음'으로 남기고 계속
            unread.append(pid)
            continue
        if post.get("plus_at"):
            continue
        (waiting if now - made_at(f) < DEPLOY_GRACE else missing).append(pid)
    todo = unfinished(started(), set(files))
    print(f"올린 보강 {len(files)}편 · 서버 반영 {len(files) - len(missing) - len(waiting) - len(unread)}편"
          + (f" · 못 읽음 {len(unread)}편(다음 점검에 다시)" if unread else ""))
    print(f"배포 기다림(30분 안): {waiting or '없음'}")
    print(f"반영 안 됨: {missing or '없음'}")
    print(f"시작했는데 안 올린 글: {todo or '없음'}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
