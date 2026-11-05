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

echo -e "\n==> 공공 API 점검 (키 값은 출력 안 함)"
# 법제처 API 는 신청 때 등록한 서버 주소에서만 됨 — 이 서버가 밖으로 나갈 때 쓰는 주소 (등록값과 같아야 함)
echo "이 서버가 나가는 주소: $(curl -s --max-time 10 https://api.ipify.org || echo 확인 실패)"
cd "$REPO_DIR" && /opt/first-lab-venv/bin/python - <<'PY' 2>&1 | tail -3
from bot import law_api, benefit_api
body = law_api.fetch("service", "law", ID="001761") or ""
print("법령 API:", "정상" if body else "실패 (신청 승인·등록한 서버 주소 확인)")
got = benefit_api.fetch("serviceList", perPage=1)
print("공공서비스 혜택 API:", "정상" if got and got.get("data") else "실패")
import datetime as dt
from bot import naver_hub
keys = naver_hub._keys()
if not keys:
    print("네이버 API HUB: 키 없음")
else:
    try:  # 호출 2회 (하루 상한 안에서 셈)
        today = dt.date.today()
        series = naver_hub.monthly_trend(["연말정산"], keys, today).get("연말정산", {})
        print(f"네이버 API HUB: 정상 (연말정산 블로그 문서 {naver_hub.blog_totals(['연말정산'], keys, today).get('연말정산')}개, "
              f"트렌드 {len(series)}개월, 성수기 {naver_hub.season(series)[0]}월)")
    except Exception as e:  # noqa: BLE001
        print(f"네이버 API HUB: 실패 ({type(e).__name__}: {str(e)[:120]})")
PY
(cd "$REPO_DIR" && /opt/first-lab-venv/bin/python -m bot.naver_hub --diag 2>&1 | tail -1) || true
tail -2 /var/lib/first-lab/bot/golden-first.log 2>/dev/null | cut -c1-400 || true

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


