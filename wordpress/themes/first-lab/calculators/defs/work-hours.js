/* 근무시간 계산기: 출근·퇴근 시각과 휴게시간으로 하루 실근로시간, 그중 야간(오후 10시~오전 6시) 시간,
 * 하루·한 주 법정 근로시간을 넘은 시간(연장근로)을 센다. 기준값은 data/work-hours.json
 * 근거: 근로기준법 제50조(1일 8시간·1주 40시간, 휴게시간 제외), 제53조①(연장 1주 12시간 한도), 제54조①(4시간 30분·8시간 1시간 휴게),
 * 제56조③(야간 = 오후 10시~다음 날 오전 6시), 제69조(15세 이상 18세 미만 1일 7시간·1주 35시간, 연장 1일 1시간·1주 5시간),
 * 대법원 2023. 12. 7. 선고 2020도15393(1주 12시간 한도는 1일 8시간 초과 여부와 관계없이 1주 근로시간 중 40시간을 넘은 시간으로 판단, 8시간을 넘겨 4시간 연장근로하면 그 도중 30분 이상 휴게),
 * 제70조②(18세 미만 야간·휴일근로 금지, 동의+인가 예외), 제11조·시행령 제7조 별표 1(상시 4명 이하 사업장은 제50조·제53조·제56조 미적용, 제54조 적용), 제18조③(1주 소정근로 15시간 미만).
 * 퇴근 시각이 출근 시각보다 이르거나 같으면 다음 날 퇴근으로 본다. 한 주는 같은 근무를 '주 근무일수'만큼 한 것으로 센다. */
