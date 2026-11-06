/* 종합소득세 중간예납 계산기: 중간예납기준액(직전 과세기간 종합소득세)의 2분의 1, 1천원 미만 버림(소득세법 제65조①),
 * 50만 원 미만 소액 부징수(제86조4호), 1천만 원 초과 분납(제77조, 시행령 제140조). 금액 기준은 data/interim-income-tax.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'interim-income-tax',
    title: '종합소득세 중간예납 계산기',
    intro: '작년 종합소득세(중간예납기준액)를 넣으면 11월에 낼 중간예납세액과 나눠 낼 수 있는 금액이 나와요.',
    inputs: [
      { name: 'base', label: '중간예납기준액 (원)', type: 'number', min: 0, step: 1, help: '작년 중간예납세액 + 5월(성실신고확인대상자는 6월) 확정신고 납부세액 + 추가납부·기한 후 신고 세액(가산세 포함)에서 환급세액을 뺀 금액이에요. 부동산매매업자는 상반기 매매차익 예정신고 세액을 따로 빼야 해요.' },
    ],

    compute(v, d, u) {
      if (v.base === '' || v.base === null || v.base === undefined) throw new u.InputError('중간예납기준액을 넣으면 결과가 나와요.');
      const base = Number(v.base);
      if (!Number.isFinite(base) || base < 0 || !Number.isInteger(base)) throw new u.InputError('기준액은 0원 이상 1원 단위 정수로 넣어 주세요.');
      if (base > d.max_input) throw new u.InputError('금액이 너무 커요. 1조 원 이하로 넣어 주세요.');
      const f = u.floor;
      const notes = [];
      const half = f(base / 2 / d.unit) * d.unit; // 2분의 1, 1천원 미만 버림
      const exempt = half < d.min_collect;
      const interimTax = exempt ? 0 : half;
      let installment = 0;
      if (interimTax > d.split_over) {
        installment = interimTax <= d.split_high ? interimTax - d.split_over : f((interimTax * d.split_rate_pct) / 100);
      }
      const payByNov = interimTax - installment;
      const values = { half, interim_tax: interimTax, exempt, installment, pay_by_nov: payByNov };
      const rows = [
        ['중간예납기준액', u.won(base)],
        ['기준액의 2분의 1 (1천 원 미만 버림)', u.won(half)],
      ];
      if (exempt) {
        rows.push(['중간예납세액', `0원 (${u.won(d.min_collect)} 미만은 걷지 않음)`]);
        notes.push('중간예납세액이 50만 원 미만이면 소득세법 제86조에 따라 걷지 않아요.');
      } else {
        rows.push(['중간예납세액', u.won(interimTax)]);
        if (installment > 0) {
          rows.push(['나눠 낼 수 있는 금액 (최대)', u.won(installment)]);
          rows.push(['11월 30일까지 낼 금액 (최소)', u.won(payByNov)]);
          notes.push(interimTax <= d.split_high
            ? '2천만 원 이하라 1천만 원을 넘는 금액을 납부기한이 지난 뒤 2개월 안에 나눠 낼 수 있어요.'
            : '2천만 원을 넘어 세액의 50% 이하를 납부기한이 지난 뒤 2개월 안에 나눠 낼 수 있어요.');
        } else {
          notes.push('1천만 원 이하라 나눠 내지 못하고 11월 30일까지 전액 내요.');
        }
        notes.push('올해 상반기 소득으로 계산한 세액(중간예납추계액)이 기준액의 30%에 못 미치면 11월 30일까지 추계액으로 신고해 그 금액만 낼 수 있어요.');
      }
      notes.push('올해 새로 사업을 시작했거나 이자·배당·근로·연금·기타소득만 있으면 중간예납 대상이 아니에요.');
      return { values, rows, notes };
    },
  });
})();
