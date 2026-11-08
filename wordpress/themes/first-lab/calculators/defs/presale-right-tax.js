/* 분양권 양도소득세 계산기: 양도가액 - 취득가액 - 필요경비 = 양도차익(장기보유특별공제 없음 — 소득세법 제95조②는 토지·건물과 조합원입주권만),
   기본공제 연 250만 원(그해 다른 부동산·부동산 권리 양도와 합쳐 한 번, 제103조①1호) 뺀 과세표준 × 70%(보유 1년 미만) 또는 60%(1년 이상, 제104조①1~3호)
   + 지방소득세 과세표준 × 7%·6%(지방세법 제103조의3). 10원 미만 버림은 국고금관리법 제47조. 요율은 data/presale-right-tax.json */
(function () {
  'use strict';

  const num = (raw, name, u, d) => {
    const n = raw === undefined || raw === null || raw === '' ? 0 : Number(raw);
    if (!(n >= 0) || !Number.isInteger(n)) throw new u.InputError(`${name}은 0 이상 1원 단위 정수로 넣어 주세요.`);
    if (n > d.max_input) throw new u.InputError(`${name}이 너무 커요. 1,000억 원 이하로 넣어 주세요.`);
    return n;
  };

  FirstLabCalc.register({
    id: 'presale-right-tax',
    title: '분양권 양도소득세 계산기',
    intro: '분양권을 판 금액·산 금액·비용과 보유기간을 넣으면 기본공제 250만 원을 뺀 과세표준과 양도소득세·지방소득세가 나와요.',
    inputs: [
      { name: 'sale', label: '판 금액 (양도가액, 원)', type: 'number', min: 0, step: 100000, help: '매수자에게 받은 돈 전체예요. 그동안 낸 계약금·중도금에 받은 프리미엄을 더한 금액이에요.' },
      { name: 'acquire', label: '산 금액 (취득가액, 원)', type: 'number', min: 0, step: 100000, help: '분양받았다면 그동안 낸 계약금·중도금, 전매로 샀다면 그때 낸 돈 전체(프리미엄 포함)예요.' },
      { name: 'expense', label: '팔 때 든 비용 (원, 없으면 비워 둬요)', type: 'number', min: 0, step: 10000, help: '중개수수료처럼 양도에 직접 든 비용이에요.' },
      { name: 'used', label: '올해 다른 부동산 양도로 이미 쓴 기본공제 (원, 없으면 비워 둬요)', type: 'number', min: 0, step: 10000, help: '같은 해에 집·땅·다른 분양권을 먼저 팔았다면 250만 원 공제를 함께 써요.' },
      { name: 'hold', label: '보유기간', type: 'select', default: 'short', options: [['short', '1년 미만 (70%)'], ['long', '1년 이상 (60%)']] },
    ],

    compute(v, d, u) {
      const sale = Number(v.sale);
      if (!(sale > 0)) throw new u.InputError('판 금액을 넣으면 결과가 나와요.');
      if (!Number.isInteger(sale)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (sale > d.max_input) throw new u.InputError('금액이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      const acquire = num(v.acquire, '산 금액', u, d);
      const expense = num(v.expense, '비용', u, d);
      const used = num(v.used, '이미 쓴 기본공제 금액', u, d);
      const long = v.hold === 'long';
      const rate = long ? d.rate_long_pct : d.rate_short_pct;
      const local = long ? d.local_long_permille : d.local_short_permille;
      const gain = sale - acquire - expense;
      const deduction = Math.max(0, Math.min(d.basic_deduction - Math.min(used, d.basic_deduction), Math.max(0, gain)));
      const base = Math.max(0, gain - deduction);
      const f = u.floor;
      // 10원 미만 버림 (국고금관리법 제47조①)
      const incomeTax = f((base * rate) / 100 / 10) * 10;
      const localTax = f((base * local) / 1000 / 10) * 10;
      const total = incomeTax + localTax;
      const values = { gain, deduction, base, income_tax: incomeTax, local_tax: localTax, total };
      const rows = [
        ['판 금액', u.won(sale)],
        ['산 금액 + 비용', u.won(acquire + expense)],
        [gain < 0 ? '양도차익 (손실)' : '양도차익', u.won(gain)],
        ['기본공제', u.won(deduction)],
        ['과세표준', u.won(base)],
        [`양도소득세 (과세표준의 ${rate}%)`, u.won(incomeTax)],
        [`지방소득세 (과세표준의 ${local / 10}%)`, u.won(localTax)],
        ['세금 합계', u.won(total)],
      ];
      const notes = [];
      if (gain <= 0) notes.push('판 금액이 산 금액과 비용보다 크지 않아 낼 세금이 없어요. 그래도 판 달 말일부터 2개월 안에 예정신고는 해요.');
      else if (base === 0) notes.push(used > 0 ? '양도차익이 남은 기본공제 안이라 세금이 0원이에요.' : '양도차익이 기본공제 250만 원 안이라 세금이 0원이에요.');
      notes.push('세금은 10원 미만을 버렸어요.');
      notes.push('같은 해에 부동산을 여러 건 팔았을 때의 합산 비교, 미등기·감면 특례는 넣지 않았어요.');
      return { values, rows, notes };
    },
  });
})();
