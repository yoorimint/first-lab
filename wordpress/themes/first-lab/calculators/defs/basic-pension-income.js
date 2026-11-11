/* 기초연금 소득인정액 계산기: 소득평가액 + 재산의 소득환산액을 선정기준액과 비교.
 *   소득평가액 = 0.7 × (근로소득 - 기본공제) [부부는 각자] + 기타소득
 *   재산의 월 소득환산액 = [{(일반재산 - 지역별 기본재산액)⁺ + (금융재산 - 2,000만 원)⁺ - 부채}⁺ × 연 4% ÷ 12] + 고급자동차·회원권 가액
 * 근거: 기초연금법 제2조제4호, 시행령 제3조, 시행규칙 제2조·제4조, 보건복지부 고시(선정기준액·소득인정액 산정 세부기준) 제2조·제6조·제8조·제9조·제11조.
 * 금액은 모두 data/basic-pension-income.json. 자격을 확정하지 않는다 — 결정은 시·군·구가 조사 후 한다. */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'basic-pension-income',
    title: '기초연금 소득인정액 계산기',
    intro: '부부 합산 소득과 재산을 넣으면 소득인정액(월)과 선정기준액 이하인지가 나와요. 금액은 모두 부부 합계로, 비워 둔 칸은 0원으로 계산해요.',
    inputs: [
      { name: 'household', label: '가구', type: 'select', default: 'single', options: [['single', '단독 가구 (배우자 없음)'], ['couple', '부부 가구 (배우자 있음)']] },
      {
        name: 'region', label: '사는 곳', type: 'select', default: 'metro',
        options: [
          ['metro', '대도시: 특별시·광역시의 구(도농복합군 포함), 특례시'],
          ['city', '중소도시: 도의 시, 세종특별자치시'],
          ['rural', '농어촌: 도의 군'],
        ],
      },
      { name: 'earned_self', label: '본인 근로소득 (월, 원)', type: 'number', min: 0, step: 1, help: '일용근로·공공일자리·자활근로 소득은 넣지 않아요.' },
      { name: 'earned_spouse', label: '배우자 근로소득 (월, 원)', type: 'number', min: 0, step: 1, help: '부부 가구일 때만 계산에 넣어요.' },
      { name: 'other_income', label: '그 밖의 소득 (월, 원)', type: 'number', min: 0, step: 1, help: '국민연금 등 공적연금, 사업·임대소득, 민간연금 소득의 부부 합계예요. 이자소득은 아래 칸에 따로 넣어요.' },
      { name: 'interest', label: '이자소득 (월, 원)', type: 'number', min: 0, step: 1, help: '예금·적금 이자와 배당 등이에요. 월 4만 원을 빼고 더해요.' },
      { name: 'general', label: '일반재산 (원)', type: 'number', min: 0, step: 1, help: '집·토지·건물 시가표준액(주택은 공시가격), 내가 낸 전월세 보증금, 4천만 원 미만 자동차 등이에요.' },
      { name: 'financial', label: '금융재산 (원)', type: 'number', min: 0, step: 1, help: '예금·적금·주식·보험 등이에요.' },
      { name: 'debt', label: '부채 (원)', type: 'number', min: 0, step: 1, help: '대출금·연체금·카드 미결제금액 등이에요. 세를 놓고 받은 임대보증금은 그 주택 공시가격의 50%까지만 넣어요.' },
      { name: 'luxury', label: '고급자동차·회원권 가액 (원)', type: 'number', min: 0, step: 1, help: '차량가액 4천만 원 이상 차(10년 이상 등 예외는 일반재산에)와 골프·콘도 등 회원권이에요.' },
    ],

    compute(v, d, u) {
      const num = (key, label) => {
        const raw = v[key];
        if (raw === undefined || raw === null || raw === '') return 0;
        const n = Number(raw);
        if (!(n >= 0) || !Number.isInteger(n)) throw new u.InputError(`${label} 칸은 0 이상 1원 단위 정수로 넣어 주세요.`);
        if (n > d.max_input) throw new u.InputError(`${label} 칸 금액이 너무 커요.`);
        return n;
      };
      const couple = v.household === 'couple';
      if (!couple && v.household !== 'single') throw new u.InputError('가구를 골라 주세요.');
      const basic = d.basic_property[v.region];
      if (basic === undefined) throw new u.InputError('사는 곳을 골라 주세요.');
      const earnedSelf = num('earned_self', '본인 근로소득');
      const earnedSpouse = couple ? num('earned_spouse', '배우자 근로소득') : 0;
      const other = num('other_income', '그 밖의 소득');
      const interest = num('interest', '이자소득');
      const general = num('general', '일반재산');
      const financial = num('financial', '금융재산');
      const debt = num('debt', '부채');
      const luxury = num('luxury', '고급자동차·회원권');

      // 소수 오차 없이 비교하려고 모든 금액을 '월 금액 × 300' 정수로 계산 (×0.7 과 연 4% ÷ 12 = ÷300 이 섞이면
      // 정확히 선정기준액인데 '넘어요'가 나옴 — 11/11 독립 검수). 근로소득: 기본공제 뒤 30% 추가 공제 = 남은 금액의 70% (고시 제6조, 부부는 각자)
      const S = 300;
      const pct = d.conversion_pct; // 연 4% → 월 환산은 base × 4 / 1200 = base / 300
      if (pct * S !== 1200) throw new u.InputError('환산율 설정이 바뀌었어요. 계산기를 고쳐야 해요.');
      const evalEarned300 = (x) => (Math.max(0, x - d.earned_deduction) * (100 - d.earned_extra_pct) * S) / 100;
      // 이자소득: 월 4만 원을 빼고 0 하한 (고시 제6조의2). 부부 이자도 한 번만 뺀다 (보수적으로 — 안내 문장으로 알림)
      const interestNet = Math.max(0, interest - d.interest_deduction);
      const income300 = evalEarned300(earnedSelf) + evalEarned300(earnedSpouse) + (other + interestNet) * S;
      const generalNet = Math.max(0, general - basic);
      const financialNet = Math.max(0, financial - d.financial_deduction);
      const base = Math.max(0, generalNet + financialNet - debt);
      const property300 = base + luxury * S;
      const total300 = income300 + property300;
      const threshold = couple ? d.threshold_couple : d.threshold_single;
      const eligible = total300 <= threshold * S;
      // 화면 금액: 원 단위 반올림. 합계는 두 줄을 반올림해 더하지 않고 정확한 합을 반올림
      const r = (n300) => Math.round(n300 / S);
      const incomeEval = r(income300);
      const propertyMonthly = r(property300);
      const total = r(total300);
      const values = { income_eval: incomeEval, property_monthly: propertyMonthly, total, threshold, eligible };
      const rows = [
        ['소득평가액 (월)', u.won(incomeEval)],
        [`일반재산 - 기본재산액 ${u.won(basic)}`, u.won(generalNet)],
        [`금융재산 - ${u.won(d.financial_deduction)}`, u.won(financialNet)],
        ['위 두 금액 합계 - 부채', u.won(base)],
        ['재산의 소득환산액 (월)', u.won(propertyMonthly)],
        ['소득인정액 (월)', u.won(total)],
        [`${couple ? '부부' : '단독'} 가구 선정기준액 (${d.year}년)`, u.won(threshold)],
        ['결과', eligible ? '선정기준액 이하예요' : '선정기준액을 넘어요'],
      ];
      const notes = [];
      if (eligible) notes.push('만 65세 이상이고 국내에 사는 한국 국적자라면 신청해 볼 수 있어요. 받을지는 신청 뒤 소득·재산 조사로 정해져요.');
      else notes.push(`소득인정액이 선정기준액보다 ${u.won(Math.max(1, Math.ceil((total300 - threshold * S) / S)))} 많아요. 빠진 부채나 자동차 예외가 있는지 다시 보세요.`);
      if (luxury > 0) notes.push('고급자동차·회원권은 기본재산액을 빼지 않고 가액 전부를 한 달 소득으로 봐요.');
      if (couple) notes.push('부부 가구는 근로소득 공제를 부부 각자에게 따로 해요.');
      if (interest > 0) notes.push(`이자소득은 월 ${u.won(d.interest_deduction)}을 빼고 더했어요.${couple ? ' 부부가 각자 이자소득이 있으면 공제가 더 될 수 있으니 국민연금공단(1355)에 확인하세요.' : ''}`);
      notes.push('공무원·사학·군인·별정우체국 연금과 직역 10년 이상 연계퇴직연금 수급권자와 배우자는 원칙적으로 기초연금 대상에서 빠져요.');
      notes.push('2011년 7월 1일 이후 남에게 준(증여)·처분한 재산, 자녀 명의 6억 원 이상 주택에 사는 경우의 소득은 이 계산에 넣지 않았어요.');
      notes.push('금액은 원 단위로 반올림해 보여 줘서, 소득평가액과 재산 환산액을 더한 값이 소득인정액과 1원 다를 수 있어요. 정확한 금액은 기초연금 누리집 모의계산이나 국민연금공단(1355)에서 확인하세요.');
      return { values, rows, notes };
    },
  });
})();
