/* 자동차세 계산기 (승용차): 배기량 × cc당 세액(지방세법 제127조①) → 차령 경감(같은 항 제2호, 3년차부터 5%씩·최대 50%)
 * → 기분(1~6월·7~12월) 세액 → 지방교육세 30%(비영업용만, 제150조·제151조) → 1월·6월 연납 공제(제128조③, 시행령 제125조⑥ 5%).
 * 끝수: 10원 미만 버림 (지방세기본법 제59조 → 국고금관리법 제47조). 값은 data/car-tax.json */
(function () {
  'use strict';

  const KIND = [
    ['private', '비영업용 승용 (자가용, 휘발유·경유·LPG·하이브리드)'],
    ['business', '영업용 승용 (택시·렌터카 등)'],
    ['ev_private', '비영업용 전기차 등 (전기·태양열·알코올)'],
    ['ev_business', '영업용 전기차 등 (전기·태양열·알코올)'],
  ];

  const floor10 = (n) => FirstLabCalc.util.floor(n / 10) * 10;
  const isLeap = (y) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;

  FirstLabCalc.register({
    id: 'car-tax',
    title: '자동차세 계산기 (승용차)',
    intro: '배기량과 최초 등록일을 넣으면 차령 경감을 반영한 6월·12월 자동차세와 지방교육세, 1월·6월 연납 때 내는 금액까지 계산해요.',
    inputs: [
      { name: 'kind', label: '차 종류', type: 'select', default: 'private', options: KIND },
      { name: 'cc', label: '배기량 (cc)', type: 'number', min: 0, step: 1, default: 2500, help: '자동차등록증의 배기량 숫자 그대로 넣어요 (예: 2,497). 전기차 등은 배기량과 상관없이 계산돼요.' },
      { name: 'first_date', label: '최초 등록일', type: 'date', default: '2025-03-10', help: '차령은 최초 신규등록일부터 세요. 만든 해에 등록하지 않은 차는 제작연도 12월 31일을 넣어요.' },
      { name: 'year', label: '세금을 내는 해 (비우면 올해)', type: 'number', min: 2000, step: 1, help: '내년 1월 연납 금액이 궁금하면 내년 연도를 넣어요.' },
    ],

    compute(v, d, u) {
      const kind = v.kind;
      if (!(kind in d.kinds)) throw new u.InputError('차 종류를 골라 주세요.');
      const rule = d.kinds[kind];
      const cc = Number(v.cc) || 0;
      if (rule.per_cc && !(cc > 0)) throw new u.InputError('배기량(cc)을 넣으면 결과가 나와요.');
      const first = u.parseDate(v.first_date);
      if (first === null) throw new u.InputError('최초 등록일을 넣어 주세요.');
      const year = Number(v.year) || Number(u.todayIso().slice(0, 4));
      if (!Number.isInteger(year)) throw new u.InputError('연도는 2027처럼 네 자리로 넣어 주세요.');
      const fy = new Date(first).getUTCFullYear();
      const fm = new Date(first).getUTCMonth() + 1;
      if (year <= fy) throw new u.InputError('등록한 해에는 등록일부터 날짜만큼 나눠 계산(일할계산)돼서, 다음 해부터 계산할 수 있어요.');

      // 연세액 (표준세율)
      let annualBase;
      if (rule.per_cc) {
        const band = rule.per_cc.find((b) => b.upto === null || cc <= b.upto);
        annualBase = cc * band.won;
      } else {
        annualBase = rule.flat;
      }

      // 차령 (시행령 제122조②): 기산일이 7~12월이면 1기분은 1년 적게
      const n2 = year - fy + 1;
      const n1 = fm >= 7 ? year - fy : n2;
      const halfOf = (n) => {
        const half = annualBase / 2;
        if (!rule.age_relief || n < d.age.from) return floor10(half);
        const m = Math.min(n, d.age.cap);
        return floor10(half - (half * d.age.pct_per_year * (m - 2)) / 100);
      };
      const half1 = halfOf(n1);
      const half2 = halfOf(n2);
      const annual = half1 + half2;
      const eduRate = rule.edu ? d.edu_pct : 0;
      const edu1 = floor10((half1 * eduRate) / 100);
      const edu2 = floor10((half2 * eduRate) / 100);
      const total = annual + edu1 + edu2;

      // 1월 연납: 1월 31일까지 내면 2월 1일~12월 31일 일수 × 5% 공제
      const yearDays = isLeap(year) ? 366 : 365;
      const janDays = yearDays - 31;
      const janDeduct = (annual * janDays * d.prepay_pct) / yearDays / 100;
      const janTax = floor10(annual - janDeduct);
      const janEdu = floor10((janTax * eduRate) / 100);
      const janTotal = janTax + janEdu;
      // 6월 연납: 제2기분 세액 × 5% 공제
      const junTax2 = floor10(half2 - (half2 * d.prepay_pct) / 100);
      const junEdu2 = floor10((junTax2 * eduRate) / 100);
      const junTotal = half1 + edu1 + junTax2 + junEdu2;

      const ageText = (n) => (rule.age_relief && n >= d.age.from ? `${Math.min(n, d.age.cap)}년차 (${(Math.min(n, d.age.cap) - 2) * d.age.pct_per_year}% 경감)` : `${n}년차 (경감 없음)`);
      const rows = [
        ['연세액 (경감 전)', u.won(annualBase)],
        ['1기분 차령', rule.age_relief ? ageText(n1) : '차령 경감 대상 아님'],
        ['2기분 차령', rule.age_relief ? ageText(n2) : '차령 경감 대상 아님'],
        ['6월 자동차세 + 지방교육세', `${u.won(half1)} + ${u.won(edu1)} = ${u.won(half1 + edu1)}`],
        ['12월 자동차세 + 지방교육세', `${u.won(half2)} + ${u.won(edu2)} = ${u.won(half2 + edu2)}`],
        ['1년 합계 (나눠 낼 때)', u.won(total)],
        [`1월 연납 (1월 16~31일 납부, ${janDays}/${yearDays} × ${d.prepay_pct}% 공제)`, `${u.won(janTotal)} (${u.won(total - janTotal)} 절약)`],
        [`6월 연납 (12월분을 6월에 함께)`, `${u.won(junTotal)} (${u.won(total - junTotal)} 절약)`],
      ];
      const notes = [
        '지방자치단체가 조례로 세율을 달리 정한 곳은 금액이 다를 수 있어요. 이 계산은 지방세법 표준세율 기준이에요.',
        '연납 금액은 고지 금액과 조금 다를 수 있으니 실제 금액은 연납 고지 금액을 따라요.',
      ];
      if (!rule.edu) notes.push('영업용 승용차 자동차세에는 지방교육세가 붙지 않아요.');
      if (annual <= d.small_annual) notes.push('연세액이 10만 원 이하면 6월 고지 때 1년분을 한꺼번에 부과할 수 있어요 (12월분 5% 공제).');
      if (rule.age_relief && Math.min(n1, n2) < d.age.from) notes.push('차령 3년차(최초 등록 연도를 1년차로 셈)부터 해마다 5%씩 줄어요.');
      if (rule.age_relief && n2 >= d.age.cap) notes.push('차령 12년차부터는 50% 경감이 최대라서 더 줄지 않아요.');
      return {
        values: { annual_base: annualBase, n1, n2, half1, half2, annual, edu1, edu2, total, jan_days: janDays, jan_tax: janTax, jan_edu: janEdu, jan_total: janTotal, jun_tax2: junTax2, jun_edu2: junEdu2, jun_total: junTotal },
        rows,
        notes,
      };
    },
  });
})();
