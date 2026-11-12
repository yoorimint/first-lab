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
| `run.py` | 진입점. systemd 타이머가 1분마다 실행 (수동 요청·글 작성 시각·연속 작성·매일 점검 → 글쓰기 작업 시작, 예약 공개) |
| `routine.py` / `api_writer.py` | 글쓰기 작업 시작: 월정액 = Claude 예약 작업 / API 봇 = 서버 안에서 같은 지시서(`automation/daily-writer.md`)로 Claude Code 실행 (Claude API 키 요금). 결과는 둘 다 `publish_queue.py` 관문 |
| `discover.py` | 오늘의 키워드 수집: 구글 급상승어 + 씨앗 단어 전부 + 큰 키워드×수식어 롱테일 조합 (하루 1번 최대한). 8일치만 보관. 별도 작업 `firstlab-discover` (매일 04시, 배포 시 오늘 미수집이면 즉시) |
| `seed_words.py` / `seeds.py` | 씨앗 단어 목록 / 구글 급상승 검색어 |
| `keyword_db.py` | 키워드 창고 SQLite (`/var/lib/first-lab/bot/keywords.db`) |
| `scoring.py` | 점수 가중치 (`WEIGHTS`) |
| `keyword_filter.py` | 금지어·질문형 단어 목록 |
| `competition.py` | 경쟁도: Gemini 구글 검색 연동(무료)으로 상위 출처 도메인 → 대형 사이트 비율 = 난이도. 점검: `python -m bot.competition 키워드` |
| `search_console.py` | 구글 서치콘솔 성과 → 기회 키워드 추가·잘 되는 주제 가산·안 되는 주제 감점 (매일, 키: `/etc/first-lab/gsc-service-account.json`) |
| `related.py` | 내부 링크: 발행된 글 중 관련 후보 → 글쓰기 AI 가 본문에 1~3개 링크 (목록 외 주소는 제거). 글 하단 '함께 보면 좋은 글'은 `first-lab-related.php` |
| `factcheck.py` | 자동 사실 검증: 완성된 글의 핵심 수치를 별도 AI(Haiku+웹검색)가 재확인·수정, 미확인 많으면 초안 유지 |
| `refresh.py` | 발행 글 갱신 (주 5개, 하루 최대 1개): 순위 정체·하락 → 보강, 1·7월 제도 글 / 6개월 지난 글 → 수치 재검증 |
| `safety.py` | AI 안전 검사 (인물·정치·종교·의료·장소찾기 등 제외) |
| `select_keyword.py` | 후보 선정. 점검: `python -m bot.select_keyword` |
| `llm.py` | AI 제공사 교체 (Gemini/Claude/OpenAI) |
| `writer.py` / `publisher.py` | 글 작성(JSON) / 워드프레스 저장 (블록 형식) |
| `images.py` / `blocks.py` | 대표 이미지(제목 카드) + 본문 AI 이미지(Cloudflare Workers AI 무료) / HTML→블록 변환 |
| `settings.py` / `state.py` | 설정 페이지 값 읽기 / 실행 기록·현황 저장 |

- 설정: 워드프레스 관리자 > 설정 > **퍼스트랩 봇** (`wordpress/mu-plugins/first-lab-bot-settings.php`)
- 서버 명령 (가상환경): `cd /opt/first-lab && /opt/first-lab-venv/bin/python -m bot.run --now`
- 키: 설정 페이지 값 우선, 없으면 `/etc/first-lab/secrets.env` (`server/set-secrets.sh` 로 입력)

> 공개 저장소이므로 비밀번호·API 키는 절대 커밋하지 않는다.
