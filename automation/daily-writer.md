# 매일 글쓰기 (예약 세션용 작업 지시서)

Claude 예약 작업으로 시작되는 세션이 이 문서대로 **새 글 1편**을 써서 올리고, 갱신할 글이 있으면 (하루 1개까지) 고친다.
실행은 사이트 서버가 시킨다: 설정의 "글 작성 시각"마다 1번, "지금 글 1개 만들기" 버튼, 연속 작성 중에는 한 편 끝날 때마다 1번 (`bot/routine.py`).
그래서 실행 1번 = 새 글 1편이다 (하루에 여러 번 실행될 수 있음).
이 문서를 고치면 다음 실행부터 바로 반영된다 (예약 작업은 "이 문서를 따르라"고만 한다).

새 글이 실패하면 아무것도 올리지 말고 넘어간다 (월정액 방식에는 API 예비 봇이 없다).
갱신은 예비가 없다 (실패하면 다음 날 서버가 다시 고름).

## 0. 준비
- 저장소 루트에서 작업한다. 최신 main 을 받는다: `git fetch origin main && git checkout -B daily-writer origin/main`
- 환경 변수 `FIRSTLAB_API_TOKEN` 이 없으면 글을 쓰지 말고 "토큰 없음"으로 끝낸다.
- 스크래치 폴더(작업 파일)는 저장소 밖에 둔다.

## 1. 글감 고르기
```
curl -s -H "X-Firstlab-Token: $FIRSTLAB_API_TOKEN" https://first-lab.kr/wp-json/firstlab/v1/next
```
- 응답의 `write_mode` 가 `"direct"` 가 아니면(설정 페이지에서 "API 봇" 선택) 글을 쓰지 말고 "봇 방식이라 쉼"으로 끝낸다.
- `candidates` 는 점수순 후보다. 위에서부터 보고 아래에 해당하면 건너뛴다:
  - 날마다 값이 바뀌는 시세·가격(예: 오늘의 금 시세, 농산물 가격) — 글이 금방 틀린 정보가 됨
  - 특정 회사·브랜드 상품 비교, 추천·순위·이벤트, 의료·건강 진단, 정치·종교
  - `recent_posts` 에 이미 같은 주제(앞뒤 수식어만 다른 것)가 있는 키워드
- 처음으로 통과한 후보 1개를 고른다. 후보 키워드(`keyword`)는 띄어쓰기 없는 원문 그대로 쓴다.
- 위 기준으로 **건너뛴 후보**는 다시 안 뜨게 기록한다 (`recent_posts` 와 겹쳐서 건너뛴 것은 제외. 서버가 이미 막음):
  `wordpress/queue/skip-<날짜>-<시각>.json` = `{"type": "skip", "keywords": [{"keyword": "<원문>", "why": "<이유 한 줄>"}]}`
  새 글과 같은 커밋에 넣는다. 건너뛴 게 없으면 만들지 않는다.

## 2. 쓰기 규칙 불러오기
봇과 같은 작가·검수 규칙을 쓴다 (저장소의 코드가 기준):
```
python3 - <<'EOF'
from bot.writer import SYSTEM_PROMPT, build_prompt
from bot.factcheck import SYSTEM as REVIEW
# keyword, persona, target_length, extra_prompt 는 1단계 응답 값으로 채운다
print(SYSTEM_PROMPT); print(build_prompt(KEYWORD, TARGET_LENGTH, EXTRA_PROMPT, persona=PERSONA)); print(REVIEW)
EOF
```
작가 규칙(`SYSTEM_PROMPT` + `build_prompt`)대로 쓰고, 검수 규칙(`REVIEW`)으로 스스로 점검한다.

