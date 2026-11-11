/* 고용보험료 계산기: 월 보수(세전 총액 − 비과세) × 실업급여 보험료율 1.8%의 절반(근로자·사업주 각각) + 사업주만 내는
   고용안정·직업능력개발 보험료(회사 규모별 0.25~0.85%). 65세 이후 새로 고용된 사람은 실업급여 보험료를 안 냄.
   요율은 data/employment-insurance.json (고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률 제2조3호·제13조②③④,
   같은 법 시행령 제2조의2·제12조①) */
(function () {
  'use strict';

  const pct = (n) => `${Math.round(n * 1000) / 1000}%`;

  FirstLabCalc.register({
    id: 'employment-insurance',
    title: '고용보험료 계산기',
    intro: '이번 달 세전 월급과 비과세 금액, 회사 규모를 넣으면 근로자가 내는 고용보험료와 회사가 내는 몫을 나눠서 계산해요.',
    inputs: [
      { name: 'pay', label: '이번 달 세전 월급 합계 (원)', type: 'number', min: 0, step: 1, default: 3200000, help: '기본급·수당을 모두 더한, 세금 떼기 전 금액이에요.' },
      { name: 'nontax', label: '그중 비과세 금액 (원)', type: 'number', min: 0, step: 1, default: 200000, help: '급여명세서에 비과세로 적힌 식대 등이에요. 보험료 계산에서 빠져요.' },
      {
        name: 'size', label: '회사 규모 (상시근로자 수)', type: 'select', default: 'under150',
        options: [
          ['under150', '150명 미만'],
          ['priority', '150명 이상 + 우선지원대상기업'],
          ['under1000', '150명 이상 1,000명 미만'],
          ['over1000', '1,000명 이상 · 국가·지자체'],
        ],
      },
      {
        name: 'age65', label: '65세 이후 새로 고용됐나요', type: 'select', default: 'no',
        options: [['no', '아니요'], ['yes', '예 (실업급여 보험료 안 냄)']],
      },
    ],

    compute(v, d, u) {
      const floor = FirstLabCalc.util.floor;
      const pay = Number(v.pay);
      const nontax = Number(v.nontax) || 0;
      if (!(pay > 0)) throw new u.InputError('세전 월급을 넣어 주세요.');
      if (nontax < 0) throw new u.InputError('비과세 금액은 0 이상으로 넣어 주세요.');
      if (nontax > pay) throw new u.InputError('비과세 금액이 월급보다 클 수 없어요.');
      const stableRate = d.stable_rate_per_10000[v.size];
      if (stableRate === undefined) throw new u.InputError('회사 규모를 골라 주세요.');

      const base = pay - nontax; // 보험료징수법 제2조3호: 근로소득 − 비과세 근로소득 = 보수
      const no65 = v.age65 === 'yes';
      // 실업급여 보험료: 요율 1천분의 18을 근로자·사업주가 절반씩 (제13조②④2호), 65세 이후 고용이면 안 냄 (제13조③)
      const employee = no65 ? 0 : floor((base * d.unemployment_rate_per_1000) / 2000);
      const employerUnemp = employee;
      // 고용안정·직업능력개발 보험료: 사업주만 (제13조④1호, 시행령 제12조①1호)
      const employerStable = floor((base * stableRate) / 10000);
      const employer = employerUnemp + employerStable;
      const total = employee + employer;

      const half = pct(d.unemployment_rate_per_1000 / 20);
      const rows = [
        ['보험료 기준 보수', `${u.won(pay)} − 비과세 ${u.won(nontax)} = ${u.won(base)}`],
        [no65 ? '근로자 몫 (65세 이후 고용이라 안 냄)' : `근로자 몫 (보수 × ${half})`, u.won(employee)],
        [no65 ? '회사 실업급여 몫 (안 냄)' : `회사 실업급여 몫 (보수 × ${half})`, u.won(employerUnemp)],
        [`회사 고용안정·직업능력개발 몫 (보수 × ${pct(stableRate / 100)})`, u.won(employerStable)],
        ['회사 몫 합계', u.won(employer)],
        ['근로자 + 회사 합계', u.won(total)],
      ];
      const notes = [];
      if (no65) notes.push('65세 전부터 고용보험에 들어 있던 사람이 65세 이후에도 계속 일하면 실업급여 보험료를 그대로 내요.');
      notes.push('2026년까지는 매달 떼는 고용보험료가 전년도 보수로 정한 월평균보수 기준이라 그달 월급으로 한 계산과 다를 수 있고, 다음 해 정산 때 차이를 더 내거나 돌려받아요. 2027년 1월부터는 회사가 매달 신고한 그달 보수가 기준이에요.');
      notes.push('원 미만은 버렸어요. 실제 고지액은 끝자리가 조금 다를 수 있어요.');
      if (d.planned_note) notes.push(d.planned_note);
      return {
        values: { base, employee, employer_unemp: employerUnemp, employer_stable: employerStable, employer, total },
        rows,
        notes,
      };
    },
  });
})();
