/* 일용직 4대보험·세금 계산기: 일당·이번 달 일한 날수로 일용근로소득세(일 15만 원 공제·6%·세액공제 55%·1천 원 미만 소액부징수),
   고용보험(근로자 0.9%), 가입 기준을 넘으면 국민연금·건강보험·장기요양보험 근로자 몫. 요율·기준은 data/daily-worker-insurance.json
   (소득세법 제47조②·제59조③·제86조·제129조①4호, 국민연금법 시행령 제2조제1호, 국민건강보험법 제6조②1호, 고용보험법 제10조②) */
(function () {
  'use strict';

  const pct = (n) => `${Math.round(n * 1000) / 1000}%`;

  FirstLabCalc.register({
    id: 'daily-worker-insurance',
    title: '일용직 4대보험·세금 계산기',
    intro: '일당과 이번 달 일한 날수를 넣으면 일용근로소득세와 고용보험, 가입 기준에 해당할 때의 국민연금·건강보험까지 빼고 손에 쥐는 금액을 계산해요.',
    inputs: [
      { name: 'wage', label: '일당 (원, 세전)', type: 'number', min: 0, step: 1, default: 200000, help: '하루 일하고 받기로 한 금액이에요. 날마다 같다고 보고 계산해요.' },
      { name: 'days', label: '이번 달 일한 날수', type: 'number', min: 1, step: 1, default: 10, help: '같은 사업장에서 이번 달(1일~말일)에 일한 날이에요. 몇 시간만 일한 날도 하루로 세요.' },
      { name: 'hours', label: '이번 달 일한 시간 합계', type: 'number', min: 0, step: 1, default: 80, help: '국민연금 월 60시간 기준을 볼 때만 써요.' },
      {
        name: 'month1', label: '같은 사업장에서 1개월 이상 이어서 일하나요', type: 'select', default: 'yes',
        options: [['yes', '예 (1개월 이상)'], ['no', '아니요 (1개월 안 됨)']],
      },
      {
        name: 'site', label: '일하는 곳', type: 'select', default: 'general',
        options: [['general', '건설현장이 아닌 곳'], ['construction', '건설현장']],
      },
      {
        name: 'age60', label: '나이', type: 'select', default: 'no',
        options: [['no', '18세 이상 60세 미만'], ['yes', '60세 이상 (국민연금 안 냄)']],
      },
      {
        name: 'age65', label: '65세 이후 새로 고용됐나요', type: 'select', default: 'no',
        options: [['no', '아니요'], ['yes', '예 (실업급여 보험료 안 냄)']],
      },
    ],

    compute(v, d, u) {
      const floor = FirstLabCalc.util.floor;
      const floor10 = (n) => floor(n / 10) * 10; // 국고금관리법 제47조①: 10원 미만 끝수 버림
      const wage = Number(v.wage);
      const days = Number(v.days);
      const hours = Number(v.hours) || 0;
      if (!(wage > 0)) throw new u.InputError('일당을 넣어 주세요.');
      if (!Number.isInteger(days) || days < 1 || days > 31) throw new u.InputError('이번 달 일한 날수는 1일부터 31일까지 넣어 주세요.');
      if (hours < 0) throw new u.InputError('일한 시간은 0 이상으로 넣어 주세요.');

      const pay = wage * days; // 이번 달 보수 합계

      // 일용근로소득세 (하루치): (일당 − 15만 원) × 6% − 그 세액의 55%, 10원 미만 버림, 1천 원 미만이면 안 뗌
      const taxBase = Math.max(0, wage - d.daily_deduction);
      // 산출세액 × (1 − 55%) 를 한 번에 정수로 계산 (3,000 × 0.55 처럼 소수 곱셈 오차로 10원이 빠지지 않게)
      let dayTax = floor10((taxBase * d.tax_rate_pct * (100 - d.tax_credit_pct)) / 10000);
      if (dayTax < d.small_tax_floor) dayTax = 0;
      const dayLocal = floor10(dayTax / 10);
      const incomeTax = dayTax * days;
      const localTax = dayLocal * days;

      // 고용보험(실업급여) 근로자 몫: 일용근로자는 일한 날수·시간과 상관없이 적용, 65세 이후 새로 고용되면 안 냄
      const employment = v.age65 === 'yes' ? 0 : floor((pay * d.employment_rate_employee_per_1000) / 1000);

      // 국민연금: 1개월 이상 계속 + (월 8일 이상 또는 월 60시간 이상(건설현장 제외) 또는 월 소득 고시 금액 이상)
      const month1 = v.month1 !== 'no';
      // 국민연금법 제8조①: 18세 이상 60세 미만만 사업장가입자 (11/5 외부 검수: 60세 이상도 연금을 뗌)
      const over60 = v.age60 === 'yes';
      const pensionIn = !over60 && month1 && (days >= d.min_days || (v.site !== 'construction' && hours >= d.pension_min_hours) || pay >= d.pension_income_threshold);
      let pensionBase = 0;
      let pension = 0;
      if (pensionIn) {
        pensionBase = Math.min(Math.max(floor(pay / 1000) * 1000, d.pension_min), d.pension_max);
        pension = floor10((pensionBase * d.pension_rate_employee_per_10000) / 10000);
      }
      // 건강보험: 1개월 이상 계속 + 월 8일 이상 (근로시간과 상관없음). 근로자 몫 = 보수 × 7.19% ÷ 2, 상한·하한
      const healthIn = month1 && days >= d.min_days;
      let health = 0;
      let ltc = 0;
      const ltcRatio = Math.round((d.ltc_rate_per_1000000 * 100) / d.health_rate_per_10000); // 노인장기요양보험법 제9조①: 소수점 이하 다섯째자리 반올림
      if (healthIn) {
        health = Math.min(Math.max(floor10((pay * d.health_rate_per_10000) / 20000), d.health_employee_min), d.health_employee_max);
        ltc = floor((health * ltcRatio) / 10000);
      }

      const total = incomeTax + localTax + employment + pension + health + ltc;
      const net = pay - total;

      const rows = [
        ['이번 달 보수', `${u.won(wage)} × ${days}일 = ${u.won(pay)}`],
        [`소득세 (하루 ${u.won(dayTax)} × ${days}일)`, u.won(incomeTax)],
        [`지방소득세 (하루 ${u.won(dayLocal)} × ${days}일)`, u.won(localTax)],
        [v.age65 === 'yes' ? '고용보험 (65세 이후 고용이라 안 냄)' : `고용보험 (× ${pct(d.employment_rate_employee_per_1000 / 10)})`, u.won(employment)],
        [pensionIn ? `국민연금 (${u.won(pensionBase)} × ${pct(d.pension_rate_employee_per_10000 / 100)})` : (over60 ? '국민연금 (60세 이상이라 안 냄)' : '국민연금 (가입 기준 아래라 안 냄)'), u.won(pension)],
        [healthIn ? `건강보험 (× ${pct(d.health_rate_per_10000 / 200)})` : '건강보험 (가입 기준 아래라 안 냄)', u.won(health)],
        [healthIn ? `장기요양보험 (건강보험료 × ${(ltcRatio / 100).toFixed(2)}%)` : '장기요양보험', u.won(ltc)],
        ['공제 합계', u.won(total)],
        ['손에 쥐는 금액', u.won(net)],
      ];
      const notes = [];
      if (dayTax === 0) notes.push(`하루 소득세가 ${u.won(d.small_tax_floor)} 미만이면 떼지 않아요 (소액부징수). 일당 ${u.won(d.daily_deduction)} 이하면 세금이 아예 없어요.`);
      notes.push('소득세는 일당을 날마다 받는다고 보고 하루씩 계산했어요. 여러 날 치를 한 번에 받으면 소액부징수 판단이 달라질 수 있어요.');
      if (month1 && !healthIn && v.site !== 'construction') notes.push(`건설현장이 아닌 일용직의 건강보험 근로일수 기준(월 ${d.min_days}일)은 국민건강보험공단(1577-1000)에서 확인해요 — 법령은 '1개월 이상 고용'만 정해요.`);
      if (!month1) notes.push('같은 사업장에서 1개월이 안 되면 국민연금·건강보험은 사업장에서 가입하지 않아요.');
      notes.push('국민연금·건강보험 가입 여부와 보험료는 회사가 공단에 신고한 내용으로 정해져서, 이 계산과 다를 수 있어요. 산재보험료는 회사가 전부 내요.');
      return {
        values: { pay, day_tax: dayTax, income_tax: incomeTax, local_tax: localTax, employment, pension_in: pensionIn, pension_base: pensionBase, pension, health_in: healthIn, health, ltc, total, net },
        rows,
        notes,
      };
    },
  });
})();
