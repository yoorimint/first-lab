/* 신용카드 소득공제 계산기 (연말정산): 신용카드·체크카드(현금영수증)·문화체육·전통시장·대중교통 사용액과 총급여 →
   최저사용금액(총급여 25%), 공제 계산액, 기본 한도(자녀 수별), 전통시장·대중교통(·문화체육) 추가 한도, 최종 소득공제액.
   공제율·한도는 data/card-deduction.json (조세특례제한법 제126조의2 ①②⑩⑪, 2026년 사용분부터 자녀 한도) */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'card-deduction',
    title: '신용카드 소득공제 계산기',
    intro: '총급여와 1년 동안 쓴 카드 금액을 결제 수단·사용처별로 넣으면 25% 문턱, 공제율, 한도까지 반영한 소득공제액이 나와요.',
    inputs: [
      { name: 'salary', label: '총급여 (원, 1년)', type: 'number', min: 0, step: 10000, help: '연봉에서 비과세 소득(식대 등)을 뺀 금액이에요. 원천징수영수증의 총급여 칸 값이에요.' },
      { name: 'credit', label: '신용카드 사용액 (원)', type: 'number', min: 0, step: 10000, default: 0, help: '아래 전통시장·대중교통·문화체육 칸에 넣은 금액은 빼고 넣어요.' },
      { name: 'debit', label: '체크카드·현금영수증 사용액 (원)', type: 'number', min: 0, step: 10000, default: 0, help: '직불·체크카드, 기명식 선불카드, 현금영수증, 간편결제 직불 수단 금액이에요. 아래 칸에 넣은 금액은 빼고 넣어요.' },
      { name: 'culture', label: '도서·공연·박물관·영화·수영장·헬스장 (원)', type: 'number', min: 0, step: 10000, default: 0, help: '총급여 7,000만 원 이하만 30%로 따로 쳐요. 넘으면 이 칸은 비우고 결제한 수단 칸에 넣어요.' },
      { name: 'market', label: '전통시장 사용액 (원)', type: 'number', min: 0, step: 10000, default: 0 },
      { name: 'transit', label: '대중교통 이용액 (원)', type: 'number', min: 0, step: 10000, default: 0, help: '버스·지하철·기차 등 대중교통 요금이에요 (택시·항공은 아님).' },
      {
        name: 'children', label: '한도를 늘려 주는 자녀 (20세 이하 등)', type: 'select', default: '0',
        options: [['0', '없음'], ['1', '1명'], ['2', '2명 이상']],
        help: '소득금액 100만 원 이하(근로소득만이면 총급여 500만 원 이하)이고 다른 사람의 기본공제를 받지 않는 20세 이하 자녀·손자녀(장애인은 나이 무관)예요. 2026년 사용분부터 적용돼요.',
      },
    ],

    compute(v, d, u) {
      const num = (x) => (x === undefined || x === null || x === '' ? 0 : Number(x));
      const salary = num(v.salary);
      if (!(salary > 0)) throw new u.InputError('총급여를 넣으면 결과가 나와요.');
      const amt = {};
      for (const [k, label] of [['credit', '신용카드'], ['debit', '체크카드·현금영수증'], ['culture', '문화체육'], ['market', '전통시장'], ['transit', '대중교통']]) {
        const x = num(v[k]);
        if (!(x >= 0) || !Number.isInteger(x)) throw new u.InputError(`${label} 사용액은 0 이상 1원 단위로 넣어 주세요.`);
        if (x > d.max_input) throw new u.InputError(`${label} 사용액이 너무 커요.`);
        amt[k] = x;
      }
      if (salary > d.max_input) throw new u.InputError('총급여가 너무 커요.');
      const kids = Math.min(2, Math.max(0, parseInt(v.children ?? '0', 10) || 0));
      const low = salary <= d.low_salary_max;
      const r = d.rates_pct;
      const notes = [];

      // 7,000만 원 초과면 문화체육 사용분을 따로 치지 않음 → 결제 수단을 모르니 신용카드(15%)로 셈
      let credit = amt.credit;
      let culture = amt.culture;
      if (!low && culture > 0) {
        credit += culture;
        notes.push('총급여 7,000만 원 초과라 문화체육 칸 금액은 신용카드(15%)로 셌어요. 체크카드로 냈다면 체크카드 칸에 넣으면 30%로 계산돼요.');
        culture = 0;
      }
      const total = credit + amt.debit + culture + amt.market + amt.transit;
      const threshold = u.floor((salary * d.threshold_pct) / 100);

      // 사용분 × 공제율 (백분율 그대로 더한 뒤 마지막에 원 미만 버림)
      const marketP = amt.market * r.market;
      const transitP = amt.transit * r.transit;
      const cultureP = culture * r.culture;
      const debitP = amt.debit * r.debit;
      const creditP = credit * r.credit;
      const sumP = marketP + transitP + cultureP + debitP + creditP;

      // 최저사용금액(25%)은 신용카드 → 체크카드·문화체육 → 전통시장·대중교통 순으로 뺌 (제126조의2②6호)
      // 문턱(총급여 × 25%)은 법에 끝수 규칙이 없어 1/100원 단위 그대로 계산 (T = 문턱 × 100)
      const T = salary * d.threshold_pct;
      let cutP100;
      const midUsed = amt.debit + culture;
      if (T <= credit * 100) cutP100 = T * r.credit;
      else if (T <= (credit + midUsed) * 100) cutP100 = credit * 100 * r.credit + (T - credit * 100) * r.debit;
      else cutP100 = credit * 100 * r.credit + midUsed * 100 * r.debit + (T - (credit + midUsed) * 100) * r.market;

      const gross = total * 100 > T ? Math.max(0, u.floor((sumP * 100 - cutP100) / 10000)) : 0;
      const limit = (low ? d.limit_low : d.limit_high)[kids];
      const base = Math.min(gross, limit);
      const extraCap = low ? d.extra_cap_low : d.extra_cap_high;
      const extraPool = u.floor((marketP + transitP + (low ? cultureP : 0)) / 100);
      const extra = gross > limit ? Math.min(gross - limit, extraPool, extraCap) : 0;
      const deduction = base + extra;
      const lost = gross - deduction;

      const values = { threshold, total, gross, limit, base, extra, deduction, lost };
      const rows = [
        ['최저사용금액 (총급여의 25%, 원 미만 버려 표시)', u.won(threshold)],
        ['1년 사용액 합계', u.won(total)],
        ['공제 계산액 (25% 넘는 부분 × 공제율)', u.won(gross)],
        [`기본 한도 (총급여 ${low ? '7,000만 원 이하' : '7,000만 원 초과'}${kids ? `, 자녀 ${kids === 2 ? '2명 이상' : '1명'}` : ''})`, u.won(limit)],
        [`추가 한도로 더 받는 금액 (${low ? '전통시장·대중교통·문화체육' : '전통시장·대중교통'}, 최대 ${u.won(extraCap)})`, u.won(extra)],
        ['신용카드 등 소득공제액', u.won(deduction)],
      ];
      if (total <= threshold) {
        notes.push(`사용액이 총급여의 25%(${u.won(threshold)})를 넘지 않아 공제가 없어요. ${u.won(threshold - total + 1)} 이상 더 써야 공제가 시작돼요.`);
      } else if (credit >= threshold) {
        notes.push('25% 문턱은 신용카드 사용액으로 다 채워졌어요. 남은 기간에는 체크카드·현금영수증(30%)으로 쓰는 쪽이 공제가 커요.');
      }
      if (lost > 0) notes.push(`한도를 넘어 ${u.won(lost)}은 공제받지 못해요.`);
      notes.push('소득공제액은 세금에서 바로 빠지는 돈이 아니라 과세표준에서 빠지는 금액이에요. 실제로 줄어드는 세금은 이 금액 × 내 세율(6~45%)에 지방소득세 10%를 더한 정도예요.');
      notes.push('보험료, 학교 수업료, 세금·공과금, 아파트관리비, 상품권 구입, 자동차 구입(중고차는 10%만 인정), 해외 사용분은 사용액에 넣지 않아요.');
      return { values, rows, notes };
    },
  });
})();
