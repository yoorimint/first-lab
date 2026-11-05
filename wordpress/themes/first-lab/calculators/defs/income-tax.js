/* 종합소득세 계산기: 종합소득금액·공제·기납부세액 → 과세표준, 산출세액, 낼(돌려받을) 세액, 지방소득세. 세율표는 data/income-tax.json */
(function () {
  'use strict';

  /** 과세표준 × 세율 − 누진공제 (원 미만 버림). 세율은 % 정수로 계산해 소수 오차를 막는다 */
  function baseTax(base, brackets) {
    const b = brackets.find((x) => x.upto === null || base <= x.upto);
    return { tax: Math.max(0, FirstLabCalc.util.floor((base * b.rate_pct) / 100) - b.deduction), rate: b.rate_pct };
  }

  FirstLabCalc.register({
    id: 'income-tax',
    title: '종합소득세 계산기',
    intro: '종합소득금액과 공제, 이미 낸 세금을 넣으면 과세표준, 산출세액, 5월에 낼(돌려받을) 세액과 지방소득세를 계산해요.',
    inputs: [
      { name: 'income', label: '종합소득금액 (원)', type: 'number', min: 0, step: 10000, help: '수입에서 필요경비를 뺀 소득을 모두 더한 금액이에요 (근로소득은 근로소득공제 후 금액).' },
      { name: 'people', label: '기본공제 인원 (본인 포함)', type: 'number', min: 1, step: 1, default: 1, help: '본인과 요건을 갖춘 배우자·부양가족 1명당 150만 원씩 빠져요.' },
      { name: 'other_deduction', label: '그 밖의 소득공제 (원)', type: 'number', min: 0, step: 10000, default: 0, help: '추가공제, 연금보험료공제, 노란우산공제 같은 소득공제 합계' },
      { name: 'credit', label: '세액공제·감면 합계 (원)', type: 'number', min: 0, step: 1000, default: 0 },
      { name: 'prepaid', label: '이미 낸 소득세 (원)', type: 'number', min: 0, step: 1000, default: 0, help: '원천징수로 떼인 소득세, 중간예납세액 (지방소득세는 빼고)' },
    ],

    compute(v, d, u) {
      const income = Number(v.income);
      const people = Number(v.people ?? 1);
      const other = Number(v.other_deduction) || 0;
      const credit = Number(v.credit) || 0;
      const prepaid = Number(v.prepaid) || 0;
      if (!(income > 0)) throw new u.InputError('종합소득금액을 넣으면 결과가 나와요.');
      if (!Number.isInteger(people) || people < 1) throw new u.InputError('기본공제 인원은 본인 포함 1명 이상으로 넣어 주세요.');
      if (other < 0 || credit < 0 || prepaid < 0) throw new u.InputError('공제와 이미 낸 세금은 0 이상으로 넣어 주세요.');

      const deduction = people * d.basic_deduction_per_person + other;
      const base = Math.max(0, income - deduction);
      const { tax, rate } = baseTax(base, d.brackets);
      const determined = Math.max(0, tax - credit);
      const due = determined - prepaid;
      const local = FirstLabCalc.util.floor(determined / d.local_ratio_div);
      const localDue = local - FirstLabCalc.util.floor(prepaid / d.local_ratio_div);

      const rows = [
        ['소득공제 합계', u.won(deduction)],
        ['과세표준', u.won(base)],
        ['적용 세율', `${rate}%`],
        ['산출세액', u.won(tax)],
        ['결정세액 (세액공제·감면 뒤)', u.won(determined)],
        due >= 0 ? ['5월에 낼 소득세', u.won(due)] : ['돌려받을 소득세 (환급)', u.won(-due)],
        localDue >= 0 ? ['지방소득세 낼 세액', u.won(localDue)] : ['지방소득세 환급', u.won(-localDue)],
      ];
      return {
        values: { deduction, base, rate, tax, determined, due, local, local_due: localDue },
        rows,
        notes: [
          '지방소득세는 결정세액의 10%로 계산했고, 이미 낸 소득세의 10%만큼 지방소득세도 미리 냈다고 봤어요.',
          '가산세, 분리과세 소득, 금융소득 비교과세 같은 특례는 넣지 않았어요. 실제 신고 금액은 홈택스 신고 화면에서 확인해요.',
        ],
      };
    },
  });
})();
