/* 연말정산 계산기: 총급여·공제·이미 낸 소득세 → 근로소득공제, 과세표준, 산출세액, 근로소득세액공제(한도), 자녀·표준세액공제,
   결정세액, 환급(추가 납부) 소득세·지방소득세. 공제표·세율표는 data/year-end-tax.json (소득세법 제47조·제55조·제59조·제59조의2·제59조의4) */
(function () {
  'use strict';

  /** 근로소득공제 (원 미만 버림, 최대 2,000만 원, 총급여보다 클 수 없음) */
  function wageDeduction(salary, d) {
    const r = d.wage_deduction.find((x) => x.upto === null || salary <= x.upto);
    const amount = r.base + Math.floor(((salary - r.over) * r.rate_pct) / 100);
    return Math.min(amount, d.wage_deduction_max, salary);
  }

  function baseTax(base, brackets) {
    const b = brackets.find((x) => x.upto === null || base <= x.upto);
    return { tax: Math.max(0, Math.floor((base * b.rate_pct) / 100) - b.deduction), rate: b.rate_pct };
  }

  /** 근로소득세액공제 = min(산출세액 기준 공제액, 총급여 기준 한도) */
  function wageCredit(tax, salary, c) {
    const raw = tax <= c.threshold
      ? Math.floor((tax * c.low_pct) / 100)
      : c.high_base + Math.floor(((tax - c.threshold) * c.high_pct) / 100);
    const l = c.limits.find((x) => x.upto === null || salary <= x.upto);
    const limit = Math.max(l.floor, l.start - Math.floor(((salary - l.over) * l.per_mille) / 1000));
    return { credit: Math.min(raw, limit), limit };
  }

  function childCredit(n, c) {
    if (n <= 0) return 0;
    if (n === 1) return c.one;
    return c.two + (n - 2) * c.per_extra;
  }

  FirstLabCalc.register({
    id: 'year-end-tax',
    title: '연말정산 계산기',
    intro: '총급여와 공제, 1년 동안 월급에서 떼인 소득세를 넣으면 결정세액과 돌려받을(더 낼) 세금을 지방소득세까지 계산해요.',
    inputs: [
      { name: 'salary', label: '총급여 (원)', type: 'number', min: 0, step: 10000, help: '1년 급여에서 비과세 소득(식대 등)을 뺀 금액이에요. 근로소득 원천징수영수증의 총급여 칸 값이에요.' },
      { name: 'people', label: '기본공제 인원 (본인 포함)', type: 'number', min: 1, step: 1, default: 1, help: '본인과 요건을 갖춘 배우자·부양가족 1명당 150만 원씩 빠져요.' },
      { name: 'other_deduction', label: '그 밖의 소득공제 합계 (원)', type: 'number', min: 0, step: 10000, default: 0, help: '국민연금·건강·장기요양·고용보험료(근로자 부담분), 신용카드 소득공제, 주택자금 공제, 추가공제 등을 더한 금액' },
      { name: 'children', label: '자녀세액공제 대상 자녀 수', type: 'number', min: 0, step: 1, default: 0, help: '2026년분은 기본공제 대상인 9세 이상 자녀·손자녀 (출산·입양 공제는 아래 세액공제 칸에 더해요)' },
      { name: 'other_credit', label: '그 밖의 세액공제 합계 (원)', type: 'number', min: 0, step: 1000, default: 0, help: '연금계좌·보험료·의료비·교육비·기부금·월세 세액공제 등 (근로소득·자녀·표준세액공제는 빼고)' },
      {
        name: 'special', label: '건강보험료 공제·특별세액공제·월세 공제', type: 'select', default: 'yes',
        options: [['yes', '하나라도 받는다'], ['no', '하나도 안 받는다 (표준세액공제 13만 원)']],
      },
      { name: 'prepaid', label: '1년 동안 떼인 소득세 (원)', type: 'number', min: 0, step: 1000, default: 0, help: '기납부세액: 매달 월급에서 원천징수된 소득세 합계 (지방소득세는 빼고)' },
    ],

    compute(v, d, u) {
      const salary = Number(v.salary);
      const people = Number(v.people ?? 1);
      const children = Number(v.children) || 0;
      const other = Number(v.other_deduction) || 0;
      const otherCredit = Number(v.other_credit) || 0;
      const prepaid = Number(v.prepaid) || 0;
      if (!(salary > 0)) throw new u.InputError('총급여를 넣으면 결과가 나와요.');
      if (!Number.isInteger(people) || people < 1) throw new u.InputError('기본공제 인원은 본인 포함 1명 이상으로 넣어 주세요.');
      if (!Number.isInteger(children) || children < 0) throw new u.InputError('자녀 수는 0 이상 정수로 넣어 주세요.');
      if (other < 0 || otherCredit < 0 || prepaid < 0) throw new u.InputError('공제와 떼인 세금은 0 이상으로 넣어 주세요.');

      const wageDed = wageDeduction(salary, d);
      const earned = salary - wageDed;
      const deduction = people * d.basic_deduction_per_person + other;
      const base = Math.max(0, earned - deduction);
      const { tax, rate } = baseTax(base, d.brackets);
      const { credit: wage, limit } = wageCredit(tax, salary, d.wage_credit);
      const child = childCredit(children, d.child_credit);
      const standard = v.special === 'no' ? d.standard_credit : 0;
      const credits = wage + child + standard + otherCredit;
      const determined = Math.max(0, tax - credits);
      const due = determined - prepaid;
      const local = Math.floor(determined / d.local_ratio_div);
      const localDue = local - Math.floor(prepaid / d.local_ratio_div);
      const total = due + localDue;

      const rows = [
        ['근로소득공제', u.won(wageDed)],
        ['근로소득금액', u.won(earned)],
        ['소득공제 합계 (기본공제 포함)', u.won(deduction)],
        ['과세표준', u.won(base)],
        ['적용 세율', `${rate}%`],
        ['산출세액', u.won(tax)],
        ['근로소득세액공제 (한도 ' + u.won(limit) + ')', u.won(wage)],
        ['자녀세액공제', u.won(child)],
        ['표준세액공제', u.won(standard)],
        ['결정세액 (소득세)', u.won(determined)],
        due >= 0 ? ['더 낼 소득세', u.won(due)] : ['돌려받을 소득세', u.won(-due)],
        localDue >= 0 ? ['더 낼 지방소득세', u.won(localDue)] : ['돌려받을 지방소득세', u.won(-localDue)],
        total >= 0 ? ['합계: 더 낼 세금', u.won(total)] : ['합계: 돌려받을 세금', u.won(-total)],
      ];
      return {
        values: { wage_deduction: wageDed, earned, deduction, base, rate, tax, wage_credit: wage, wage_limit: limit, child_credit: child, standard_credit: standard, determined, due, local, local_due: localDue, total },
        rows,
        notes: [
          '세액공제가 산출세액보다 많으면 결정세액은 0원이에요. 남는 공제는 돌려받지 못해요.',
          '지방소득세는 결정세액의 10%로, 떼인 소득세의 10%만큼 지방소득세도 미리 냈다고 봤어요.',
          '중도 입사·두 곳 이상 근무, 외국납부세액공제 같은 경우는 넣지 않았어요. 실제 금액은 회사가 내주는 원천징수영수증으로 확인해요.',
        ],
      };
    },
  });
})();
