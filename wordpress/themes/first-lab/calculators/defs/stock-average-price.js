/* 주식 평단가 계산기: 추가 매수 후 평단가(이동평균), 목표 평단가까지 필요한 수량, 본전 매도가(매도 세금·수수료 포함).
   평단가는 표준 수학 공식, 매도 세율(증권거래세·농어촌특별세)만 data/stock-average-price.json */
(function () {
  'use strict';

  const r2 = (n) => Math.round(n * 100) / 100;
  const price = (n) => `${r2(n).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}원`;
  const pct = (n) => `${(Math.round(n * 100) / 100).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}%`;

  FirstLabCalc.register({
    id: 'stock-average-price',
    title: '주식 평단가 계산기',
    intro: '지금 평단가·수량에 추가로 살 가격을 넣으면 새 평단가, 목표 평단가까지 더 사야 할 수량, 본전이 되는 매도 가격을 계산해요.',
    inputs: [
      {
        name: 'mode', label: '무엇을 계산할까요', type: 'select', default: 'add',
        options: [
          ['add', '추가로 사면 평단가가 얼마가 되나'],
          ['target', '목표 평단가까지 몇 주를 더 사야 하나'],
        ],
      },
      { name: 'held_price', label: '지금 평단가 (원)', type: 'number', min: 0, step: 1 },
      { name: 'held_qty', label: '지금 보유 수량 (주)', type: 'number', min: 0, step: 1 },
      { name: 'buy_price', label: '추가로 살 가격 (원)', type: 'number', min: 0, step: 1 },
      { name: 'buy_qty', label: '추가로 살 수량 (주, 평단가 계산용)', type: 'number', min: 0, step: 1 },
      { name: 'target_price', label: '목표 평단가 (원, 수량 계산용)', type: 'number', min: 0, step: 1 },
      {
        name: 'market', label: '팔 시장 (본전 매도가 계산용)', type: 'select', default: 'kospi',
        options: [
          ['kospi', '유가증권시장(코스피) 0.20%'],
          ['kosdaq', '코스닥 0.20%'],
          ['konex', '코넥스 0.10%'],
          ['none', '세금 빼고 계산'],
        ],
      },
      { name: 'fee', label: '매매 수수료율 (%, 한 번 거래할 때)', type: 'number', min: 0, step: 0.001, default: 0, help: '내 증권사 수수료율을 넣어요. 0이면 수수료는 빼고 계산해요.' },
    ],

    compute(v, d, u) {
      const A = Number(v.held_price);
      const Q = Number(v.held_qty);
      const P = Number(v.buy_price);
      if (!(A > 0) || !(Q > 0) || !(P > 0)) throw new u.InputError('지금 평단가·보유 수량·추가로 살 가격을 넣으면 결과가 나와요.');
      const fee = (Number(v.fee) || 0) / 100;
      const tax = d.sell_tax[v.market] ?? 0;
      if (fee < 0 || fee + tax >= 1) throw new u.InputError('수수료율을 다시 확인해 주세요.');

      let n;
      const rows = [];
      const notes = [];
      if (v.mode === 'target') {
        const T = Number(v.target_price);
        if (!(T > 0)) throw new u.InputError('목표 평단가를 넣어 주세요.');
        if (!((T - A) * (T - P) < 0)) {
          throw new u.InputError('목표 평단가는 지금 평단가와 추가로 살 가격 사이에 있어야 해요.');
        }
        const exact = (Q * (A - T)) / (T - P);
        // 부동소수점 오차로 100.0000001 이 101 이 되지 않게 아주 작은 값은 버림
        n = Math.ceil(exact - 1e-9);
        rows.push(['필요한 추가 수량', `${n.toLocaleString('ko-KR')}주 (계산값 ${r2(exact).toLocaleString('ko-KR')}주를 올림)`]);
        rows.push(['추가 매수 금액', u.won(P * n)]);
      } else {
        n = Number(v.buy_qty);
        if (!(n > 0) || !Number.isInteger(n)) throw new u.InputError('추가로 살 수량을 1주 이상 정수로 넣어 주세요.');
      }

      const qty = Q + n;
      const cost = A * Q + P * n;
      const avg = cost / qty;
      // 본전: 매도가 × (1 − 매도 세율 − 수수료율) = 평단가 × (1 + 수수료율)  (평단가는 수수료를 뺀 매수 단가로 보고 사는 쪽 수수료를 더함)
      const breakeven = Math.ceil((avg * (1 + fee)) / (1 - tax - fee) - 1e-9);

      rows.push(['새 평단가', price(avg)]);
      rows.push(['총 보유 수량', `${qty.toLocaleString('ko-KR')}주`]);
      rows.push(['총 매수 금액', u.won(cost)]);
      rows.push(['평단가 변화', `${price(avg - A)} (${pct(((avg - A) / A) * 100)})`]);
      rows.push(['본전 매도가 (세금·수수료 포함)', `${breakeven.toLocaleString('ko-KR')}원 이상`]);

      notes.push('평단가 = 총 매수 금액 ÷ 총 보유 수량이에요. 살 때마다 다시 평균을 내요.');
      notes.push('본전 매도가는 판 금액에서 매도 세금과 수수료를 빼도 평단가(살 때 수수료 포함)가 남는 가격이에요. 호가 단위는 반영하지 않았어요.');
      if (fee === 0) notes.push('수수료율 0으로 계산했어요. 실제로는 증권사 수수료만큼 본전 가격이 조금 올라가요.');

      return { values: { avg: r2(avg), qty, cost: Math.round(cost), n, breakeven }, rows, notes };
    },
  });
})();
