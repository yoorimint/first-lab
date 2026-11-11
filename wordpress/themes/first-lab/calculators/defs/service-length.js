/* 근속연수 계산기: 입사일·마지막 근무일(둘 다 포함) → 근속 기간(○년 ○개월 ○일)·재직일수,
 * 퇴직금 대상 여부(계속근로 1년 이상, 근로자퇴직급여 보장법 제4조①), 퇴직소득세 근속연수(1년 미만 기간은 1년, 소득세법 제48조①)와
 * 근속연수공제(같은 조 제1호 표), 입사일 기준 연차(근로기준법 제60조①④: 15일 + 최초 1년 넘는 매 2년 1일, 25일 한도).
 * 기간 끝: 그 달에 같은 날이 없으면 다음 달 1일 (민법 제160조③, annual-leave.js 와 같음). 기준값은 data/service-length.json */
(function () {
  'use strict';

  /** t + k개월의 같은 날짜. 그 달에 그 날짜가 없으면 다음 달 1일 */
  function plusMonths(t, k) {
    const d = new Date(t);
    const y = d.getUTCFullYear();
    const m = d.getUTCMonth() + k;
    const day = d.getUTCDate();
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return day <= last ? Date.UTC(y, m, day) : Date.UTC(y, m + 1, 1);
  }

  FirstLabCalc.register({
    id: 'service-length',
    title: '근속연수 계산기',
    intro: '입사일과 마지막 근무일을 넣으면 근속 기간, 퇴직금 대상 여부, 퇴직소득세 근속연수와 근속연수공제를 계산해요.',
    inputs: [
      { name: 'start', label: '입사일 (첫 근무일)', type: 'date', default: '2023-08-21' },
      { name: 'last', label: '마지막 근무일 (재직 중이면 오늘)', type: 'date', default: 'today', help: '근로관계가 끝나는 마지막 날을 넣어요. 입사일과 이 날을 모두 근속에 넣어 세요.' },
    ],

    compute(v, d, u) {
      const start = u.parseDate(v.start);
      const last = u.parseDate(v.last === 'today' ? u.todayIso() : v.last);
      if (start === null || last === null) throw new u.InputError('입사일과 마지막 근무일을 날짜로 넣어 주세요.');
      if (last < start) throw new u.InputError('마지막 근무일이 입사일보다 앞이에요.');

      const endExcl = u.addDays(last, 1); // 마지막 근무일 다음 날
      let k = 0; // 다 채운 개월 수
      while (plusMonths(start, k + 1) <= endExcl) k += 1;
      const years = FirstLabCalc.util.floor(k / 12);
      const months = k % 12;
      const days = u.daysBetween(plusMonths(start, k), endExcl);
      const totalDays = u.daysBetween(start, endExcl);
      const eligible = k >= 12;

      const taxYears = Math.max(1, years + (months > 0 || days > 0 ? 1 : 0));
      const b = d.deduction.find((r) => taxYears <= r.upto) || d.deduction[d.deduction.length - 1];
      const deduction = b.base + b.per * (taxYears - b.over);

      let leaveYears = 0; // 연차가 생긴 해 수 (입사 기념일까지 재직)
      while (plusMonths(start, (leaveYears + 1) * 12) <= last) leaveYears += 1;
      const leave = leaveYears >= 1 ? Math.min(d.leave_max, d.leave_base + FirstLabCalc.util.floor((leaveYears - 1) / 2)) : 0;

      const rows = [
        ['근속 기간', `${years}년 ${months}개월 ${days}일`],
        ['재직일수 (입사일·마지막 근무일 포함)', `${totalDays.toLocaleString('ko-KR')}일`],
        ['퇴직금', eligible ? '계속근로 1년 이상이라 대상이에요 (주 15시간 이상일 때)' : '계속근로 1년 미만이라 법정 퇴직금 대상이 아니에요'],
        ['퇴직소득세 근속연수 (1년 미만은 1년)', `${taxYears}년`],
        ['근속연수공제', u.won(deduction)],
        ['올해 연차 (입사일 기준, 80% 이상 출근 시)', leaveYears >= 1 ? `${leave}일` : '1년 미만: 1개월 개근마다 1일 (최대 11일)'],
      ];
      const notes = [
        '육아휴직 기간은 근속기간에 들어가요. 회사 규정상 퇴직금 계산에서 빠지는 휴직 기간이 있으면 그만큼 짧아질 수 있어요.',
        '퇴직소득세 근속연수는 중간정산을 받았다면 중간정산 다음 날부터 세요. 퇴직소득금액이 근속연수공제보다 적으면 그 금액까지만 공제돼요.',
        '연차는 상시 5명 이상 사업장, 1주 소정근로시간 15시간 이상 기준이에요.',
      ];
      if (!eligible && k === 11) notes.unshift(`1년을 채우려면 ${u.korDate(u.addDays(plusMonths(start, 12), -1))}까지 근무해야 해요.`);
      return {
        values: { years, months, days, total_days: totalDays, eligible, tax_years: taxYears, deduction, leave },
        rows,
        notes,
      };
    },
  });
})();
