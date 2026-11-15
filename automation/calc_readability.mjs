// 계산기 읽기 쉬움 검사 (11/16 운영자 '뭐든 처음 보는 사람도 바로 알 수 있어야 된다 — 계산기도 간단하게').
// 질문은 짧게, 고르는 칸은 짧은 단추로. 어려운 말·설명은 help(질문 아래 작은 글씨)로 보낸다.
// 사용: node automation/calc_readability.mjs  (문제 목록, 있으면 종료코드 1) — tests/test_calculators.mjs 도 같은 규칙으로 막음
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const LIMITS = { label: 24, option: 20, choiceMaxOptions: 4 };

export function problems(def) {
  const out = [];
  for (const i of def.inputs || []) {
    if ((i.label || '').length > LIMITS.label) out.push(`질문이 김 (${i.label.length}자 > ${LIMITS.label}): ${i.label}`);
    if (i.type === 'select' || i.type === 'choice') {
      for (const [, text] of i.options) {
        if (text.length > LIMITS.option) out.push(`선택지가 김 (${text.length}자 > ${LIMITS.option}): ${i.name} '${text}'`);
      }
      if (i.type === 'select' && i.options.length <= LIMITS.choiceMaxOptions) out.push(`선택지 ${i.options.length}개는 단추(choice)로: ${i.name}`);
    }
  }
  return out;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'wordpress', 'themes', 'first-lab', 'calculators', 'defs');
  const defs = {};
  globalThis.window = globalThis;
  globalThis.FirstLabCalc = { register: (d) => { defs[d.id] = d; }, util: {} };
  for (const f of fs.readdirSync(dir).sort()) (0, eval)(fs.readFileSync(path.join(dir, f), 'utf8'));
  let bad = 0;
  for (const [id, d] of Object.entries(defs)) {
    const p = problems(d);
    if (p.length) { bad++; console.log(`\n${id}`); p.forEach((x) => console.log('  ' + x)); }
  }
  console.log(`\n손볼 계산기 ${bad}개 / ${Object.keys(defs).length}개`);
  process.exit(bad ? 1 : 0);
}
