# +@ 보강 지시문 (감시 세션이 서브에이전트에게 줌, model: opus) — 11/11 운영자 '+@ 가 중요하다, 초안 전부 +@ 작업해라'

순서: `python3 automation/plus_queue.py 4` 로 글 고르기 → ① 이 지시문으로 보강(서브에이전트 A) → ② 다른 서브에이전트가 새로 더한 사실만 공식 출처로 검수(이 파일 끝 '사실 검수') →
③ 감시 세션이 `python3 automation/plain_diff.py /tmp/firstlab-review/plus-{N}.json /tmp/firstlab-review/plus-{N}-r.html` 로 더한 곳을 **끝까지 직접 읽고** 계산을 다시 해 본 뒤에만
`python3 automation/plus_check.py {N} /tmp/firstlab-review/plus-{N}.json /tmp/firstlab-review/plus-{N}-r.html /tmp/firstlab-review/plus-{N}-rec.json --write`.

---
You are adding "+@" to ONE existing Korean info blog post that is already in plain Korean. Do NOT edit repo files.
Inputs: /tmp/firstlab-review/plus-{N}.json (content_html, title, excerpt, keyword) and /tmp/firstlab-review/naver-{N}.txt (Naver top posts for the keyword: blog tab + web results, with body text).

0. **Only what this searcher needs** (운영자 11/11 '+@ 도 아무거나 넣으면 안 되는 거 알제'): every addition must answer a real question of someone who searched this keyword. No general background, no padding to make it longer, no off-topic tips, no promotion, no guesses or rumours (e.g. a budget proposal is not a fact — say it is a proposal and when it would apply), nothing already in the post. Fewer good additions beat many. No snapshot numbers that change daily (잔여예산·남은 인원·대기자 수 — the post goes live days later, #980): say where to check them instead. If the post already answers everything well, add only what is truly missing.
If naver-{N}.txt starts with '# 감시 세션 메모', do that first (a verified fix found earlier).
1. **+ (what top posts have and we lack)**: read naver-{N}.txt. List the questions/topics the top posts answer (headings, '?' sentences, tables). Mark which ones our post does not answer.
2. **@ (what nobody has)**: pick 1–3 additions top posts lack and readers need: a worked example with real numbers (계산 예시), situation-by-situation judgement (이런 경우엔 이렇게), a recent/upcoming legal change (with 시행일), or how/where to check one's own case (조회 경로·문의처).
3. **Facts only from official sources**: every new fact or number must be checked on a law (`cd /home/user/first-lab && python3 -m bot.lawtext <법령명> <제N조>`, annex: bot.law_api.annex_text) or a government/public page (.go.kr .or.kr etc., open it with WebFetch or curl). NEVER take facts, numbers or sentences from the top posts — they are only a list of questions. Do not copy their wording or their table of contents order. If a top-post question cannot be verified officially, answer it with where to check (기관·전화·누리집 메뉴), not with a guess.
4. **Write** the full new content_html to /tmp/firstlab-review/plus-{N}.html:
   - Keep ALL existing text meaning, numbers, law articles, links and [firstlab_calc] codes. Only add (or fix a verified error — report it).
   - Put each addition where a reader expects it (inside the right h2, a new h2, or a new 자주 묻는 질문 item). Keep the box, '이런 건 꼭 확인하세요' and '한 줄 정리' (add to the 확인 list only if the addition is a real 주의점).
   - Same plain style as the post: short sentences, ≤2 sentences per paragraph, explain hard words once, conditions kept (원칙적으로·이상/초과·대상 조건). Dated items carry the year; past dates written as past (today is in the prompt).
5. **Write the record** /tmp/firstlab-review/plus-{N}-rec.json:
   `{"serp": {"urls": [top post URLs you used, ≥3 different sites/bloggers], "questions": [≥3 questions the top posts answer — ALL must now be answered in our body], "added": "what we added that top posts lack"}, "review": [{"claim": "새 사실 한 문장 (숫자는 본문과 같은 표기)", "urls": ["공식 주소"], "verdict": "맞음", "action": "유지"}, ...], "added": [{"type": "+질문|@계산예시|@상황판단|@최신개정|@조회방법", "for": "the searcher's question it answers (for +질문: the exact serp.questions item)", "text": "what you added, one line"}, ...]}` — @ at most 3; the body must not grow more than 60%.
   Every new number / law article in the body must appear inside a 맞음 claim with an official URL.
6. **Check**: `cd /home/user/first-lab && python3 automation/plus_check.py {N} /tmp/firstlab-review/plus-{N}.json /tmp/firstlab-review/plus-{N}.html /tmp/firstlab-review/plus-{N}-rec.json` until "통과" (never --write). If it says "요약문:", write a new excerpt (≤120자) to /tmp/firstlab-review/plus-excerpt-{N}.txt and add --excerpt=that file.
7. Report in Korean: the + list (top-post questions we lacked), the @ list, each new fact with its official source, any original-text error found (with source), and the check result.

---
사실 검수 지시 (②): ORIGINAL=/tmp/firstlab-review/plus-{N}.json, NEW=/tmp/firstlab-review/plus-{N}.html, RECORD=plus-{N}-rec.json. Compare NEW with ORIGINAL: list every added or changed sentence. For each new fact, open its official source yourself (do not trust the record) and check the number, condition and 시행일; fix wrong ones (or remove), make sure no old sentence's meaning changed, that the additions answer serp.questions, and that nothing was copied from naver-{N}.txt. Also cut any addition a searcher of this keyword would not need (background, padding, off-topic, promotion, guesses) — fewer good additions beat many. Write the fixed body to plus-{N}-r.html and fixed record to plus-{N}-rec.json, run plus_check (no --write) until 통과, and report in Korean: 틀림 (원문 → 고친 글, 근거), 확인불가, 그대로 둔 것.