(function () {
  'use strict';

  const hm = (min) => {
    const h = Math.round(min) / 60;
    const whole = FirstLabCalc.util.floor(h);
    const rest = Math.round(min) - whole * 60;
    const dec = Math.round(h * 100) / 100;
    return `${whole}시간${rest ? ` ${rest}분` : ''} (${dec.toLocaleString('ko-KR')}시간)`;
  };
  const toHours = (min) => Math.round((min / 60) * 100) / 100;
  const overlap = (a1, a2, b1, b2) => Math.max(0, Math.min(a2, b2) - Math.max(a1, b1));

  FirstLabCalc.register({
    id: 'work-hours',
    title: '근무시간 계산기',
    intro: '출근·퇴근 시각과 쉰 시간을 넣으면 하루 실제 근로시간, 밤 10시~아침 6시 근무 시간, 법정 근로시간을 넘은 연장근로 시간을 세어 줘요.',
    inputs: [
      { name: 'start_h', label: '출근 시각 – 시 (0~23)', type: 'number', min: 0, step: 1, default: 9 },
      { name: 'start_m', label: '출근 시각 – 분 (0~59)', type: 'number', min: 0, step: 1, default: 0 },
      { name: 'end_h', label: '퇴근 시각 – 시 (0~23)', type: 'number', min: 0, step: 1, default: 18, help: '밤을 넘겨 다음 날 퇴근하면 그대로 넣어요 (예: 22시 출근 → 7시 퇴근).' },
      { name: 'end_m', label: '퇴근 시각 – 분 (0~59)', type: 'number', min: 0, step: 1, default: 0 },
      { name: 'break_min', label: '휴게시간 (분)', type: 'number', min: 0, step: 1, default: 60, help: '자유롭게 쉰 시간만 넣어요. 사용자의 지휘·감독 아래 기다린 대기시간은 근로시간이에요.' },
      { name: 'night_break_min', label: '그중 밤 10시~아침 6시에 쉰 시간 (분)', type: 'number', min: 0, step: 1, default: 0 },
      { name: 'days', label: '이렇게 일하는 날 (주, 일)', type: 'number', min: 1, step: 1, default: 5 },
      {
        name: 'size', label: '상시 근로자 수', type: 'select', default: 'five',
        options: [['five', '5명 이상'], ['small', '4명 이하']],
      },
      {
        name: 'age', label: '근로자 나이', type: 'select', default: 'adult',
        options: [['adult', '18세 이상'], ['minor', '15세 이상 18세 미만']],
      },
    ],

    compute(v, d, u) {
      const nums = ['start_h', 'start_m', 'end_h', 'end_m', 'break_min', 'night_break_min', 'days'].map((k) => Number(v[k]));
      const [sh, sm, eh, em, brk, nightBrk, days] = nums;
      if (nums.some((x) => !Number.isInteger(x) || x < 0)) throw new u.InputError('시각·분·일수는 0 이상의 정수로 넣어 주세요.');
      if (sh > 23 || eh > 23) throw new u.InputError('시는 0~23으로 넣어 주세요.');
      if (sm > 59 || em > 59) throw new u.InputError('분은 0~59로 넣어 주세요.');
      if (!(days >= 1 && days <= 7)) throw new u.InputError('주 근무일수는 1~7일로 넣어 주세요.');

      const start = sh * 60 + sm;
      let end = eh * 60 + em;
      if (end <= start) end += 1440; // 다음 날 퇴근
      const span = end - start;
      if (brk >= span) throw new u.InputError('휴게시간이 출근~퇴근 시간보다 길거나 같아요.');
      if (nightBrk > brk) throw new u.InputError('밤에 쉰 시간은 전체 휴게시간보다 클 수 없어요.');

      // 야간 구간: 전날 22시~당일 6시, 당일 22시~다음 날 6시 (분, 출근일 0시 기준)
      const nightSpan = [[-120, 360], [1320, 1800], [2760, 3240]].reduce((s, [a, b]) => s + overlap(start, end, a, b), 0);
      if (nightBrk > nightSpan) throw new u.InputError('밤에 쉰 시간이 출근~퇴근 사이의 밤 10시~아침 6시 시간보다 길어요.');
      if (brk - nightBrk > span - nightSpan) throw new u.InputError('밤 10시~아침 6시 밖에서 쉰 시간이 그 시간대보다 길어요. 그중 밤에 쉰 시간을 늘려 주세요.');
      const work = span - brk;
      const night = nightSpan - nightBrk;

      const minor = v.age === 'minor';
      const dayLimit = (minor ? d.minor_day_hours : d.day_hours) * 60;
      const weekLimit = (minor ? d.minor_week_hours : d.week_hours) * 60;
      const otLimit = (minor ? d.minor_week_overtime : d.week_overtime) * 60;
      const dayOver = Math.max(work - dayLimit, 0);
      const weekly = work * days;
      // 가산 대상 연장근로: 하루 한도를 넘은 시간 + (하루 한도 안의 시간 합이 주 한도를 넘은 시간) — 같은 시간을 두 번 세지 않음
      const weekOver = dayOver * days + Math.max(Math.min(work, dayLimit) * days - weekLimit, 0);
      // 1주 연장 한도 판단: 1일 초과와 관계없이 1주 근로시간 중 주 한도를 넘은 시간 (대법원 2020도15393, 11/7 검수)
      const limitOver = Math.max(weekly - weekLimit, 0);
      // 휴게: 4시간 30분·8시간 1시간 (제54조①), 8시간을 넘겨 4시간 연장할 때마다 30분 더 (대법원 2020도15393, 11/7 검수)
      const b8 = d.break_8h_hours * 60;
      const required = work >= b8 ? d.break_8h_min + d.break_4h_min * FirstLabCalc.util.floor((work - b8) / (d.break_4h_hours * 60))
        : work >= d.break_4h_hours * 60 ? d.break_4h_min : 0;
      // 4명 이하 사업장: 제50·53·56조 미적용, 단 18세 미만 제69조·제70조②는 적용 (시행령 별표 1, 11/7 검수)
      const small = v.size === 'small';
      const exempt = small && !minor;

      const rows = [
        ['출근~퇴근', `${hm(span)}${eh * 60 + em <= start ? ' — 다음 날 퇴근' : ''}`],
        ['하루 실근로시간', `${hm(work)} (휴게 ${brk}분 제외)`],
        ['그중 야간근로 (밤 10시~아침 6시)', hm(night)],
        ['법정 최소 휴게시간', required ? `${required}분 이상` : '없음 (4시간 미만)'],
        [`하루 ${dayLimit / 60}시간을 넘은 시간`, exempt ? `${hm(dayOver)} — 4명 이하 사업장은 법정 근로시간 규정 미적용` : hm(dayOver)],
        ['한 주 실근로시간', `${hm(weekly)} (${days}일)`],
        [`한 주 연장근로 시간 (가산 대상, 주 ${weekLimit / 60}시간 기준)`, small ? `${hm(weekOver)} — 4명 이하 사업장은 가산 미적용` : hm(weekOver)],
        [`주 ${otLimit / 60}시간 한도로 보는 연장 시간 (주 근로 − ${weekLimit / 60}시간)`, exempt ? `${hm(limitOver)} — 4명 이하 사업장은 연장 한도 미적용` : hm(limitOver)],
      ];

      const notes = [];
      if (brk < required) notes.push(`실근로 ${hm(work)}이면 휴게시간을 근로시간 도중에 ${required}분 이상 줘야 해요 (근로기준법 제54조, 4명 이하 사업장도 적용). 지금 넣은 휴게는 ${brk}분이에요.`);
      if (work > b8) notes.push('대법원은 하루 8시간을 넘겨 4시간을 연장근로하게 할 때 그 도중에 30분 이상의 휴게를 더 줘야 한다고 봤어요 (2023. 12. 7. 선고 2020도15393). 그래서 실근로 12시간이면 휴게는 모두 1시간 30분 이상이에요.');
      if (work === d.break_4h_hours * 60 && brk < d.break_4h_min) notes.push('2026년 12월 10일부터는 근로시간이 4시간인 날 근로자가 휴게시간을 쓰지 않겠다고 명시적으로 요청하면 휴게 없이 일할 수 있어요.');
      if (!exempt && limitOver > otLimit) notes.push(`한 주 근로시간 중 ${weekLimit / 60}시간을 넘은 시간이 ${otLimit / 60}시간 한도를 넘어요. ${minor ? '15세 이상 18세 미만은 합의해도 1일 1시간·1주 5시간까지만 연장할 수 있어요 (제69조).' : '당사자가 합의해도 원칙적으로 1주 12시간까지예요 (고용노동부장관 인가를 받은 특별연장근로 등은 예외).'}`);
      if (minor && dayOver > d.minor_day_overtime * 60) notes.push('15세 이상 18세 미만은 하루 연장근로가 1시간을 넘을 수 없어요 (제69조).');
      if (!small && (weekOver > 0 || night > 0)) notes.push('연장근로와 야간근로에는 통상임금의 50% 이상을 더 줘야 해요 (제56조). 금액은 연장근로수당 계산기로 계산해요.');
      if (minor && night > 0) notes.push('18세 미만은 밤 10시~아침 6시와 휴일에 일할 수 없어요. 본인 동의가 있고 고용노동부장관 인가를 받은 경우만 예외예요 (제70조②, 4명 이하 사업장도 적용).');
      if (small && minor) notes.push('18세 미만의 하루 7시간·한 주 35시간 기준과 연장 한도(제69조)는 상시 4명 이하 사업장에도 적용돼요 (근로기준법 시행령 별표 1). 연장·야간 가산은 적용되지 않아요.');
      else if (small) notes.push('상시 4명 이하 사업장은 1일 8시간·1주 40시간, 연장 1주 12시간 한도, 연장·야간 가산 규정이 적용되지 않아요 (근로기준법 시행령 별표 1). 휴게시간 규정은 적용돼요.');
      if (weekly < d.weekly_15_hours * 60) notes.push('1주 소정근로시간이 15시간 미만(4주 평균)이면 주휴일과 연차휴가 규정이 적용되지 않아요 (제18조③).');
      notes.push('한 주 시간은 같은 근무를 입력한 일수만큼 했다고 보고 센 값이에요. 날마다 다르면 하루씩 계산해 더해요. 휴일에 일한 시간은 가산수당에서는 휴일근로로 따로 계산하지만(제56조②), 1주 12시간 한도에는 들어가요(\'1주\'는 휴일 포함 7일, 제2조).');
      if (days === 7) notes.push('7일을 모두 일했다면 그중 휴일에 일한 시간은 가산수당에서 연장이 아니라 휴일근로로 계산해요. 가산 대상 연장 시간은 휴일을 뺀 일수로 다시 계산해 보세요.');

      return {
        values: {
          span_hours: toHours(span), work_hours: toHours(work), night_hours: toHours(night), required_break_min: required,
          break_short: brk < required, day_over_hours: toHours(dayOver), week_hours: toHours(weekly), week_over_hours: toHours(weekOver),
          limit_over_hours: toHours(limitOver), over_limit: !exempt && limitOver > otLimit,
        },
        rows,
        notes,
      };
    },
  });
})();
