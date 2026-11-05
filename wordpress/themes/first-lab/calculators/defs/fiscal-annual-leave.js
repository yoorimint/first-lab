/* 회계연도 연차 계산기: 입사일·기준일 → 회계연도(1월 1일) 기준과 입사일 기준 연차를 나란히 비교. 기준값은 data/fiscal-annual-leave.json
 * 근거: 근로기준법 제60조①②(1년 80% 이상 출근 15일, 1년 미만 1개월 개근 1일)
 *   고용노동부 질의회시(근기68207-620 등): 회계연도 기준이면 연도 중 입사자에게 기본휴가 15일에 비례해 다음 회계연도 초일에 부여,
 *   퇴직 시 입사일 기준 일수에 모자라면 그 일수를 수당으로 정산 (2004 질의회신). 비례 일수 = 15일 × 입사 연도 재직일수 ÷ 365 (역일 추산 예시)
 * 범위: 입사 후 둘째 1월 1일(첫 15일)까지. 3년 이상 가산휴가는 회사 규정에 따라 시점이 달라 계산하지 않음. */
(function () {
  'use strict';

  /** 입사일 + k개월의 같은 날짜. 그 달에 그 날짜가 없으면 다음 달 1일 (annual-leave 와 같은 규칙) */
  function plusMonths(t, k) {
    const d = new Date(t);
    const y = d.getUTCFullYear();
    const m = d.getUTCMonth() + k;
    const day = d.getUTCDate();
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return day <= last ? Date.UTC(y, m, day) : Date.UTC(y, m + 1, 1);
  }
  const r1 = (n) => Math.round(n * 10) / 10;
  const fmt = (n) => `${r1(n).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}일`;

  FirstLabCalc.register({
    id: 'fiscal-annual-leave',
    title: '회계연도 연차 계산기',
    intro: '입사일과 기준일을 넣으면 회계연도(1월 1일) 기준으로 받는 연차와 입사일 기준 연차를 나란히 비교해요.',
    inputs: [
      { name: 'start', label: '입사일', type: 'date', default: '2025-07-01' },
      { name: 'base', label: '기준일 (오늘, 퇴사한다면 근로관계가 끝나는 마지막 재직일)', type: 'date', default: 'today', help: '입사 다음다음 해 12월 31일까지 넣을 수 있어요 (둘째 1월 1일 연차까지 비교).' },
    ],

    compute(v, d, u) {
      const start = u.parseDate(v.start);
      const base = u.parseDate(v.base === 'today' ? u.todayIso() : v.base);
      if (start === null) throw new u.InputError('입사일을 넣어 주세요.');
      if (base === null) throw new u.InputError('기준일을 넣어 주세요.');
      if (base < start) throw new u.InputError('기준일이 입사일보다 앞이에요.');
      const y = new Date(start).getUTCFullYear();
      const jan1 = (k) => Date.UTC(y + k, 0, 1);
      if (base >= jan1(3)) throw new u.InputError(`이 계산기는 입사 후 둘째 1월 1일 연차까지 비교해요. 기준일을 ${y + 2}년 12월 31일 이전으로 넣어 주세요.`);

      let months = 0;
      while (plusMonths(start, months + 1) <= base) months += 1;
      const monthly = Math.min(months, d.first_year_max);

      const workedDays = u.daysBetween(start, jan1(1)); // 입사일~그해 12월 31일 (양 끝 포함)
      const prorated = Math.min(d.base_days, r1(d.base_days * workedDays / d.year_days));
      const gotProrated = base >= jan1(1);
      const gotFirstFull = base >= jan1(2);
      const fiscalTotal = r1(monthly + (gotProrated ? prorated : 0) + (gotFirstFull ? d.base_days : 0));

      const ann1 = plusMonths(start, 12);
      const ann2 = plusMonths(start, 24);
      const hireTotal = monthly + (base >= ann1 ? d.base_days : 0) + (base >= ann2 ? d.base_days : 0);
      const diff = r1(hireTotal - fiscalTotal);

      const rows = [
        ['1년 미만 1개월 개근마다 생긴 연차 (두 방식 같음)', `${monthly}일`],
        [`${y + 1}년 1월 1일 비례 연차`, `${fmt(prorated)} (15일 × ${workedDays}일 ÷ ${d.year_days}일)${gotProrated ? '' : ' · 아직 안 생김'}`],
        [`${y + 2}년 1월 1일 연차`, `${d.base_days}일${gotFirstFull ? '' : ' · 아직 안 생김'}`],
        ['회계연도 기준 합계 (기준일까지)', fmt(fiscalTotal)],
        ['입사일 기준 합계 (기준일까지)', `${hireTotal}일 (1년 되는 ${u.korDate(ann1)}에 15일, 2년 되는 ${u.korDate(ann2)}에 15일)`],
        ['차이', diff > 0 ? `회계연도 기준이 ${fmt(diff)} 적어요` : diff < 0 ? `회계연도 기준이 ${fmt(-diff)} 많아요` : '같아요'],
      ];
      const notes = [
        diff > 0
          ? `이 날짜에 퇴사한다면 입사일 기준보다 모자란 ${fmt(diff)}은 연차수당으로 정산받을 수 있어요.`
          : '회계연도 기준이 더 많으면, 취업규칙에 "퇴직 때 입사일 기준으로 다시 계산한다"는 규정이 없는 한 회계연도 기준대로 적용돼요.',
        '비례 연차는 입사한 해 재직일수를 365일로 나눈 추산이에요. 원래 식은 15일 × 출근일수 ÷ 연간 소정근로일수라 회사 계산과 조금 다를 수 있고, 소수점 처리도 취업규칙을 따라요.',
        '매달 개근하고 80% 이상 출근했다고 보고 계산했어요. 상시 5명 이상 사업장, 1주 소정근로시간 15시간 이상인 근로자 기준이에요.',
      ];
      return { values: { months, monthly, worked_days: workedDays, prorated, fiscal_total: fiscalTotal, hire_total: hireTotal, diff }, rows, notes };
    },
  });
})();