## 3. 조사
- WebSearch·WebFetch 합쳐 10번 안팎. 공식 출처(정부·공공기관·해당 기관 공식 사이트) 먼저.
- 공식 값과 블로그 값이 다르면 공식 값을 쓴다. 확인 못 한 것은 단정하지 않거나 뺀다.
- **근거 장부**를 만든다: 글에 쓸 수치·기준·조건·절차 하나하나를 스크래치 폴더 `claims.json` 에 적는다.
  `[{"claim": "글에 쓸 문장(요지)", "url": "확인한 페이지 주소", "quote": "그 페이지의 해당 원문 그대로"}]`
  원문을 못 찾은 주장은 장부에 넣지 말고 글에서도 단정하지 않는다.

## 4. 작성
- 출력 형식은 작가 규칙의 JSON 그대로 (title, excerpt, content_html, tags, category, images 3개).
- 계산 예시는 Python 으로 계산해서 그 결과를 그대로 쓴다. 표의 합계는 항목 합과 같아야 한다.
- 내부 링크: 1단계 응답 `recent_posts` 중 `url` 이 있는(발행된) 글이 본문과 실제로 관련 있으면, 관련 문장 안에
  `<a href="<url>">자연스러운 문구</a>` 로 1~3개 건다. 관련 없으면 걸지 않는다. 목록에 없는 주소·외부 사이트 주소는 서버가 링크를 지운다.
- 다 쓰면 숫자 일치를 확인한다 (False 면 고친다):
```
python3 -c "import json;from bot.writer import parse_article,align_with_body;a=parse_article(open('ARTICLE.json').read());print(align_with_body(a))"
```
  출력이 `[]` 이어야 한다.

## 4-1. 독립 검수 (쓴 사람이 아닌 새 눈으로)
글을 쓴 세션은 자기가 믿게 된 틀린 내용을 못 본다. 그래서 **글 내용을 모르는 하위 에이전트**(Agent/Task 도구)에게 검수를 맡긴다.
- 하위 에이전트에게 주는 것: 글 JSON 파일 경로, `claims.json` 경로, 아래 지시. 내 판단·요약은 주지 않는다.
- 지시 내용:
  1. 장부의 주소를 하나씩 직접 열어(WebFetch) `quote` 가 실제로 있는지, 그 원문이 `claim` 을 뒷받침하는지 확인
  2. 글 본문의 수치·기준·절차 중 장부에 없는 것을 찾아 목록으로
  3. 계산 예시를 Python 으로 다시 계산 (표 합계 포함)
  4. 결과를 항목별 `맞음 / 틀림(원문 값) / 확인 불가` 로 보고 (고치지는 않음)
- 받은 결과로 내가 고친다: 틀림 → 원문 값으로 고친다.
- **근거가 약한 주장은 그대로 쓰지 않는다.** 약한 주장 = `확인 불가`, 장부에 없음, 출처가 1곳뿐이거나 공식 출처가 아님, 자료끼리 값이 다름.
  1. 먼저 **보강**한다: 법령(law.go.kr)·중앙부처·공단·지자체 등 공식 출처를 추가로 찾아(주장마다 검색 2~3번) 2곳 이상에서 같은 값이 확인되면 장부에 추가하고 쓴다.
     오래된 자료와 최신 자료가 다르면 개정 이력(개정 이유·시행일)을 확인해 최신 값을 쓴다.
  2. 보강이 안 되면 **빼거나**, 꼭 필요한 내용이면 독자가 손해 보지 않는 쪽으로 **완화**한다 (예: '안 돼요' → '안 될 수 있으니 계약 전에 ○○에 확인').
  3. 글의 핵심(제목·첫 문단의 답)이 보강도 완화도 안 되면 그 글은 `"status": "draft"` 로 올린다 (공개 대기열에 안 들어감) 그리고 보고에 이유를 적는다.
- 고친 항목만 한 번 더 같은 방식으로 확인.
- 4단계의 숫자 일치 확인(`align_with_body`)을 다시 돌린다.

