/* 대구공항 주차요금 계산기: 차종·입차·출차 일시·할인 → 예상 주차요금.
 * 요금표·할인율은 data/daegu-airport-parking.json (한국공항공사 대구국제공항 주차요금 안내)
 * 규칙: 입차 시각부터 24시간 단위로 나눠, 각 24시간 구간은 그 구간이 시작하는 날의 요일 요금(월~목 / 금~일)을 쓴다.
 *   첫 구간 = 30분까지 기본요금, 넘으면 기본요금 + 넘은 시간을 단위 시간(소형 15분·대형 10분)마다 올림해 단위요금.
 *   둘째 구간부터 = 단위 시간마다 단위요금(기본요금 없음). 구간마다 1일 최대요금까지. 할인은 합계에 할인율 적용.
 *   (공사 예상 주차요금 조회 결과와 대조: tests/daegu-airport-parking.json)
 * 법정공휴일은 금~일 요금이지만 날짜 목록이 해마다 달라 계산하지 않고 안내 문장으로 알린다. */
(function () {
  'use strict';

  const TIMES = [];
  for (let h = 0; h < 24; h += 1) {
    for (let m = 0; m < 60; m += 5) {
      const t = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
      TIMES.push([t, t]);
    }
  }
  const DAY_MIN = 1440;
  const WEEKDAY_KO = ['일', '월', '화', '수', '목', '금', '토'];

  function toMinutes(u, date, time) {
    const t = u.parseDate(date);
    const m = /^(\d{2}):(\d{2})$/.exec(String(time || ''));
    if (t === null || !m) return null;
    return t / 60000 + Number(m[1]) * 60 + Number(m[2]);
  }

  FirstLabCalc.register({
    id: 'daegu-airport-parking',
    title: '대구공항 주차요금 계산기',
    intro: '차종과 입차·출차 일시를 넣으면 대구공항 공식 요금표로 예상 주차요금을 계산해요.',
    inputs: [
      { name: 'size', label: '차종', type: 'select', default: 'small', options: [['small', '소형 (승용차 전부, 15인승 이하 승합 등)'], ['large', '대형 (16인승 이상 승합 등)']] },
      { name: 'in_date', label: '입차 날짜', type: 'date', default: 'today' },
      { name: 'in_time', label: '입차 시각', type: 'select', default: '09:00', options: TIMES },
      { name: 'out_date', label: '출차 날짜', type: 'date', default: 'today' },
      { name: 'out_time', label: '출차 시각', type: 'select', default: '18:00', options: TIMES },
      { name: 'discount', label: '할인', type: 'select', default: 'none', options: [
        ['none', '할인 없음'],
        ['half', '50% (경차·장애인·상이 국가유공자·다자녀·저공해 1·2종 등)'],
        ['low3', '20% (저공해 3종)'],
      ] },
    ],

    compute(v, d, u) {
      const fee = d.fees[v.size];
      if (!fee) throw new u.InputError('차종을 골라 주세요.');
      const rate = d.discount_rates[v.discount || 'none'];
      if (rate === undefined) throw new u.InputError('할인 종류를 골라 주세요.');
      const inDate = v.in_date === 'today' ? u.todayIso() : v.in_date;
      const outDate = v.out_date === 'today' ? u.todayIso() : v.out_date;
      const start = toMinutes(u, inDate, v.in_time);
      const end = toMinutes(u, outDate, v.out_time);
      if (start === null) throw new u.InputError('입차 날짜와 시각을 넣어 주세요.');
      if (end === null) throw new u.InputError('출차 날짜와 시각을 넣어 주세요.');
      const total = end - start;
      if (total <= 0) throw new u.InputError('출차 일시가 입차 일시보다 뒤여야 해요.');
      if (total > d.max_days * DAY_MIN) throw new u.InputError(`이 계산기는 ${d.max_days}일까지 계산해요.`);

      const rows = [];
      let sum = 0;
      for (let k = 0; k * DAY_MIN < total; k += 1) {
        const blockStart = start + k * DAY_MIN;
        const mins = Math.min(DAY_MIN, total - k * DAY_MIN);
        const dow = new Date(blockStart * 60000).getUTCDay();
        const weekend = dow === 0 || dow === 5 || dow === 6;
        const cap = weekend ? fee.daily_max_weekend : fee.daily_max_weekday;
        let charge;
        if (k === 0) {
          charge = fee.base_fee + Math.ceil(Math.max(0, mins - d.base_minutes) / fee.unit_minutes) * fee.unit_fee;
        } else {
          charge = Math.ceil(mins / fee.unit_minutes) * fee.unit_fee;
        }
        charge = Math.min(charge, cap);
        sum += charge;
        const dt = new Date(blockStart * 60000);
        const label = `${k + 1}구간 ${dt.getUTCMonth() + 1}/${dt.getUTCDate()}(${WEEKDAY_KO[dow]}) ${String(dt.getUTCHours()).padStart(2, '0')}:${String(dt.getUTCMinutes()).padStart(2, '0')}부터 ${u.floor(mins / 60)}시간 ${mins % 60}분`;
        const kind = v.size === 'large' ? '대형' : (weekend ? '금~일' : '월~목');
        rows.push([label, `${u.won(charge)} (${kind} 요금${charge === cap ? ', 1일 최대' : ''})`]);
      }
      const discounted = u.floor(sum * (1 - rate));
      const values = { minutes: total, fee_before_discount: sum, fee: discounted };
      const head = [['주차 시간', `${u.floor(total / 60)}시간 ${total % 60}분`]];
      const tail = rate > 0 ? [['할인 전 합계', u.won(sum)], [`예상 주차요금 (${Math.round(rate * 100)}% 할인)`, u.won(discounted)]]
        : [['예상 주차요금', u.won(sum)]];
      const notes = [
        '24시간마다 구간을 나눠, 각 구간이 시작하는 날의 요일 요금(월~목 / 금~일)을 써요.',
        '법정공휴일에 시작하는 구간은 금~일 요금이 적용돼요. 공휴일이 끼면 그 구간은 결과보다 늘어날 수 있어요.',
        '사전무인정산기에서 정산하면 20분 안에 출차해야 추가요금이 붙지 않아요.',
        '공식 요금표대로 계산해 아주 짧게 머문 회차도 기본요금으로 보여요. 실제 정산 금액은 이보다 적을 수 있어요.',
      ];
      if (v.size === 'large') notes.push('대형은 요일과 관계없이 1일 최대 요금이 같아요.');
      return { values, rows: [...head, ...rows, ...tail], notes };
    },
  });
})();
