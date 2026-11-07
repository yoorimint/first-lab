/* 연금저축 세액공제 계산기: 연금저축 납입액(연 600만 원까지) + 퇴직연금(IRP·DC 본인 납입) = 합산 900만 원까지 공제 대상,
   총급여 5,500만 원(근로소득만) 또는 종합소득금액 4,500만 원 이하면 15%, 넘으면 12% + 지방소득세분(소득세 공제액의 10%).
   요율·한도는 data/pension-tax-credit.json (소득세법 제59조의3·제61조②) */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'pension-tax-credit',
    title: '연금저축 세액공제 계산기',
    intro: '한 해 동안 연금저축·IRP에 넣은 금액과 소득을 넣으면 공제 대상 금액과 돌려받는 세금(지방소득세 포함)이 나와요.',
    inputs: [
      {
        name: 'income_type', label: '소득 종류', type: 'select', default: 'wage',
        options: [
          ['wage', '근로소득만 있어요 (직장인)'],
          ['other', '사업·연금 등 다른 종합소득도 있어요'],
        ],
        help: '근로소득만 있으면 총급여 5,500만 원, 다른 종합소득이 있으면 종합소득금액 4,500만 원이 공제율 기준이에요.',
      },
      { name: 'income', label: '총급여 또는 종합소득금액 (원, 1년)', type: 'number', min: 0, step: 1, help: '총급여는 연봉에서 비과세 소득(식대 등)을 뺀 금액이에요. 원천징수영수증의 총급여 칸을 보면 돼요.' },
      { name: 'savings', label: '연금저축 납입액 (원, 1년)', type: 'number', min: 0, step: 1 },
      { name: 'retire', label: 'IRP·DC 본인 납입액 (원, 1년, 없으면 비워 둬요)', type: 'number', min: 0, step: 1, help: '회사가 넣어 주는 DC 부담금과 퇴직금이 IRP로 들어온 돈은 빼고 넣어요.' },
    ],

    compute(v, d, u) {
      const num = (x) => (x === undefined || x === null || x === '' ? 0 : Number(x));
      const income = num(v.income);
      const savings = num(v.savings);
      const retire = num(v.retire);
      if (v.income === undefined || v.income === null || v.income === '') throw new u.InputError('총급여 또는 종합소득금액을 넣으면 결과가 나와요.');
      for (const [x, label] of [[income, '소득'], [savings, '연금저축 납입액'], [retire, 'IRP·DC 납입액']]) {
        if (!(x >= 0) || !Number.isInteger(x)) throw new u.InputError(`${label}은 0 이상 1원 단위 정수로 넣어 주세요.`);
        if (x > d.max_input) throw new u.InputError(`${label}이 너무 커요.`);
      }
      if (savings + retire === 0) throw new u.InputError('연금저축이나 IRP에 넣은 금액을 넣어 주세요.');
      const limit = v.income_type === 'other' ? d.income_limit_other : d.income_limit_wage;
      if (v.income_type !== 'wage' && v.income_type !== 'other') throw new u.InputError('소득 종류를 골라 주세요.');
      const high = income <= limit;
      const ratePct = high ? d.rate_high_pct : d.rate_low_pct;
      const savingsUsed = Math.min(savings, d.savings_cap);
      const eligible = Math.min(savingsUsed + retire, d.total_cap);
      const f = u.floor;
      const incomeTaxCredit = f((eligible * ratePct) / 100);
      const localCredit = f((incomeTaxCredit * d.local_pct) / 100);
      const total = incomeTaxCredit + localCredit;
      const over = savings + retire - eligible;
      const values = { rate_pct: ratePct, eligible, income_tax_credit: incomeTaxCredit, local_credit: localCredit, total, over };
      const basis = v.income_type === 'other' ? '종합소득금액' : '총급여';
      const rows = [
        [`공제율 (${basis} ${u.won(limit)} ${high ? '이하' : '초과'})`, `${ratePct}% (지방소득세까지 ${(ratePct * (100 + d.local_pct)) / 100}%)`],
        ['연금저축 인정액 (연 600만 원까지)', u.won(savingsUsed)],
        ['공제 대상 금액 (IRP 합산 900만 원까지)', u.won(eligible)],
        [`소득세 공제액 (공제 대상 × ${ratePct}%)`, u.won(incomeTaxCredit)],
        [`지방소득세 공제분 (소득세 공제액의 ${d.local_pct}%)`, u.won(localCredit)],
        ['돌려받는(덜 내는) 세금 합계', u.won(total)],
      ];
      const notes = [];
      if (savings > d.savings_cap) notes.push('연금저축은 한 해 600만 원까지만 공제 대상이에요. 넘은 금액은 IRP에 넣어야 900만 원까지 채울 수 있어요.');
      if (over > 0) notes.push(`공제 한도를 넘은 ${u.won(over)}은 올해 공제받지 못해요. 다음 해 이후 납입액으로 돌리는 신청(전환 특례)을 할 수 있어요.`);
      if (eligible < d.total_cap) notes.push(`공제 한도까지 ${u.won(d.total_cap - eligible)}이 남아 있어요${savingsUsed < d.savings_cap ? ' (연금저축만으로는 600만 원까지)' : ' (남은 한도는 IRP로만 채울 수 있어요)'}.`);
      notes.push('낼 세금(종합소득산출세액)보다 많이 공제받지는 못해요. 세금이 적으면 실제로 돌려받는 금액이 이보다 작아요.');
      notes.push('원 미만은 버렸어요. ISA 만기 자금을 옮긴 금액의 추가 한도는 이 계산에 넣지 않았어요.');
      return { values, rows, notes };
    },
  });
})();
