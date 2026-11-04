# 운영 감시 (35분마다, 운영자가 그만하라고 할 때까지)

운영·감시를 맡은 Claude 세션이 따르는 문서. 어느 세션이든 이 문서만 읽으면 바로 이어서 할 수 있게 쓴다.
운영 원칙은 저장소 맨 위 `CLAUDE.md`, 운영자 지시 기록은 `automation/operator-notes.md`.

## 시작 (새 세션이 이어받을 때)
1. `CLAUDE.md` → `automation/operator-notes.md` → 이 문서 순서로 읽는다.
2. `send_later` 로 35분 뒤 감시 예약 (메시지: "35분 감시 차례: automation/monitor.md 대로 …"). 예약은 그 세션에만 오므로 세션이 바뀌면 새로 건다.
3. 감시 차례마다 아래를 하고, 끝나면 다시 35분 예약.

## 감시 차례마다
1. **검수할 글 찾기**: `git pull` 후 `python3 automation/unreviewed.py` — 서버의 공개 대기·보류 글 전체를 글 번호로 `automation/reviewed.txt` 와 대조해, 검수 안 된 글과 최신 본문 파일(보류 수정본 우선)을 알려 준다. **시각 기준으로 새 글을 찾지 않는다** (경계에 걸린 #163 이 빠진 적 있음).
2. **새 글 전부 외부 검수** (운영자 지시):
   - `automation/review-prompt.md` 로 서브에이전트 검수. 3~4편씩 묶어 병렬, **model 은 "opus" 로 직접 지정**.
   - 검수 결과는 **내가 한 번 더 훑어본다**: 근거 링크가 실제로 그 말을 하는지, '주장' 원문이 글에 그대로 있는지, 수정안이 더 틀리지 않았는지.
   - **'찾지 못함'은 '없음'이 아니다**: 사이트 메뉴·기능·제도가 '확인불가'면 내가 직접 한 번 더 찾아본다(WebFetch). 그래도 안 되면 '없다/못 찾았다'고 단정하는 수정안은 쓰지 않고 '○○에서 확인해 보세요'로만 완화한다 (11/4 #219 파인 대출 계산기를 없다고 잘못 고친 일).
   - 고치기: `wordpress/patches.json` (post, why, find, replace_paragraph, tag p/li/tr). find 문구는 고친 뒤에 남으면 안 됨 (테스트가 막음).
   - 같은 종류 오류가 2편 이상이면 → 글쓰기 단계(검수 관문 `bot/review_gate.py`, 지시서 `automation/daily-writer.md`)를 **코드로** 보강.
   - 검수한 **글 번호**를 `automation/reviewed.txt` 에 추가해 커밋 (`번호 제목`).
3. **글 품질 훑기**: 제목 숫자가 본문에 있는지, 같은 문장 반복, 어색한 문장, 계산 제목은 다시 계산.
4. **서버 상태** (읽기 API `/wp-json/firstlab/v1/maintenance`, 헤더 X-Firstlab-Token):
   - 봇 기록의 오류, 연속 작성 자동 정지, 보류 초안(이유가 진짜인지 관문 오탐인지), 예약 공개.
   - 배포: `curl -s https://first-lab.kr/deployed-version.txt` 가 main 과 다르면 15분 따라잡기(deploy.yml schedule)가 도는지.
   - 배포 기록의 `[대표 사진] 요약: 사진 N · 대기 M · 없음 K` — K 가 늘면 원인 확인.
   - 사진: 한도(하루 약 50장, 오전 9시 초기화) 소진 시 '대기' 로 넘어가는지, 오전 9시 이후 '사진 채우기' 로 채워지는지.
5. **문제를 찾으면**: 지금 것 바로잡기 + 재발 방지(코드·테스트) → 테스트 종료코드 확인 → main 에 올림 → 배포 확인.
6. 운영자에게는 한글로, 바뀐 것과 결과만 짧게.

## 알아 둘 것 (겪은 일)
- WP 첨부는 상태가 `inherit`, 첨부 이름은 올릴 때 준 한국어 제목 → 사진은 파일 경로(`_wp_attached_file`)로 찾는다 (`bot/images.attachment_id`).
- GitHub 오류로 push 배포가 시작 안 된 적 있음 → 15분 따라잡기로 해결. 배포를 직접 실행할 권한은 없음(403).
- Cloudflare 무료 한도 소진 응답: HTTP 429 + "daily free allocation" → `images.QuotaExceeded`.
