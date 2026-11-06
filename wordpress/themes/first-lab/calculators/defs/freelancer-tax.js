/* 프리랜서 3.3% 계산기: 원천징수대상 사업소득 소득세 3% + 개인지방소득세(소득세의 10%), 10원 미만 버림, 소액부징수(계속·반복 인적용역은 예외).
   세전 지급액 → 실수령액, 받고 싶은 실수령액 → 세전 금액(역산). 요율은 data/freelancer-tax.json */
(function () {
  'use strict';

  const pct = (r) => `${Math.round(r * 1000) / 10}%`;

  /** 세전 지급액 하나의 원천징수 (소득세법 제129조①3호·제86조1호, 지방세법 제103조의13, 국고금관리법 제47조①) */
  function withhold(gross, repeat, d) {
    const f = FirstLabCalc.util.floor;
    let incomeTax = f((gross * Math.round(d.rate * 1000)) / 1000 / 10) * 10;
    let smallExempt = false;
    if (!repeat && incomeTax < d.small_exempt_below) {
      incomeTax = 0;
      smallExempt = true;
    }
    const localTax = f((incomeTax * Math.round(d.local_rate * 100)) / 100 / 10) * 10;
    const total = incomeTax + localTax;
    return { gross, income_tax: incomeTax, local_tax: localTax, total, net: gross - total, small_exempt: smallExempt };
  }

  FirstLabCalc.register({
    id: 'freelancer-tax',
    title: '프리랜서 3.3% 계산기',
    intro: '세전 금액을 넣으면 떼는 소득세 3%·지방소득세 0.3%와 실수령액이 나와요. 받고 싶은 실수령액으로 세전 금액도 거꾸로 구해요.',
    inputs: [
      {
        name: 'mode', label: '무엇을 계산할까요', type: 'select', default: 'gross',
        options: [
          ['gross', '세전 금액 → 실수령액'],
          ['net', '받고 싶은 실수령액 → 세전 금액 (역산)'],
        ],
      },
      { name: 'amount', label: '금액 (원)', type: 'number', min: 0, step: 1, help: '한 번에 지급받는 금액(1건)을 넣어요.' },
      {
        name: 'kind', label: '소득 종류', type: 'select', default: 'repeat',
        options: [
          ['repeat', '계속·반복하는 인적용역 (일반 프리랜서)'],
          ['other', '그 밖의 원천징수대상 사업소득'],
        ],
        help: '고용관계 없이 같은 일을 계속 해 주고 받는 돈이면 위쪽이에요.',
      },
    ],

    compute(v, d, u) {
      const amount = Number(v.amount);
      if (!(amount > 0)) throw new u.InputError('금액을 넣으면 결과가 나와요.');
      if (!Number.isInteger(amount)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (amount > d.max_input) throw new u.InputError('금액이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      if (v.kind !== 'repeat' && v.kind !== 'other') throw new u.InputError('소득 종류를 골라 주세요.');
      const repeat = v.kind === 'repeat';
      const notes = [];
      let r;
      if (v.mode === 'gross') {
        r = withhold(amount, repeat, d);
      } else if (v.mode === 'net') {
        // 실수령액은 10원 단위 버림 때문에 금액이 1원 늘 때 줄기도 해서, 목표 이상이 되는 가장 작은 세전 금액을 찾는다
        // 소액 부징수로 세금이 0원이면 실수령액 = 세전 금액 (11/6 검수: 그 밖의 사업소득 3,000~33,333원 역산이 크게 나옴)
        r = withhold(amount, repeat, d);
        let g = amount;
        if (r.net < amount) {
          g = Math.max(amount, FirstLabCalc.util.floor((amount * 1000) / 967) - 100);
          r = withhold(g, repeat, d);
        }
        while (r.net < amount) {
          g += 1;
          r = withhold(g, repeat, d);
        }
        if (r.net !== amount) notes.push(`세금을 10원 단위로 버려서 실수령액이 딱 ${u.won(amount)}이 되는 금액이 없어요. 그 이상이 되는 가장 작은 금액이에요.`);
        else notes.push('세금 끝자리를 버리는 방식 때문에 같은 실수령액이 되는 세전 금액이 여러 개일 수 있어요. 그중 가장 작은 금액이에요.');
      } else {
        throw new u.InputError('계산할 항목을 골라 주세요.');
      }
      const rows = [
        ['세전 금액 (지급액)', u.won(r.gross)],
        [`소득세 (${pct(d.rate)})`, u.won(r.income_tax)],
        [`지방소득세 (소득세의 ${pct(d.local_rate)})`, u.won(r.local_tax)],
        ['떼는 세금 합계', u.won(r.total)],
        ['실수령액', u.won(r.net)],
      ];
      notes.push('세금은 10원 미만을 버렸어요.');
      if (r.small_exempt) notes.push(`소득세가 ${u.won(d.small_exempt_below)} 미만이라 떼지 않아요 (소액 부징수).`);
      if (repeat && r.income_tax > 0 && r.income_tax < d.small_exempt_below) notes.push(`계속·반복하는 인적용역 소득은 소득세가 ${u.won(d.small_exempt_below)} 미만이어도 떼요.`);
      notes.push('떼인 세금은 다음 해 5월 종합소득세 신고 때 낼 세금에서 빼고, 더 냈으면 돌려받아요.');
      return { values: r, rows, notes };
    },
  });
})();
