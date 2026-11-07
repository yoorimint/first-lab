/* 주택 취득세 계산기: 유상 매매로 주택을 살 때 취득세·지방교육세·농어촌특별세. 세율은 data/acquisition-tax.json */
(function () {
  'use strict';

  const floor10 = (n) => FirstLabCalc.util.floor(n / 10) * 10; // 10원 미만 버림

  /** 지방세법 제11조①8호: 6억 이하 1%, 6억 초과 9억 이하 (가액×2/3억 − 3)×1/100 을 소수점 넷째자리(= 0.01% 단위)까지 반올림, 9억 초과 3% → 결과는 1만분의 1 % 단위 정수 */
  function generalRate(base, g) {
    if (base <= g.low_max) return g.low_rate * 1000000;
    if (base > g.high_min) return g.high_rate * 1000000;
    return Math.round((2 * base - 900000000) / 3000000) * 100; // 0.01% 단위 (정수 나눗셈이라 0.5 경계도 정확)
  }

  FirstLabCalc.register({
    id: 'acquisition-tax',
    title: '주택 취득세 계산기',
    intro: '집을 사고팔 때(유상 매매) 내는 취득세와 같이 붙는 지방교육세·농어촌특별세를 주택 수와 지역에 따라 계산해요.',
    inputs: [
      { name: 'price', label: '취득가액 (원)', type: 'number', min: 0, step: 1000000, help: '매매계약서의 실제 거래가격이에요.' },
      {
        name: 'homes', label: '이 집을 포함한 1세대 주택 수', type: 'select', default: '1',
        options: [['1', '1주택'], ['2', '2주택'], ['3', '3주택'], ['4', '4주택 이상']],
        help: '2020년 8월 12일 이후 계약해 취득한 분양권·입주권, 주택분 재산세가 나오는 오피스텔도 주택 수에 들어가요(상속 5년 이내 주택·시가표준액 1억 원 이하 오피스텔 등은 빼요).',
      },
      {
        name: 'adjusted', label: '새로 사는 집이 조정대상지역에 있나요', type: 'select', default: 'no',
        options: [['no', '아니요'], ['yes', '예']],
        help: '취득일 기준이에요. 지정 고시일 이전에 계약하고 계약금을 낸 사실이 증빙되면 지정 전 취득으로 봐요. 지정 지역은 국토교통부 공고로 확인해요.',
      },
      {
        name: 'temporary', label: '일시적 2주택 (종전 집을 기한 안에 팔 예정)', type: 'select', default: 'no',
        options: [['no', '아니요'], ['yes', '예']],
        help: '2주택일 때만 봐요. 종전 집을 3년(둘 다 조정대상지역이면 2년, 2026년 8월 26일 이전 계약·계약금 증빙분은 3년) 안에 팔면 1주택 세율이에요.',
      },
      {
        name: 'area', label: '전용면적', type: 'select', default: 'small',
        options: [['small', '85㎡ 이하'], ['large', '85㎡ 초과']],
        help: '수도권 밖 도시지역이 아닌 읍·면은 100㎡ 이하까지 농어촌특별세가 붙지 않으니 100㎡ 이하면 "85㎡ 이하"를 골라요.',
      },
    ],

    compute(v, d, u) {
      const base = Number(v.price) || 0;
      if (!(base > 0)) throw new u.InputError('취득가액을 넣으면 결과가 나와요.');
      const homes = Number(v.homes);
      if (![1, 2, 3, 4].includes(homes)) throw new u.InputError('주택 수를 골라 주세요.');
      const adjusted = v.adjusted === 'yes';
      const large = v.area === 'large';

      let level = 'general';
      if (homes === 2 && adjusted && v.temporary !== 'yes') level = 'heavy8';
      if (homes === 3) level = adjusted ? 'heavy12' : 'heavy8';
      if (homes >= 4) level = 'heavy12';

      let rate4; // 취득세율 (1만분의 1 % 단위)
      let eduRate4;
      let ruralRate4;
      if (level === 'general') {
        rate4 = generalRate(base, d.general);
        eduRate4 = rate4 * d.general.edu_share; // 세율×50%×20% (지방세법 제151조①1호) — 반올림하지 않음
        ruralRate4 = large ? d.general.rural_rate * 1000000 : 0;
      } else {
        const h = d[level];
        rate4 = h.rate * 1000000;
        eduRate4 = h.edu_rate * 1000000;
        ruralRate4 = large ? h.rural_rate * 1000000 : 0;
      }
      rate4 = Math.round(rate4);

      const tax = floor10((base * rate4) / 1000000);
      const edu = floor10((base * eduRate4) / 1000000);
      const rural = floor10((base * ruralRate4) / 1000000);
      const total = tax + edu + rural;
      const pct = (r4) => `${(r4 / 10000).toFixed(4).replace(/\.?0+$/, '')}%`;

      const levelText = { general: '일반 세율', heavy8: '중과 8%', heavy12: '중과 12%' }[level];
      const rows = [
        ['적용 세율', `${levelText} (취득세 ${pct(rate4)})`],
        ['취득세', u.won(tax)],
        ['지방교육세', `${u.won(edu)} (${pct(eduRate4)})`],
        ['농어촌특별세', rural ? `${u.won(rural)} (${pct(ruralRate4)})` : '0원 (국민주택 규모 이하 비과세)'],
        ['합계', `${u.won(total)} (취득가액의 ${pct(Math.round((total / base) * 1000000))})`],
      ];

      const notes = [
        '취득일(보통 잔금일)부터 60일 안에 신고·납부해요.',
        '법인 취득, 증여·상속처럼 돈을 주고 사지 않은 경우는 세율이 달라 이 계산기로 계산하지 않아요.',
        '건축물대장상 주택이 아닌 오피스텔·상가·토지는 4%(지방교육세·농어촌특별세 포함 4.6%)라 이 계산기 대상이 아니에요.',
        '고급주택(시가표준액 9억 원 초과에 면적·시설 기준을 넘는 주택, 지방세법 제13조⑤3호)은 8%가 더해져 이 계산과 달라요.',
      ];
      if (level !== 'general') {
        notes.push(`수도권은 시가표준액 ${d.exempt_low.capital}, 그 밖의 지역은 ${d.exempt_low.other} 이하인 주택이면 중과하지 않아요 (정비구역 등은 제외).`);
      }
      if (homes === 2 && adjusted && v.temporary === 'yes') {
        notes.push('일시적 2주택으로 1주택 세율을 적용했어요. 기간 안에 종전 집을 팔지 못하면 그 기간이 지난 날부터 60일 안에 중과세율과의 차액을 신고·납부해야 해요 (지방세법 제20조②, 2026년부터).');
      }
      if (homes === 1 && base <= d.first_home.max_price) {
        notes.push(`생애최초 구입이면 취득세에서 ${d.first_home.relief}(소형·인구감소지역 주택 ${d.first_home.relief_small})까지 감면될 수 있어요. 이 계산에는 넣지 않았어요.`);
      }

      return { values: { rate4, tax, edu, rural, total }, rows, notes };
    },
  });
})();
