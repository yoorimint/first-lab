/* 교육비 세액공제 계산기: 본인 교육비는 한도 없이, 가족은 1명당 취학 전·초중고 연 300만 원·대학생 연 900만 원까지(대학원 제외),
   장애인 특수교육비는 한도 없이 → 합계의 15%를 소득세에서 공제.
   한도·공제율은 data/education-tax-credit.json (소득세법 제59조의4③, 시행령 제118조의6) */
(function () {
  'use strict';

  const KINDS = [
    ['none', '없음'],
    ['preschool', '초등학교 입학 전 아이'],
    ['school', '초·중·고등학생'],
    ['univ', '대학생'],
    ['grad', '대학원생'],
  ];

  const family = (n) => [
    {
      name: `kind${n}`, label: `가족 ${n} 학교 단계`, type: 'select', default: 'none', options: KINDS,
      help: n === 1 ? '기본공제대상자인 배우자·자녀·형제자매 등이에요 (나이·소득은 안 봐요). 부모님(직계존속)의 일반 교육비는 공제 대상이 아니라 넣지 않아요.' : undefined,
    },
    { name: `amount${n}`, label: `가족 ${n} 교육비 (원, 1년, 장학금 뺀 금액)`, type: 'number', min: 0, step: 1 },
  ];

  FirstLabCalc.register({
    id: 'education-tax-credit',
    title: '교육비 세액공제 계산기',
    intro: '한 해 동안 낸 교육비를 사람별로 넣으면 한도를 적용한 공제 대상 금액과 소득세에서 빼 주는 금액(15%)이 나와요.',
    inputs: [
      { name: 'self', label: '본인 교육비 (원, 1년, 없으면 비워 둬요)', type: 'number', min: 0, step: 1, help: '본인 대학·대학원 등록금, 직업훈련 수강료(지원금 뺀 금액), 학자금 대출 원리금 상환액이에요. 한도가 없어요.' },
      ...family(1),
      ...family(2),
      ...family(3),
      { name: 'special', label: '장애인 특수교육비 (원, 1년, 없으면 비워 둬요)', type: 'number', min: 0, step: 1, help: '기본공제대상자인 장애인의 재활교육비예요. 부모님도 포함되고 한도가 없어요. 나라·지자체 지원금은 빼고, 발달재활서비스 기관 비용은 18세 미만만 넣어요.' },
    ],

    compute(v, d, u) {
      const num = (x) => (x === undefined || x === null || x === '' ? 0 : Number(x));
      const check = (x, label) => {
        if (!(x >= 0) || !Number.isInteger(x)) throw new u.InputError(`${label} 칸에는 0 이상 1원 단위 정수를 넣어 주세요.`);
        if (x > d.max_input) throw new u.InputError(`${label} 칸의 금액이 너무 커요.`);
      };
      const self = num(v.self);
      const special = num(v.special);
      check(self, '본인 교육비');
      check(special, '장애인 특수교육비');
      const rows = [];
      const notes = [];
      const values = { self };
      let familySum = 0;
      let paidSum = self + special;
      const capOf = { preschool: d.cap_school, school: d.cap_school, univ: d.cap_univ, grad: 0 };
      for (const n of [1, 2, 3]) {
        const kind = v[`kind${n}`] || 'none';
        const amount = num(v[`amount${n}`]);
        check(amount, `가족 ${n} 교육비`);
        if (!KINDS.some(([k]) => k === kind)) throw new u.InputError(`가족 ${n} 학교 단계를 골라 주세요.`);
        if (kind === 'none') {
          if (amount > 0) throw new u.InputError(`가족 ${n} 학교 단계를 골라 주세요.`);
          continue;
        }
        const used = Math.min(amount, capOf[kind]);
        values[`used${n}`] = used;
        familySum += used;
        paidSum += amount;
        const name = KINDS.find(([k]) => k === kind)[1];
        rows.push([`가족 ${n} (${name}) 인정액`, kind === 'grad' ? '0원 (가족의 대학원 교육비는 공제 안 됨)' : `${u.won(used)}${amount > used ? ` (한도 ${u.won(capOf[kind])})` : ''}`]);
        if (kind === 'grad' && amount > 0) notes.push('가족의 대학원 등록금은 공제 대상이 아니에요. 대학원은 본인이 다닐 때만 공제돼요.');
        if (kind === 'school') notes.push('초·중·고등학생 학원비는 넣지 않아요. 다만 2026년에 낸 돈부터 초등 2학년 이하(또는 9세 미만) 아이의 예능학원·체육시설 수강료(월 단위·주 1회 이상)는 넣어요.');
        if (kind === 'preschool') notes.push('입학 전 아이는 어린이집·유치원 비용과 학원·체육시설 수강료(월 단위·주 1회 이상)를 넣어요.');
      }
      if (paidSum === 0) throw new u.InputError('교육비를 하나 이상 넣어 주세요.');
      const eligible = self + familySum + special;
      const credit = u.floor((eligible * d.rate_pct) / 100);
      Object.assign(values, { family: familySum, special, eligible, credit, over: paidSum - eligible });
      rows.unshift(['본인 인정액 (한도 없음)', u.won(self)]);
      if (special > 0) rows.push(['장애인 특수교육비 인정액 (한도 없음)', u.won(special)]);
      rows.push(['공제 대상 금액 합계', u.won(eligible)]);
      rows.push([`세액공제액 (공제 대상 × ${d.rate_pct}%)`, u.won(credit)]);
      if (values.over > 0) notes.unshift(`한도를 넘거나 공제 대상이 아닌 ${u.won(values.over)}은 공제받지 못해요.`);
      notes.push('교복은 중·고등학생 1명당 50만 원, 현장체험학습비는 1명당 30만 원까지만 넣어요 (그 안에서도 1명 한도에 같이 들어가요).');
      notes.push('낼 세금(산출세액)보다 많이 공제받지는 못해요. 소득세가 줄면 지방소득세(소득세의 10%)도 같이 줄어요. 원 미만은 버렸어요.');
      return { values: Object.fromEntries([...new Set(Object.keys(values))].map((k) => [k, values[k]])), rows, notes: [...new Set(notes)] };
    },
  });
})();
