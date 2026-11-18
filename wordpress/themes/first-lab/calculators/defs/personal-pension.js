/* 개인연금(연금저축) 계산기: 매달 넣는 돈·기간·예상 수익률로 쌓이는 돈(매달 말 납입, 월 복리),
   그 돈을 같은 수익률로 운용하며 정한 기간 동안 매달 같은 금액으로 받을 때 월 연금액(원리금균등 공식),
   해마다 돌려받는 세액공제(연 600만 원까지 16.5%·13.2%, 소득세법 제59조의3)와
   받을 때 연금소득세(나이별 5·4·3% + 지방소득세 10%, 소득세법 제129조①5의2)를 보여 준다.
   요율·한도는 data/personal-pension.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'personal-pension',
    title: '개인연금 계산기',
    intro: '연금저축에 매달 넣을 돈, 넣는 기간, 예상 수익률, 받을 기간을 넣으면 모이는 돈과 매달 받을 연금, 세금이 나와요.',
    inputs: [
      { name: 'monthly', label: '매달 넣는 돈 (원)', type: 'number', min: 0, step: 1, help: '세액공제 한도(연 600만 원)에 맞춰 월 50만 원까지 넣을 수 있게 했어요.' },
      { name: 'years', label: '넣는 기간 (년)', type: 'number', min: 1, step: 1 },
      { name: 'rate', label: '예상 연 수익률 (%)', type: 'number', min: 0, step: 'any', default: 3, help: '실제 수익률은 상품·시장에 따라 달라져요. 원금 보장이 아닌 상품은 손실도 날 수 있어요.' },
      { name: 'start_age', label: '연금 받기 시작하는 나이 (만)', type: 'number', min: 55, step: 1, default: 55 },
      { name: 'payout_years', label: '연금 받는 기간 (년)', type: 'number', min: 10, step: 1, default: 10, help: '10년보다 짧게 나눠 받으면 해마다 꺼낼 수 있는 한도(연금수령한도)를 넘기기 쉬워 10년부터 넣게 했어요.' },
      {
        name: 'income', label: '1년 소득', type: 'choice', default: 'low',
        options: [
          ['low', '총급여 5,500만 원 이하'],
          ['high', '총급여 5,500만 원 넘음'],
        ],
        help: '월급 말고 다른 종합소득도 있으면 종합소득금액 4,500만 원이 기준이에요.',
      },
    ],

    compute(v, d, u) {
      const empty = (x) => x === undefined || x === null || x === '';
      if (empty(v.monthly)) throw new u.InputError('매달 넣는 돈을 넣으면 결과가 나와요.');
      if (empty(v.years)) throw new u.InputError('넣는 기간을 넣어 주세요.');
      const monthly = Number(v.monthly);
      const years = Number(v.years);
      const rate = empty(v.rate) ? 0 : Number(v.rate);
      const startAge = empty(v.start_age) ? d.min_age : Number(v.start_age);
      const payoutYears = empty(v.payout_years) ? d.min_payout_years : Number(v.payout_years);
      if (!(monthly >= 0) || !Number.isInteger(monthly) || monthly > d.max_monthly) throw new u.InputError('매달 넣는 돈은 0원부터 50만 원까지 1원 단위로 넣어 주세요.');
      if (!(years >= 1) || !Number.isInteger(years) || years > 60) throw new u.InputError('넣는 기간은 1년부터 60년까지 정수로 넣어 주세요.');
      if (!(rate >= 0) || rate > 20) throw new u.InputError('예상 수익률은 0%부터 20%까지 넣어 주세요.');
      if (!Number.isInteger(startAge) || startAge > 100) throw new u.InputError('나이는 정수로 넣어 주세요.');
      if (startAge < d.min_age) throw new u.InputError(`연금으로 받으려면 ${d.min_age}세 이후에 연금 개시를 신청해야 해요.`);
      if (!Number.isInteger(payoutYears) || payoutYears < d.min_payout_years || payoutYears > 50) throw new u.InputError(`받는 기간은 ${d.min_payout_years}년부터 50년까지 정수로 넣어 주세요.`);
      if (v.income !== 'low' && v.income !== 'high') throw new u.InputError('소득 구간을 골라 주세요.');

      const i = rate / 100 / 12;
      const n = years * 12;
      const m = payoutYears * 12;
      const totalPaid = monthly * n;
      const balanceRaw = i === 0 ? totalPaid : monthly * ((Math.pow(1 + i, n) - 1) / i);
      const balance = u.floor(balanceRaw);
      const payRaw = i === 0 ? balanceRaw / m : (balanceRaw * i) / (1 - Math.pow(1 + i, -m));
      const pay = u.floor(payRaw);
      const yearlyPay = pay * 12;

      // 세액공제 (해마다)
      const yearly = monthly * 12;
      const creditable = Math.min(yearly, d.savings_cap);
      const creditPct = v.income === 'low' ? d.rate_high_pct : d.rate_low_pct;
      const creditTax = u.floor((creditable * creditPct) / 100);
      const creditYear = creditTax + u.floor((creditTax * d.local_pct) / 100);
      const creditTotal = creditYear * years;

      // 연금소득세 (받는 해의 나이별)
      const ageRate = (age) => (age < 70 ? d.rate_under70_pct : age < 80 ? d.rate_70to79_pct : d.rate_80plus_pct);
      const taxOn = (amount, pct) => {
        let it = u.floor10((amount * pct) / 100);
        if (it < d.small_tax) it = 0; // 소액 부징수: 원천징수세액 1천 원 미만은 떼지 않음 (소득세법 제86조1호)
        return it + u.floor10((it * d.local_pct) / 100);
      };
      const firstRate = ageRate(startAge);
      const firstTax = taxOn(pay, firstRate);
      const afterTax = pay - firstTax;
      let taxTotal = 0;
      for (let y = 0; y < payoutYears; y += 1) taxTotal += taxOn(pay, ageRate(startAge + y)) * 12;

      // 연금수령한도: 받기 시작한 해를 1년차로 보고 1~10년차를 확인 (해마다 1월 1일 잔액 기준)
      let limitOver = 0;
      let bal = balanceRaw;
      for (let y = 1; y <= Math.min(payoutYears, d.no_limit_year - 1); y += 1) {
        const limit = (bal * d.limit_pct) / (100 * (d.no_limit_year - y));
        if (yearlyPay > limit + 1) { limitOver = y; break; }
        for (let k = 0; k < 12; k += 1) bal = bal * (1 + i) - pay;
      }

      const values = {
        total_paid: totalPaid, balance, monthly_pay: pay, monthly_tax: firstTax, monthly_after_tax: afterTax,
        tax_rate_pct: firstRate, credit_year: creditYear, credit_total: creditTotal, tax_total: taxTotal,
        over_1500: yearlyPay > d.separate_limit ? 1 : 0, limit_over_year: limitOver,
      };
      const rows = [
        ['넣은 돈 합계', `${u.won(totalPaid)} (월 ${u.won(monthly)} × ${n}개월)`],
        ['연금 받기 전 모인 돈', `${u.won(balance)} (연 ${rate}% 가정)`],
        ['매달 받는 연금 (세금 떼기 전)', `${u.won(pay)} (${payoutYears}년 동안)`],
        ['매달 떼는 연금소득세', `${u.won(firstTax)} (${startAge}세 기준 ${firstRate}%에 지방소득세 10%)`],
        ['매달 손에 쥐는 돈', u.won(afterTax)],
        ['해마다 돌려받는 세액공제', `${u.won(creditYear)} (공제 대상 ${u.won(creditable)} × ${(creditPct * (100 + d.local_pct)) / 100}%)`],
        ['넣는 동안 세액공제 합계', u.won(creditTotal)],
        ['받는 동안 연금소득세 합계', u.won(taxTotal)],
      ];
      const notes = [];
      if (yearlyPay > d.separate_limit) notes.push(`이 계좌 연금만 한 해 ${u.won(yearlyPay)}으로 1,500만 원을 넘어요(국민연금 같은 공적연금은 빼고 셈). 이때는 종합과세와 15% 분리과세(지방소득세까지 16.5%) 중 골라야 해요.`);
      if (limitOver) notes.push(`받기 시작한 해를 1년차로 보면 ${limitOver}년차에 연금수령한도를 넘어요. 넘긴 금액은 연금외수령이라 16.5%를 떼요. 연금수령연차는 연금을 처음 받을 수 있게 된 해부터 세니, 늦게 받기 시작했다면 실제 한도는 더 넉넉할 수 있어요.`);
      if (years < d.min_join_years) notes.push('연금으로 받으려면 가입한 날부터 5년이 지나야 해요.');
      notes.push('세액공제는 낸 세금(결정세액)이 있어야 돌려받아요. 낸 세금보다 많이 돌려받지는 못해요.');
      notes.push('수익률은 가정이에요. 매달 말에 넣고 받는다고 보고 월 복리로 계산했어요. 원 미만은 버렸어요.');
      return { values, rows, notes };
    },
  });
})();
