#!/usr/bin/env bash
# 전체 글을 한 편씩 읽는 보정 — 배포를 기다리게 하지 않게 따로 실행 (server/maint-start.sh 가 firstlab-maint 로 띄움, 11/9·11/16).
#   패치 반영·블록 형식(fix_posts) → 반영 확인(patch_check) → 본문 사진 정리(migrate_photos) → 사진 기다리는 글 표시(pending_images)
# 단계마다 걸린 시간을 봇 기록(/maintenance)에 한 줄로 남김 (11/16 '왜 부하 생기냐' — 얼마나 오래 도는지 기록이 없었음).
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
P=/opt/first-lab-venv/bin/python
AGAIN=/var/lib/first-lab/bot/maint-again
cd "$REPO_DIR"
# 한 번에 하나만 (11/17 09:04·09:17·09:22 에 20분짜리 보정 3개가 겹쳐 끝남 — 어디서 겹쳐 띄웠든 여기서 막음).
# 이미 도는 중이면 '한 번 더' 표시만 남기고 끝 → 도는 쪽이 끝날 때 한 번 더 함 (바뀐 패치를 놓치지 않게)
exec 9>/var/lib/first-lab/bot/maint-run.lock
if ! flock -n 9; then
  touch "$AGAIN"; echo "보정이 이미 도는 중 → 끝나면 한 번 더"; exit 0
fi
started=$(date '+%H:%M')
for round in 1 2 3; do
  rm -f "$AGAIN"
  times=""
  for mod in bot.fix_posts bot.patch_check bot.migrate_photos bot.pending_images; do
    echo "== $mod $(date '+%H:%M:%S')"
    start=$(date +%s)
    "$P" -m "$mod" || { echo "$mod 실패 (다음 배포 때 다시)"; times="$times ${mod#bot.} 실패"; }
    times="$times ${mod#bot.} $(( $(date +%s) - start ))초"
  done
  echo "== 끝 $(date '+%H:%M:%S')"
  "$P" -m bot.maint_report "기존 글 보정 끝 ($round 번째, $started 시작):$times" || true
  [ -f "$AGAIN" ] || break   # 도는 동안 새 배포가 '한 번 더'를 남겼으면 다시 (최대 3번)
done
