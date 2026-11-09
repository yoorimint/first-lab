/* 상속 취득세 계산기: 상속받은 부동산의 시가표준액(지방세법 제10조의2②1호) × 상속 세율(제11조①1호 농지 2.3%·농지 외 2.8%,
   1가구 1주택 상속은 제15조①2호가목으로 2% 뺀 0.8%) + 지방교육세(제151조①1호: (제11조 세율 − 2%) × 20%)
   + 농어촌특별세(농어촌특별세법 제5조①6호: 2%로 계산한 취득세의 10% = 0.2%, 제4조 10호의4 1가구 1주택 상속·11호 국민주택 규모 이하 주택은 비과세).
   10원 미만 버림은 국고금관리법 제47조. 세율은 data/inheritance-acquisition-tax.json (만분율) */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'inheritance-acquisition-tax',
    title: '상속 취득세 계산기',
    intro: '상속받은 부동산의 종류와 시가표준액을 넣으면 취득세·지방교육세·농어촌특별세와 합계가 나와요.',
    inputs: [
      {
        name: 'kind', label: '상속받은 부동산', type: 'select', default: 'house',
        options: [['house', '주택'], ['farm', '농지 (논·밭·과수원 등)'], ['other', '그 밖의 토지·건물 (상가·임야·대지 등)']],
      },
      { name: 'base', label: '내 몫의 시가표준액 (원)', type: 'number', min: 0, step: 1000000, help: '주택은 공시가격, 토지는 개별공시지가 × 면적이에요. 여러 명이 나눠 받으면 내 지분만큼만 넣어요.' },
      {
        name: 'one_home', label: '1가구 1주택 상속 특례 (주택만)', type: 'select', default: 'no',
        options: [['no', '아니요'], ['yes', '예']],
        help: '상속받는 이 집을 빼면 우리 가구(주민등록표에 함께 있는 가족)에 국내 주택이 없을 때예요. 여러 명이 함께 상속받으면 지분이 가장 큰 상속인(같으면 그 집에 사는 사람, 그다음 나이 많은 사람)의 가구를 기준으로 봐요. 고급주택과 주민등록상 재외국민 상속인은 빠져요.',
      },
      {
        name: 'area', label: '주택 전용면적 (주택만)', type: 'select', default: 'small',
        options: [['small', '85㎡ 이하'], ['large', '85㎡ 초과']],
        help: '수도권 밖 도시지역이 아닌 읍·면은 100㎡ 이하까지 농어촌특별세가 붙지 않으니 100㎡ 이하면 "85㎡ 이하"를 골라요.',
      },
    ],

    compute(v, d, u) {
      const base = Number(v.base);
      if (!(base > 0)) throw new u.InputError('시가표준액을 넣으면 결과가 나와요.');
      if (!Number.isInteger(base)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (base > d.max_input) throw new u.InputError('금액이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      const kind = v.kind;
      if (!['house', 'farm', 'other'].includes(kind)) throw new u.InputError('부동산 종류를 골라 주세요.');
      const house = kind === 'house';
      const special = house && v.one_home === 'yes';

      const stdRate = kind === 'farm' ? d.rate_farm : d.rate_other; // 제11조①1호 (만분율)
      const rate = special ? stdRate - d.base_rate : stdRate; // 제15조① 중과기준세율 2% 뺌
      const eduRate = ((stdRate - d.base_rate) * d.edu_pct) / 100; // 제151조①1호
      let ruralRate = d.rural_rate;
      let ruralWhy = '';
      if (special) { ruralRate = 0; ruralWhy = '1가구 1주택 상속 비과세'; }
      else if (house && v.area !== 'large') { ruralRate = 0; ruralWhy = '국민주택 규모 이하 주택 비과세'; }

      const f10 = u.floor10;
      const tax = f10((base * rate) / 10000);
      const edu = f10((base * eduRate) / 10000);
      const rural = f10((base * ruralRate) / 10000);
      const total = tax + edu + rural;
      const pct = (r) => `${(r / 100).toFixed(2).replace(/\.?0+$/, '')}%`;

      const rows = [
        ['적용 세율', special ? `1가구 1주택 특례 (취득세 ${pct(rate)})` : `상속 ${kind === 'farm' ? '농지' : '농지 외'} (취득세 ${pct(rate)})`],
        ['취득세', u.won(tax)],
        ['지방교육세', `${u.won(edu)} (${pct(eduRate)})`],
        ['농어촌특별세', rural ? `${u.won(rural)} (${pct(ruralRate)})` : `0원 (${ruralWhy})`],
        ['합계', `${u.won(total)} (세율 합계 ${pct(rate + eduRate + ruralRate)})`],
      ];

      const notes = [
        '상속개시일(돌아가신 날)이 속한 달의 말일부터 6개월 안에 신고·납부해요. 외국에 주소를 둔 상속인이 있으면 9개월이에요.',
        '고급주택·고급오락장 같은 사치성 재산은 세율이 더 높아 이 계산과 달라요.',
      ];
      if (special) {
        notes.push('여러 명이 함께 상속받으면 지분이 가장 큰 상속인(같으면 그 집에 사는 사람, 그다음 나이가 많은 사람)을 집주인으로 보고 1가구 1주택인지 따져요.');
      }
      if (house && !special && v.area !== 'large') {
        notes.push('단독주택 땅이 주택 바닥면적의 일정 배율을 넘으면 넘는 땅에는 농어촌특별세가 붙을 수 있어요.');
      }
      if (kind === 'farm') {
        notes.push('자경농민 감면 대상 농지면 세율 특례가 있어 이 계산보다 적을 수 있어요.');
      }
      return { values: { rate, tax, edu, rural, total }, rows, notes };
    },
  });
})();
