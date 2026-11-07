/* 예금·적금 이자 계산기 (이율 계산기): 원금 또는 월 납입액·연 이율·기간·단리/월복리 → 세전 이자, 이자소득세(14%)·지방소득세(소득세의 10%), 세후 이자, 만기 수령액.
 * 이자 계산은 표준 수학 공식, 세율은 data/savings-interest.json (소득세법 제129조①1호라목, 지방세법 제103조의13①, 10원 미만 버림 국고금관리법 제47조①) */
(function () {
  'use strict';

  /** 세전 이자 (원 미만 버림, 정수 분수로 정확히 계산 — 소수 계산은 적금 월복리에서 1원이 빠졌음, 11/7 독립 검수).
   *  A = 금액(원, 정수), R = 연 이율 × 100 (정수, 3.39% → 339), 월 이율 i = R / 120000, n = 개월 수 */
  function grossInterest(product, compound, A, R, n) {
    const a = BigInt(A), r = BigInt(R), D = 120000n, N = BigInt(n);
    if (compound === 'monthly') {
      // 예금: A × ((1+i)^n − 1) / 적금: 회차별 A × ((1+i)^k − 1), k = 1..n 의 합 (첫 회차는 n개월, 마지막 회차는 1개월)
      const Dn = D ** N;
      let num = 0n;
      const ks = product === 'deposit' ? [n] : Array.from({ length: n }, (_, j) => j + 1);
      for (const k of ks) { const K = BigInt(k); num += a * ((D + r) ** K - D ** K) * D ** (N - K); }
      return Number(num / Dn);
    }
    // 단리 — 예금: A × i × n / 적금: A × i × (n + (n−1) + … + 1) = A × i × n(n+1)/2
    if (product === 'deposit') return Number((a * r * N) / D);
    return Number((a * r * N * (N + 1n)) / (2n * D));
  }

  FirstLabCalc.register({
    id: 'savings-interest',
    title: '예금·적금 이자 계산기 (이율 계산기)',
    intro: '예금은 맡길 돈, 적금은 매달 넣을 돈과 연 이율·기간을 넣으면 세전 이자, 떼는 세금, 세후 이자와 만기에 받는 돈이 나와요.',
    inputs: [
      { name: 'product', label: '상품', type: 'select', default: 'deposit', options: [['deposit', '정기예금 (한 번에 맡김)'], ['installment', '정기적금 (매달 같은 금액)']] },
      { name: 'amount', label: '금액 (원)', type: 'number', min: 0, step: 10000, help: '예금은 맡기는 돈 전체, 적금은 한 달에 넣는 돈이에요.' },
      { name: 'rate', label: '연 이율 (%)', type: 'number', min: 0, step: 0.01, help: '상품 설명서의 연 금리(세전). 우대금리를 받으면 더한 값을 넣어요.' },
      { name: 'months', label: '기간 (개월)', type: 'number', min: 1, step: 1 },
      { name: 'compound', label: '이자 방식', type: 'select', default: 'simple', options: [['simple', '단리 (대부분의 예금·적금)'], ['monthly', '월복리']] },
      { name: 'tax', label: '세금', type: 'select', default: 'normal', options: [['normal', '일반과세 15.4%'], ['none', '비과세 0%']] },
    ],

    compute(v, d, u) {
      const f = u.floor;
      const A = Number(v.amount);
      const ratePct = Number(v.rate);
      const n = Number(v.months);
      if (!(A > 0)) throw new u.InputError('금액을 넣으면 결과가 나와요.');
      if (A > d.max_amount) throw new u.InputError('금액이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      if (v.rate === '' || v.rate === undefined || !(ratePct >= 0) || ratePct > d.max_rate) throw new u.InputError(`연 이율을 0~${d.max_rate}% 사이로 넣어 주세요.`);
      if (!Number.isInteger(n) || n < 1 || n > d.max_months) throw new u.InputError(`기간은 1~${d.max_months}개월로 넣어 주세요.`);
      if (!Number.isInteger(A)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      const R = Math.round(ratePct * 100);
      if (Math.abs(ratePct * 100 - R) > 1e-6) throw new u.InputError('연 이율은 소수 둘째 자리까지 넣어 주세요 (예: 3.39).');
      const product = v.product === 'installment' ? 'installment' : 'deposit';
      const compound = v.compound === 'monthly' ? 'monthly' : 'simple';

      const principal = product === 'deposit' ? A : A * n;
      const interest = grossInterest(product, compound, A, R, n);
      let incomeTax = 0;
      let localTax = 0;
      if (v.tax !== 'none') {
        // 이자소득세 14%, 지방소득세는 그 10% — 각각 10원 미만 버림
        incomeTax = f((interest * Math.round(d.income_tax_rate * 100)) / 100 / 10) * 10;
        localTax = f((incomeTax * Math.round(d.local_rate * 100)) / 100 / 10) * 10;
      }
      const tax = incomeTax + localTax;
      const net = interest - tax;
      const rows = [
        [product === 'deposit' ? '맡긴 원금' : `낸 원금 (${n}개월)`, u.won(principal)],
        ['세전 이자', u.won(interest)],
      ];
      if (v.tax === 'none') rows.push(['세금', '0원 (비과세)']);
      else rows.push(['세금', `${u.won(tax)} (이자소득세 ${u.won(incomeTax)} + 지방소득세 ${u.won(localTax)})`]);
      rows.push(['세후 이자', u.won(net)], ['만기 수령액 (세후)', u.won(principal + net)]);

      const notes = [
        '금리가 기간 내내 같고, 적금은 매달 같은 날 넣는다고 보고 계산했어요. 실제 이자는 입금일·일수 계산·원 단위 처리에 따라 몇 원~몇백 원 다를 수 있어요.',
        '중도에 해지하면 약정 이율이 아니라 더 낮은 중도해지 이율이 적용돼요.',
      ];
      if (product === 'installment') notes.push('적금은 나중에 넣은 돈일수록 이자가 붙는 기간이 짧아서, 같은 이율의 예금(전액을 처음부터 맡김)보다 이자가 적어요.');
      if (compound === 'monthly') notes.push('월복리는 매달 생긴 이자를 원금에 더해 다음 달 이자를 계산한 값이에요. 상품마다 복리 주기가 다를 수 있어요.');
      if (v.tax !== 'none') notes.push('이자·배당소득이 한 해 2,000만 원을 넘으면 넘는 부분은 다음 해 5월 종합소득세 신고 때 다른 소득과 합쳐 계산해요.');
      if (v.tax === 'none') notes.push('농협·수협·신협·새마을금고 같은 조합 예탁금은 비과세로 안내돼도 농어촌특별세가 붙거나, 2026년부터 가입한 예탁금은 조합원·소득 기준에 따라 5%(2027년 가입분부터 9%)를 뗄 수 있어요(조세특례제한법 제89조의3). 가입한 곳에서 적용 세율을 확인하세요.');
      return { values: { principal, interest, income_tax: incomeTax, local_tax: localTax, tax, net, maturity: principal + net }, rows, notes };
    },
  });
})();
