/* 주식 수익률 계산기: 매수가·매도가(또는 현재가)·수량으로 단순 수익률, 수수료·매도 세금을 뺀 실제 수익률과 순수익,
   보유 기간을 넣으면 연환산 수익률, 목표 수익률을 넣으면 그 수익률이 되는 매도가.
   수익률은 표준 수학 공식, 매도 세율(증권거래세·농어촌특별세)만 data/stock-return.json */
(function () {
  'use strict';

  const pct = (n) => `${(Math.round(n * 100) / 100).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}%`;
  const r4 = (n) => Math.round(n * 10000) / 10000;

  FirstLabCalc.register({
    id: 'stock-return',
    title: '주식 수익률 계산기',
    intro: '산 가격·판 가격(또는 지금 가격)·수량을 넣으면 수수료와 매도 세금까지 뺀 실제 수익률과 순수익을 계산해요.',
    inputs: [
      { name: 'buy_price', label: '산 가격 (원, 평단가)', type: 'number', min: 0, step: 0.01 },
      { name: 'qty', label: '수량 (주)', type: 'number', min: 0, step: 1 },
      { name: 'sell_price', label: '판 가격 또는 지금 가격 (원)', type: 'number', min: 0, step: 1 },
      {
        name: 'market', label: '시장', type: 'select', default: 'kospi',
        options: [
          ['kospi', '유가증권시장(코스피) 0.20%'],
          ['kosdaq', '코스닥 0.20%'],
          ['konex', '코넥스 0.10%'],
          ['none', '세금 빼고 계산'],
        ],
      },
      { name: 'fee', label: '매매 수수료율 (%, 한 번 거래할 때)', type: 'number', min: 0, step: 0.001, default: 0, help: '내 증권사 수수료율을 넣어요. 살 때와 팔 때 같은 율로 계산해요.' },
      { name: 'dividend', label: '받은 배당금 합계 (원, 세금 뗀 뒤, 선택)', type: 'number', min: 0, step: 1, default: 0 },
      { name: 'days', label: '보유 기간 (일, 연환산 수익률용, 선택)', type: 'number', min: 0, step: 1, default: 0 },
      { name: 'target', label: '목표 수익률 (%, 목표 매도가용, 선택)', type: 'number', step: 0.01, default: 0 },
    ],

    compute(v, d, u) {
      const B = Number(v.buy_price);
      const q = Number(v.qty);
      const S = Number(v.sell_price);
      if (!(B > 0) || !(q > 0) || !(S > 0)) throw new u.InputError('산 가격·수량·판 가격(또는 지금 가격)을 넣으면 결과가 나와요.');
      if (!Number.isInteger(q)) throw new u.InputError('수량은 1주 이상 정수로 넣어 주세요.');
      const fee = (Number(v.fee) || 0) / 100;
      const tax = d.sell_tax[v.market] ?? 0;
      if (fee < 0 || fee + tax >= 1) throw new u.InputError('수수료율을 다시 확인해 주세요.');
      const div = Number(v.dividend) || 0;
      if (div < 0) throw new u.InputError('배당금은 0 이상으로 넣어 주세요.');
      const days = Number(v.days) || 0;
      if (days < 0 || !Number.isInteger(days)) throw new u.InputError('보유 기간은 0 이상 정수(일)로 넣어 주세요.');

      const buyAmt = B * q;
      const buyFee = buyAmt * fee;
      const cost = buyAmt + buyFee;
      const sellAmt = S * q;
      const sellFee = sellAmt * fee;
      const sellTax = sellAmt * tax;
      const net = sellAmt - sellFee - sellTax + div - cost;
      const simple = (S - B) / B;
      const real = net / cost;

      const rows = [
        ['매수 금액', u.won(buyAmt)],
        ['매도(평가) 금액', u.won(sellAmt)],
        ['수수료 (살 때 + 팔 때)', u.won(buyFee + sellFee)],
        ['매도 세금', u.won(sellTax)],
      ];
      if (div > 0) rows.push(['받은 배당금', u.won(div)]);
      rows.push(['순수익', u.won(net)]);
      rows.push(['단순 수익률 (가격만)', pct(simple * 100)]);
      rows.push(['실제 수익률 (비용 뺀 뒤)', pct(real * 100)]);

      const notes = [
        '단순 수익률 = (판 가격 − 산 가격) ÷ 산 가격이에요. 수수료·매도 세금은 빼지 않은 가격 변동만의 값이에요.',
        '실제 수익률 = 순수익 ÷ (매수 금액 + 매수 수수료)예요. 순수익은 매도 금액에서 수수료·매도 세금을 빼고 배당금을 더한 뒤 들인 돈을 뺀 값이에요.',
        '매도 세금은 이익이 났는지와 상관없이 판 금액 전체에 붙어요.',
      ];
      if (fee === 0) notes.push('수수료율 0으로 계산했어요. 실제로는 증권사 수수료만큼 수익률이 조금 낮아져요.');

      const values = {
        buy_amt: Math.round(buyAmt), sell_amt: Math.round(sellAmt),
        fees: Math.round(buyFee + sellFee), sell_tax: Math.round(sellTax),
        net: Math.round(net), simple_pct: r4(simple * 100), real_pct: r4(real * 100),
      };

      if (S < B) {
        const need = B / S - 1;
        rows.push(['산 가격까지 필요한 상승률', pct(need * 100)]);
        values.recover_pct = r4(need * 100);
        notes.push('떨어진 만큼 다시 오르려면 더 큰 비율이 필요해요 (예: 50% 떨어지면 100% 올라야 원래 가격).');
      }

      if (days > 0) {
        if (1 + real <= 0) throw new u.InputError('손실이 들인 돈보다 커서 연환산 수익률을 낼 수 없어요.');
        const annual = Math.pow(1 + real, 365 / days) - 1;
        if (!Number.isFinite(annual)) throw new u.InputError('보유 기간이 너무 짧아 연환산 수익률을 낼 수 없어요. 보유 기간을 비우고 계산해 주세요.');
        rows.push([`연환산 수익률 (${days.toLocaleString('ko-KR')}일 보유)`, pct(annual * 100)]);
        values.annual_pct = r4(annual * 100);
        notes.push('연환산 수익률 = (1 + 실제 수익률)^(365 ÷ 보유 일수) − 1 이에요. 같은 속도로 1년을 이어 갔다고 가정한 값이라, 보유 기간이 짧으면 크게 부풀려 보여요.');
      }

      const T = Number(v.target) || 0;
      if (T !== 0) {
        if (T <= -100) throw new u.InputError('목표 수익률은 −100%보다 커야 해요.');
        // 목표: (S×q×(1 − 세율 − 수수료율) + 배당 − 들인 돈) ÷ 들인 돈 = T  →  S = (들인 돈 × (1+T) − 배당) ÷ (q × (1 − 세율 − 수수료율))
        const raw = (cost * (1 + T / 100) - div) / (q * (1 - tax - fee));
        const targetPrice = Math.ceil(raw - 1e-9);
        rows.push([`목표 수익률 ${pct(T)} 매도가`, `${Math.max(targetPrice, 0).toLocaleString('ko-KR')}원 이상`]);
        values.target_price = Math.max(targetPrice, 0);
        notes.push('목표 매도가는 수수료·매도 세금을 낸 뒤 그 수익률이 되는 가장 낮은 1원 단위 가격이에요. 호가 단위는 반영하지 않았어요.');
      }

      return { values, rows, notes };
    },
  });
})();
