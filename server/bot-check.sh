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



echo -e "\n==> [임시] 경쟁도 vs 키워드도구 지표 상관 분석 (DB 읽기만)"
/opt/first-lab-venv/bin/python - <<'PY' 2>&1 | tail -40 || true
import sqlite3, math, statistics as st
c = sqlite3.connect("file:/var/lib/first-lab/bot/keywords.db?mode=ro", uri=True)
rows = c.execute("SELECT keyword,total,ad_comp,ad_clicks,difficulty,comp_info FROM keywords WHERE difficulty IS NOT NULL").fetchall()
print("경쟁도 조회된 키워드:", len(rows))
def corr(x, y):
    mx, my = st.mean(x), st.mean(y); sx = math.sqrt(sum((a-mx)**2 for a in x)); sy = math.sqrt(sum((b-my)**2 for b in y))
    return round(sum((a-mx)*(b-my) for a, b in zip(x, y)) / (sx*sy), 2) if sx and sy else None
if len(rows) >= 5:
    d = [r[4] for r in rows]
    feats = {
        "log 검색량": [math.log10(max(r[1], 1)) for r in rows],
        "광고경쟁(높음1/중간.5/낮음0)": [{"높음": 1, "중간": .5}.get(r[2] or "", 0) for r in rows],
        "log 광고클릭": [math.log10(1 + (r[3] or 0)) for r in rows],
        "클릭/검색량 비율": [(r[3] or 0) / max(r[1], 1) for r in rows],
        "글자수": [len(r[0].replace(" ", "")) for r in rows],
    }
    for k, v in feats.items():
        print(f"{k:28} 상관 {corr(v, d)}")
    for r in sorted(rows, key=lambda r: r[4]):
        print(f"{r[4]:.2f} {r[0]} 검색{r[1]} 광고{r[2]} 클릭{r[3]:.0f}")
PY
