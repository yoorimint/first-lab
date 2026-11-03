#!/usr/bin/env bash
# first-lab.kr 서버 설정 적용 스크립트 (여러 번 실행해도 안전)
#  - 블로그 전용 사용자 + PHP 풀로 사주데이와 격리
#  - nginx 페이지 캐시, 로그인 횟수 제한
#  - wp-config 보안 설정
# 실패하면 nginx/php 설정을 자동으로 이전 상태로 되돌린다.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SITE=first-lab
WEB_ROOT=/var/www/$SITE
WP_USER=firstlab
PHP_VER=8.1
TMP_DIR=/var/lib/$SITE/tmp
CACHE_DIR=/var/cache/nginx/$SITE
BACKUP=/root/backup-$SITE-$(date +%Y%m%d-%H%M%S)

log() { echo -e "\n==> $*"; }

log "배포 버전: $(git -C "$REPO_DIR/.." log -1 --format='%h %s' 2>/dev/null || echo unknown)"

# ---------- 백업 & 실패 시 복구 ----------
log "기존 설정 백업: $BACKUP"
mkdir -p "$BACKUP"
cp -a /etc/nginx/sites-available/$SITE "$BACKUP/" 2>/dev/null || true
cp -a /etc/php/$PHP_VER/fpm/pool.d "$BACKUP/"

restore() {
  echo "!! 오류 발생 → 설정 복구 중"
  cp -a "$BACKUP/$SITE" /etc/nginx/sites-available/$SITE 2>/dev/null || true
  rm -f /etc/nginx/conf.d/$SITE-http.conf
  rm -rf /etc/php/$PHP_VER/fpm/pool.d && cp -a "$BACKUP/pool.d" /etc/php/$PHP_VER/fpm/
  systemctl restart php$PHP_VER-fpm || true
  nginx -t && systemctl reload nginx
  echo "!! 복구 완료 (사이트는 이전 상태로 동작)"
}
trap restore ERR

# ---------- 1. 전용 사용자 ----------
log "블로그 전용 사용자($WP_USER) 생성"
id "$WP_USER" &>/dev/null || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin "$WP_USER"

# ---------- 2. 파일 권한 ----------
log "파일 권한 설정 (소유자=$WP_USER, nginx는 읽기만)"
mkdir -p "$TMP_DIR" "$CACHE_DIR"
chown "$WP_USER:$WP_USER" "$TMP_DIR" && chmod 700 "$TMP_DIR"
chown www-data:www-data "$CACHE_DIR"
chown -R "$WP_USER:www-data" "$WEB_ROOT"
find "$WEB_ROOT" -type d -exec chmod 750 {} +
find "$WEB_ROOT" -type f -exec chmod 640 {} +

# ---------- 3. wp-config 보안 상수 ----------
log "wp-config.php 보안 설정"
WP_CONFIG=$WEB_ROOT/wp-config.php
add_const() {  # 이미 있으면 건너뜀
  grep -q "'$1'" "$WP_CONFIG" || sed -i "1a define( '$1', $2 );" "$WP_CONFIG"
}
add_const DISALLOW_FILE_EDIT true            # 관리자 화면 코드 편집기 끄기
add_const FS_METHOD "'direct'"               # 플러그인/업데이트 FTP 묻지 않기
add_const WP_TEMP_DIR "'$TMP_DIR'"
chmod 600 "$WP_CONFIG" && chown "$WP_USER:$WP_USER" "$WP_CONFIG"  # nginx도 못 읽게

# ---------- 4. PHP 풀 ----------
log "PHP 전용 풀 설치"
install -m 644 "$REPO_DIR/php-fpm/$SITE.conf" /etc/php/$PHP_VER/fpm/pool.d/$SITE.conf
# 기본 풀(www)은 사용처가 없으므로 끔
[ -f /etc/php/$PHP_VER/fpm/pool.d/www.conf ] && mv /etc/php/$PHP_VER/fpm/pool.d/www.conf{,.disabled}
php-fpm$PHP_VER -t
systemctl restart php$PHP_VER-fpm

# ---------- 5. nginx ----------
log "nginx 설정 설치"
install -m 644 "$REPO_DIR/nginx/$SITE-http.conf" /etc/nginx/conf.d/$SITE-http.conf
install -m 644 "$REPO_DIR/nginx/$SITE-ssl.conf"  /etc/nginx/snippets/$SITE-ssl.conf
install -m 644 "$REPO_DIR/nginx/$SITE.conf"      /etc/nginx/sites-available/$SITE
ln -sf /etc/nginx/sites-available/$SITE /etc/nginx/sites-enabled/$SITE
nginx -t
systemctl reload nginx

trap - ERR

# ---------- 6. 확인 ----------
log "확인"
sleep 1
for i in 1 2; do
  curl -s -o /dev/null -D - https://first-lab.kr/ | grep -iE "^HTTP|^x-cache" | tr '\n' ' '; echo
done
systemctl is-active php$PHP_VER-fpm nginx sajuday
ps -o user=,cmd= -C php-fpm$PHP_VER | sort | uniq -c
# 오래된 백업 정리 (최근 5개 유지)
ls -dt /root/backup-$SITE-* 2>/dev/null | tail -n +6 | xargs -r rm -rf
echo -e "\n완료! (백업 위치: $BACKUP)"
