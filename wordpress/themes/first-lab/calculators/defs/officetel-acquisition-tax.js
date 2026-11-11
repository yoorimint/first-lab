/* 오피스텔 취득세 계산기: 돈을 주고 오피스텔을 살 때(매매·분양) 취득세·지방교육세·농어촌특별세. 세율은 data/officetel-acquisition-tax.json */
(function () {
  'use strict';

  const floor10 = (n) => FirstLabCalc.util.floor(n / 10) * 10; // 10원 미만 버림

  FirstLabCalc.register({
    id: 'officetel-acquisition-tax',
    title: '오피스텔 취득세 계산기',
    intro: '오피스텔을 사거나 분양받을 때 내는 취득세와 같이 붙는 지방교육세·농어촌특별세를 계산해요.',
    inputs: [
      { name: 'price', label: '취득가액 (원)', type: 'number', min: 0, step: 1000000, help: '실제로 낸 가격이에요. 분양가에 부가가치세가 들어 있으면 부가세를 뺀 금액을 넣어요.' },
    ],

    compute(v, d, u) {
      const base = Number(v.price) || 0;
      if (!(base > 0)) throw new u.InputError('취득가액을 넣으면 결과가 나와요.');
      if (!Number.isInteger(base)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');

      // 비율은 1만분의 1 % 단위 정수로 곱해 소수 오차를 막음
      const r = (x) => Math.round(x * 1000000);
      const tax = floor10((base * r(d.rate)) / 1000000); // 지방세법 제11조①7호나 4%
      const edu = floor10((base * r(d.edu_rate)) / 1000000); // 제151조①1호: (4% - 2%) × 20% = 0.4%
      const rural = floor10((base * r(d.rural_rate)) / 1000000); // 농어촌특별세법 제5조①6호: 2% × 10% = 0.2%
      const total = tax + edu + rural;
      const pct = (x) => `${Math.round(x * 100000) / 1000}%`;

      const rows = [
        ['취득세', `${u.won(tax)} (${pct(d.rate)})`],
        ['지방교육세', `${u.won(edu)} (${pct(d.edu_rate)})`],
        ['농어촌특별세', `${u.won(rural)} (${pct(d.rural_rate)})`],
        ['합계', `${u.won(total)} (${pct(d.rate + d.edu_rate + d.rural_rate)})`],
      ];
      const notes = [
        '각 세금은 10원 미만을 버렸어요.',
        '취득일(보통 잔금을 치른 날)부터 60일 안에 신고·납부해요.',
        '증여·상속으로 받거나, 법인이 대도시(과밀억제권역)에서 사는 경우 등은 세율이 달라 이 계산과 달라요.',
        '건축물대장에 주택으로 적힌 집은 주택 세율이라 주택 취득세 계산기로 계산해요.',
      ];
      return { values: { tax, edu, rural, total }, rows, notes };
    },
  });
})();
