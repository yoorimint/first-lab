/* 신용대출 DSR 계산기: 연소득·신용대출 금액·금리·상환 방식 → 1년 원리금, DSR, 더 받을 수 있는 신용대출 어림값.
 * 규칙(data/credit-loan-dsr.json): 일시상환(마이너스통장 포함) 신용대출은 원금을 5년에 나눠 갚는다고 보고,
 * 분할상환은 약정만기를 5년 이상·10년 이내로 맞춘 원리금균등으로 어림한다 (은행업감독업무시행세칙 별표 18 <표 3>
 * '대출총액 / 약정만기 (5년 이상, 10년 이내)' — 11/12 독립 검수: 1~4년을 그대로 나누고 11년 이상을 막던 것을 고침). 신용대출 잔액(기존+신규)이 1억 원을 넘으면 새로 받는 대출(신규취급분) 금리에 스트레스 금리를 더한다
 * (금융위원회 스트레스 DSR 안내: 대상대출 = 신규취급분 — 11/12 독립 검수, 기존 대출에도 더하던 것을 고침). */
(function () {
  'use strict';

  /** 1년 원리금. ratePct = 계산에 쓰는 연 금리(%), years = 분할상환 만기(년) */
  function annual(P, ratePct, method, years, d) {
    if (!(P > 0)) return 0;
    if (method === 'installment') {
      const r = ratePct / 100 / 12;
      const n = Math.min(Math.max(years, d.credit_years), d.max_years) * 12;  // 5~10년으로 맞춤 (별표 18)
      const monthly = r === 0 ? P / n : (P * r) / -Math.expm1(-n * Math.log1p(r));
      return monthly * 12;
    }
    return P / d.credit_years + (P * ratePct) / 100;
  }

  /** 1원을 빌릴 때 늘어나는 1년 원리금 */
  const perWon = (ratePct, method, years, d) => annual(1e8, ratePct, method, years, d) / 1e8;

  FirstLabCalc.register({
    id: 'credit-loan-dsr',
    title: '신용대출 DSR 계산기',
    intro: '연소득과 신용대출 조건을 넣으면 DSR에 잡히는 1년 원리금과 DSR 비율을 계산해요.',
    inputs: [
      { name: 'income', label: '연소득 (원)', type: 'number', min: 0, step: 1000000, help: '1년 소득이에요. 은행이 소득 서류로 인정하는 금액으로 넣어요.' },
      {
        name: 'sector', label: '대출받는 곳', type: 'select', default: 'bank',
        options: [['bank', '은행 (DSR 40%)'], ['nonbank', '저축은행·카드사 등 제2금융권 (DSR 50%)']],
      },
      { name: 'loan', label: '새 신용대출 금액 (원)', type: 'number', min: 0, step: 1000000, help: '마이너스통장은 쓴 돈이 아니라 한도를 넣어요.' },
      { name: 'rate', label: '새 신용대출 연 금리 (%)', type: 'number', min: 0, step: 0.01 },
      {
        name: 'method', label: '상환 방식', type: 'select', default: 'bullet',
        options: [['bullet', '만기일시·마이너스통장 (5년에 나눠 갚는다고 봄)'], ['installment', '분할상환 (원리금균등, 약정만기 5~10년으로 봄)']],
      },
      { name: 'years', label: '분할상환 만기 (년)', type: 'number', min: 1, step: 1, default: 5, help: '분할상환일 때만 써요. DSR은 만기가 5년보다 짧아도 5년, 10년보다 길어도 10년으로 봐요.' },
      { name: 'credit_now', label: '이미 있는 신용대출 잔액 (원)', type: 'number', min: 0, step: 1000000, default: 0, help: '마이너스통장은 한도로 넣어요. 만기일시로 보고 계산해요.' },
      { name: 'credit_now_rate', label: '이미 있는 신용대출 연 금리 (%)', type: 'number', min: 0, step: 0.01, default: 0 },
      { name: 'other_yearly', label: '다른 대출의 1년 원리금 (원)', type: 'number', min: 0, step: 100000, default: 0, help: '주택담보대출·자동차 할부·카드론 등 1년에 갚는 원금과 이자 합계예요.' },
    ],

    compute(v, d, u) {
      const income = Number(v.income);
      const P = Number(v.loan);
      const rate = Number(v.rate);
      const method = v.method === 'installment' ? 'installment' : 'bullet';
      const years = Number(v.years || 0);
      const C = Number(v.credit_now || 0);
      const cRate = Number(v.credit_now_rate || 0);
      const other = Number(v.other_yearly || 0);
      if (!(income > 0)) throw new u.InputError('연소득을 넣으면 결과가 나와요.');
      if (!(P >= 0) || v.loan === '' || v.loan === undefined) throw new u.InputError('새 신용대출 금액을 넣어 주세요 (없으면 0).');
      if (!(rate >= 0) || v.rate === '' || v.rate === undefined) throw new u.InputError('연 금리를 넣어 주세요.');
      if (method === 'installment' && !(Number.isInteger(years) && years >= 1 && years <= 50)) {
        throw new u.InputError('분할상환 만기는 1~50년 사이 정수(년)로 넣어 주세요.');
      }
      if (!(C >= 0) || !(cRate >= 0) || !(other >= 0)) throw new u.InputError('이미 있는 대출 칸은 0 이상으로 넣어 주세요.');

      const limit = v.sector === 'nonbank' ? d.limit_nonbank : d.limit_bank;
      const stressOn = P + C > d.stress_threshold;
      const add = stressOn ? d.stress_rate : 0;

      const newYear = annual(P, rate + add, method, years, d);
      const nowYear = annual(C, cRate, 'bullet', 0, d);
      const total = newYear + nowYear + other;
      const ratio = (total / income) * 100;
      const dsr = Math.round(ratio * 10 + 1e-9) / 10;
      const dsr2 = Math.round(ratio * 100 + 1e-9) / 100;

      // 새 신용대출 최대 금액 (같은 금리·상환 방식): 스트레스 없이 1억 원까지, 넘으면 스트레스 금리로 다시 계산
      const cap = (income * limit) / 100;
      const room0 = cap - annual(C, cRate, 'bullet', 0, d) - other;
      const max0 = Math.max(0, room0 / perWon(rate, method, years, d));
      const under = Math.max(0, d.stress_threshold - C); // 신용대출 합계가 1억 원을 넘지 않는 새 대출 금액
      const room1 = room0; // 스트레스 금리는 새 대출에만
      const max1 = Math.max(0, room1 / perWon(rate + d.stress_rate, method, years, d));
      let dsrMax = Math.min(max0, under);
      if (max1 > under) dsrMax = Math.max(dsrMax, max1);
      const incomeCap = Math.max(0, income - C);
      const maxLoan = u.floor(Math.min(dsrMax, incomeCap));

      const notes = [];
      if (stressOn) notes.push(`신용대출 잔액이 1억 원을 넘어 새 대출 금리에 스트레스 금리 ${d.stress_rate}%포인트를 더해 계산했어요. 고정금리 기간이 긴 대출은 덜 더할 수 있어요.`);
      else notes.push('신용대출 잔액이 1억 원 이하라 스트레스 금리를 더하지 않았어요.');
      if (method === 'bullet') notes.push('만기일시 신용대출과 마이너스통장은 실제 만기와 관계없이 원금을 5년에 나눠 갚는다고 보고 계산했어요.');
      if (method === 'installment') notes.push(`분할상환은 DSR에서 약정만기를 5년 이상·10년 이내로 봐요. ${years < d.credit_years ? `넣은 ${years}년 대신 ${d.credit_years}년으로` : years > d.max_years ? `넣은 ${years}년 대신 ${d.max_years}년으로` : `${years}년으로`} 원리금균등 1년 납입액을 어림했어요. 이자를 어떻게 넣는지는 금융회사마다 다를 수 있어요.`);
      notes.push(ratio <= limit ? `DSR ${dsr2}%로 ${limit}% 안이에요.` : `DSR ${dsr2}%로 ${limit}%를 넘어요. 금액을 줄이거나 다른 대출을 먼저 갚아야 할 수 있어요.`);
      if (C > 0) notes.push('이미 있는 신용대출은 넣은 금리 그대로 계산했어요. 금융회사가 어떤 금리로 계산하는지는 상담 때 확인해요.');
      if (incomeCap < dsrMax) notes.push('신용대출은 연소득 이내로만 받을 수 있어, 새 신용대출 최대 금액을 연소득에서 이미 있는 신용대출을 뺀 값으로 줄였어요.');
      notes.push('총대출액이 1억 원 이하면 DSR 40%·50% 규제 대신 금융회사 자체 심사로 정해요. 실제 한도는 신용점수·소득 인정 방식에 따라 달라요.');

      return {
        values: { stress: add, new_yearly: Math.round(newYear), now_yearly: Math.round(nowYear), total_yearly: Math.round(total), dsr, limit, max_loan: maxLoan },
        rows: [
          ['계산에 쓴 새 대출 금리', `연 ${Math.round((rate + add) * 100) / 100}%${stressOn ? ` (스트레스 ${d.stress_rate}%p 포함)` : ''}`],
          ['새 신용대출 1년 원리금', u.won(newYear)],
          ['이미 있는 신용대출 1년 원리금', u.won(nowYear)],
          ['1년 원리금 합계', u.won(total)],
          ['DSR', `${dsr}% (기준 ${limit}%)`],
          ['같은 조건의 새 신용대출 최대 금액 (어림값)', u.won(maxLoan)],
        ],
        notes,
      };
    },
  });
})();
