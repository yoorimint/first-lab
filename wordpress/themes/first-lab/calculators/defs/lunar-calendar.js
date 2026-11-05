/* 음력 달력 계산기: 양력 ↔ 음력 변환 (윤달 포함), 그해 간지·띠, 그날 일진, 같은 음력 날짜의 앞으로 몇 해 양력 날짜.
 * 음력 달 길이·윤달 표는 data/lunar-calendar.json (한국천문연구원 음양력 변환 결과로 1900~2049년 확인) */
(function () {
  'use strict';

  const DAY_MS = 86400000;
  const WEEK = ['일', '월', '화', '수', '목', '금', '토'];

  // 표 → 해마다 달 목록 [{month, leap, days, start}] (처음 한 번만 만듦)
  let cache = null;
  function table(d, u) {
    if (cache && cache.d === d) return cache;
    const years = d.years.map(([start, leap, bits], i) => {
      let t = u.parseDate(start);
      const months = [];
      let m = 1;
      for (let k = 0; k < bits.length; k += 1) {
        const isLeap = leap > 0 && k === leap; // 윤달은 같은 이름의 평달 바로 뒤
        const days = bits[k] === '1' ? 30 : 29;
        months.push({ month: isLeap ? leap : m, leap: isLeap, days, start: t });
        t += days * DAY_MS;
        if (!isLeap) m += 1;
      }
      return { year: d.first_year + i, start: u.parseDate(start), end: t, months };
    });
    cache = { d, years };
    return cache;
  }

  function ganji(n) {
    const i = ((n % 60) + 60) % 60;
    return i;
  }

  FirstLabCalc.register({
    id: 'lunar-calendar',
    title: '음력 달력 계산기',
    intro: '양력 날짜를 음력으로, 음력 날짜를 양력으로 바꿔요. 윤달도 고를 수 있고, 같은 음력 날짜가 앞으로 몇 해 동안 양력 며칠인지도 보여 줘요.',
    inputs: [
      { name: 'mode', label: '바꾸는 방향', type: 'select', default: 's2l', options: [['s2l', '양력 → 음력'], ['l2s', '음력 → 양력']] },
      { name: 'solar', label: '양력 날짜 (양력 → 음력일 때)', type: 'date', default: 'today' },
      { name: 'lyear', label: '음력 연도 (음력 → 양력일 때)', type: 'number', min: 1900, step: 1 },
      { name: 'lmonth', label: '음력 월', type: 'number', min: 1, step: 1 },
      { name: 'lday', label: '음력 일', type: 'number', min: 1, step: 1 },
      { name: 'leap', label: '평달·윤달', type: 'select', default: '0', options: [['0', '평달'], ['1', '윤달']], help: '윤달 생일·제사처럼 음력 날짜가 윤달이면 윤달을 골라요.' },
    ],

    compute(v, d, u) {
      const { years } = table(d, u);
      const first = years[0];
      const last = years[years.length - 1];
      const range = `양력 ${u.korDate(first.start)} ~ ${u.korDate(last.end - DAY_MS)} (음력 ${first.year}~${last.year}년)`;

      let t;
      let y;
      let mo;
      let day;
      if (v.mode === 'l2s') {
        const ly = Number(v.lyear);
        const lm = Number(v.lmonth);
        const ld = Number(v.lday);
        const leap = String(v.leap) === '1';
        if (![ly, lm, ld].every((n) => Number.isInteger(n) && n > 0)) throw new u.InputError('음력 연도·월·일을 넣으면 양력 날짜가 나와요.');
        y = years[ly - d.first_year];
        if (!y) throw new u.InputError(`이 계산기는 ${range}까지 계산해요.`);
        if (lm > 12) throw new u.InputError('음력 월은 1~12월로 넣어 주세요.');
        mo = y.months.find((m) => m.month === lm && m.leap === leap);
        if (!mo) throw new u.InputError(`음력 ${ly}년에는 윤${lm}월이 없어요. ${y.months.some((m) => m.leap) ? `이해 윤달은 윤${y.months.find((m) => m.leap).month}월이에요.` : '이해는 윤달이 없어요.'}`);
        if (ld > mo.days) throw new u.InputError(`음력 ${ly}년 ${leap ? '윤' : ''}${lm}월은 ${mo.days}일까지 있어요.`);
        day = ld;
        t = mo.start + (ld - 1) * DAY_MS;
      } else {
        t = u.parseDate(v.solar);
        if (t === null) throw new u.InputError('양력 날짜를 고르면 음력 날짜가 나와요.');
        y = years.find((x) => x.start <= t && t < x.end);
        if (!y) throw new u.InputError(`이 계산기는 ${range}까지 계산해요.`);
        mo = y.months.find((m) => m.start <= t && t < m.start + m.days * DAY_MS);
        day = u.daysBetween(mo.start, t) + 1;
      }

      const yi = ganji(y.year - 4);
      const jd = Math.round(t / DAY_MS) + 2440588; // 그날 정오의 율리우스일
      const di = ganji(jd + 49);
      const yearGanji = `${d.stems[yi % 10]}${d.branches[yi % 12]}(${d.stems_hanja[yi % 10]}${d.branches_hanja[yi % 12]})`;
      const dayGanji = `${d.stems[di % 10]}${d.branches[di % 12]}(${d.stems_hanja[di % 10]}${d.branches_hanja[di % 12]})`;
      const weekday = WEEK[new Date(t).getUTCDay()];
      const lunarText = `음력 ${y.year}년 ${mo.leap ? '윤' : ''}${mo.month}월 ${day}일`;
      const solarText = `양력 ${u.korDate(t)} (${weekday})`;

      // 같은 음력 월·일의 앞으로 몇 해 양력 날짜 (윤달은 평달 같은 날로)
      const thisYear = Number(u.todayIso().slice(0, 4));
      const repeat = [];
      const repeatRows = [];
      for (let k = 0; k < d.repeat_years; k += 1) {
        const ry = years[thisYear + k - d.first_year];
        if (!ry) break;
        const rm = ry.months.find((m) => m.month === mo.month && !m.leap);
        if (day > rm.days) {
          repeat.push('없음');
          repeatRows.push([`음력 ${ry.year}년 ${mo.month}월 ${day}일`, `없음 (그해 ${mo.month}월은 ${rm.days}일까지)`]);
          continue;
        }
        const rt = rm.start + (day - 1) * DAY_MS;
        repeat.push(u.isoDate(rt));
        repeatRows.push([`음력 ${ry.year}년 ${mo.month}월 ${day}일`, `양력 ${u.korDate(rt)} (${WEEK[new Date(rt).getUTCDay()]})`]);
      }

      const values = {
        solar: u.isoDate(t),
        lunar_year: y.year,
        lunar_month: mo.month,
        lunar_day: day,
        leap: mo.leap,
        month_days: mo.days,
        year_ganji: yearGanji,
        day_ganji: dayGanji,
        weekday,
        repeat: repeat.join(','),
      };
      const rows = v.mode === 'l2s'
        ? [['음력', lunarText], ['양력', solarText]]
        : [['양력', solarText], ['음력', lunarText]];
      rows.push(
        [`음력 ${mo.leap ? '윤' : ''}${mo.month}월`, `${mo.days}일까지 (${mo.days === 30 ? '큰달' : '작은달'})`],
        ['그해 간지·띠', `${yearGanji}년, ${d.animals[yi % 12]}띠 (음력 해 기준)`],
        ['그날 일진', dayGanji],
        ...repeatRows,
      );
      const notes = [
        '간지·띠는 음력 해 기준이라, 양력 1월~설 전 날짜는 앞 해 간지·띠로 나와요. 입춘으로 해를 나누는 방식과는 다를 수 있어요.',
        `음력 ${mo.month}월 ${day}일은 해마다 양력 날짜가 바뀌어요. 위 표의 앞으로 날짜는 평달 기준이에요.`,
        `계산 범위: ${range}.`,
      ];
      return { values, rows, notes };
    },
  });
})();
