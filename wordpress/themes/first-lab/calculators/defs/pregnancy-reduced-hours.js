/* 임산부 단축근무(임신기 근로시간 단축) 계산기: 출산예정일 → 쓸 수 있는 기간, 하루 근로시간 → 단축 뒤 시간, 시작 희망일 → 신청 마감일.
 * 기간: 근로기준법 제74조⑦ "임신 후 12주 이내 또는 32주 이후", 날짜 환산은 고용노동부 1350 상담 답변(2026.7.28·8.19) 기준
 *   12주 이내 = 임신 후 84일까지(진단서 12주 0일), 32주 이후 = 임신 후 218일부터(31주 1일), 출산전후휴가 시작 전날까지.
 *   임신 0주 0일 = 출산예정일 − 280일 (진단서 주수는 0주 0일부터 셈).
 * 위험 질환(시행규칙 제12조의3·별표 3) 진단 뒤에는 임신 전 기간. 8시간 미만 근로자는 6시간이 되도록 허용할 수 있음(제74조⑦ 단서).
 * 신청: 단축 개시 예정일 3일 전까지 (시행령 제43조의2) = 사이에 꼬박 3일이 남게 시작일 4일 앞 날까지 (1350 2026.10.21 답변 '9.30. 개시면 9.26.에는 신청'). 기준값은 data/pregnancy-reduced-hours.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'pregnancy-reduced-hours',
    title: '임산부 단축근무 계산기',
    intro: '출산예정일을 넣으면 임신기 근로시간 단축을 쓸 수 있는 날짜와 단축 뒤 근무시간, 신청서 마감일을 알려 드려요.',
    inputs: [
      { name: 'due', label: '출산예정일', type: 'date', default: '2027-06-01', help: '산모수첩이나 진단서에 적힌 날짜를 넣어요.' },
      { name: 'hours', label: '지금 하루 근로시간', type: 'number', default: 8, min: 1, step: 0.5, help: '휴게시간을 뺀 시간이에요.' },
      { name: 'risk', label: '유산·조산 위험 질환 진단', type: 'choice', options: [['no', '없음'], ['yes', '진단받음']], default: 'no',
        help: '근로기준법 시행규칙 별표 3의 질환(조기진통·전치태반·다태임신 등)이에요.' },
      { name: 'diag', label: '진단받은 날', type: 'date', default: '2026-12-01', show: { risk: ['yes'] } },
      { name: 'start', label: '단축 시작 희망일 (선택)', type: 'date', default: '', help: '넣으면 그날 쓸 수 있는지와 신청 마감일이 나와요.' },
      { name: 'leave', label: '출산휴가 시작일 (선택)', type: 'date', default: '', help: '32주 이후 단축은 출산휴가 시작 전날까지예요.' },
    ],

    compute(v, d, u) {
      const due = u.parseDate(v.due);
      if (due === null) throw new u.InputError('출산예정일을 날짜로 넣어 주세요.');
      const hours = Number(v.hours);
      if (!(hours > 0)) throw new u.InputError('하루 근로시간을 숫자로 넣어 주세요.');
      if (hours > d.daily_max) throw new u.InputError(`하루 근로시간은 ${d.daily_max}시간까지 넣어 주세요.`);

      const day0 = u.addDays(due, -d.days_to_due); // 임신 0주 0일
      const earlyEnd = u.addDays(day0, d.early_last_day);
      const lateStart = u.addDays(day0, d.late_first_day);
      const leave = v.leave ? u.parseDate(v.leave) : null;
      if (v.leave && leave === null) throw new u.InputError('출산휴가 시작일을 날짜로 넣어 주세요.');
      const lateEnd = leave !== null ? u.addDays(leave, -1) : null;
      if (leave !== null && leave <= day0) throw new u.InputError('출산휴가 시작일이 임신 기간보다 앞이에요. 날짜를 다시 확인해 주세요.');
      const risk = v.risk === 'yes';
      const diag = risk ? u.parseDate(v.diag) : null;
      if (risk && diag === null) throw new u.InputError('위험 질환을 진단받은 날을 넣어 주세요.');
      if (risk && (diag < day0 || diag > due)) throw new u.InputError('진단받은 날이 임신 기간 밖이에요. 출산예정일을 다시 확인해 주세요.');
      const week = (t) => {
        const n = u.daysBetween(day0, t);
        return `${FirstLabCalc.util.floor(n / 7)}주 ${n % 7}일`;
      };

      const mustAllow = hours >= d.daily_max;
      const after = mustAllow ? hours - d.cut_hours : Math.min(hours, d.floor_hours);
      const canCut = hours > d.floor_hours;
      const fmtH = (h) => `${h.toLocaleString('ko-KR')}시간`;

      const rows = [
        ['임신 0주 0일 (출산예정일 − 280일)', u.korDate(day0)],
        ['초기 단축: 끝나는 날', `${u.korDate(earlyEnd)}까지 (12주 0일)`],
        ['후기 단축: 시작하는 날', `${u.korDate(lateStart)}부터 (31주 1일)`],
        ['후기 단축: 끝나는 날', lateEnd !== null ? `${u.korDate(lateEnd)}까지 (출산휴가 전날)` : '출산휴가 시작 전날까지'],
      ];
      if (risk) {
        const from = diag > earlyEnd ? diag : u.addDays(earlyEnd, 1);
        rows.push(['위험 질환 진단 뒤', diag < lateStart ? `${u.korDate(from)}부터 중간 기간도 쓸 수 있어요` : '진단일이 이미 후기 기간이에요']);
      }
      rows.push(['단축 뒤 하루 근로시간', mustAllow ? `${fmtH(after)} (2시간 단축, 회사는 허용해야 해요)`
        : canCut ? `${fmtH(after)}까지 (회사가 허용할 수 있어요)` : `${fmtH(hours)} 그대로 (6시간 이하는 단축 의무 없음)`]);
      rows.push(['임금', '줄어든 시간만큼 깎을 수 없어요']);

      let startOk = null;
      let applyBy = '';
      if (v.start) {
        const start = u.parseDate(v.start);
        if (start === null) throw new u.InputError('단축 시작 희망일을 날짜로 넣어 주세요.');
        if (start < day0) throw new u.InputError('시작 희망일이 임신 0주 0일보다 앞이에요. 출산예정일을 다시 확인해 주세요.');
        const inLate = start >= lateStart && (lateEnd === null || start <= lateEnd);
        const inRisk = risk && start >= diag && (lateEnd === null || start <= lateEnd);
        startOk = (start >= day0 && start <= earlyEnd) || inLate || inRisk;
        const by = u.addDays(start, -(d.apply_days_before + 1)); // 3일 전까지 = 시작일을 빼고 꼬박 3일 앞 (민법 제157조 초일 불산입, 1350: 9.30 시작 → 9.26 제출)
        applyBy = u.isoDate(by);
        rows.push(['시작 희망일', `${u.korDate(start)} (${week(start)}) — ${startOk ? '쓸 수 있는 날이에요' : '쓸 수 있는 기간이 아니에요'}`]);
        rows.push(['신청서 마감', `${u.korDate(by)}까지 회사에 내기`]);
      }

      const notes = [
        '날짜는 출산예정일로 센 값이에요. 회사에 낼 때는 진단서에 적힌 임신 주수를 기준으로 해요.',
        '신청서에는 임신 기간, 시작·끝 예정일, 출근·퇴근 시각을 적고 의사 진단서를 붙여요. 같은 임신으로 다시 신청할 때는 진단서를 빼도 돼요.',
        '12주 이내에 썼어도 32주가 지나면 다시 쓸 수 있어요. 쓰지 못한 기간은 나중에 몰아 쓸 수 없어요.',
      ];
      if (!mustAllow && canCut) notes.unshift('하루 8시간 미만 근로자는 6시간이 되도록 단축을 허용받을 수 있어요. 회사와 시간을 정해요.');
      if (risk) notes.push('위험 질환은 진단받은 날부터 출산휴가 시작 전날까지 임신 전 기간에 단축을 쓸 수 있어요.');

      return {
        values: {
          day0: u.isoDate(day0), early_end: u.isoDate(earlyEnd), late_start: u.isoDate(lateStart),
          late_end: lateEnd !== null ? u.isoDate(lateEnd) : '', after_hours: canCut || mustAllow ? after : hours,
          must_allow: mustAllow, start_ok: startOk, apply_by: applyBy,
        },
        rows,
        notes,
      };
    },
  });
})();
