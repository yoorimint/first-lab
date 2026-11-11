/* 육아휴직 급여 계산기: 휴직 시작일 기준 월 통상임금·휴직 개월 수·유형 → 달마다 받는 금액과 합계. 기준값은 data/parental-leave-benefit.json
 * 근거: 고용보험법 시행령 제95조①(일반: 1~3개월 100%·상한 250만, 4~6개월 100%·상한 200만, 7개월~ 80%·상한 160만, 하한 70만),
 *       제95조의3①(6+6: 자녀 출생 후 18개월까지 부모 모두 휴직 — 부모가 각각 N개월이면 N달까지 상한 250·250·300·350·400·450만)·③(한부모: 1~3개월 상한 300만).
 * 1개월이 안 되는 달은 일수에 비례(제95조③)해 계산이 달라서 여기서는 온전한 달만 센다. */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'parental-leave-benefit',
    title: '육아휴직 급여 계산기',
    intro: '휴직을 시작한 날 기준 월 통상임금과 휴직 개월 수를 넣으면, 달마다 받는 육아휴직 급여와 합계가 나와요.',
    inputs: [
      { name: 'wage', label: '월 통상임금 (원)', type: 'number', min: 1, step: 1, default: 3000000, help: '휴직을 시작한 날 기준 월 통상임금이에요. 정기적으로, 같은 기준으로 주기로 정한 임금이에요.' },
      { name: 'months', label: '휴직 개월 수', type: 'number', min: 1, max: 18, step: 1, default: 12, help: '나눠 쓰면 모두 더한 개월 수를 넣어요. 1년이 넘는 기간(최대 18개월)은 추가 사용 대상일 때만이에요.' },
      {
        name: 'kind', label: '어떤 경우인가요', type: 'select', default: 'general',
        options: [
          ['general', '일반 육아휴직'],
          ['single', '한부모 근로자'],
          ['both', '6+6: 생후 18개월 안에 부모 모두 휴직 (둘이 같은 개월 수)'],
        ],
      },
    ],

    compute(v, d, u) {
      const wage = Number(v.wage);
      const months = Number(v.months);
      if (!(wage > 0) || !Number.isInteger(wage)) throw new u.InputError('월 통상임금을 1원 단위로 넣어 주세요.');
      if (String(v.months ?? '').trim() === '' || !Number.isInteger(months) || months < 1 || months > d.max_months) {
        throw new u.InputError(`휴직 개월 수를 1~${d.max_months} 사이의 정수로 넣어 주세요.`);
      }
      const kind = ['general', 'single', 'both'].includes(v.kind) ? v.kind : 'general';
      const capOf = (m) => {
        if (kind === 'both' && m <= d.both_months) {
          const n = Math.min(months, d.both_months); // 부모가 각각 n개월 쓴 경우의 목 (제95조의3①1호 가~바목)
          return d.both_caps[n - 1][m - 1];
        }
        if (m <= 3) return kind === 'single' ? d.single_cap_1to3 : d.cap_1to3;
        if (m <= 6) return d.cap_4to6;
        return d.cap_7plus;
      };
      const pay = [];
      for (let m = 1; m <= months; m += 1) {
        const base = m <= 6 ? wage : u.floor((wage * d.rate_7plus_pct) / 100);
        pay.push(Math.max(d.floor_amount, Math.min(base, capOf(m))));
      }
      const total = pay.reduce((a, b) => a + b, 0);
      // 같은 금액이 이어지는 달끼리 묶어 표로
      const rows = [];
      let start = 1;
      for (let m = 2; m <= months + 1; m += 1) {
        if (m > months || pay[m - 1] !== pay[start - 1] || (start <= 6) !== (m <= 6)) {
          const label = start === m - 1 ? `${start}개월째` : `${start}~${m - 1}개월째`;
          const how = start <= 6 ? '통상임금 100%' : `통상임금 ${d.rate_7plus_pct}%`;
          rows.push([label, `월 ${u.won(pay[start - 1])} (${how}, 상한·하한 적용)`]);
          start = m;
        }
      }
      rows.push([`${months}개월 합계`, u.won(total)]);
      const notes = [];
      if (kind === 'both') {
        notes.push('6+6 특례 상한은 부모가 각각 쓴 개월 수로 정해져요. 둘의 개월 수가 다르면 금액이 달라질 수 있어 고용센터에서 확인하세요.');
        notes.push('배우자 몫은 배우자의 월 통상임금으로 따로 계산해요.');
      }
      if (months > 12) notes.push('1년을 넘는 기간은 부모가 같은 자녀로 각각 3개월 이상 쓴 경우, 한부모, 장애아동 부모만 쓸 수 있어요.');
      if (wage < d.floor_amount) notes.push(`월 통상임금이 ${u.won(d.floor_amount)}보다 적어도 하한액 ${u.won(d.floor_amount)}을 받아요.`);
      notes.push('온전한 한 달 기준이에요. 1개월이 안 되는 달은 휴직한 일수에 비례해 줄어요. 80% 계산의 원 미만은 버렸어요.');
      notes.push('휴직 중 회사에서 휴직을 이유로 받은 돈과 급여를 합쳐 월 통상임금을 넘으면, 넘는 만큼 급여가 줄어요.');
      return { values: { first: pay[0], last: pay[months - 1], total }, rows, notes };
    },
  });
})();
