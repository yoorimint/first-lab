/* 보험나이 계산기: 생년월일·가입(계약)일 → 만 나이, 마지막 생일 뒤 지난 개월 수, 보험나이,
 * 보험나이가 1세 오르는 날(상령일)과 그 전날.
 * 보험나이 = 계약일 현재 만 나이에서 6개월 미만 끝수는 버리고 6개월 이상은 1년으로 올림, 이후 매년 계약해당일에 1세 증가
 * (생명보험·질병상해보험·실손보험 표준약관, 금융감독원 2023-01-27 보도참고자료 '보험나이' 안내).
 * 만 나이는 출생일을 넣어 계산 (민법 제158조). 개월 끝: 그 달에 같은 날이 없으면 다음 달 1일 (민법 제160조③, service-length.js 와 같음).
 * 금액 계산은 없음 (data/insurance-age.json) */
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
    id: 'insurance-age',
    title: '보험나이 계산기',
    intro: '생년월일과 가입할 날을 넣으면 그날의 보험나이와 보험나이가 한 살 오르는 날(상령일)을 알려 줘요.',
    inputs: [
      { name: 'birth', label: '생년월일', type: 'date', default: '1990-05-20' },
      { name: 'contract', label: '가입할 날 (계약일)', type: 'date', default: 'today', help: '보험나이는 계약일 기준이에요. 오늘 가입하면 오늘 날짜 그대로 두면 돼요.' },
    ],

    compute(v, d, u) {
      const birth = u.parseDate(v.birth);
      const day = u.parseDate(v.contract === 'today' ? u.todayIso() : v.contract);
      if (birth === null || day === null) throw new u.InputError('생년월일과 가입할 날을 날짜로 넣어 주세요.');
      if (day < birth) throw new u.InputError('가입할 날이 생년월일보다 앞이에요.');

      let k = 0; // 태어난 뒤 다 채운 개월 수
      while (plusMonths(birth, k + 1) <= day) k += 1;
      const manAge = FirstLabCalc.util.floor(k / 12);
      const months = k % 12;
      const insAge = manAge + (months >= d.round_up_months ? 1 : 0);
      const next = plusMonths(birth, 12 * (insAge) + d.round_up_months); // 보험나이가 insAge+1 이 되는 날
      const lastDay = u.addDays(next, -1);
      const daysLeft = u.daysBetween(day, next);

      const rows = [
        ['만 나이', `${manAge}세 ${months}개월`],
        ['보험나이', `${insAge}세`],
        ['다음 상령일 (보험나이 +1세)', `${u.korDate(next)} (${daysLeft}일 남음)`],
        [`보험나이 ${insAge}세로 가입할 수 있는 마지막 날`, u.korDate(lastDay)],
      ];
      const notes = [
        months >= d.round_up_months
          ? '마지막 생일에서 6개월이 지나 만 나이보다 1세 많게 쳐요.'
          : '마지막 생일에서 6개월이 안 지나 만 나이 그대로예요.',
        '가입한 뒤에는 생일이 아니라 매년 계약한 날(계약해당일)에 보험나이가 1세 늘어요.',
        '약관에서 만 나이로 정한 상품이나 법에서 나이를 정한 경우(15세 미만 사망보험 등)는 보험나이를 쓰지 않아요.',
      ];
      if (daysLeft <= 30) notes.unshift(`${daysLeft}일 뒤면 보험나이가 ${insAge + 1}세가 돼요. 가입을 앞두고 있다면 날짜를 먼저 확인해 보세요.`);
      return {
        values: { man_age: manAge, months, ins_age: insAge, next_change: u.isoDate(next), days_left: daysLeft },
        rows,
        notes,
      };
    },
  });
})();
