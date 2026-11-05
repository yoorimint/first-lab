/* 퇴직금 계산기: 1일 평균임금(3개월 임금 + 연간 상여금·연차수당의 3/12, ÷ 3개월 일수) × 30일 × 재직일수/365. 기준은 data/severance-pay.json */
(function () {
  'use strict';

  /** 퇴직일의 '3개월 전 같은 날' (그 달에 같은 날이 없으면 다음 달 1일) */
  function threeMonthsBefore(t, u) {
    const d = new Date(t);
    let y = d.getUTCFullYear();
    let m = d.getUTCMonth() - 3;
    if (m < 0) { m += 12; y -= 1; }
    const day = d.getUTCDate();
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return day <= last ? Date.UTC(y, m, day) : Date.UTC(y, m + 1, 1);
  }

  /** 입사일 1년 뒤 같은 날 (2월 29일 입사는 다음 해 3월 1일) */
  function oneYearAfter(t) {
    const d = new Date(t);
    const y = d.getUTCFullYear() + 1;
    const m = d.getUTCMonth();
    const day = d.getUTCDate();
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return day <= last ? Date.UTC(y, m, day) : Date.UTC(y, m + 1, 1);
  }

  FirstLabCalc.register({
    id: 'severance-pay',
    title: '퇴직금 계산기',
    intro: '입사일, 퇴직일과 퇴직 전 3개월 임금을 넣으면 1일 평균임금과 법정 퇴직금(세전)을 계산해요.',
    inputs: [
      { name: 'start', label: '입사일', type: 'date', default: '2023-03-02' },
      { name: 'end', label: '퇴직일 (마지막 근무일 다음 날)', type: 'date', default: 'today', help: '마지막으로 일한 날이 10월 31일이면 11월 1일을 넣어요.' },
      { name: 'wage_3m', label: '퇴직 전 3개월 임금 합계 (세전, 원)', type: 'number', min: 0, step: 1, default: 9000000, help: '기본급과 매달 받는 수당(식대·직책수당 등)을 모두 더해요. 상여금은 아래 칸에 따로 넣어요.' },
      { name: 'bonus_year', label: '퇴직 전 1년 동안 받은 상여금 (원)', type: 'number', min: 0, step: 1, default: 0, help: '없으면 0. 이 금액의 3/12만 평균임금에 더해져요.' },
      { name: 'leave_pay', label: '연차수당 (원)', type: 'number', min: 0, step: 1, default: 0, help: '퇴직 전 1년 동안 받은 연차수당이에요. 없으면 0. 3/12만 더해져요.' },
      { name: 'ordinary_daily', label: '1일 통상임금 (원, 모르면 0)', type: 'number', min: 0, step: 1, default: 0, help: '평균임금보다 크면 이 금액으로 계산해요.' },
    ],

    compute(v, d, u) {
      const start = u.parseDate(v.start);
      const end = u.parseDate(v.end);
      if (start === null || end === null) throw new u.InputError('입사일과 퇴직일을 날짜로 넣어 주세요.');
      if (end <= start) throw new u.InputError('퇴직일은 입사일보다 뒤여야 해요.');
      const wage = Number(v.wage_3m);
      const bonus = Number(v.bonus_year || 0);
      const leave = Number(v.leave_pay || 0);
      const ordinary = Number(v.ordinary_daily || 0);
      if (!(wage >= 0) || !(bonus >= 0) || !(leave >= 0) || !(ordinary >= 0)) throw new u.InputError('금액은 0 이상 숫자로 넣어 주세요.');

      const tenure = u.daysBetween(start, end); // 재직일수 = 퇴직일 - 입사일
      const periodStart = Math.max(threeMonthsBefore(end, u), start);
      const days3m = u.daysBetween(periodStart, end); // 퇴직일 전날까지의 3개월 일수
      const total = wage + bonus * d.bonus_share + leave * d.bonus_share;
      const avgExact = total / days3m;
      const useOrdinary = ordinary > avgExact;
      const daily = useOrdinary ? ordinary : avgExact;
      const eligible = end >= oneYearAfter(start);
      const pay = eligible ? FirstLabCalc.util.floor(daily * d.days_per_year * tenure / 365) : 0;

      const rows = [
        ['재직일수', `${tenure.toLocaleString('ko-KR')}일`],
        ['평균임금 계산 기간', `${u.korDate(periodStart)} ~ ${u.korDate(u.addDays(end, -1))} (${days3m}일)`],
        ['3개월 임금 + 상여금·연차수당 3/12', u.won(total)],
        ['1일 평균임금', u.won(FirstLabCalc.util.floor(avgExact))],
      ];
      if (useOrdinary) rows.push(['1일 통상임금 (평균임금보다 커서 이 금액 적용)', u.won(ordinary)]);
      rows.push(['퇴직금 (세전)', eligible ? u.won(pay) : '계속근로 1년 미만이라 법정 퇴직금 대상이 아니에요']);

      const notes = [
        '퇴직금 = 1일 평균임금 × 30일 × 재직일수 ÷ 365. 원 미만은 버려서 고용노동부 계산기(평균임금 소수 둘째 자리 올림)와 1원 안팎 차이가 날 수 있어요.',
        '4주 평균 주 15시간 미만으로 일했다면 법정 퇴직금 대상이 아니에요.',
        '육아휴직·출산전후휴가·수습 3개월 등 평균임금에서 빼는 기간이 있으면 실제 금액이 달라질 수 있어요.',
        '세금(퇴직소득세)을 떼기 전 금액이에요.',
      ];
      return {
        values: { tenure_days: tenure, days_3m: days3m, wage_total: total, avg_daily: FirstLabCalc.util.floor(avgExact), eligible, severance: pay },
        rows,
        notes,
      };
    },
  });
})();
