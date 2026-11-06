/* 근무일수 계산기: 시작일~종료일(둘 다 포함)의 달력 일수, 근무 요일 수, 그중 공휴일, 공휴일을 뺀 근무일수.
   공휴일은 data/work-days.json (관공서의 공휴일에 관한 규정 제2·3조, 월력요항). 목록 기간 밖의 공휴일은 빼지 못해 안내한다. */
(function () {
  'use strict';

  const NAMES = ['일', '월', '화', '수', '목', '금', '토'];

  FirstLabCalc.register({
    id: 'work-days',
    title: '근무일수 계산기',
    intro: '시작일과 마지막 날을 넣으면 그 기간의 달력 일수와, 주말·공휴일을 뺀 근무일수를 세어 줘요. 시작일과 마지막 날은 둘 다 셈에 들어가요.',
    inputs: [
      { name: 'start', label: '시작일 (입사일·첫 근무일)', type: 'date', default: '2026-01-01' },
      { name: 'end', label: '마지막 날 (퇴사일이면 마지막 근무일)', type: 'date', default: 'today' },
      {
        name: 'pattern', label: '일하는 요일', type: 'select', default: 'mon-fri',
        options: [['mon-fri', '월~금 (주 5일)'], ['mon-sat', '월~토 (주 6일)']],
      },
      {
        name: 'holidays', label: '공휴일', type: 'select', default: 'skip',
        options: [['skip', '공휴일은 쉬는 날로 빼기'], ['keep', '공휴일도 일하는 날로 세기']],
        help: '빼는 공휴일은 2026~2027년 관공서 공휴일·대체공휴일이에요. 회사가 따로 정한 휴무일은 빠지지 않아요.',
      },
    ],

    compute(v, d, u) {
      const start = u.parseDate(v.start);
      const end = u.parseDate(v.end);
      if (start === null) throw new u.InputError('시작일을 넣어 주세요.');
      if (end === null) throw new u.InputError('마지막 날을 넣어 주세요.');
      if (end < start) throw new u.InputError('마지막 날이 시작일보다 앞이에요.');
      const calendarDays = u.daysBetween(start, end) + 1;
      if (calendarDays > d.max_days) throw new u.InputError('기간은 10년(3,660일) 안에서 넣어 주세요.');
      const pattern = v.pattern || 'mon-fri';
      if (!['mon-fri', 'mon-sat'].includes(pattern)) throw new u.InputError('일하는 요일을 골라 주세요.');
      const skipHolidays = (v.holidays || 'skip') === 'skip';

      let workdays = 0;
      let saturdays = 0;
      let sundays = 0;
      let holidayHits = 0;
      const hitNames = [];
      for (let i = 0; i < calendarDays; i += 1) {
        const t = u.addDays(start, i);
        const dow = new Date(t).getUTCDay();
        if (dow === 0) { sundays += 1; continue; }
        if (dow === 6) saturdays += 1;
        if (dow === 6 && pattern === 'mon-fri') continue;
        workdays += 1;
        const name = d.holidays[u.isoDate(t)];
        if (name) {
          holidayHits += 1;
          if (hitNames.length < 30) hitNames.push(`${u.isoDate(t).slice(5).replace('-', '/')}(${NAMES[dow]}) ${name}`);
        }
      }
      const workDays = skipHolidays ? workdays - holidayHits : workdays;
      const outOfRange = u.isoDate(start) < d.holiday_from || u.isoDate(end) > d.holiday_to;

      const label = pattern === 'mon-fri' ? '월~금' : '월~토';
      const rows = [
        ['달력 일수 (시작일·마지막 날 포함)', `${calendarDays.toLocaleString('ko-KR')}일`],
        ['토요일 · 일요일', `${saturdays}일 · ${sundays}일`],
        [`${label} 날 수`, `${workdays.toLocaleString('ko-KR')}일`],
        [`그중 공휴일 (${label}에 겹친 날)`, `${holidayHits}일`],
        [skipHolidays ? '근무일수 (공휴일 뺌)' : '근무일수 (공휴일 포함)', `${workDays.toLocaleString('ko-KR')}일`],
      ];
      if (hitNames.length) rows.push(['겹친 공휴일', hitNames.join(', ') + (holidayHits > hitNames.length ? ' 등' : '')]);

      const notes = [];
      if (outOfRange) notes.push('2026년 1월 1일 이전·2027년 12월 31일 이후의 공휴일은 목록에 없어 빼지 못했어요. 그 기간의 공휴일은 달력으로 따로 빼 주세요.');
      notes.push('일요일과 겹친 공휴일은 이미 쉬는 날이라 한 번만 빠져요. 대체공휴일은 목록에 넣어 계산했어요.');
      if (pattern === 'mon-fri') notes.push('토요일과 겹친 공휴일은 주 5일 근무에서 원래 쉬는 날이라 그날로는 근무일수가 줄지 않아요. 노동절·제헌절처럼 대체공휴일이 붙는 공휴일은 그다음 평일이 빠져요.');
      notes.push('회사 창립기념일·하계휴가처럼 회사가 정한 휴무일, 연차·결근은 빠지지 않아요. 실제 출근한 날은 근무표로 맞춰 봐요.');

      return {
        values: { calendarDays, saturdays, sundays, workdays, holidayHits, workDays, outOfRange },
        rows,
        notes,
      };
    },
  });
})();
