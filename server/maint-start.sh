#!/usr/bin/env bash
# 전체 글 보정(server/maint.sh)을 이름 붙은 별도 실행(firstlab-maint)으로 띄움 — bot-setup.sh·quick-deploy.sh 가 부름.
# 11/16 운영자 '왜 부하 생기냐': 배포 안에서 setsid 로 띄워 서버 CPU 기록에 '임시 실행'으로만 보였고(배포 없는 21시대에도 코어 1개 가까이),
# 배포가 겹치면 잠금 뒤에 줄줄이 기다렸다가 연달아 돌았음. 이미 도는 중이면 새로 띄우지 않고 '한 번 더' 표시만 →
# 도는 쪽이 끝날 때 한 번 더 (그 사이 바뀐 패치를 놓치지 않게, 여러 번 와도 한 번).
set -u
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR=/var/lib/first-lab/bot
LOG="$STATE_DIR/maint.log"
if command -v systemd-run >/dev/null; then
  if systemctl is-active --quiet firstlab-maint; then
    touch "$STATE_DIR/maint-again"; echo "기존 글 보정이 이미 도는 중 → 끝나면 한 번 더"; exit 0
  fi
  systemd-run --unit=firstlab-maint --collect --quiet -p CPUWeight=20 -p IOWeight=20 -p Nice=10 -p RuntimeMaxSec=10800 \
    bash -c "bash '$REPO_DIR/server/maint.sh' > '$LOG' 2>&1" \
    || { touch "$STATE_DIR/maint-again"; echo "기존 글 보정 시작 못 함(이미 있음) → 끝나면 한 번 더"; }
else
  setsid nohup flock "$STATE_DIR/maint.lock" bash "$REPO_DIR/server/maint.sh" > "$LOG" 2>&1 < /dev/null &
fi
