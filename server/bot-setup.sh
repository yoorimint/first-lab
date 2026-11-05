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

log "봇 설정 일회성 변경 (운영자 승인 항목, 한 번만 적용 → 이후 설정 페이지 값 존중)"
# 사용: 이름=값 목록. 적용 기록(MARK)이 있으면 건너뛰므로 설정 페이지에서 다시 바꿔도 덮어쓰지 않는다.
apply_settings_once() {
  local mark="/var/lib/first-lab/bot/settings-$1.done"; shift
  [ -f "$mark" ] && return 0
  for kv in "$@"; do
    runuser -u firstlab -- wp --path=/var/www/first-lab option patch update firstlab_bot "${kv%%=*}" "${kv#*=}" \
      || { echo "설정 변경 실패: ${kv%%=*}"; return 0; }
  done
  touch "$mark" && echo "적용: $*"
}
apply_settings_once 2026-11-04-keywords discover_calls=2000 max_volume=10000

log "기존 글 보정 (블록 형식, 대표 이미지)"
(cd "$REPO_DIR" && "$VENV/bin/python" -m bot.fix_posts) || echo "보정 실패 (봇 동작에는 영향 없음)"
log "본문 AI 사진 → 대표 사진 1장으로 정리 (이미 정리된 글은 건너뜀)"
(cd "$REPO_DIR" && "$VENV/bin/python" -m bot.migrate_photos) || echo "사진 정리 실패 (봇 동작에는 영향 없음)"

log "예약 발행 대기열: 운영자 승인한 초안 넣기 (항목마다 한 번만)"
# 사용: queue_once <기록이름> <글번호...> — 아직 초안인 글만 공개 대기열에 넣는다
queue_once() {
  local mark="/var/lib/first-lab/bot/once-$1.done"; shift
  [ -f "$mark" ] && return 0
  for id in "$@"; do
    if [ "$(runuser -u firstlab -- wp --path=/var/www/first-lab post get "$id" --field=post_status 2>/dev/null)" = "draft" ]; then
      runuser -u firstlab -- wp --path=/var/www/first-lab post meta update "$id" _firstlab_queue ready --quiet && echo "대기열 추가: $id"
      runuser -u firstlab -- wp --path=/var/www/first-lab post meta delete "$id" _firstlab_hold --quiet 2>/dev/null || true  # 보류 표시 지움
    fi
  done
  touch "$mark"
}
queue_once 2026-11-04-queue-direct 80 85 90 99   # 기존 직접 작성 초안 (예전 API 봇 초안은 다시 쓴 뒤 넣음)
queue_once 2026-11-04-queue-119 119             # 베트남 여행 준비물: 공식 기관 목록 누락(korea.kr·airport.kr)으로 잘못 보류됐던 글
queue_once 2026-11-04-queue-144 144             # 건강보험료 환급금: 정부24(gov.kr) 누락·'틀림→수정' 판정 해석 오류로 잘못 보류됐던 글
queue_once 2026-11-05-queue-567 567             # ESTA 신청: 미국 정부(.gov) 근거를 관문이 공식 출처로 못 알아봐 잘못 보류 (sources.FOREIGN_GOV 로 고침, 고친 관문 재검사 통과)
# 독립 검수에서 핵심(단말기 부담금·지원 기간)을 확인 못 한 글 → 다시 쓸 때까지 대기열에서 뺌 (초안으로 남김)
UNQUEUE_MARK=/var/lib/first-lab/bot/once-2026-11-04-unqueue-85.done
if [ ! -f "$UNQUEUE_MARK" ]; then
  runuser -u firstlab -- wp --path=/var/www/first-lab post meta delete 85 _firstlab_queue --quiet && echo "대기열 제외: 85"
  touch "$UNQUEUE_MARK"
fi