## 4-2. 계산기 (주제에 정해진 계산 공식이 있을 때만)
수수료표·세율·요율·법령 계산식이나 대출 상환 같은 **정해진 공식**이 있고, 독자가 자기 값을 넣어볼 이유가 있는 주제면 계산기를 본문에 넣는다.
**키워드에 '계산기'가 들어 있으면 반드시 넣는다** (검색한 사람이 원하는 게 계산기다).
공식이 없는 주제(신청 방법, 서류, 절차 안내)는 넣지 않는다.
- 계산기 폴더: `wordpress/themes/first-lab/calculators/` (구조 설명은 `calc.js` 머리말).
  있는 계산기: `car-inspection`(자동차검사 기간·수수료·과태료), `loan-repayment`(할부·대출 상환).
- 이미 있는 계산기(`defs/*.js`)로 되면 새로 만들지 말고 그걸 넣는다.
- 없으면 새로 만든다 (id 는 영문 소문자-하이픈):
  - `defs/<id>.js`: `car-inspection.js` 와 같은 형식 (입력 항목 + compute). 화면 문구는 본문과 같은 말투.
  - `data/<id>.json`: 요율·금액은 전부 여기에. `basis_date`(오늘), `sources`(조사한 공식 출처 이름·주소)
  - `tests/<id>.json`: 검증 사례 3개 이상. 계산기로 낸 값을 그대로 기대값으로 쓰지 않는다. 기대값 만드는 법은 두 종류:
    - **기관이 정한 값**(요금표·세율·공제·과태료 기준): 공식 기관 계산 예시나 법령 문구로 직접 확인한 값
    - **표준 수학 공식**(대출 상환, 비율·단위 환산 등): 공식을 Python 으로 따로 계산한 값 + 극단값(금리 0%, 1개월 등) + 본문 표의 숫자와 같은지
  - `wordpress/pages/<id>-calculator.html`: 단독 페이지 (첫 줄 `<!-- title: ○○ 계산기 -->`, 짧은 안내 + 계산기 + 결과 읽는 법)
  - 검사: `node tests/test_calculators.mjs` 가 통과해야 한다
- 기관이 정한 요율·기준 중 하나라도 공식 출처로 확인 못 하면 계산기를 만들지 않는다 (글만 올린다).
  표준 수학 공식은 공식 기관의 계산 예시가 없어도 위 방법으로 검증해 만든다.
- 본문에 넣는 법: `content_html` 의 계산 예시 근처에 **독립된 줄**로 `<p>[firstlab_calc id="<id>"]</p>`
- 계산기는 별도 커밋으로 먼저 올린다: `git add wordpress/themes/first-lab/calculators wordpress/pages && git commit -m "계산기: <이름>"`

## 5. 올리기
- `wordpress/queue/<영문-슬러그>.json` 으로 저장한다. 4단계 JSON 에 `"keyword": "<후보 원문>"`, `"status": "auto"` 를 더한다
  (auto = 설정 페이지의 발행 방식을 따름).
- `"review"`: 독립 검수 기록 (서버가 초안 저장 때 확인 → 통과 못 하면 공개 대기열에 안 넣고 초안 보류).
  주장마다 `{"claim": "요지", "urls": ["확인한 주소"], "verdict": "맞음|틀림|확인불가", "action": "유지|수정|완화|삭제"}`.
  본문에 단정형으로 남긴 주장(유지·수정)은 `맞음` + 공식 기관 주소(.go.kr .or.kr 등)가 있어야 통과. 사실대로 적는다.
- `"sources"`: 독립 검수에서 `맞음` 이 나온 장부 주소 중 **정부·공공기관 페이지**(.go.kr .or.kr .re.kr .ac.kr) 2~3개를
  `[{"name": "기관명 + 페이지 내용 (예: 한국교통안전공단 검사 수수료 안내)", "url": "https://..."}]` 로 넣는다.
  서버가 실제로 열리는지 확인한 뒤 글 끝에 '공식 안내 바로가기'로 붙인다 (본문 안 외부 링크는 지워짐).
