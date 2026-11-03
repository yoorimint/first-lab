#!/usr/bin/env bash
# 봇 연결 점검: 서버에 키가 있으면 네이버 검색량 API를 시험 호출한다. (키 값은 출력하지 않음)
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SECRETS=/etc/first-lab/secrets.env

echo -e "\n==> 봇 연결 점검"
if [ ! -f "$SECRETS" ]; then
  echo "키 파일 없음($SECRETS) → 건너뜀"; exit 0
fi
chmod 600 "$SECRETS"
cd "$REPO_DIR" && python3 -m bot.keywords 다이어트 --top 5 \
  && echo "네이버 검색광고 API: 정상" \
  || echo "네이버 검색광고 API: 실패 (키 값을 다시 확인하세요)"

echo -e "\n==> 봇 프로세스"
systemctl status firstlab-bot.service --no-pager 2>/dev/null | sed -n '1,4p' || true
systemctl status firstlab-discover.service --no-pager 2>/dev/null | sed -n '1,4p' || true
journalctl -u firstlab-discover.service -n 5 --no-pager -o cat 2>/dev/null | grep "\[수집\]\|키워드 수집" || true
ls -la /var/lib/first-lab/bot/ 2>/dev/null || echo "키워드 창고 폴더 없음"

echo -e "\n==> 봇 상태"
/opt/first-lab-venv/bin/python -m bot.status 2>&1 | tail -12 || true

echo -e "\n==> 봇 오류 (systemd, 최근)"
journalctl -u firstlab-bot.service --since "-5min" --no-pager -o cat 2>/dev/null \
  | grep -vE "pam_unix|Starting|Finished|Deactivated|Consumed" | tail -15 || true

