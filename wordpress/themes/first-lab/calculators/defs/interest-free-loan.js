/* 무이자 차용 증여 계산기: 빌린 돈·약정 이율 → 1년 적정 이자(연 4.6%), 실제 이자, 차이(무상대출 이익), 기준금액 1천만 원 이상인지.
 * 상속세및증여세법 제41조의4①(대출금액 × 적정 이자율 − 실제 이자, 기준금액 미만이면 제외)·②(기간 없으면 1년, 1년 넘으면 매년 새로),
 * 같은 법 시행령 제31조의4①②(기준금액 1천만 원, 법인에게 빌리면 다른 이율), 시행규칙 제10조의5 → 법인세법 시행규칙 제43조②(연 1,000분의 46).
 * 값은 data/interest-free-loan.json. 계산은 원·만분율 정수(BigInt)로 정확히 — 기준금액 비교는 버림 전 값으로. */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'interest-free-loan',
    title: '무이자 차용 증여 계산기 (적정 이자율 4.6%)',
    intro: '가족 등에게 빌린 돈과 약속한 이율을 넣으면, 1년 동안 덜 낸 이자가 1,000만 원 이상인지 나와요. 개인에게 빌린 경우 기준이에요.',
    inputs: [
      { name: 'amount', label: '빌린 돈 (원)', type: 'number', min: 0, step: 1000000, help: '차용증에 적은 원금이에요. 나눠 받았다면 받은 날마다 따로 넣어요.' },
      { name: 'rate', label: '약속한 이율 (연 %)', type: 'number', min: 0, step: 0.1, default: 0, help: '무이자면 0을 넣어요.' },
    ],

    compute(v, d, u) {
      const A = Number(v.amount);
      if (!(A > 0)) throw new u.InputError('빌린 돈을 넣으면 결과가 나와요.');
      if (!Number.isInteger(A)) throw new u.InputError('빌린 돈은 1원 단위 정수로 넣어 주세요.');
      if (A > d.max_amount) throw new u.InputError('금액이 너무 커요. 1조 원 이하로 넣어 주세요.');
      const ratePct = v.rate === '' || v.rate === undefined ? 0 : Number(v.rate);
      if (!(ratePct >= 0) || ratePct > d.max_rate) throw new u.InputError(`이율을 0~${d.max_rate}% 사이로 넣어 주세요.`);
      const R = Math.round(ratePct * 100); // 만분율 정수 (4.6% → 460)
      if (Math.abs(ratePct * 100 - R) > 1e-6) throw new u.InputError('이율은 소수 둘째 자리까지 넣어 주세요 (예: 2.15).');
      const P = Math.round(d.proper_rate * 10000); // 460
      const a = BigInt(A);
      const proper = Number((a * BigInt(P)) / 10000n);
      const actual = Number((a * BigInt(R)) / 10000n);
      const gapRaw = R < P ? a * BigInt(P - R) : 0n; // 원 × 10000
      const benefit = Number(gapRaw / 10000n);
      const taxable = gapRaw >= BigInt(d.threshold) * 10000n;
      const gift = taxable ? benefit : 0;
      // 무이자로 기준 미만인 가장 큰 금액: A × P < 기준 × 10000
      const freeLimit = Number((BigInt(d.threshold) * 10000n - 1n) / BigInt(P));
      // 기준 미만이 되는 가장 낮은 이율(만분율 정수): A × (P − R) < 기준 × 10000
      let minR = 0;
      if (a * BigInt(P) >= BigInt(d.threshold) * 10000n) {
        minR = P - Number((BigInt(d.threshold) * 10000n - 1n) / a);
      }
      const pct = (r) => `연 ${(r / 100).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}%`;
      const values = { proper_interest: proper, actual_interest: actual, benefit, taxable, gift_value: gift, free_limit: freeLimit, min_rate: minR / 100 };
      const rows = [
        ['빌린 돈', u.won(A)],
        [`1년 적정 이자 (${pct(P)})`, u.won(proper)],
        [`1년 실제 이자 (${pct(R)})`, u.won(actual)],
        ['덜 낸 이자 (차이)', u.won(benefit)],
        ['1,000만 원 기준', taxable ? '1,000만 원 이상 — 차이 전체가 증여재산가액' : '1,000만 원 미만 — 증여로 보지 않음'],
        ['증여재산가액 (1년)', u.won(gift)],
        ['무이자로 기준 안 넘는 금액', `${u.won(freeLimit)}까지`],
      ];
      if (minR > 0) rows.push(['기준을 안 넘는 최저 이율', `${pct(minR)} 이상`]);
      const notes = [];
      notes.push('원 미만은 버렸어요. 1,000만 원 기준은 버리기 전 값으로 따져요.');
      if (taxable) notes.push('증여재산가액은 다른 증여와 합쳐 증여재산공제(성년 자녀가 부모에게 받으면 10년간 5천만 원)를 뺀 뒤 세금을 계산해요.');
      notes.push('갚을 날을 정하지 않았으면 1년으로 봐요. 1년이 넘으면 1년이 되는 다음 날 다시 빌린 것으로 보고 해마다 새로 계산해요.');
      notes.push('법인에게 빌렸다면 법인세법 시행령 제89조제3항의 이율을 적정 이자율로 봐서 이 계산과 다를 수 있어요.');
      return { values, rows, notes };
    },
  });
})();
