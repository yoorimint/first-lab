/* IRP·연금저축 연금수령한도 계산기: 연금수령한도 = 연금계좌 평가액 ÷ (11 − 연금수령연차) × 120/100 (11년차부터 한도 없음),
   세액공제받은 납입액·운용수익의 연금소득세 원천징수세율(나이별 5·4·3%, 종신계약 3% 중 낮은 것) + 지방소득세 10%.
   요율은 data/pension-withdrawal-limit.json (소득세법 시행령 제40조의2③④, 소득세법 제129조①5의2) */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'pension-withdrawal-limit',
    title: 'IRP 연금수령한도 계산기',
    intro: '연금계좌 평가액, 연금수령연차, 나이를 넣으면 올해 연금으로 꺼낼 수 있는 한도와 연금소득세율이 나와요.',
    inputs: [
      { name: 'balance', label: '연금계좌 평가액 (원)', type: 'number', min: 0, step: 1, help: '1월 1일(연금 개시를 신청한 해는 신청한 날) 현재 계좌 평가액이에요.' },
      { name: 'year', label: '연금수령연차 (년차)', type: 'number', min: 1, step: 1, default: 1, help: '연금을 처음 받을 수 있게 된 해가 1년차예요. 2013년 3월 1일 전에 가입한 계좌는 6년차부터 시작해요.' },
      { name: 'age', label: '연금 받는 날 나이 (만)', type: 'number', min: 55, step: 1 },
      {
        name: 'life', label: '연금 받는 방식', type: 'select', default: 'no',
        options: [
          ['no', '기간·금액을 정해 받아요'],
          ['yes', '사망할 때까지 받는 종신계약이에요'],
        ],
      },
    ],

    compute(v, d, u) {
      const empty = (x) => x === undefined || x === null || x === '';
      if (empty(v.balance)) throw new u.InputError('연금계좌 평가액을 넣으면 결과가 나와요.');
      if (empty(v.age)) throw new u.InputError('연금 받는 날 나이를 넣어 주세요.');
      const balance = Number(v.balance);
      const year = empty(v.year) ? 1 : Number(v.year);
      const age = Number(v.age);
      if (!(balance >= 0) || !Number.isInteger(balance) || balance > d.max_input) throw new u.InputError('평가액은 0 이상 1원 단위 정수로 넣어 주세요.');
      if (!(year >= 1) || !Number.isInteger(year) || year > 100) throw new u.InputError('연금수령연차는 1 이상 정수로 넣어 주세요.');
      if (!Number.isInteger(age) || age > 130) throw new u.InputError('나이는 정수로 넣어 주세요.');
      if (age < d.min_age) throw new u.InputError(`연금수령은 ${d.min_age}세 이후에 연금 개시를 신청해야 해요. 그 전에 꺼내면 부득이한 사유(요양·천재지변 등)가 아닌 한 연금외수령이에요.`);
      if (v.life !== 'yes' && v.life !== 'no') throw new u.InputError('연금 받는 방식을 골라 주세요.');

      const noLimit = year >= d.no_limit_year;
      const limit = noLimit ? balance : u.floor((balance * d.limit_pct) / (100 * (d.no_limit_year - year)));
      const ageRate = age < 70 ? d.rate_under70_pct : age < 80 ? d.rate_70to79_pct : d.rate_80plus_pct;
      const rate = v.life === 'yes' ? Math.min(ageRate, d.rate_life_pct) : ageRate;
      const rateWithLocal = (rate * (100 + d.local_pct)) / 100;
      const values = { limit, no_limit: noLimit ? 1 : 0, rate_pct: rate, rate_with_local_pct: rateWithLocal };
      const rows = [
        ['올해 연금수령한도', noLimit ? '한도 없음 (11년차부터)' : `${u.won(limit)} (평가액 ÷ ${d.no_limit_year - year} × 120%)`],
        ['세액공제받은 돈·운용수익의 연금소득세율', `${rate}% (지방소득세까지 ${rateWithLocal}%)`],
        ['퇴직금으로 넣은 돈', '퇴직소득세의 70% (실제로 연금 받은 해가 10년 넘으면 60%, 20년 넘으면 50%)'],
      ];
      const notes = [];
      if (!noLimit) notes.push('한도를 넘겨 꺼낸 금액은 연금외수령이라, 퇴직금은 퇴직소득세 전액, 세액공제받은 돈·운용수익은 기타소득세 15%(지방소득세까지 16.5%)를 내요.');
      notes.push('퇴직금이 아닌 연금소득(세액공제받은 돈·운용수익)이 한 해 1,500만 원을 넘으면 종합과세나 15% 분리과세 중 골라요. 퇴직금으로 받는 연금은 이 1,500만 원에 넣지 않아요.');
      notes.push('세액공제를 받지 않고 넣은 돈은 꺼낼 때 세금이 없어요.');
      notes.push('원 미만은 버렸어요.');
      return { values, rows, notes };
    },
  });
})();