# 11/4 연속 작성 동시 실행으로 생긴 중복 글(고용보험 실업급여 조건 #379, #376 과 같은 키워드) → 휴지통 (되돌릴 수 있음)
DUP_MARK=/var/lib/first-lab/bot/once-2026-11-04-dup-379.done
if [ ! -f "$DUP_MARK" ]; then
  runuser -u firstlab -- wp --path=/var/www/first-lab post delete 379 --quiet && echo "중복 글 휴지통: 379"
  touch "$DUP_MARK"
fi

# 11/5 대부업체 대출 조회 #436 은 #283(대부업 대출 조회)과 같은 주제 → 휴지통 (되돌릴 수 있음)
DUP436_MARK=/var/lib/first-lab/bot/once-2026-11-05-dup-436.done
if [ ! -f "$DUP436_MARK" ]; then
  runuser -u firstlab -- wp --path=/var/www/first-lab post delete 436 --quiet && echo "중복 글 휴지통: 436"
  touch "$DUP436_MARK"
fi

# 사진을 사람이 이미 눈으로 확인한 글 → 자동 사진 검사(bot/image_check.py) 대상에서 뺌 (한 번만)
IMGCHECK_MARK=/var/lib/first-lab/bot/once-2026-11-04-img-checked.done
if [ ! -f "$IMGCHECK_MARK" ]; then
  for id in 34 65 80 85 90 99 113 119 138 144 163; do
    runuser -u firstlab -- wp --path=/var/www/first-lab post meta update "$id" _firstlab_img_checked 1 --quiet 2>/dev/null || true
  done
  echo "사진 확인 완료 표시: 34 65 80 85 90 99 113 119 138 144 163"
  touch "$IMGCHECK_MARK"
fi

log "글쓰기 세션용 API 토큰 (서버 비밀값에만 원문, 워드프레스엔 해시)"
SECRETS=/etc/first-lab/secrets.env
if ! grep -q '^FIRSTLAB_API_TOKEN=' "$SECRETS" 2>/dev/null; then
  echo "FIRSTLAB_API_TOKEN=$(openssl rand -hex 24)" >> "$SECRETS" && chmod 600 "$SECRETS" && echo "새 토큰 생성됨"
fi
TOKEN_HASH=$(grep '^FIRSTLAB_API_TOKEN=' "$SECRETS" | tail -1 | cut -d= -f2- | tr -d '\n' | sha256sum | cut -d' ' -f1)
runuser -u firstlab -- wp --path=/var/www/first-lab option update firstlab_api_token_hash "$TOKEN_HASH" --quiet 2>/dev/null \
  || runuser -u firstlab -- wp --path=/var/www/first-lab option add firstlab_api_token_hash "$TOKEN_HASH" --quiet || true
echo "토큰 준비됨 (끝자리 $(grep '^FIRSTLAB_API_TOKEN=' "$SECRETS" | tail -1 | tail -c 5))"

# 공공 API 키 → 워드프레스 옵션 (API 통로 /law·/benefits 가 씀, first-lab-api.php). 값은 출력하지 않음
copy_secret_option() {  # 비밀값 이름, 옵션 이름, 설명
  local value
  value=$(grep "^$1=" "$SECRETS" | tail -1 | cut -d= -f2- | tr -d '\n' || true)
  if [ -n "$value" ]; then
    runuser -u firstlab -- wp --path=/var/www/first-lab option update "$2" "$value" --autoload=no --quiet 2>/dev/null \
      || runuser -u firstlab -- wp --path=/var/www/first-lab option add "$2" "$value" --autoload=no --quiet || true
    echo "$3: 있음 (${#value}자)"
  else
    echo "$3: 없음"
  fi
}
copy_secret_option LAW_OPEN_API_OC firstlab_law_oc "법령 API 신청값"
copy_secret_option DATA_GO_KR_KEY firstlab_data_key "공공데이터 인증키"

