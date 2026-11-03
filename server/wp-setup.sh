#!/usr/bin/env bash
# first-lab.kr 워드프레스 기본/SEO 설정 (WP-CLI 사용, 여러 번 실행해도 안전)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ===== 설정값 (여기만 고치면 됨) =====
SITE=first-lab
WEB_ROOT=/var/www/$SITE
WP_USER=firstlab
SITE_TITLE="퍼스트랩"
AUTHOR_NAME="퍼스트랩"      # 글 작성자 표시 이름
AUTHOR_SLUG="firstlab"      # 작성자 주소(/author/...)에 쓰는 이름
TIMEZONE="Asia/Seoul"
PERMALINK="/%postname%/"
# SEO 플러그인: The SEO Framework (가볍고 DB 테이블을 추가하지 않아 SQLite와 궁합이 좋음)
PLUGINS_INSTALL=(autodescription)
PLUGINS_REMOVE=(akismet hello)
# 테마: 저장소의 자식 테마(wordpress/themes/first-lab) 사용. 부모 테마 외 미사용 테마는 삭제
THEME=first-lab
PARENT_THEME=twentytwentyfive
# ====================================

export WP_CLI_CACHE_DIR=/var/lib/$SITE/wp-cli-cache
mkdir -p "$WP_CLI_CACHE_DIR" && chown "$WP_USER:$WP_USER" "$WP_CLI_CACHE_DIR"

log() { echo -e "\n==> $*"; }
wp() { runuser -u "$WP_USER" -- env WP_CLI_CACHE_DIR="$WP_CLI_CACHE_DIR" /usr/local/bin/wp --path="$WEB_ROOT" "$@"; }

# ---------- WP-CLI 설치 ----------
if [ ! -x /usr/local/bin/wp ]; then
  log "WP-CLI 설치"
  curl -fsSL https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar -o /usr/local/bin/wp
  chmod 755 /usr/local/bin/wp
fi

# ---------- 기본 설정 ----------
log "기본 설정 (사이트 제목, 시간대, 고유주소, 검색 노출, 핑백 끔)"
wp option update blogname "$SITE_TITLE"
wp option update timezone_string "$TIMEZONE"
wp option update blog_public 1                 # 검색엔진 노출 허용
wp option update default_pingback_flag 0       # 스팸성 핑백 끔
wp option update default_ping_status closed
wp rewrite structure "$PERMALINK"

# ---------- 기본 샘플 정리 ----------
log "샘플 글/페이지, 불필요 플러그인 삭제"
for id in 1 2; do  # 설치 시 생기는 '안녕하세요' 글, 샘플 페이지
  wp post delete "$id" --force 2>/dev/null || true
done
for p in "${PLUGINS_REMOVE[@]}"; do
  wp plugin is-installed "$p" && wp plugin delete "$p" || true
done

# ---------- 플러그인 ----------
log "SEO 플러그인 설치"
for p in "${PLUGINS_INSTALL[@]}"; do
  wp plugin is-installed "$p" || wp plugin install "$p"
  wp plugin is-active "$p"   || wp plugin activate "$p"
done

# ---------- 관리자 로그인 아이디가 글 작성자로 노출되지 않게 ----------
log "작성자 표시 이름 설정 (로그인 아이디 숨김)"
for uid in $(wp user list --role=administrator --field=ID); do
  login=$(wp user get "$uid" --field=user_login)
  if [ "$(wp user get "$uid" --field=display_name)" = "$login" ] || [ "$(wp user get "$uid" --field=user_nicename)" = "$login" ]; then
    wp user update "$uid" --display_name="$AUTHOR_NAME" --nickname="$AUTHOR_NAME" --user_nicename="$AUTHOR_SLUG-$uid"
  fi
done

# ---------- 저장소의 mu-plugins 배포 (wordpress/mu-plugins → wp-content/mu-plugins) ----------
log "mu-plugins 배포"
MU_DIR=$WEB_ROOT/wp-content/mu-plugins
install -d -o "$WP_USER" -g www-data -m 750 "$MU_DIR"
install -o "$WP_USER" -g www-data -m 640 "$REPO_DIR"/wordpress/mu-plugins/*.php "$MU_DIR"/

# ---------- 테마 (wordpress/themes/<THEME> → wp-content/themes/<THEME>) ----------
log "테마 배포"
wp theme is-installed "$PARENT_THEME" || wp theme install "$PARENT_THEME"
THEME_DIR=$WEB_ROOT/wp-content/themes/$THEME
rm -rf "$THEME_DIR" && cp -r "$REPO_DIR/wordpress/themes/$THEME" "$THEME_DIR"
chown -R "$WP_USER:www-data" "$THEME_DIR"
find "$THEME_DIR" -type d -exec chmod 750 {} + && find "$THEME_DIR" -type f -exec chmod 640 {} +
wp theme is-active "$THEME" || wp theme activate "$THEME"
for t in $(wp theme list --status=inactive --field=name); do
  if [ "$t" != "$PARENT_THEME" ]; then wp theme delete "$t"; fi
done

# ---------- 고정 페이지 (wordpress/pages/<slug>.html → 페이지) ----------
# 파일 첫 줄 <!-- title: 제목 --> 이 페이지 제목. 없으면 만들고, 있으면 내용을 갱신.
log "고정 페이지 동기화"
for file in "$REPO_DIR"/wordpress/pages/*.html; do
  slug=$(basename "$file" .html)
  title=$(sed -n '1s/^<!-- title: \(.*\) -->$/\1/p' "$file")
  content=$(tail -n +2 "$file")
  id=$(wp post list --post_type=page --name="$slug" --post_status=any --field=ID)
  if [ -z "$id" ]; then
    id=$(wp post create --post_type=page --post_status=publish --post_name="$slug" \
          --post_title="$title" --post_content="$content" --porcelain)
  else
    wp post update "$id" --post_status=publish --post_title="$title" --post_content="$content"
  fi
  if [ "$slug" = "privacy-policy" ]; then
    wp option update wp_page_for_privacy_policy "$id"
  fi
done

# ---------- 페이지 캐시 비우기 (변경 즉시 반영) ----------
rm -rf /var/cache/nginx/$SITE/*

# ---------- 확인 ----------
log "확인"
wp option get blogname
wp option get timezone_string
wp option get permalink_structure
wp plugin list --fields=name,status --format=csv
wp theme list --fields=name,status --format=csv
for path in / /sitemap.xml /robots.txt /about/ /contact/ /privacy-policy/; do
  curl -s -o /dev/null -w "$path %{http_code}\n" "https://first-lab.kr$path"
done
echo -e "\n워드프레스 설정 완료!"
