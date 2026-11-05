/* 내신 등급 계산기: 석차·수강자수로 과목 석차등급(5등급제·9등급제), 과목 등급으로 학점 가중 평균.
   비율·반올림·중간석차 규칙은 data/school-grade.json (경기도교육청 2026학년도 고등학교 학업성적관리 시행지침) */
(function () {
  'use strict';

  const SUBJECTS = 6;
  const subjectInputs = [];
  for (let i = 1; i <= SUBJECTS; i += 1) {
    subjectInputs.push(
      { name: `g${i}`, label: `과목 ${i} 등급`, type: 'number', min: 1, step: 1, help: i === 1 ? '평균 모드에서만 써요. 빈칸인 과목은 빼고 계산해요.' : undefined },
      { name: `c${i}`, label: `과목 ${i} 학점(단위수)`, type: 'number', min: 0, step: 1 },
    );
  }

  FirstLabCalc.register({
    id: 'school-grade',
    title: '내신 등급 계산기',
    intro: '과목 석차와 수강자수를 넣으면 몇 등급인지, 과목별 등급과 학점을 넣으면 평균 등급을 보여줘요.',
    inputs: [
      {
        name: 'mode', label: '무엇을 계산할까요', type: 'select', default: 'rank',
        options: [['rank', '석차로 과목 등급 찾기'], ['average', '과목 등급으로 평균 등급 내기']],
      },
      {
        name: 'system', label: '등급제', type: 'select', default: 'five',
        options: [['five', '5등급제 (2025년 이후 고1 입학)'], ['nine', '9등급제 (2024년 이전 고1 입학)']],
      },
      { name: 'total', label: '수강자수 (명)', type: 'number', min: 1, step: 1, help: '그 과목을 들은 전체 학생 수예요.' },
      { name: 'rank', label: '내 석차 (등)', type: 'number', min: 1, step: 1, help: '동점자가 있으면 동점자 중 가장 높은 석차를 넣어요.' },
      { name: 'ties', label: '동석차 인원수 (나 포함, 명)', type: 'number', min: 1, step: 1, default: 1, help: '학교 동점자 처리 규정을 적용한 뒤에도 같은 석차인 인원(나 포함)이에요. 없으면 1이에요.' },
      ...subjectInputs,
    ],

    compute(v, d, u) {
      const blank = (x) => x === undefined || x === null || String(x).trim() === '';
      const int = (x, name, min) => {
        const n = Number(x);
        if (blank(x) || !Number.isInteger(n) || n < min) throw new u.InputError(`${name}을(를) ${min} 이상의 정수로 넣어 주세요.`);
        return n;
      };
      const system = v.system;
      if (!['five', 'nine'].includes(system)) throw new u.InputError('등급제를 골라 주세요.');
      const pcts = d.cutoffs[system];
      const levels = pcts.length;
      const name = system === 'five' ? '5등급제' : '9등급제';

      if (v.mode === 'average') {
        let weighted = 0;
        let credits = 0;
        let count = 0;
        for (let i = 1; i <= SUBJECTS; i += 1) {
          if (blank(v[`g${i}`])) continue;
          const g = int(v[`g${i}`], `과목 ${i} 등급`, 1);
          if (g > levels) throw new u.InputError(`${name} 등급은 1~${levels} 사이로 넣어 주세요.`);
          const c = int(v[`c${i}`], `과목 ${i} 학점`, 1);
          weighted += g * c;
          credits += c;
          count += 1;
        }
        if (!count) throw new u.InputError('과목 등급과 학점을 한 과목 이상 넣어 주세요.');
        const avg = Math.round((weighted / credits) * 100 + 1e-9) / 100;
        return {
          values: { avg, credits, subjects: count },
          rows: [['평균 등급', `${avg.toFixed(2)}등급`], ['넣은 과목·학점', `${count}과목 · ${credits}학점`], ['계산식', `(등급×학점 합 ${weighted}) ÷ 학점 합 ${credits}`]],
          notes: [
            '학점(단위수)을 가중치로 한 평균이에요 (소수 셋째 자리에서 반올림).',
            '대학마다 반영 교과·학년별 비율이 달라서, 실제 환산 점수는 지원할 대학의 모집요강으로 확인해요.',
          ],
        };
      }
      if (v.mode !== 'rank') throw new u.InputError('무엇을 계산할지 골라 주세요.');

      const total = int(v.total, '수강자수', 1);
      const rank = int(v.rank, '석차', 1);
      const ties = blank(v.ties) ? 1 : int(v.ties, '동석차 인원수', 1);
      if (rank + ties - 1 > total) throw new u.InputError('석차와 동석차 인원수가 수강자수를 넘어요. 숫자를 다시 확인해 주세요.');

      // 등급별 누적 학생수 = 수강자수 × 누적 비율을 반올림 (정수로 계산해 소수 오차 없음)
      const cum = pcts.map((p) => u.floor((total * p + 50) / 100));
      const byCount = (x) => cum.findIndex((c) => x <= c) + 1;
      const mid = rank + (ties - 1) / 2;  // 중간석차 = 석차 + (동석차 인원수 - 1) / 2
      const midPct = Math.round((mid / total) * 10000 + 1e-9) / 100;
      const rankPct = Math.round((rank / total) * 10000 + 1e-9) / 100;  // 표에 보이는 석차 백분율은 내 석차 기준 (11/5 검수: 중간석차 값을 '석차 백분율'로 보임)
      const first = byCount(rank);
      const last = byCount(rank + ties - 1);
      const usedMid = ties > 1 && first !== last;  // 동점자가 등급 경계에 걸침 → 중간석차백분율로
      const grade = usedMid ? pcts.findIndex((p) => mid * 100 <= p * total) + 1 : first;

      const rows = [
        ['과목 등급', `${grade}등급 (${name})`],
        usedMid ? ['중간석차백분율', `${midPct}%`] : ['석차 백분율', `${rankPct}%`],
        ['등급별 누적 인원', cum.slice(0, -1).map((c, k) => `${k + 1}등급 ${c}명`).join(' · ')],
      ];
      const notes = [];
      if (usedMid) notes.push(`동점자 ${ties}명이 등급 경계에 걸쳐서 중간석차 ${mid}등(${midPct}%)으로 모두 같은 등급을 받아요.`);
      if (grade > 1 && !usedMid) rows.push(['한 등급 위 기준', `${grade - 1}등급은 ${cum[grade - 2]}등 이내`]);
      if (total <= d.small_class[system]) notes.push(`수강자가 ${d.small_class[system]}명 이하인 과목은 석차등급 대신 '·'로 적힐 수 있어요 (같은 입학년도 교육과정의 같은 과목은 한 가지 방식으로 통일).`);
      notes.push(system === 'five' ? '사회·과학 융합 선택 9과목, 과학탐구실험, 체육·예술, 교양, 공동교육과정 과목 등은 석차등급이 나오지 않아요.' : '진로 선택 과목, 과학탐구실험, 체육·예술, 교양, 전문교과Ⅱ 과목 등은 석차등급이 나오지 않아요.');
      notes.push('경기도교육청 2026학년도 학업성적관리 시행지침의 계산 방식이에요. 소속 시·도 지침과 학교 규정으로 최종 확인해요.');
      return { values: { grade, cum1: cum[0], midPct, rankPct, usedMid, shown_pct: usedMid ? midPct : rankPct }, rows, notes };
    },
  });
})();