# 공개 대기 글 키워드 다시 조회 → 황금 점수·성수기로 공개 순서 (11/5 운영자 지시, 한 번 — 다음부터 매일 키워드 수집 끝에 자동)
QGOLD_MARK=/var/lib/first-lab/bot/once-2026-11-05-queue-gold.done
if [ ! -f "$QGOLD_MARK" ] && grep -q '^NAVER_HUB_ID=' "$SECRETS"; then
  log "공개 대기 글 키워드 다시 조회"
  (cd "$REPO_DIR" && "$VENV/bin/python" -m bot.naver_hub --queue) && touch "$QGOLD_MARK" || echo "대기 글 키워드 조회 실패 (다음 배포 때 다시)"
fi

# 황금 키워드 첫 조회 (11/5 운영자 지시: 무료인 동안 하루 한도까지 키워드 창고 전체를 훑음 — 다음부터는 매일 키워드 수집 끝에 자동).
# 2만 회라 10분 넘게 걸려서 배포를 막지 않게 따로 돌린다 (결과는 봇 기록·/var/lib/first-lab/bot/golden-first.log)
GOLD_MARK=/var/lib/first-lab/bot/once-2026-11-05-golden6.done  # 6: 황금지수(검색량 ÷ 글 수) 하나로 — 운영자 결정 (대기 글 포함)
if [ ! -f "$GOLD_MARK" ] && grep -q '^NAVER_HUB_ID=' "$SECRETS" && ! pgrep -f "bot.naver_hub --daily" >/dev/null; then
  log "황금 키워드 첫 조회 시작 (따로 실행)"
  touch "$GOLD_MARK"
  setsid nohup bash -c "cd '$REPO_DIR' && '$VENV/bin/python' -m bot.naver_hub --daily" > /var/lib/first-lab/bot/golden-first.log 2>&1 < /dev/null &
fi

log "직접 작성한 글 올리기 (wordpress/queue → 이미지·카드·카테고리까지 봇과 같은 과정)"
(cd "$REPO_DIR" && "$VENV/bin/python" -m bot.publish_queue) || echo "직접 작성 글 올리기 실패 (봇 동작에는 영향 없음)"
(cd "$REPO_DIR" && "$VENV/bin/python" -m bot.pending_images) || echo "사진 없는 글 표시 실패 (봇 동작에는 영향 없음)"

log "systemd 타이머 (1분마다 실행)"
install -m 644 "$REPO_DIR"/server/systemd/firstlab-{bot,discover,backup}.{service,timer} /etc/systemd/system/
systemctl daemon-reload
# 이전 버전 코드로 오래 멈춰 있는 실행이 있으면 정리 (12분 초과)
if systemctl is-active -q firstlab-bot.service; then
  start_ts=$(systemctl show firstlab-bot.service -p ExecMainStartTimestampMonotonic --value 2>/dev/null || echo 0)
  now_us=$(awk '{printf "%d", $1*1000000}' /proc/uptime)
  if [ "${start_ts:-0}" -gt 0 ] && [ $(( (now_us - start_ts) / 60000000 )) -ge 12 ]; then
    echo "오래 멈춘 봇 실행 정리"; systemctl stop firstlab-bot.service
  fi
fi
systemctl enable --now firstlab-bot.timer firstlab-discover.timer firstlab-backup.timer
systemctl is-active firstlab-bot.timer firstlab-discover.timer firstlab-backup.timer
ls /var/backups/first-lab/wordpress-*.gz &>/dev/null || systemctl start --no-block firstlab-backup.service  # 첫 백업

log "키워드 수집 (오늘 아직 안 했으면 바로 시작, 백그라운드)"
if systemctl is-active -q firstlab-discover.service; then
  echo "이미 수집 중"
elif [ "$(cat /var/lib/first-lab/bot/discover-date-v2 2>/dev/null)" = "$(date +%F)" ]; then
  echo "오늘 수집 완료 → 건너뜀"
else
  systemctl start --no-block firstlab-discover.service && echo "수집 시작됨"
fi

