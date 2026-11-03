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
흐름: **키워드 창고 확장 → 점수 → AI 안전 검사 → 글쓰기 → 초안/발행**

| 파일 | 역할 |
|---|---|
| `run.py` | 진입점. systemd 타이머가 1분마다 실행 (수동 요청, 자동 발행 시각, 하루 1회 창고 확장) |
| `discover.py` / `seeds.py` | 눈덩이 방식 키워드 발굴 (급상승어 + 창고 키워드 확장, 창고가 작을 땐 시동용 씨앗) |
| `keyword_db.py` | 키워드 창고 SQLite (`/var/lib/first-lab/bot/keywords.db`) |
| `scoring.py` | 점수 가중치 (`WEIGHTS`) |
| `keyword_filter.py` | 금지어·질문형 단어 목록 |
| `safety.py` | AI 안전 검사 (인물·정치·종교·의료·장소찾기 등 제외) |
| `select_keyword.py` | 후보 선정. 점검: `python -m bot.select_keyword` |
| `llm.py` | AI 제공사 교체 (Gemini/Claude/OpenAI) |
| `writer.py` / `publisher.py` | 글 작성(JSON) / 워드프레스 저장 |
| `settings.py` / `state.py` | 설정 페이지 값 읽기 / 실행 기록·현황 저장 |

- 설정: 워드프레스 관리자 > 설정 > **퍼스트랩 봇** (`wordpress/mu-plugins/first-lab-bot-settings.php`)
- 서버 명령 (가상환경): `cd /opt/first-lab && /opt/first-lab-venv/bin/python -m bot.run --now`
- 키: 설정 페이지 값 우선, 없으면 `/etc/first-lab/secrets.env` (`server/set-secrets.sh` 로 입력)

> 공개 저장소이므로 비밀번호·API 키는 절대 커밋하지 않는다.
