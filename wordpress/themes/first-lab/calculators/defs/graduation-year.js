/* 졸업년도 계산기: 출생 연월(또는 초등학교 입학 연도)로 초·중·고·대학 입학·졸업 연도.
   취학 기준·학년도·수업연한은 data/graduation-year.json (초·중등교육법 제13·24·39·42·46조, 2007.8.3 개정 전 제13조, 고등교육법 제20·31·48조) */
(function () {
  'use strict';

  const MONTHS = [];
  for (let m = 1; m <= 12; m += 1) MONTHS.push([String(m), `${m}월`]);

  FirstLabCalc.register({
    id: 'graduation-year',
    title: '졸업년도 계산기',
    intro: '태어난 연도와 달을 넣으면 초등학교·중학교·고등학교·대학교 입학 연도와 졸업 연도를 보여줘요. 빠른년생·조기입학·입학 연기·재수·휴학도 반영돼요.',
    inputs: [
      {
        name: 'mode', label: '무엇으로 계산할까요', type: 'select', default: 'birth',
        options: [['birth', '태어난 연도·달로'], ['entry', '초등학교 입학 연도로']],
      },
      { name: 'year', label: '태어난 연도', type: 'number', min: 1970, step: 1, help: '예: 1995' },
      { name: 'month', label: '태어난 달', type: 'select', default: '3', options: MONTHS, help: '2002년까지의 1·2월생(빠른년생)은 한 해 일찍 입학한 것으로 계산해요.' },
      {
        name: 'entry_type', label: '초등학교 입학', type: 'select', default: '0',
        options: [['0', '제때 입학'], ['-1', '1년 일찍 입학 (조기입학)'], ['1', '1년 늦게 입학 (입학 연기)']],
      },
      { name: 'entry_year', label: '초등학교 입학 연도 (입학 연도로 계산할 때)', type: 'number', min: 1976, step: 1 },
      {
        name: 'college', label: '대학 과정', type: 'select', default: '4',
        options: [['4', '4년제 대학'], ['2', '2년제 전문대학'], ['3', '3년제 전문대학'], ['6', '6년제 (의학·약학 등)'], ['0', '대학 안 감']],
      },
      { name: 'gap', label: '고교 졸업 뒤 대학 입학까지 쉰 해 (재수 등)', type: 'number', min: 0, step: 1, default: 0 },
      { name: 'leave', label: '대학 휴학·군 복무로 늦어진 해', type: 'number', min: 0, step: 1, default: 0, help: '학기 단위 휴학은 졸업 학기가 바뀔 수 있어 대학 학사 일정으로 확인해요.' },
    ],

    compute(v, d, u) {
      const blank = (x) => x === undefined || x === null || String(x).trim() === '';
      const int = (x, name, min, max) => {
        const n = Number(x);
        if (blank(x) || !Number.isInteger(n) || n < min || n > max) throw new u.InputError(`${name}을(를) ${min}~${max} 사이의 정수로 넣어 주세요.`);
        return n;
      };
      const thisYear = Number(u.todayIso().slice(0, 4));
      const notes = [];
      const rows = [];
      let entry;
      let earlyBorn = false;

      if (v.mode === 'entry') {
        entry = int(v.entry_year, '초등학교 입학 연도', d.min_birth_year + 6, thisYear + 8);
      } else if (v.mode === 'birth') {
        const year = int(v.year, '태어난 연도', d.min_birth_year, thisYear);
        const month = int(v.month, '태어난 달', 1, 12);
        const shift = blank(v.entry_type) ? 0 : Number(v.entry_type);
        if (![-1, 0, 1].includes(shift)) throw new u.InputError('초등학교 입학 방법을 골라 주세요.');
        // 2007.8.3 개정 전 제13조: 만6세가 된 날의 다음날 이후 최초 학년초(3월 1일) → 1·2월생은 6세가 된 그해 3월 입학
        earlyBorn = year <= d.old_rule_last_year && month <= d.old_rule_last_month;
        entry = year + (earlyBorn ? 6 : 7) + shift;
        rows.push(['제때 입학 기준', `${year + (earlyBorn ? 6 : 7)}년 3월${earlyBorn ? ' (빠른년생: 6세가 된 해 3월)' : ' (6세가 된 해의 다음 해 3월)'}`]);
        if (earlyBorn) notes.push('2002년까지의 1·2월생은 옛 취학 기준(만 6세가 된 다음 날 이후 첫 3월)으로 한 해 위 학년과 함께 입학했어요.');
        if (shift === -1 && entry >= 2009) notes.push('조기입학은 5세가 된 해의 10월 1일부터 12월 31일까지 사는 곳 읍·면·동장(주민센터)에게 신청서를 내요.');
        if (shift === 1 && entry >= 2009) notes.push('입학 연기는 6세가 된 해의 10월 1일부터 12월 31일까지 사는 곳 읍·면·동장(주민센터)에게 신청서를 내요.');
      } else {
        throw new u.InputError('무엇으로 계산할지 골라 주세요.');
      }

      const gap = blank(v.gap) ? 0 : int(v.gap, '쉰 해', 0, 20);
      const leave = blank(v.leave) ? 0 : int(v.leave, '늦어진 해', 0, 20);
      const college = blank(v.college) ? 4 : Number(v.college);
      if (![0, 2, 3, 4, 6].includes(college)) throw new u.InputError('대학 과정을 골라 주세요.');

      const y = d.years;
      const elemGrad = entry + y.elementary;
      const midGrad = elemGrad + y.middle;
      const highGrad = midGrad + y.high;
      const values = { entry, elemGrad, midEntry: elemGrad, midGrad, highEntry: midGrad, highGrad, earlyBorn };

      rows.push(['초등학교', `${entry}년 3월 입학 → ${elemGrad}년 졸업`]);
      rows.push(['중학교', `${elemGrad}년 3월 입학 → ${midGrad}년 졸업`]);
      rows.push(['고등학교', `${midGrad}년 3월 입학 → ${highGrad}년 졸업`]);
      if (college) {
        const uniEntry = highGrad + gap;
        const uniGrad = uniEntry + college + leave;
        values.uniEntry = uniEntry;
        values.uniGrad = uniGrad;
        const label = college === 2 || college === 3 ? `전문대학 (${college}년)` : `대학교 (${college}년)`;
        rows.push([label, `${uniEntry}년 3월 입학 → ${uniGrad}년 졸업${gap || leave ? ` (쉰 해 ${gap}년·늦어진 해 ${leave}년 포함)` : ''}`]);
      }

      notes.push('학년도는 3월 1일에 시작해 다음 해 2월 말일에 끝나서, 졸업 연도는 마지막 학년도가 끝나는 해(그해 2월 말일까지)예요. 대학은 학칙으로 학년도를 달리 정하거나 학점을 일찍 채워 일찍 졸업할 수 있어요.');
      notes.push('조기진급·조기졸업, 유급, 검정고시, 고등학교 시간제·통신제(4년), 대학 조기졸업·계절학기는 반영하지 않아요.');
      if (entry < d.verified_from_entry) notes.push(`${d.verified_from_entry}년 이전 입학은 지금 법(초·중등교육법)이 생기기 전이라, 같은 방식으로 계산한 참고값이에요. 정확한 연도는 졸업증명서로 확인해요.`);
      if (highGrad > thisYear) notes.push('아직 오지 않은 연도는 제때 진학한다고 가정한 예상 연도예요.');
      return { values, rows, notes };
    },
  });
})();
