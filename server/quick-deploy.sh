#!/usr/bin/env bash
# 빠른 배포 (11/9 운영자 '이렇게 비효율적으로 해야 돼?'): 글·화면·봇 코드만 바뀐 배포는 서버 설치 전체(15~20분)를 다시 돌리지 않는다.
#   - 바뀐 화면 파일만 설치 (mu-plugins, 테마)
#   - 새 글·갱신 파일 반영 (bot.publish_queue)
#   - 전체 글 보정·패치 확인은 따로 (server/maint.sh, 잠금)
#   - 보류 글 다시 검사, 페이지 캐시 비우기
# 서버 설정(server/·워크플로)·페이지·패키지가 바뀌었거나 그날 첫 배포면 전체 배포(setup → wp-setup → bot-setup → bot-check)를 쓴다 (deploy.yml).
# 사용: quick-deploy.sh "<바뀐 파일 목록(줄마다 하나)>"
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SITE=first-lab
WEB_ROOT=/var/www/$SITE
WP_USER=firstlab
THEME=first-lab
VENV=/opt/first-lab-venv
CHANGED="${1:-}"
log() { echo -e "\n==> $*"; }
changed() { printf '%s\n' "$CHANGED" | grep -q "$1"; }

if changed '^wordpress/mu-plugins/'; then
  log "mu-plugins 설치"
  install -o "$WP_USER" -g www-data -m 640 "$REPO_DIR"/wordpress/mu-plugins/*.php "$WEB_ROOT/wp-content/mu-plugins/"
fi

if changed "^wordpress/themes/$THEME/"; then
  log "테마 설치"
  THEME_DIR=$WEB_ROOT/wp-content/themes/$THEME
  rm -rf "$THEME_DIR" && cp -r "$REPO_DIR/wordpress/themes/$THEME" "$THEME_DIR"
  chown -R "$WP_USER:www-data" "$THEME_DIR"
  find "$THEME_DIR" -type d -exec chmod 750 {} + && find "$THEME_DIR" -type f -exec chmod 640 {} +
fi

log "직접 작성한 글·갱신 파일 올리기"
(cd "$REPO_DIR" && "$VENV/bin/python" -m bot.publish_queue) || echo "직접 작성 글 올리기 실패 (봇 동작에는 영향 없음)"

log "기존 글 보정·패치 반영 확인 (따로 실행 → /var/lib/first-lab/bot/maint.log)"
setsid nohup flock /var/lib/first-lab/bot/maint.lock bash "$REPO_DIR/server/maint.sh" > /var/lib/first-lab/bot/maint.log 2>&1 < /dev/null &

log "보류 글 다시 검사"
(cd "$REPO_DIR" && "$VENV/bin/python" -m bot.hold) || echo "보류 다시 검사 실패 (봇 동작에는 영향 없음)"

rm -rf /var/cache/nginx/$SITE/*
echo "빠른 배포 끝"
