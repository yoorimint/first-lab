/* 청약가점 계산기: 무주택기간(32)·부양가족수(35)·청약통장 가입기간(17) = 84점. 배점은 data/cheongyak-score.json
 * 근거: 주택공급에 관한 규칙 별표 1 (가점 산정기준·배우자 통장 50% 합산 최대 3점, 합계 17점 한도)
 *   무주택기간: 만 30세가 되는 날부터 (30세 전에 혼인했으면 혼인신고일부터), 주택을 처분했으면 최근 무주택이 된 날부터 (별표 1 제1호가목3)
 *   만 30세 미만 미혼·유주택은 0점 (청약홈 가점계산기 안내)
 *   미성년 때 가입한 기간: 2023년 12월 31일 이전 부분은 2년까지, 2024년 이후 부분과 합쳐 5년까지 (제10조제6항)
 * 기간은 날짜 기준 만 개월·만 년 (공고일이 시작일 + N년 날짜 이상이면 N년). */
(function () {
  'use strict';

  /** t + k개월의 같은 날짜. 그 달에 그 날짜가 없으면 다음 달 1일 */
  function plusMonths(t, k) {
    const d = new Date(t);
    const y = d.getUTCFullYear();
    const m = d.getUTCMonth() + k;
    const day = d.getUTCDate();
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return day <= last ? Date.UTC(y, m, day) : Date.UTC(y, m + 1, 1);
  }
  /** from 부터 to 까지 만 개월 수 (to < from 이면 0) */
  function fullMonths(from, to) {
    if (to < from) return 0;
    let lo = 0;
    let hi = 12 * 200;
    while (lo < hi) { // plusMonths(from, k) <= to 인 가장 큰 k
      const mid = (lo + hi + 1) >> 1;
      if (plusMonths(from, mid) <= to) lo = mid; else hi = mid - 1;
    }
    return lo;
  }
  const ym = (months) => `${FirstLabCalc.util.floor(months / 12)}년 ${months % 12}개월`;

  FirstLabCalc.register({
    id: 'cheongyak-score',
    title: '청약가점 계산기',
    intro: '생년월일·혼인신고일·주택 소유 이력·부양가족 수·청약통장 가입일을 넣으면 항목별 점수와 84점 만점 기준 총점이 나와요.',
    inputs: [
      { name: 'announce', label: '입주자모집공고일 (모르면 오늘)', type: 'date', default: 'today' },
      { name: 'birth', label: '본인 생년월일', type: 'date', default: '1991-05-01' },
      { name: 'married', label: '혼인 여부', type: 'select', default: 'no', options: [['no', '미혼'], ['yes', '기혼 (혼인신고 함)']] },
      { name: 'marriage', label: '혼인신고일 (기혼일 때)', type: 'date', default: '' },
      {
        name: 'house', label: '본인·세대원 주택 소유 (분양권 포함)', type: 'select', default: 'never',
        options: [['never', '세대원 모두 주택을 가진 적 없음'], ['past', '예전에 있었고 지금은 세대원 모두 무주택'], ['now', '지금 세대원 중 주택 소유자가 있음 (소형·저가주택 1채, 60세 이상 부모님 집 등 무주택으로 보는 경우 제외)']],
      },
      { name: 'nohouse_since', label: '마지막으로 무주택이 된 날 (예전에 주택이 있었을 때)', type: 'date', default: '' },
      { name: 'dependents', label: '부양가족 수 (본인 제외)', type: 'number', min: 0, step: 1, default: 0, help: '같은 등본의 배우자, 미혼 자녀(만 30세 이상은 최근 1년 이상 같은 등본), 본인(배우자와 등본이 따로면 배우자)이 세대주일 때 3년 이상 같은 등본에 있는 부모·조부모 등. 주택을 가진 부모님은 두 분 모두 빠져요(소형·저가주택 등 무주택으로 보는 집이면 인정, 60세 이상 예외는 적용 안 됨).' },
      { name: 'account', label: '본인 청약통장 최초 가입일', type: 'date', default: '2016-06-01' },
      { name: 'spouse_account', label: '배우자 청약통장 최초 가입일 (없으면 비움)', type: 'date', default: '' },
    ],

    compute(v, d, u) {
      const f = FirstLabCalc.util.floor;
      const announce = u.parseDate(v.announce === 'today' || !v.announce ? u.todayIso() : v.announce);
      const birth = u.parseDate(v.birth);
      if (announce === null) throw new u.InputError('입주자모집공고일을 넣어 주세요.');
      if (birth === null || birth >= announce) throw new u.InputError('생년월일을 공고일보다 앞 날짜로 넣어 주세요.');
      const married = v.married === 'yes';
      const marriage = married ? u.parseDate(v.marriage) : null;
      if (married && (marriage === null || marriage > announce)) throw new u.InputError('혼인신고일을 공고일 이전 날짜로 넣어 주세요.');
      const house = v.house;
      if (!['never', 'past', 'now'].includes(house)) throw new u.InputError('주택 소유 여부를 골라 주세요.');
      const deps = Number(v.dependents);
      if (!Number.isInteger(deps) || deps < 0) throw new u.InputError('부양가족 수를 0 이상 정수로 넣어 주세요.');
      const account = u.parseDate(v.account);
      if (account === null || account > announce) throw new u.InputError('청약통장 가입일을 공고일 이전 날짜로 넣어 주세요.');
      if (account < birth) throw new u.InputError('청약통장 가입일이 생년월일보다 앞이에요.');

      const notes = [];
      // ① 무주택기간
      const age30 = plusMonths(birth, d.age_start * 12);
      let nohouseMonths = 0;
      let nohousePts = 0;
      let nohouseText;
      if (house === 'now') {
        nohouseText = '0점 (주택 소유)';
        notes.push('지금 세대원 중 주택을 가진 사람이 있으면 무주택기간은 0점이고, 투기과열지구·청약과열지역 등에서는 1주택 세대도 가점제 대상에서 빠져 추첨제로만 신청해요. 소형·저가주택 1채만 있거나 60세 이상 부모님 집이라 무주택으로 보는 경우라면 \'세대원 모두 주택을 가진 적 없음\' 또는 \'예전에 있었고 지금은 세대원 모두 무주택\'을 골라 다시 계산해요.');
      } else {
        let start = married && marriage < age30 ? marriage : age30;
        if (house === 'past') {
          const since = u.parseDate(v.nohouse_since);
          if (since === null || since > announce) throw new u.InputError('마지막으로 무주택이 된 날(주택을 판 날 등)을 공고일 이전 날짜로 넣어 주세요.');
          if (since > start) start = since;
        }
        if (start > announce) {
          nohouseText = '0점 (만 30세 미만 미혼)';
          notes.push('만 30세 미만 미혼이면 무주택기간이 아직 시작되지 않아 0점이에요. 30세 전에 혼인신고를 하면 그날부터 셉니다.');
        } else {
          nohouseMonths = fullMonths(start, announce);
          nohousePts = Math.min(d.nohouse_max, d.nohouse_base + d.nohouse_step * f(nohouseMonths / 12));
          nohouseText = `${nohousePts}점 (${ym(nohouseMonths)}, ${u.korDate(start)}부터)`;
        }
      }
      // ② 부양가족수
      const depPts = Math.min(d.dependent_max, d.dependent_base + d.dependent_step * deps);
      // ③ 청약통장 가입기간 (미성년 기간 인정 한도)
      const adultDay = plusMonths(birth, d.adult_age * 12);
      let effStart = account;
      let minorNote = false;
      if (account < adultDay) {
        const minorEnd = Math.min(adultDay, announce);
        const cut = Date.UTC(2024, 0, 1);
        const preEnd = Math.min(minorEnd, cut);
        const pre = Math.max(0, u.daysBetween(Math.max(account, plusMonths(preEnd, -12 * d.minor_pre2024_max_years)), preEnd));
        const post = Math.max(0, u.daysBetween(Math.max(account, cut), minorEnd));
        const credit = Math.min(pre + post, u.daysBetween(plusMonths(minorEnd, -12 * d.minor_max_years), minorEnd));
        if (credit < u.daysBetween(account, minorEnd)) minorNote = true;
        effStart = u.addDays(Math.max(account, minorEnd), -credit);
      }
      const accMonths = fullMonths(effStart, announce);
      const ownPts = accMonths < 6 ? 1 : accMonths < 12 ? 2 : Math.min(d.account_max, 2 + f(accMonths / 12));
      if (minorNote) notes.push(`미성년자 때 가입한 기간은 2023년 12월 31일 이전 부분은 ${d.minor_pre2024_max_years}년까지, 2024년 이후 부분과 합쳐 ${d.minor_max_years}년까지만 인정해서 줄여 계산했어요.`);
      // 배우자 통장: 가입기간의 50% 기준 6개월 미만 1점, 6개월~1년 2점, 1년 이상 3점 (최대 3점)
      let spousePts = 0;
      let spouseMonths = null;
      if (v.spouse_account) {
        const sp = u.parseDate(v.spouse_account);
        if (sp === null || sp > announce) throw new u.InputError('배우자 청약통장 가입일을 공고일 이전 날짜로 넣어 주세요.');
        if (!married) throw new u.InputError('배우자 통장 점수는 기혼일 때만 더해요. 혼인 여부를 확인해 주세요.');
        spouseMonths = fullMonths(sp, announce);
        spousePts = Math.min(d.spouse_max, spouseMonths < 12 ? 1 : spouseMonths < 24 ? 2 : 3);
        notes.push('노부모 부양 특별공급에서는 배우자 통장 점수를 더하지 않아요 (주택공급에 관한 규칙 별표 1 비고 2).');
      }
      const accPts = Math.min(d.account_max, ownPts + spousePts);
      const total = nohousePts + depPts + accPts;

      const rows = [
        ['① 무주택기간 (최대 32점)', nohouseText],
        ['② 부양가족수 (최대 35점)', `${depPts}점 (${deps}명)`],
        ['③ 본인 청약통장', `${ownPts}점 (${ym(accMonths)})`],
      ];
      if (spouseMonths !== null) rows.push(['③ 배우자 통장 더하기 (최대 3점)', `${spousePts}점 (배우자 ${ym(spouseMonths)}의 50% 기준)`]);
      rows.push(['③ 청약통장 합계 (최대 17점)', `${accPts}점`], ['총점 (84점 만점)', `${total}점`]);
      notes.push('점수가 같으면 청약통장 가입기간이 긴 사람이 먼저 뽑혀요. 실제 점수는 입주자모집공고일 기준으로 청약홈에서 확인해요.');
      return { values: { nohouse: nohousePts, nohouse_months: nohouseMonths, dependents: depPts, own_account: ownPts, spouse: spousePts, account: accPts, total }, rows, notes };
    },
  });
})();
