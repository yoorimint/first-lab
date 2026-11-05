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
# nginx 설정은 서로 물려 있어(사이트 설정의 fastcgi_cache ↔ conf.d 의 캐시 영역) 한 묶음으로 백업·복구한다
# (11/5 복구가 conf.d 만 지워 'zone firstlab_cache is unknown' — 디스크 설정이 깨진 채 남음)
NGINX_FILES="/etc/nginx/sites-available/$SITE /etc/nginx/conf.d/$SITE-http.conf /etc/nginx/snippets/$SITE-ssl.conf /etc/nginx/snippets/$SITE-headers.conf"
for f in $NGINX_FILES; do
  [ -f "$f" ] && mkdir -p "$BACKUP/nginx$(dirname "$f")" && cp -a "$f" "$BACKUP/nginx$f"
done
cp -a /etc/php/$PHP_VER/fpm/pool.d "$BACKUP/"

restore() {
  echo "!! 오류 발생 → 설정 복구 중"
  for f in $NGINX_FILES; do
    if [ -f "$BACKUP/nginx$f" ]; then cp -a "$BACKUP/nginx$f" "$f"; else rm -f "$f"; fi   # 처음 설치라 백업이 없으면 새 파일을 지움
  done
  rm -rf /etc/php/$PHP_VER/fpm/pool.d && cp -a "$BACKUP/pool.d" /etc/php/$PHP_VER/fpm/
  systemctl restart php$PHP_VER-fpm || true
  if nginx -t && systemctl reload nginx; then
    echo "!! 복구 완료 (사이트는 이전 상태로 동작)"
  else
    echo "!! 복구 실패: nginx 설정 검사 실패 — 실행 중인 nginx 는 이전 설정으로 계속 동작, 재시작하면 멈춤 (다음 배포가 다시 설치)"
  fi
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
# 사이트가 도는 중이라 SQLite 임시 파일(-shm·-wal)이 찾은 뒤 지워질 수 있다 → 그 파일은 건너뛰고, 사라진 파일 오류는 무시
# (11/5 'chmod: cannot access …/.ht.sqlite-shm' 로 배포 실패)
LIVE=( \( -name '*.sqlite-shm' -o -name '*.sqlite-wal' -o -name '*.sqlite-journal' \) -prune -o )
perm() {  # 사라진 파일 때문에 생긴 오류만이면 계속 (남은 오류는 기록)
  find "$WEB_ROOT" -ignore_readdir_race "${LIVE[@]}" "$@" 2>&1 | grep -v "No such file or directory" | sed 's/^/  권한 경고: /' || true
}
perm -exec chown "$WP_USER:www-data" {} +
perm -type d -exec chmod 2750 {} +  # setgid: 새로 생기는 파일·폴더(업로드 이미지 등)도 그룹 www-data 를 물려받아 nginx 가 읽을 수 있게
perm -type f -exec chmod 640 {} +

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
log "PHP 확장 (imagick: 업로드 이미지 축소판 화질 개선 — 사이트 상태 점검 권장 항목)"
dpkg -s php$PHP_VER-imagick &>/dev/null \
  || DEBIAN_FRONTEND=noninteractive apt-get install -y -q php$PHP_VER-imagick
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
install -m 644 "$REPO_DIR/nginx/$SITE-headers.conf" /etc/nginx/snippets/$SITE-headers.conf
install -m 644 "$REPO_DIR/nginx/$SITE.conf"      /etc/nginx/sites-available/$SITE
ln -sf /etc/nginx/sites-available/$SITE /etc/nginx/sites-enabled/$SITE
nginx -t
systemctl reload nginx

trap - ERR

# ---------- 6. SSH 대입 공격 차단 (실패해도 사이트에는 영향 없음) ----------
log "fail2ban (SSH 비밀번호 여러 번 틀린 IP 자동 차단)"
if dpkg -s fail2ban &>/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q fail2ban; then
  install -m 644 "$REPO_DIR/fail2ban/sshd.local" /etc/fail2ban/jail.d/first-lab-sshd.local
  { systemctl enable -q --now fail2ban && systemctl reload fail2ban 2>/dev/null; } || systemctl restart fail2ban || true
  sleep 2
  fail2ban-client status sshd 2>/dev/null | grep -E "Currently|Total" | sed 's/^/  /' || echo "  fail2ban 상태 확인 실패"
else
  echo "  fail2ban 설치 실패 (사이트에는 영향 없음)"
fi

# ---------- 7. 확인 ----------
log "확인"
sleep 1
for i in 1 2; do
  { curl -s --max-time 20 -o /dev/null -D - https://first-lab.kr/ | grep -iE "^HTTP|^x-cache" | tr '\n' ' '; } || true; echo   # 확인 실패가 배포를 멈추지 않게
done
systemctl is-active php$PHP_VER-fpm nginx sajuday
ps -o user=,cmd= -C php-fpm$PHP_VER | sort | uniq -c
# 오래된 백업 정리 (최근 5개 유지)
ls -dt /root/backup-$SITE-* 2>/dev/null | tail -n +6 | xargs -r rm -rf
echo -e "\n완료! (백업 위치: $BACKUP)"
