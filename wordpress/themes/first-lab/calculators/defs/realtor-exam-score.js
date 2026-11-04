/* 공인중개사 합격 점수 계산기: 차수별 과락(과목 40점)·평균(60점) 판정. 기준은 data/realtor-exam-score.json */
(function () {
  'use strict';

  const fmt = (n) => `${Math.round(n * 100) / 100}점`;

  FirstLabCalc.register({
    id: 'realtor-exam-score',
    title: '공인중개사 합격 점수 계산기',
    intro: '과목별 점수(100점 만점)를 넣으면 과락 여부와 평균, 합격 여부를 바로 보여줘요.',
    inputs: [
      {
        name: 'stage', label: '어떤 시험을 봤나요', type: 'select', default: 'both',
        options: [
          ['both', '1·2차 함께 응시'],
          ['first', '1차만 응시'],
          ['second', '2차만 응시 (1차 면제)'],
        ],
      },
      { name: 's1', label: '1차 부동산학개론 점수', type: 'number', min: 0, step: 0.5, help: '1차를 봤다면 넣어요.' },
      { name: 's2', label: '1차 민법 및 민사특별법 점수', type: 'number', min: 0, step: 0.5 },
      { name: 's3', label: '2차 공인중개사법령 및 중개실무 점수', type: 'number', min: 0, step: 0.5, help: '2차를 봤다면 넣어요.' },
      { name: 's4', label: '2차 부동산공법 점수', type: 'number', min: 0, step: 0.5 },
      { name: 's5', label: '2차 부동산공시법령 및 세법 점수', type: 'number', min: 0, step: 0.5 },
    ],

    compute(v, d, u) {
      const stage = v.stage;
      if (!['both', 'first', 'second'].includes(stage)) throw new u.InputError('응시한 시험을 골라 주세요.');
      const read = (keys, name) => keys.map((k) => {
        const raw = v[k];
        if (raw === undefined || raw === null || raw === '') throw new u.InputError(`${name} 과목 점수를 모두 넣어 주세요.`);
        const n = Number(raw);
        if (!Number.isFinite(n) || n < 0 || n > d.full_score) throw new u.InputError(`점수는 0~${d.full_score}점 사이로 넣어 주세요.`);
        return n;
      });
      const judge = (scores) => {
        const avg = Math.round((scores.reduce((a, b) => a + b, 0) / scores.length) * 100) / 100;
        const fails = scores.filter((s) => s < d.subject_min).length;
        return { avg, fails, pass: fails === 0 && avg >= d.average_min };
      };
      const label = (r) => (r.pass ? '합격' : r.fails ? `불합격 (${d.subject_min}점 미만 ${r.fails}과목)` : `불합격 (평균 ${d.average_min}점 미만)`);

      const values = {};
      const rows = [];
      const notes = [`차수마다 매 과목 ${d.subject_min}점 이상, 전 과목 평균 ${d.average_min}점 이상이면 합격이에요.`];

      if (stage !== 'second') {
        const r1 = judge(read(['s1', 's2'], '1차'));
        Object.assign(values, { avg1: r1.avg, fail1: r1.fails, pass1: r1.pass });
        rows.push(['1차 평균', fmt(r1.avg)], ['1차 결과', label(r1)]);
      }
      if (stage !== 'first') {
        const r2 = judge(read(['s3', 's4', 's5'], '2차'));
        let pass2 = r2.pass;
        let text = label(r2);
        if (stage === 'both' && !values.pass1) {
          pass2 = false;
          text = `무효 (1차 불합격, 2차 점수 기준으로는 ${label(r2)})`;
          notes.push('1·2차를 함께 봤는데 1차에서 떨어지면 2차 점수는 무효가 돼요.');
        }
        Object.assign(values, { avg2: r2.avg, fail2: r2.fails, pass2 });
        rows.push(['2차 평균', fmt(r2.avg)], ['2차 결과', text]);
      }
      if (stage === 'first' && values.pass1) notes.push('1차에 합격하면 다음 회 시험에 한해 1차가 면제돼요.');
      if (stage === 'both' && values.pass1 && !values.pass2) notes.push('1차 합격은 인정돼서 다음 회 시험에서는 1차가 면제돼요.');
      values.final = stage === 'first' ? false : values.pass2;
      rows.push(['최종 합격', stage === 'first' ? '2차까지 합격해야 자격 취득' : values.final ? '합격' : '불합격']);
      notes.push('가채점 점수로 본 결과예요. 실제 합격 여부는 큐넷 최종 발표로 확인해요.');
      return { values, rows, notes };
    },
  });
})();
