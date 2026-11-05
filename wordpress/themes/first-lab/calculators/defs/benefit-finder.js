/* 지원금·혜택 찾기: 에너지바우처·실업급여·국민내일배움카드·근로장려금·자녀장려금·기초연금을 '받을 가능성 있음 / 조건 더 확인 / 해당 안 됨'으로 나눔.
 * 기준 금액·나이·날짜는 모두 data/benefit-finder.json (해마다 그 파일만 고친다). 자격을 확정하지 않는다 — 결정은 각 기관이 한다.
 *
 * 대원칙 (11/5 독립 검수: 받을 수 있는 사람에게 '해당 안 됨'이 나온 경우들):
 *   '해당 안 됨'은 원문상 확실할 때만. 입력으로 판단할 수 없는 예외(기준기간 연장, 직계존속 70세 판정일, 직역연금 일시금 등)가 있으면 '조건 더 확인'.
 *   서로 모순되는 입력은 계산하지 않고 안내 문장으로 막는다.
 */
(function () {
  'use strict';

  const LIKELY = 'likely';
  const CHECK = 'check';
  const NO = 'no';

  const OPTIONS = {
    welfare: [['none', '받지 않아요'], ['livelihood', '생계급여를 받아요 (다른 급여 함께 받는 경우 포함)'], ['other', '생계급여 없이 의료·주거·교육급여 중 하나 이상을 받아요']],
    trait: [
      ['none', '해당 없어요'],
      ['elderly', '노인 세대원 (올해 만 65세가 되는 해 또는 그 전에 태어남)'],
      ['disabled', '등록 장애인'],
      ['infant', '영유아 (7세 이하 취학 전 아동)'],
      ['pregnant', '임산부 (임신 중이거나 출산 후 6개월 미만)'],
      ['disease', '중증·희귀·중증난치질환자'],
      ['single_parent', '한부모가족'],
      ['child_head', '소년소녀가정 (가정위탁 포함)'],
      ['multi_child', '다자녀 (부 또는 모와 19세 미만 자녀 2명 이상)'],
    ],
    job: [
      ['employed', '회사에 다니고 있어요 (재직자)'],
      ['public', '공무원·사립학교 교직원으로 재직 중이에요'],
      ['unemployed', '퇴사하고 일자리를 찾고 있어요'],
      ['self', '자영업·프리랜서·법인 대표로 일하고 있어요'],
      ['student', '학생이에요 (고등학생·대학생)'],
      ['none', '일하지 않고 구직도 안 해요 (주부·은퇴 등)'],
    ],
    quit: [
      ['involuntary', '회사 사정 (해고·경영상 권고사직·계약기간 만료·정년·폐업 등)'],
      ['voluntary_just', '스스로 그만뒀지만 법에서 정한 정당한 사유가 있어요 (퇴사 전 1년 안에 임금체불 2개월 이상, 왕복 통근 3시간 이상 등)'],
      ['voluntary', '개인 사정으로 스스로 그만뒀고 정당한 사유는 없어요 (이직·창업 준비 포함)'],
      ['misconduct', '형법·직무 관련 법 위반으로 금고 이상 형, 장기 무단결근 등 중대한 잘못으로 해고됐어요'],
    ],
    yesno: [['no', '아니요'], ['yes', '네']],
    spouse: [
      ['none', '배우자 없음'],
      ['one', '배우자 있음 (부부 중 한 명이라도 작년 근로·사업·종교인소득 300만 원 미만)'],
      ['dual', '배우자 있음 (부부 각각 작년 근로·사업·종교인소득 300만 원 이상)'],
    ],
    parent70: [
      ['no', '없어요'],
      ['yes', '있어요 (작년 내내 70세 이상이었거나 장애인)'],
      ['near', '작년에 70세가 된 분이라 애매해요'],
    ],
  };

  const won = (n) => Math.round(n).toLocaleString('ko-KR') + '원';
  /** 240000000 → '2억 4,000만 원' (만 원 단위가 아니면 '…원') */
  const manwon = (n) => {
    if (n % 10000 !== 0) return won(n);
    const eok = FirstLabCalc.util.floor(n / 100000000);
    const man = (n % 100000000) / 10000;
    return [eok ? `${eok.toLocaleString('ko-KR')}억` : '', man ? `${man.toLocaleString('ko-KR')}만` : ''].filter(Boolean).join(' ') + ' 원';
  };
  const ymd = (iso) => iso.split('-').map(Number);
  /** 만 나이: 생일 당일부터 한 살 (2월 29일생은 평년엔 3월 1일부터) */
  const fullAge = (birthIso, todayIso) => {
    const [by, bm, bd] = ymd(birthIso);
    const [ty, tm, td] = ymd(todayIso);
    return ty - by - (tm < bm || (tm === bm && td < bd) ? 1 : 0);
  };

  FirstLabCalc.register({
    id: 'benefit-finder',
    title: '나에게 맞는 지원금·혜택 찾기',
    intro: '생년월일과 가구, 소득·재산, 일하는 상태를 고르면 에너지바우처·실업급여·국민내일배움카드·근로장려금·자녀장려금·기초연금을 받을 가능성이 있는지 알려 줘요. 최종 결정은 각 기관이 해요.',
    inputs: [
      { name: 'birth', label: '내 생년월일', type: 'date', default: '' },
      { name: 'today', label: '기준일 (보통 오늘)', type: 'date', default: 'today', help: '나이와 신청 기간을 이 날짜로 따져요.' },
      { name: 'household_size', label: '주민등록상 세대원 수 (나 포함)', type: 'number', min: 1, step: 1, default: 1, help: '에너지바우처 금액을 정할 때 써요.' },
      { name: 'welfare', label: '기초생활보장 급여 (세대 기준)', type: 'select', default: 'none', options: OPTIONS.welfare },
      { name: 'trait', label: '세대원 중 해당하는 사람 (하나만 골라도 돼요)', type: 'select', default: 'none', options: OPTIONS.trait, help: '에너지바우처 세대원 특성 기준이에요. 내 생년월일이 노인 기준 이전이면 따로 고르지 않아도 노인으로 봐요.' },
      { name: 'job', label: '지금 일하는 상태', type: 'select', default: 'employed', options: OPTIONS.job },
      { name: 'quit_reason', label: '퇴사 사유 (퇴사한 경우)', type: 'select', default: 'involuntary', options: OPTIONS.quit },
      { name: 'insured_days', label: '퇴사 전 18개월 동안 고용보험 피보험 단위기간 (일, 퇴사한 경우)', type: 'number', min: 0, step: 1, default: '', help: '임금 지급의 기초가 된 날(유급휴일 포함)을 더한 날수예요. 고용24에서 조회할 수 있어요.' },
      { name: 'base_extend', label: '퇴사 전 18개월 안에 질병·부상 등으로 30일 넘게 연달아 임금을 못 받았거나, 주 15시간 미만·주 2일 이하로 일했나요? (퇴사한 경우)', type: 'select', default: 'no', options: OPTIONS.yesno, help: '그렇다면 기준기간이 18개월보다 길어져요 (최대 3년, 또는 24개월).' },
      { name: 'spouse', label: '배우자', type: 'select', default: 'none', options: OPTIONS.spouse },
      { name: 'children', label: '부양자녀 수', type: 'number', min: 0, step: 1, default: 0, help: '작년 중 하루라도 18세 미만이었던 자녀(장애인 자녀는 나이 상관없음)예요. 따로 사는 자녀, 부모가 없거나 부양할 수 없는 손자녀, 함께 사는 그런 형제자매도 넣어요. 각자 연 소득금액 100만 원 이하여야 해요.' },
      { name: 'parent70', label: '함께 사는 70세 이상 또는 장애인 직계존속 (부모·조부모, 사망한 전 배우자의 부모 포함, 각 연 소득금액 100만 원 이하)', type: 'select', default: 'no', options: OPTIONS.parent70 },
      { name: 'earned', label: '작년에 근로·사업·종교인소득이 있었나요? (나 또는 배우자)', type: 'select', default: 'no', options: OPTIONS.yesno },
      { name: 'income', label: '나와 배우자의 작년 총소득 합계 (원)', type: 'number', min: 0, step: 1, default: 0, help: '근로소득은 총급여액, 사업소득은 총수입금액 × 업종별 조정률, 종교인소득은 총수입금액, 기타소득은 총수입금액 − 필요경비, 이자·배당·연금소득은 총수입금액을 더해요. 비과세·퇴직·양도소득은 빼요.' },
      { name: 'property', label: '가구원 모두의 재산 합계 (원)', type: 'number', min: 0, step: 1, default: 0, help: '주택·토지·건축물과 승용자동차(영업용 제외)는 시가표준액, 전세금은 주택이면 간주전세금(기준시가 × 55%)과 실제 전세금 중 작은 금액, 그리고 금융자산·유가증권·회원권 등을 더해요. 빚은 빼지 않아요.' },
      { name: 'public_pension', label: '나나 배우자가 공무원·사립학교교직원·군인·별정우체국 연금(퇴직연금·유족연금 등)을 받을 권리가 있나요?', type: 'select', default: 'no', options: OPTIONS.yesno, help: '기초연금을 따질 때 써요.' },
    ],

    compute(v, d, u) {
      const text = (name) => (v[name] === undefined || v[name] === null ? '' : String(v[name]).trim());
      const pick = (name, list, msg) => {
        const value = text(name) === '' ? list[0][0] : text(name);
        if (!list.some(([key]) => key === value)) throw new u.InputError(msg);
        return value;
      };
      const count = (name, msg, min) => {
        const n = Number(text(name));
        if (text(name) === '' || !Number.isInteger(n) || n < min) throw new u.InputError(msg);
        return n;
      };
      const amount = (name, msg) => {
        const n = Number(text(name));
        if (text(name) === '' || !Number.isFinite(n) || n < 0) throw new u.InputError(msg);
        return n;
      };
      const date = (name, msg) => {
        const t = u.parseDate(text(name));
        if (t === null) throw new u.InputError(msg);
        return u.isoDate(t);
      };

      const today = date('today', '기준일(오늘 날짜)을 넣어 주세요.');
      const birth = date('birth', '생년월일을 넣어 주세요. 없는 날짜면 다시 확인해 주세요.');
      if (birth > today) throw new u.InputError('생년월일이 기준일보다 늦어요. 다시 확인해 주세요.');
      const age = fullAge(birth, today);
      if (age > 130) throw new u.InputError('생년월일을 다시 확인해 주세요.');
      const size = count('household_size', '세대원 수를 1명 이상으로 넣어 주세요.', 1);
      const welfare = pick('welfare', OPTIONS.welfare, '기초생활보장 급여를 골라 주세요.');
      const trait = pick('trait', OPTIONS.trait, '세대원 특성을 골라 주세요.');
      const job = pick('job', OPTIONS.job, '일하는 상태를 골라 주세요.');
      const quit = pick('quit_reason', OPTIONS.quit, '퇴사 사유를 골라 주세요.');
      const extend = pick('base_extend', OPTIONS.yesno, '기준기간 항목을 골라 주세요.') === 'yes';
      const spouse = pick('spouse', OPTIONS.spouse, '배우자 항목을 골라 주세요.');
      const children = count('children', '부양자녀 수를 0 이상의 정수로 넣어 주세요.', 0);
      const parent70 = pick('parent70', OPTIONS.parent70, '직계존속 항목을 골라 주세요.');
      const earned = pick('earned', OPTIONS.yesno, '근로·사업·종교인소득 항목을 골라 주세요.') === 'yes';
      const income = amount('income', '작년 총소득을 0 이상으로 넣어 주세요. 소득이 없으면 0이에요.');
      const property = amount('property', '재산 합계를 0 이상으로 넣어 주세요. 재산이 없으면 0이에요.');
      const publicPension = pick('public_pension', OPTIONS.yesno, '직역연금 항목을 골라 주세요.') === 'yes';
      const ue = d.unemployment;
      let insuredDays = null;
      if (job === 'unemployed') {
        insuredDays = count('insured_days', '퇴사 전 18개월 동안의 고용보험 피보험 단위기간을 0 이상의 정수(일)로 넣어 주세요.', 0);
        const maxDays = extend ? ue.extended_max_days : ue.base_max_days;
        if (insuredDays > maxDays) {
          throw new u.InputError(`피보험 단위기간은 기준기간의 날수(${extend ? '최대 3년' : `${ue.base_months}개월`}, ${maxDays.toLocaleString('ko-KR')}일)보다 길 수 없어요.`);
        }
      }

      // 서로 모순되는 입력
      const e = d.eitc;
      if (spouse === 'dual' && !earned) throw new u.InputError('부부 각각 근로·사업·종교인소득이 300만 원 이상이면 \'작년에 근로·사업·종교인소득이 있었나요\'는 \'네\'여야 해요.');
      if (spouse === 'dual' && income < 2 * e.dual_each_min) throw new u.InputError(`부부 각각 ${manwon(e.dual_each_min)} 이상이면 총소득 합계가 ${manwon(2 * e.dual_each_min)} 이상이어야 해요. 소득이나 배우자 항목을 확인해 주세요.`);
      if (earned && income <= 0) throw new u.InputError('근로·사업·종교인소득이 있었다면 작년 총소득이 0원보다 커요. 소득을 넣어 주세요.');
      if (trait === 'multi_child' && size < 3) throw new u.InputError('다자녀 세대는 부 또는 모와 19세 미만 자녀 2명 이상이라 세대원이 3명 이상이어야 해요.');

      const inRange = (from, until) => (today < from ? 'before' : (today <= until ? 'open' : 'closed'));
      const results = {};

      // 1. 에너지바우처: 생계·의료·주거·교육급여 수급 세대 + 세대원 특성 하나 이상 (한국에너지공단 2026 지원대상)
      const ev = d.energy_voucher;
      const energyPeriod = inRange(ev.apply_from, ev.apply_until);
      {
        const elderly = birth <= ev.elderly_born_by; // 노인 = 주민등록기준 1961.12.31 이전 출생자 (나이 아니라 출생일로)
        const i = Math.min(size, ev.amounts.length) - 1;
        const sizeLabel = size >= ev.amounts.length ? `${ev.amounts.length}인 이상 세대` : `${size}인 세대`;
        if (welfare === 'none') {
          results.energy_voucher = [NO, '생계·의료·주거·교육급여 중 하나를 받는 세대만 받을 수 있어요.', 0];
        } else if (trait === 'none' && !elderly) {
          results.energy_voucher = [NO, `수급 세대라도 세대원 중 노인(${u.korDate(u.parseDate(ev.elderly_born_by))} 이전 출생)·장애인·영유아·임산부 등 특성 기준에 맞는 사람이 있어야 해요.`, 0];
        } else {
          const period = energyPeriod === 'closed' ? `올해 신청 기간 끝남 (${u.korDate(u.parseDate(ev.apply_until))}까지). `
            : (energyPeriod === 'before' ? `${u.korDate(u.parseDate(ev.apply_from))}부터 신청해요. ` : '');
          results.energy_voucher = [LIKELY, `${period}수급 세대 + 세대원 특성 기준 충족 → ${sizeLabel} 연 ${won(ev.amounts[i])}이에요. 연탄쿠폰·연탄전환 에너지바우처·긴급복지 연료비를 받으면 하절기분 ${won(ev.summer_only_amounts[i])}만 받아요. 세대원 모두 보장시설 수급자면 제외돼요.`, ev.amounts[i]];
        }
      }

      // 2. 실업급여(구직급여): 고용보험법 제40조·제58조·제10조③
      {
        if (job === 'public') {
          results.unemployment = [NO, '공무원·사립학교 교직원으로 재직 중이면 받을 수 없어요 (고용보험법 적용 제외).'];
        } else if (job === 'self') {
          results.unemployment = [NO, `사업을 하고 있으면 받을 수 없어요. 고용보험에 가입한 자영업자가 폐업하면 따로 따져요 (폐업 전 ${ue.self_months}개월 동안 피보험 단위기간 ${ue.self_insured_years}년 이상).`];
        } else if (job === 'employed') {
          results.unemployment = [NO, '재직 중이면 받을 수 없어요. 퇴사한 뒤 일할 의사와 능력이 있는데 취업하지 못한 상태여야 해요.'];
        } else if (job === 'none') {
          results.unemployment = [NO, '근로 의사가 있고 재취업 활동을 적극적으로 해야 받을 수 있어요. 퇴사 후 구직 중이면 \'퇴사하고 일자리를 찾고 있어요\'를 골라 주세요.'];
        } else if (job === 'student') {
          results.unemployment = [CHECK, '학생이어도 고용보험에 가입해 일하다 퇴사하고 구직 중이면 받을 수 있어요. 그렇다면 \'퇴사하고 일자리를 찾고 있어요\'를 골라 다시 확인해요.'];
        } else if (quit === 'misconduct') {
          results.unemployment = [NO, '법에서 정한 중대한 귀책사유로 해고되면 수급자격이 없어요 (고용보험법 제58조 제1호). 해당하는지는 고용센터가 판단해요.'];
        } else if (quit === 'voluntary') {
          results.unemployment = [NO, '정당한 사유 없이 개인 사정으로 그만두면 받을 수 없어요 (제58조 제2호). 임금체불·통근 곤란 등 정당한 사유가 있다면 퇴사 사유를 다시 골라 보세요.'];
        } else if (insuredDays < ue.min_insured_days && extend) {
          results.unemployment = [CHECK, `18개월 기준으로는 ${insuredDays}일이지만, 기준기간이 늘어나면(최대 3년 또는 24개월) 그 기간의 피보험 단위기간이 ${ue.min_insured_days}일 이상인지 다시 따져요.`];
        } else if (insuredDays < ue.min_insured_days) {
          results.unemployment = [NO, `퇴사 전 ${ue.base_months}개월 동안 피보험 단위기간이 ${ue.min_insured_days}일 이상이어야 해요 (지금 ${insuredDays}일).`];
        } else if (age >= ue.no_new_hire_age) {
          results.unemployment = [CHECK, `${ue.no_new_hire_age}세 이후에 새로 고용됐다면 받을 수 없고, ${ue.no_new_hire_age}세 전부터 고용보험을 유지하며 계속 일했다면 받을 수 있어요.`];
        } else {
          const why = quit === 'voluntary_just' ? ' 자진 퇴사의 정당한 사유는 고용센터가 증빙을 보고 인정해야 해요.' : '';
          results.unemployment = [LIKELY, `피보험 단위기간 ${insuredDays}일(${ue.min_insured_days}일 이상) + 수급 제한 사유 없음.${why} 적극적으로 재취업 활동을 해야 계속 받아요.`];
        }
      }

      // 3. 국민내일배움카드: 운영규정 제4조② 지원 제외 대상 (고용노동부고시 제2026-101호)
      {
        const nc = d.naeil_card;
        if (age >= nc.max_age) {
          results.naeil_card = [NO, `만 ${nc.max_age}세 이상은 지원 제외 대상이에요.`];
        } else if (job === 'public') {
          results.naeil_card = [NO, '재직 중인 공무원·사립학교 교직원은 지원 제외 대상이에요.'];
        } else if (welfare === 'livelihood') {
          results.naeil_card = [CHECK, '생계급여 수급자는 제외지만, 조건부 수급자나 조건 부과 유예자는 받을 수 있어요.'];
        } else if (job === 'student') {
          results.naeil_card = [CHECK, '고등학교 3학년, 졸업까지 남은 수업연한이 2년 이내인 대학생, 원격대학생은 받을 수 있고 고1·2와 졸업까지 2년 넘게 남은 대학생은 제외예요 (대학생이 고용보험 피보험자면 가능).'];
        } else if (job === 'self') {
          results.naeil_card = [CHECK, `사업자는 사업 기간 1년 미만이거나 최근 1년 매출 ${manwon(nc.self_revenue)} 이상(부동산 임대는 임대공급가액 ${manwon(nc.rental_supply)} 이상), 프리랜서 등 자기 계산으로 일하는 사람은 최근 3개월 월평균 소득 ${manwon(nc.freelancer_income)} 이상, 법인 대표는 사업 기간 1년 미만이거나 최근 1년 월평균 소득 ${manwon(nc.corp_ceo_income)} 이상이면 제외예요. 고용보험 피보험자면 지원받을 수 있어요.`];
        } else if (job === 'employed' && age < nc.large_firm_age) {
          results.naeil_card = [CHECK, `대규모기업에 다니고 최근 3개월 월평균 임금이 ${manwon(nc.large_firm_wage)} 이상이면 제외예요 (기간제·단시간·파견·일용, 180일 안 이직 예정, 육아휴직 등은 예외).`];
        } else {
          results.naeil_card = [LIKELY, `지원 제외 대상에 해당하지 않아 보여요. ${nc.card_years}년 동안 훈련비 ${manwon(nc.card_limit)}까지 지원돼요.`];
        }
      }

      // 4·5. 근로·자녀장려금 신청 기간 (정기: 5월 종합소득세 확정신고 기간, 기한 후: 그 다음 날부터 6개월, 95%)
      const korIso = (iso) => u.korDate(u.parseDate(iso));
      const eitcPeriod = today < e.regular_from ? 'before' : (today <= e.regular_until ? 'regular' : (today <= e.late_until ? 'late' : 'closed'));
      const periodText = {
        before: `${e.income_year}년 소득분은 ${korIso(e.regular_from)}부터 신청해요. `,
        regular: `${e.income_year}년 소득분 정기 신청은 ${korIso(e.regular_until)}까지예요. `,
        late: `${e.income_year}년 소득분 정기 신청은 끝났고 ${korIso(e.late_until)}까지 기한 후 신청하면 산정액의 ${e.late_rate_percent}%를 받아요. `,
        closed: `올해 신청 기간 끝남 (${e.income_year}년 소득분은 ${korIso(e.late_until)}까지). `,
      }[eitcPeriod];

      // 4. 근로장려금: 조세특례제한법 제100조의3 (총소득기준금액 '미만', 재산 2억4천만 원 '미만', ⑤ 가구 유형), 제100조의5④ (1억7천만 원 이상 50%)
      //    직계존속 70세 판정일은 법 조문에 없어 '작년에 70세가 된 분'은 단독·홑벌이 둘 다 따져 결과가 다르면 '조건 더 확인'
      let types;
      if (spouse === 'dual') types = ['dual'];
      else if (spouse === 'one' || children > 0 || parent70 === 'yes') types = ['one'];
      else types = parent70 === 'near' ? ['single', 'one'] : ['single'];
      const judgeEitc = (type) => {
        const limit = e.income_limit[type];
        const label = e.type_labels[type];
        if (!earned) return [NO, `${e.income_year}년에 근로·사업·종교인소득이 있어야 신청할 수 있어요.`, false];
        if (income >= limit) return [NO, `${label}는 부부 합산 총소득이 ${manwon(limit)} 미만이어야 해요 (입력 ${won(income)}).`, false];
        if (property >= e.property_limit) return [NO, `가구원 재산 합계가 ${manwon(e.property_limit)} 미만이어야 해요 (입력 ${won(property)}).`, false];
        const half = property >= e.property_half;
        return [LIKELY, `${label} 총소득 ${manwon(limit)} 미만 + 재산 ${manwon(e.property_limit)} 미만.${half ? ` 재산이 ${manwon(e.property_half)} 이상이라 산정액의 50%만 받아요.` : ''}`, half];
      };
      const judged = types.map(judgeEitc);
      let eitcType = types[0];
      let eitcReduced = judged[0][2];
      if (judged.length > 1 && judged[0][0] !== judged[1][0]) {
        eitcType = 'unsure';
        eitcReduced = judged[1][2];
        results.eitc = [CHECK, `직계존속이 70세 이상으로 인정되면 홑벌이 가구(${manwon(e.income_limit.one)} 미만), 아니면 단독가구(${manwon(e.income_limit.single)} 미만) 기준이에요. 국세청에 확인해요.`];
      } else {
        results.eitc = [judged[0][0], judged[0][1]];
      }
      if (results.eitc[0] !== NO) results.eitc[1] = periodText + results.eitc[1];

      // 5. 자녀장려금: 조세특례제한법 제100조의28 (부양자녀, 총소득 7천만 원 미만, 재산 2억4천만 원 미만), 제100조의31 → 제100조의5④ 준용
      let childReduced = false;
      {
        const c = d.child_credit;
        if (children === 0) {
          results.child_credit = [NO, '부양자녀가 있어야 해요.'];
        } else if (!earned) {
          results.child_credit = [NO, `${e.income_year}년에 근로·사업·종교인소득이 있어야 신청할 수 있어요.`];
        } else if (income >= c.income_limit) {
          results.child_credit = [NO, `부부 합산 총소득이 ${manwon(c.income_limit)} 미만이어야 해요 (입력 ${won(income)}).`];
        } else if (property >= c.property_limit) {
          results.child_credit = [NO, `가구원 재산 합계가 ${manwon(c.property_limit)} 미만이어야 해요 (입력 ${won(property)}).`];
        } else {
          childReduced = property >= c.property_half;
          const half = childReduced ? ` 재산이 ${manwon(c.property_half)} 이상이라 산정액의 50%만 받아요.` : '';
          results.child_credit = [LIKELY, `${periodText}부양자녀 ${children}명, 총소득 ${manwon(c.income_limit)} 미만 + 재산 ${manwon(c.property_limit)} 미만.${half}`];
        }
      }

      // 6. 기초연금: 만 65세 이상 + 소득인정액 ≤ 선정기준액 (기초연금법 제3조①), 직역연금 수급권자와 배우자 제외 (③)
      //    소득인정액은 공제 때문에 실제 소득보다 작거나 같다 → 공제 없이 계산한 상한(소득 전액 + (재산 - 금융재산 공제 2천만 원) × 연 4%)이
      //    선정기준액 이하면 '가능성 있음', 넘으면 공제(근로소득 116만 원·30%, 기본재산액)를 따져야 하므로 '조건 더 확인'.
      const pensionType = spouse === 'none' ? 'single' : 'couple';
      {
        const bp = d.basic_pension;
        const monthly = bp.threshold_month[pensionType];
        const typeLabel = pensionType === 'single' ? '단독가구' : '부부가구';
        const upper100 = 100 * income + bp.conversion_percent_year * Math.max(0, property - bp.financial_deduction);
        const limit100 = 100 * 12 * monthly;
        const [by, bm] = ymd(birth);
        const [ty, tm] = ymd(today);
        const monthsTo = (by + bp.min_age) * 12 + bm - (ty * 12 + tm); // 만 65세 생일이 속한 달까지 남은 달 수
        if (age < bp.min_age && monthsTo <= 1) {
          results.basic_pension = [CHECK, `${by + bp.min_age}년 ${bm}월에 만 ${bp.min_age}세가 돼요. 생일이 속한 달의 1개월 전부터 신청할 수 있으니 소득·재산 기준을 확인해요.`];
        } else if (age < bp.min_age) {
          results.basic_pension = [NO, `만 ${bp.min_age}세 이상부터 받아요. 만 ${bp.min_age}세 생일이 속한 달의 1개월 전부터 신청할 수 있어요.`];
        } else if (publicPension || job === 'public') {
          results.basic_pension = [CHECK, '공무원·사학·군인·별정우체국 연금의 퇴직연금·유족연금 등 수급권자와 그 배우자는 원칙적으로 제외예요. 퇴직일시금만 받았거나 특례에 해당하면 받을 수 있어 연금 종류를 확인해요.'];
        } else if (upper100 <= limit100) {
          results.basic_pension = [LIKELY, `공제 전 금액으로 따져도 소득인정액이 ${typeLabel} 선정기준액 월 ${won(monthly)} 이하예요.`];
        } else {
          results.basic_pension = [CHECK, `${typeLabel} 선정기준액은 월 ${won(monthly)}이에요. 근로소득 공제(${manwon(bp.earned_deduction)}을 뺀 뒤 ${bp.earned_extra_percent}% 더 공제)와 지역별 기본재산액 공제를 하면 이하일 수 있어요. 기초연금 누리집의 소득인정액 모의계산으로 확인해요.`];
        }
      }

      const ROWS = [
        ['energy_voucher', '에너지바우처'],
        ['unemployment', '실업급여 (구직급여)'],
        ['naeil_card', '국민내일배움카드'],
        ['eitc', '근로장려금'],
        ['child_credit', '자녀장려금'],
        ['basic_pension', '기초연금'],
      ];
      const rows = ROWS.map(([key, name]) => {
        const [status, reason] = results[key];
        const row = [name, `${d.status_labels[status]}: ${reason}`];
        const link = (d.links || {})[key];
        if (link) row.push(link);
        return row;
      });

      const applyKey = { energy_voucher: 'energy_voucher', unemployment: 'unemployment', naeil_card: 'naeil_card', eitc: 'eitc', child_credit: 'eitc', basic_pension: 'basic_pension' };
      const applyNotes = [...new Set(ROWS.filter(([key]) => results[key][0] !== NO).map(([key]) => d[applyKey[key]].apply))];
      const bp = d.basic_pension;
      const notes = [
        '이 결과는 입력한 내용으로 본 가능성일 뿐이에요. 받을 수 있는지와 금액은 각 기관이 소득·재산 조사와 서류를 보고 최종 결정해요.',
        ...applyNotes,
        `근로·자녀장려금은 ${e.income_year}년 소득과 ${e.income_year}년 6월 1일 기준 재산으로 따져요. 전문직 사업자, 다른 사람의 부양자녀인 사람, 대한민국 국적이 없는 사람(예외 있음)은 신청할 수 없어요.`,
        `기초연금은 본인과 배우자의 지금 소득·재산으로 따져요. ${manwon(bp.luxury_car)} 이상 고급 자동차·회원권은 그 가액을 그대로 소득으로 보고, 자녀 명의 시가표준액 ${manwon(bp.free_rent_house)} 이상 주택에 살면 연 ${bp.free_rent_rate}를 소득(무료임차소득)으로 봐요.`,
      ];

      return {
        values: {
          age,
          energy_voucher: results.energy_voucher[0],
          energy_amount: results.energy_voucher[2],
          energy_period: energyPeriod,
          unemployment: results.unemployment[0],
          naeil_card: results.naeil_card[0],
          eitc: results.eitc[0],
          eitc_type: eitcType,
          eitc_reduced: eitcReduced,
          eitc_period: eitcPeriod,
          child_credit: results.child_credit[0],
          child_reduced: childReduced,
          basic_pension: results.basic_pension[0],
          basic_pension_type: pensionType,
          likely_count: ROWS.filter(([key]) => results[key][0] === LIKELY).length,
        },
        rows,
        notes,
      };
    },
  });
})();
