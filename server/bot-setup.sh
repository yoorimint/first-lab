#!/usr/bin/env bash
# 자동 발행 봇 설치: 파이썬 가상환경 + 1분 주기 systemd 타이머 (여러 번 실행해도 안전)
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV=/opt/first-lab-venv
REQ="$REPO_DIR/bot/requirements.txt"

log() { echo -e "\n==> $*"; }

log "파이썬 가상환경"
for pkg in python3-venv fonts-nanum; do  # fonts-nanum: 제목 카드 한글 글꼴
  dpkg -s "$pkg" &>/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$pkg"
done
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
# requirements 가 바뀌었을 때만 설치
if ! cmp -s "$REQ" "$VENV/.installed-requirements"; then
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$REQ"
  cp "$REQ" "$VENV/.installed-requirements"
fi
"$VENV/bin/pip" list 2>/dev/null | grep -iE "^(anthropic|google-genai|openai) "

log "기존 글 보정 (블록 형식, 대표 이미지)"
(cd "$REPO_DIR" && "$VENV/bin/python" -m bot.fix_posts) || echo "보정 실패 (봇 동작에는 영향 없음)"

log "systemd 타이머 (1분마다 실행)"
install -m 644 "$REPO_DIR"/server/systemd/firstlab-{bot,discover}.{service,timer} /etc/systemd/system/
systemctl daemon-reload
# 이전 버전 코드로 오래 멈춰 있는 실행이 있으면 정리 (12분 초과)
if systemctl is-active -q firstlab-bot.service; then
  start_ts=$(systemctl show firstlab-bot.service -p ExecMainStartTimestampMonotonic --value 2>/dev/null || echo 0)
  now_us=$(awk '{printf "%d", $1*1000000}' /proc/uptime)
  if [ "${start_ts:-0}" -gt 0 ] && [ $(( (now_us - start_ts) / 60000000 )) -ge 12 ]; then
    echo "오래 멈춘 봇 실행 정리"; systemctl stop firstlab-bot.service
  fi
fi
systemctl enable --now firstlab-bot.timer firstlab-discover.timer
systemctl is-active firstlab-bot.timer firstlab-discover.timer

log "키워드 수집 (오늘 아직 안 했으면 바로 시작, 백그라운드)"
if systemctl is-active -q firstlab-discover.service; then
  echo "이미 수집 중"
elif [ "$(cat /var/lib/first-lab/bot/discover-date-v2 2>/dev/null)" = "$(date +%F)" ]; then
  echo "오늘 수집 완료 → 건너뜀"
else
  systemctl start --no-block firstlab-discover.service && echo "수집 시작됨"
fi
