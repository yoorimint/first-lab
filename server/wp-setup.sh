#!/usr/bin/env bash
# first-lab.kr 워드프레스 기본/SEO 설정 (WP-CLI 사용, 여러 번 실행해도 안전)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ===== 설정값 (여기만 고치면 됨) =====
SITE=first-lab
WEB_ROOT=/var/www/$SITE
WP_USER=firstlab
SITE_TITLE="퍼스트랩"
SITE_TAGLINE="생활에 필요한 정보를 꼼꼼히 비교해 핵심만 정리하는 블로그"  # 검색결과 사이트 설명
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
wp option update blogdescription "$SITE_TAGLINE"
# SEO 플러그인(The SEO Framework)의 메인 페이지 설명에도 같은 문구 지정
wp option patch update autodescription-site-settings homepage_description "$SITE_TAGLINE" 2>/dev/null \
  || wp option patch insert autodescription-site-settings homepage_description "$SITE_TAGLINE" || true
wp option update timezone_string "$TIMEZONE"
wp option update blog_public 1                 # 검색엔진 노출 허용
wp option update default_pingback_flag 0       # 스팸성 핑백 끔
wp option update default_ping_status closed
wp option update default_comment_status closed   # 새 글 댓글 닫힘 (first-lab-comments-off.php 와 함께)
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

# ---------- 카테고리 (bot/categories.py 목록) ----------
log "카테고리 동기화"
existing_slugs=$(wp term list category --field=slug)
existing_names=$(wp term list category --field=name)
while IFS=$'\t' read -r name slug; do
  if echo "$existing_slugs" | grep -qx "$slug" || echo "$existing_names" | grep -qxF "$name"; then
    continue  # 이미 있음 (관리자 화면에서 주소를 바꿨어도 이름이 같으면 그대로 둠)
  fi
  wp term create category "$name" --slug="$slug" --porcelain
done < <(cd "$REPO_DIR" && python3 -m bot.categories)
default_id=$(wp term get category life --by=slug --field=term_id 2>/dev/null \
  || wp term list category --name="생활정보" --field=term_id | head -1)
[ -n "$default_id" ] && wp option update default_category "$default_id"   # 새 글 기본값 = 생활정보 ('미분류' 대신)

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
  # 서버 로캘과 무관하게 UTF-8 로 읽음 (sed 는 로캘에 따라 한글 제목을 못 읽은 적이 있음)
  title=$(python3 -c 'import re,sys; m=re.match(r"<!-- title: (.*) -->\s*$", open(sys.argv[1], encoding="utf-8").readline()); print(m.group(1) if m else "")' "$file")
  if [ -z "$title" ]; then
    echo "경고: $slug 페이지 제목 줄(<!-- title: ... -->)을 못 읽음 → 이 페이지는 건너뜀"; continue
  fi
  echo "  페이지 $slug → $title"
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

# ---------- 플러그인 자동 업데이트 (보안 패치 자동 적용) ----------
wp plugin auto-updates enable --all >/dev/null 2>&1 || true

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
