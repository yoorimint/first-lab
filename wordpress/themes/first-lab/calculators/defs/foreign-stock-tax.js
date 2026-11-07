/* 해외주식 양도소득세 계산기: 한 해 동안 판 해외주식의 양도가액 합계 - 취득가액 합계 - 필요경비 = 양도소득금액,
   기본공제 연 250만 원(국내 과세대상 주식과 합쳐 한 번) 뺀 과세표준 × 20%(중소기업 주식 10%) + 지방소득세 과세표준 × 2%(1%).
   10원 미만 버림은 국고금관리법 제47조·지방회계법 제55조. 요율은 data/foreign-stock-tax.json (소득세법 제92조·제102조·제103조·제104조①12호, 지방세법 제103조의3) */
(function () {
  'use strict';

  const num = (raw, name, u, d) => {
    const n = raw === undefined || raw === null || raw === '' ? 0 : Number(raw);
    if (!(n >= 0) || !Number.isInteger(n)) throw new u.InputError(`${name}은 0 이상 1원 단위 정수로 넣어 주세요.`);
    if (n > d.max_input) throw new u.InputError(`${name}이 너무 커요. 1,000억 원 이하로 넣어 주세요.`);
    return n;
  };

  FirstLabCalc.register({
    id: 'foreign-stock-tax',
    title: '해외주식 양도소득세 계산기',
    intro: '한 해 동안 판 해외주식의 판 금액·산 금액·수수료 합계를 원화로 넣으면 기본공제 250만 원을 뺀 과세표준과 양도소득세·지방소득세가 나와요.',
    inputs: [
      { name: 'sale', label: '판 금액 합계 (양도가액, 원화)', type: 'number', min: 0, step: 1, help: '그해 판 해외주식 전체를 원화로 바꾼 금액이에요. 손실 난 종목도 같이 넣어요.' },
      { name: 'acquire', label: '그 주식을 산 금액 합계 (취득가액, 원화)', type: 'number', min: 0, step: 1, help: '판 주식의 산 가격에 살 때 낸 수수료까지 더해 넣어요. 아직 안 판 주식은 빼요.' },
      { name: 'expense', label: '수수료 등 팔 때 든 비용 (원, 없으면 비워 둬요)', type: 'number', min: 0, step: 1 },
      { name: 'used', label: '국내 과세대상 주식에서 이미 쓴 기본공제 (원, 없으면 비워 둬요)', type: 'number', min: 0, step: 1, help: '대주주·비상장 주식처럼 양도세를 내는 국내 주식도 판 경우에만 넣어요. 250만 원 공제를 함께 써요.' },
      { name: 'kind', label: '주식 종류', type: 'select', default: 'general', options: [['general', '일반 해외주식 (20%)'], ['sme', '중소기업 주식 (10%)']] },
    ],

    compute(v, d, u) {
      const sale = Number(v.sale);
      if (!(sale > 0)) throw new u.InputError('판 금액 합계를 넣으면 결과가 나와요.');
      if (!Number.isInteger(sale)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (sale > d.max_input) throw new u.InputError('금액이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      const acquire = num(v.acquire, '산 금액', u, d);
      const expense = num(v.expense, '비용', u, d);
      const used = num(v.used, '이미 쓴 기본공제', u, d);
      const rate = v.kind === 'sme' ? d.rate_sme_pct : d.rate_pct;
      const local = v.kind === 'sme' ? d.local_sme_permille : d.local_permille;
      const income = sale - acquire - expense;
      const deduction = Math.max(0, Math.min(d.basic_deduction - Math.min(used, d.basic_deduction), Math.max(0, income)));
      const base = Math.max(0, income - deduction);
      const f = u.floor;
      // 10원 미만 버림 (국고금관리법 제47조①)
      const incomeTax = f((base * rate) / 100 / 10) * 10;
      const localTax = f((base * local) / 1000 / 10) * 10;
      const total = incomeTax + localTax;
      const values = { income, deduction, base, income_tax: incomeTax, local_tax: localTax, total };
      const rows = [
        ['판 금액 합계', u.won(sale)],
        ['산 금액 + 비용', u.won(acquire + expense)],
        [income < 0 ? '양도소득금액 (손실)' : '양도소득금액 (이익 - 손실 합산)', u.won(income)],
        ['기본공제', u.won(deduction)],
        ['과세표준', u.won(base)],
        [`양도소득세 (과세표준의 ${rate}%)`, u.won(incomeTax)],
        [`지방소득세 (과세표준의 ${local / 10}%)`, u.won(localTax)],
        ['세금 합계', u.won(total)],
      ];
      const notes = [];
      if (income <= 0) notes.push('이익보다 손실이 커서 낼 세금이 없어요. 손실은 같은 해에 판 다른 주식의 이익과만 합쳐요.');
      else if (base === 0) notes.push('양도소득금액이 기본공제 250만 원 안이라 세금이 0원이에요.');
      notes.push('세금은 10원 미만을 버렸어요.');
      notes.push('2026년 국내시장복귀계좌로 판 주식의 소득공제(조세특례제한법 제91조의26)와 환헤지 상품 공제는 넣지 않았어요.');
      return { values, rows, notes };
    },
  });
})();
