# 쉬운 말 다듬기 지시문 (감시 세션이 서브에이전트에게 줌, model: opus) — 11/9 운영자 '기존 글 전체 쉬운 말로'

`{N}` 자리에 글 번호를 넣어 그대로 준다.

---
You are rewriting ONE existing Korean info blog post into plain, easy Korean — same facts, easier words.
Input: /tmp/firstlab-review/post-{N}.json (field content_html; also title/excerpt for context). Do NOT edit any repo files.
Write the full rewritten content_html to /tmp/firstlab-review/plain-{N}.html.

Target reader: a Korean middle-school first grader (중학교 1학년). Every sentence should be understandable to them on first read.

Rules
0. Plain words: '사용자'→'회사(사장님)', '근로자'→'일하는 사람' where natural; say what a thing IS before its legal name. Put law names/articles at the END of a sentence in parentheses, e.g. "회사는 일주일에 하루 이상 돈 주는 쉬는 날을 줘야 해요(근로기준법 제55조)." One idea per sentence; average sentence ≤ 40 characters (the check script measures it). Before a hard rule, show a tiny example using ONLY numbers already in the original.
1. Facts must not change. Keep EVERY number, amount, rate, date, period, law article (제N조…), link (<a href>) and shortcode ([firstlab_calc …]) exactly; keep every condition and exception (다만 …). Do not add new facts or numbers. Do not delete sentences that carry a condition.
2. Keep the HTML structure: same h2/h3 headings (you may reword heading text slightly but keep the count), same tables (same rows; you may simplify cell wording but keep numbers), same lists. Keep the "공식 안내 바로가기" section as is.
3. First paragraph: start with ONE plain sentence that answers the reader's question like you would to a friend — max 70 characters, no parentheses, no law article, no jargon. Then the original first-paragraph content (made easier).
4. Jargon: the first time a technical term appears (e.g. 소정근로일, 통상임금, 과세표준, 원천징수), say it in everyday words first, e.g. "일하기로 정한 날(소정근로일)". Use the everyday words afterwards where natural.
4-1. Do NOT reword legal threshold words that change meaning when simplified — keep '상시 ○명', '소정', '이상·이하·초과·미만', '원칙적으로' as they are (you may add a plain explanation next to them). Do NOT add examples or explanations that are not in the original (e.g. '경비처럼') — explain only with words the original already supports. (11/9 시범 #1419: '상시 5명' → '늘 5명'으로 뜻이 바뀜)
5. Short sentences: max 100 characters per sentence, paragraph ≤ 300, list item ≤ 200. Keep the friendly ~해요 tone. No jokes, no invented anecdotes.
6. Check: run `cd /home/user/first-lab && python3 automation/plain_check.py {N} /tmp/firstlab-review/post-{N}.json /tmp/firstlab-review/plain-{N}.html` and fix until it prints "통과". (Repeating an existing number in the first sentence is allowed.)
Report in Korean: the new first sentence, 3–5 examples of before→after (jargon/sentences), and anything you were unsure about. Do not write the --write file.