- 검사: `python3 -m unittest discover -s tests && node tests/test_calculators.mjs`
- 커밋하고 main 에 올린다 (운영자 승인된 자동 작업): 
  `git add wordpress/queue && git commit -m "매일 글: <키워드>" && git push origin HEAD:main`
- push 가 다른 실행과 겹쳐 거절되면(non-fast-forward) `git pull --rebase origin main` 후 한 번 더 push 한다.
- 배포 결과 확인 (예약 세션엔 GitHub 도구가 없으니 공개 API 사용, 2~5분 걸림):
  `curl -s "https://api.github.com/repos/yoorimint/first-lab/actions/runs?head_sha=$(git rev-parse HEAD)" | python3 -c "import json,sys;[print(r['name'],r['status'],r['conclusion']) for r in json.load(sys.stdin)['workflow_runs']]"`
  conclusion 이 success 면 끝. failure 면 원인만 보고하고 끝낸다.
- push 가 권한 문제로 실패하면 다른 방법을 찾지 말고 "push 실패"로 보고하고 끝낸다.

## 6. 글 갱신 (있는 날만)
1단계 응답의 `refresh_target` 이 `null` 이면 건너뛴다. 있으면 그 글 1개를 고친다 (새 글이 실패했어도 따로 진행).
- `mode` 가 `"enhance"`(보강): `bot.refresh.ENHANCE_SYSTEM` 규칙대로. `siblings` 는 이 글에서 함께 답할 검색어다.
  `why` 가 순위 정체·하락이면 상위 글들이 다루는데 이 글에 빠진 하위 질문을 채우는 게 핵심이다.
- `mode` 가 `"recheck"`(재검증): `bot.factcheck.SYSTEM` 규칙대로 수치·기준만 공식 출처로 다시 확인하고 바뀐 것만 고친다.
- `mode` 가 `"retitle"`(제목): 순위는 괜찮은데 검색 결과에서 안 눌리는 글이다. 본문은 건드리지 않고 **제목·요약문만** 다시 쓴다.
  `queries`(이 글로 실제 들어온 검색어)를 보고, 검색한 사람이 찾는 답이 이 글에 있다는 게 제목에서 바로 보이게.
  규칙은 `bot.refresh.RETITLE_SYSTEM` (본문에 있는 숫자만, 과장·낚시 금지, 제목 60자·요약문 120자 이내).
  파일에는 `content_html` 대신 `"title"`, `"excerpt"` 를 넣는다. 숫자가 본문에 없으면 서버가 거부한다.
- 보강·재검증 공통: `content_html` 의 구조·말투, 이미지(figure)·링크(a) 태그는 그대로 둔다. 본문을 줄이지 않는다 (기존의 80% 미만이면 서버가 거부).
  제목은 바꾸지 않는다. 조사는 3단계와 같은 원칙.
- `wordpress/queue/refresh-<id>-<오늘 날짜>.json` 으로 저장:
  `{"type": "refresh", "post_id": <id>, "base_modified": "<응답 값 그대로>", "content_html": "<고친 본문 전체>", "changes": "<무엇을 바꿨는지 한 줄>"}`
  (제목 모드는 `content_html` 대신 `"title": "...", "excerpt": "..."`)
  (`base_modified` 는 그 사이 운영자가 글을 고쳤으면 덮어쓰지 않게 하는 값이다. 바꾸지 말 것)
- 고칠 게 없으면 파일을 만들지 않는다.
- 5단계와 같이 검사 → 커밋(`매일 갱신: <글 제목>`) → main 에 올림 → 배포 확인.

## 7. 마무리 보고 (세션 마지막 메시지)
- 새 글: 고른 키워드와 건너뛴 후보(이유), 제목, 근거 약한 주장 처리 결과(보강·완화·삭제 각각 무엇을), 배포 결과.
  본문에 근거 약한 채로 **단정형**으로 남은 주장이 있으면 안 된다.
- 갱신: 대상 글, 바꾼 내용, 배포 결과 (없으면 "갱신 대상 없음").
