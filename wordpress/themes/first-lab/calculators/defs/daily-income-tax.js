/* 일용직 소득세 계산기: 일당·일한 날수·받는 방식(날마다 / 여러 날 치 한 번에)으로 일용근로소득세와 지방소득세.
   (일당 − 15만 원) × 6% − 그 세액의 55%, 원천징수세액 1천 원 미만은 안 뗌 — 소액부징수는 지급금액 기준이라
   여러 날 치를 한 번에 받으면 그 합계로 판단 (국세청 일용근로소득 안내). 요율은 data/daily-income-tax.json
   (소득세법 제47조②·제59조③·제86조·제129조①4호, 지방세법 제103조의13①) */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'daily-income-tax',
    title: '일용직 소득세 계산기',
    intro: '일당과 일한 날수, 일당을 받는 방식을 넣으면 떼는 소득세·지방소득세와 세금을 뺀 금액을 계산해요.',
    inputs: [
      { name: 'wage', label: '일당 (원, 비과세 제외)', type: 'number', min: 0, step: 1, default: 200000, help: '하루 일하고 받기로 한 금액이에요. 식대 같은 비과세 금액은 빼고 넣어요.' },
      { name: 'days', label: '일한 날수', type: 'number', min: 1, step: 1, default: 5, help: '같은 사업장에서 일한 날이에요. 날마다 일당이 같다고 보고 계산해요.' },
      {
        name: 'pay', label: '일당을 받는 방식', type: 'select', default: 'lump',
        options: [['daily', '날마다 그날 일당을 받음'], ['lump', '여러 날 치를 한 번에 받음']],
      },
    ],

    compute(v, d, u) {
      const floor = FirstLabCalc.util.floor;
      const floor10 = (n) => floor(n / 10) * 10; // 국고금관리법 제47조①: 10원 미만 끝수 버림
      const wage = Number(v.wage);
      const days = Number(v.days);
      if (!(wage > 0)) throw new u.InputError('일당을 넣어 주세요.');
      if (!Number.isInteger(days) || days < 1 || days > 366) throw new u.InputError('일한 날수는 1일 이상 정수로 넣어 주세요.');
      const lump = v.pay !== 'daily';

      const gross = wage * days;
      const taxBase = Math.max(0, wage - d.daily_deduction);
      // 하루 결정세액 = 산출세액 × (1 − 55%) — 한 번에 정수로 계산해 소수 곱셈 오차를 막음
      const dayTax = floor10((taxBase * d.tax_rate_pct * (100 - d.tax_credit_pct)) / 10000);
      let incomeTax;
      let localTax;
      let exempt;
      if (lump) {
        // 지급금액 기준 (소득세법 제134조③ '지급할 때 그 근로소득에'): 여러 날 치를 한 번에 주면 합계 세액을 한 번에 계산해
        // 10원 미만을 한 번만 버리고, 그 합계가 1천 원 미만일 때만 안 뗌
        incomeTax = floor10((taxBase * days * d.tax_rate_pct * (100 - d.tax_credit_pct)) / 10000);
        exempt = incomeTax < d.small_tax_floor;
        if (exempt) incomeTax = 0;
        localTax = floor10(incomeTax / 10);
      } else {
        exempt = dayTax < d.small_tax_floor;
        const perDay = exempt ? 0 : dayTax;
        incomeTax = perDay * days;
        localTax = floor10(perDay / 10) * days;
      }
      const total = incomeTax + localTax;
      const net = gross - total;

      const rows = [
        ['받는 금액 (세전)', `${u.won(wage)} × ${days}일 = ${u.won(gross)}`],
        ['하루 과세 금액', `${u.won(wage)} − ${u.won(d.daily_deduction)} = ${u.won(taxBase)}`],
        ['하루 결정세액 (10원 미만 버림)', `${u.won(taxBase)} × ${d.tax_rate_pct}% × (1 − ${d.tax_credit_pct}%) = ${u.won(dayTax)}`],
        [lump ? `소득세 (${days}일 치 합계, 10원 미만 버림)` : `소득세 (하루 ${u.won(exempt ? 0 : dayTax)} × ${days}일)`, u.won(incomeTax)],
        ['지방소득세 (소득세의 10%)', u.won(localTax)],
        ['세금 합계', u.won(total)],
        ['세금 뺀 금액', u.won(net)],
      ];
      const notes = [];
      if (taxBase === 0) notes.push(`일당이 ${u.won(d.daily_deduction)} 이하라 소득세가 없어요.`);
      else if (exempt && lump) notes.push(`${days}일 치 세액 합계가 ${u.won(d.small_tax_floor)} 미만이라 떼지 않아요 (소액부징수).`);
      else if (exempt) notes.push(`날마다 받으면 하루 세액이 ${u.won(d.small_tax_floor)} 미만이라 떼지 않아요 (소액부징수).`);
      else if (lump && dayTax < d.small_tax_floor) notes.push(`하루 세액은 ${u.won(d.small_tax_floor)} 미만이지만, 여러 날 치를 한 번에 받으면 합계로 판단해서 떼요.`);
      notes.push('일용근로소득은 이렇게 떼는 것으로 세금이 끝나서 연말정산이나 종합소득세 신고에 넣지 않아요.');
      notes.push('고용보험료는 따로 빠지고(65세 이후 새로 고용됐다면 근로자 몫 없음), 국민연금·건강보험은 1개월 이상 일하는 등 가입 조건에 해당할 때 따로 빠져요. 하루에 두 곳 이상에서 일했다면 사업장마다 따로 계산해요.');
      return {
        values: { gross, tax_base: taxBase, day_tax: dayTax, income_tax: incomeTax, local_tax: localTax, exempt, total, net },
        rows,
        notes,
      };
    },
  });
})();
