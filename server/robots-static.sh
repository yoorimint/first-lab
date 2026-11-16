#!/usr/bin/env bash
# robots.txt 를 정적 파일로 만들어 nginx 가 PHP 없이 바로 주게 함 (quick-deploy.sh·wp-setup.sh 가 부름 — 배포마다·매일 새벽 다시 만듦).
# 11/17 구글 robots.txt 문서 점검: robots.txt 가 5xx 면 '처음 12시간 동안 사이트 크롤링을 중단'. 지금은 매 요청 워드프레스(PHP 3개)가
# 만들고 페이지 캐시에서도 빠져 있어(11/15 '캐시 예외') 서버가 바쁠 때 실패할 수 있음 → 저장본 없이 늘 새 내용이면서 PHP 와 무관하게.
# 만들기에 실패하면 파일을 지워 예전처럼 워드프레스가 직접 줌.
set -u
WEB_ROOT=/var/www/first-lab
WP_USER=firstlab
out=$(runuser -u "$WP_USER" -- env WP_CLI_CACHE_DIR=/var/lib/first-lab/wp-cli-cache /usr/local/bin/wp --path="$WEB_ROOT" eval 'ob_start(); do_robots(); echo ob_get_clean();' 2>/dev/null)
if printf '%s\n' "$out" | grep -q '^User-agent:' && printf '%s\n' "$out" | grep -q '^Sitemap: https://first-lab.kr/sitemap.xml'; then
  printf '%s\n' "$out" > "$WEB_ROOT/robots.txt.tmp" && chmod 644 "$WEB_ROOT/robots.txt.tmp" && mv -f "$WEB_ROOT/robots.txt.tmp" "$WEB_ROOT/robots.txt"
  echo "robots.txt 정적 파일 갱신"
else
  rm -f "$WEB_ROOT/robots.txt" "$WEB_ROOT/robots.txt.tmp"
  echo "robots.txt 만들기 실패 → 워드프레스가 직접 줌"
fi
