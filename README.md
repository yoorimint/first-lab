# first-lab.kr

사주·운세 워드프레스 블로그 (애드센스). 서버는 사주데이와 같은 iwinv 서버(Ubuntu 22.04, nginx)를 사용한다.

## 구조
| 경로 | 설명 |
|---|---|
| `server/setup.sh` | 서버 설정 적용 스크립트 (여러 번 실행해도 안전, 실패 시 자동 복구) |
| `server/wp-setup.sh` | 워드프레스 기본·SEO 설정 (WP-CLI). 설정값은 파일 상단에서 수정 |
| `wordpress/themes/first-lab/` | 자식 테마 (부모: Twenty Twenty-Five). 글꼴·푸터 등 |
| `wordpress/pages/` | 고정 페이지 내용 (소개·문의·개인정보처리방침). 첫 줄 `<!-- title: 제목 -->` |
| `wordpress/mu-plugins/` | 사이트 기능 코드 (항상 켜지는 플러그인). 배포 시 자동 설치 |
| `server/nginx/` | nginx 사이트 설정, 캐시·로그인 제한, SSL |
| `server/php-fpm/` | 블로그 전용 PHP 풀 (사주데이와 격리, 메모리 절약) |

## 배포 (자동)
`main` 브랜치에 `server/`·`wordpress/` 변경이 push 되면 GitHub Actions(`.github/workflows/deploy.yml`)가
서버에 접속해 `server/setup.sh` → `server/wp-setup.sh` 를 실행한다. Actions 탭에서 수동 실행도 가능.

필요한 저장소 Secrets: `SERVER_HOST`, `SERVER_USER`, `SERVER_PASSWORD`

## 자동 발행 봇 (`bot/`)
- 표준 라이브러리만 사용 (서버에 추가 설치 없음)
- 키는 서버의 `/etc/first-lab/secrets.env` 에만 저장. 입력: `bash /opt/first-lab/server/set-secrets.sh 키이름 ...`
- 검색량 조회: `cd /opt/first-lab && python3 -m bot.keywords 다이어트`

> 공개 저장소이므로 비밀번호·API 키는 절대 커밋하지 않는다.
