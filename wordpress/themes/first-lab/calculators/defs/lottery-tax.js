/* 로또 세금 계산기: 복권 당첨금 원천징수 소득세(20%, 3억 원 초과분 30%) + 지방소득세(소득세의 10%), 200만 원 이하 과세최저한. 요율은 data/lottery-tax.json */
(function () {
  'use strict';

  const pct = (r) => `${Math.round(r * 1000) / 10}%`;

  FirstLabCalc.register({
    id: 'lottery-tax',
    title: '로또 세금 계산기',
    intro: '당첨금을 넣으면 떼는 소득세·지방소득세와 실제로 받는 금액이 나와요. 로또·스피또처럼 한 번에 받는 복권 당첨금 기준이에요.',
    inputs: [
      { name: 'prize', label: '당첨금 (원, 세전)', type: 'number', min: 0, step: 1, help: '동행복권에 공시된 당첨금(1게임 기준)을 그대로 넣어요.' },
    ],

    compute(v, d, u) {
      const prize = Number(v.prize);
      if (!(prize > 0)) throw new u.InputError('당첨금을 넣으면 결과가 나와요.');
      if (!Number.isInteger(prize)) throw new u.InputError('당첨금은 1원 단위 정수로 넣어 주세요.');
      const f = FirstLabCalc.util.floor;
      const notes = [];
      let incomeTax = 0;
      let localTax = 0;
      const exempt = prize <= d.exempt_max;
      if (!exempt) {
        const low = Math.min(prize, d.high_threshold);
        const high = Math.max(prize - d.high_threshold, 0);
        // 세율은 정수 퍼센트로 곱해 소수 오차를 줄이고, 10원 미만은 버림 (국고금관리법 제47조①)
        const raw = (low * Math.round(d.rate_low * 100) + high * Math.round(d.rate_high * 100)) / 100;
        incomeTax = f(raw / 10) * 10;
        localTax = f((incomeTax * Math.round(d.local_rate * 100)) / 100 / 10) * 10;
      }
      const total = incomeTax + localTax;
      const net = prize - total;
      const values = { income_tax: incomeTax, local_tax: localTax, total, net, exempt };
      const rows = [['당첨금 (세전)', u.won(prize)]];
      if (exempt) {
        rows.push(['세금', `0원 (${u.won(d.exempt_max)} 이하는 과세하지 않음)`]);
      } else {
        const low = Math.min(prize, d.high_threshold);
        rows.push([`소득세 (3억 원까지 ${pct(d.rate_low)}${prize > d.high_threshold ? `, 넘는 부분 ${pct(d.rate_high)}` : ''})`, u.won(incomeTax)]);
        rows.push([`지방소득세 (소득세의 ${pct(d.local_rate)})`, u.won(localTax)]);
        rows.push(['세금 합계', u.won(total)]);
        if (prize > d.high_threshold) notes.push(`3억 원까지는 ${u.won(low)} × 22%, 3억 원을 넘는 ${u.won(prize - d.high_threshold)}에만 33%가 붙어요.`);
      }
      rows.push(['실수령액', u.won(net)]);
      rows.push(['실제 세율 (세금 ÷ 당첨금)', `${(Math.round((total / prize) * 10000) / 100).toFixed(2)}%`]);
      notes.push('세금은 10원 미만을 버렸어요.');
      notes.push('복권 당첨금은 지급할 때 세금을 떼고 끝나는 분리과세라 종합소득세 신고에 더하지 않아요.');
      notes.push('연금복권처럼 나눠 받는 당첨금은 이 계산과 달라요.');
      return { values, rows, notes };
    },
  });
})();
