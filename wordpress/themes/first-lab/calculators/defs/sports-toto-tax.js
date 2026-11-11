/* 스포츠토토 세금 계산기: 체육진흥투표권 환급금 과세최저한(소득세법 제84조 제1호) 판정 +
   (환급금 - 적중 투표권 구매금액) × 20%(3억 원 초과분 30%) + 지방소득세 10%. 요율은 data/sports-toto-tax.json */
(function () {
  'use strict';

  const pct = (r) => `${Math.round(r * 1000) / 10}%`;

  FirstLabCalc.register({
    id: 'sports-toto-tax',
    title: '스포츠토토 세금 계산기',
    intro: '적중한 투표를 산 금액과 적중금을 넣으면 세금이 붙는지, 떼는 세금과 받는 돈이 얼마인지 나와요.',
    inputs: [
      { name: 'stake', label: '적중한 투표권을 산 금액 (원)', type: 'number', min: 0, step: 1, help: '맞힌 투표 하나에 건 금액이에요. 예: 1만 원' },
      { name: 'payout', label: '적중금 (원, 세금 떼기 전)', type: 'number', min: 0, step: 1, help: '산 금액 × 적중 배당률이에요. 예: 1만 원 × 150배 = 150만 원' },
    ],

    compute(v, d, u) {
      const stake = Number(v.stake);
      const payout = Number(v.payout);
      if (!(stake > 0)) throw new u.InputError('적중한 투표권을 산 금액을 넣으면 결과가 나와요.');
      if (!(payout > 0)) throw new u.InputError('적중금을 넣으면 결과가 나와요.');
      if (!Number.isInteger(stake) || !Number.isInteger(payout)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (payout > d.max_input || stake > d.max_input) throw new u.InputError('금액이 너무 커요. 1조 원 이하로 넣어 주세요.');
      if (payout < stake) throw new u.InputError('적중금이 산 금액보다 적어요. 두 칸을 다시 확인해 주세요.');
      const f = u.floor;
      const notes = [];
      const ratio = payout / stake;
      const smallTicket = stake <= d.ticket_max;
      // 제84조 제1호: 권면 합계 10만 원 이하 + (가) 환급금 10만 원 이하 또는 (나) 100배 이하이면서 200만 원 이하
      const exempt = smallTicket && (payout <= d.small_payout_max || (payout <= stake * d.ratio_max && payout <= d.exempt_max));
      let income = 0;
      let incomeTax = 0;
      let localTax = 0;
      if (!exempt) {
        income = payout - stake; // 필요경비 = 적중된 투표권의 단위투표금액 (소득세법 제37조 제1항 제1호)
        const low = Math.min(income, d.high_threshold);
        const high = Math.max(income - d.high_threshold, 0);
        const raw = (low * Math.round(d.rate_low * 100) + high * Math.round(d.rate_high * 100)) / 100;
        incomeTax = f(raw / 10) * 10;
        localTax = f((incomeTax * Math.round(d.local_rate * 100)) / 100 / 10) * 10;
        if (incomeTax < d.min_collect) { // 소득세법 제86조 제1호: 원천징수세액 1천원 미만은 징수하지 않음
          incomeTax = 0;
          localTax = 0;
          notes.push('원천징수할 소득세가 1,000원 미만이라 떼지 않아요.');
        }
      }
      const total = incomeTax + localTax;
      const net = payout - total;
      const values = { exempt, income, income_tax: incomeTax, local_tax: localTax, total, net };
      const rows = [
        ['적중금 (세전)', u.won(payout)],
        ['적중 배당률 (적중금 ÷ 산 금액)', payout > stake * d.ratio_max && ratio < d.ratio_max + 0.01 ? `${d.ratio_max}배 초과` : `${f(ratio * 100) / 100}배`],
      ];
      if (exempt) {
        rows.push(['세금', '0원 (과세최저한 이하)']);
        if (payout <= d.small_payout_max) notes.push('적중금이 10만 원 이하라서 배당률과 관계없이 세금이 없어요.');
        else notes.push('배당률이 100배 이하이고 적중금이 200만 원 이하라서 세금이 없어요.');
      } else {
        rows.push(['산 금액 (필요경비)', u.won(stake)]);
        rows.push(['세금 매기는 이익 (적중금 - 산 금액)', u.won(income)]);
        rows.push([`소득세 (3억 원까지 ${pct(d.rate_low)}${income > d.high_threshold ? `, 넘는 부분 ${pct(d.rate_high)}` : ''})`, u.won(incomeTax)]);
        rows.push([`지방소득세 (소득세의 ${pct(d.local_rate)})`, u.won(localTax)]);
        rows.push(['세금 합계', u.won(total)]);
        if (!smallTicket) notes.push('한 건에 산 투표권 금액이 10만 원을 넘으면 비과세 기준(10만 원·100배·200만 원)을 쓰지 않아 세금이 붙어요.');
        else if (payout > d.exempt_max) notes.push('적중금이 200만 원을 넘어서 배당률과 관계없이 세금이 붙어요.');
        else notes.push('배당률이 100배를 넘고 적중금이 10만 원을 넘어서 세금이 붙어요.');
        notes.push('세금은 10원 미만을 버렸어요.');
      }
      rows.push(['받는 돈', u.won(net)]);
      notes.push('적중금 세금은 줄 때 떼고 끝나는 분리과세라 종합소득세 신고에 더하지 않아요.');
      notes.push('한 번에 여러 투표를 맞혔다면 적중한 투표마다 따로 넣어 계산해요.');
      return { values, rows, notes };
    },
  });
})();
