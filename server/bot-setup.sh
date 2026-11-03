#!/usr/bin/env bash
# 자동 발행 봇 설치: 파이썬 가상환경 + 1분 주기 systemd 타이머 (여러 번 실행해도 안전)
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV=/opt/first-lab-venv
REQ="$REPO_DIR/bot/requirements.txt"

log() { echo -e "\n==> $*"; }

log "파이썬 가상환경"
dpkg -s python3-venv &>/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
# requirements 가 바뀌었을 때만 설치
if ! cmp -s "$REQ" "$VENV/.installed-requirements"; then
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$REQ"
  cp "$REQ" "$VENV/.installed-requirements"
fi
"$VENV/bin/pip" list 2>/dev/null | grep -iE "^(anthropic|google-genai|openai) "

log "systemd 타이머 (1분마다 실행)"
install -m 644 "$REPO_DIR"/server/systemd/firstlab-bot.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now firstlab-bot.timer
systemctl is-active firstlab-bot.timer
