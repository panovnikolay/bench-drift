/* The page's data model over what the utility emits: a generated corpus
   (tests/make_sample_runs.py — three boards, planted scenarios, holes) and a
   history built from the real sample report.

     node --test tests/

   The planted scenarios are described in tests/make_sample_runs.py; the
   corpus numbers pinned here are written down in CLAUDE.md under "The test
   corpus" — when one legitimately changes, change it there first. */
const test=require("node:test"), assert=require("node:assert/strict");
const fs=require("fs"), path=require("path");
const {loadModel, sevCounts, noisyCount, tmpdir, dumpPayload, boardArgs, makeCorpus, makeHistory, makeSnapshot}=require("./model.js");

/* one corpus per day count per process — the model is small (37 benchmarks),
   the generator and the dump are what cost time */
const corpora=new Map();
function corpus(days){
  if(corpora.has(days)) return corpora.get(days);
  const dir=makeCorpus(days);
  const c={dir, payload:dumpPayload(boardArgs(dir))};
  corpora.set(days, c);
  return c;
}

/* a payload of one board, one benchmark, two reports — the smallest history
   there is, with the dates and the two medians given */
function twoReports(dates, med){
  const runs=dates.map((date,i)=>({date, day:date.slice(0,10), report:"ab"[i]+".json"}));
  return {metric:"cpu_time", reps:2,
    boards:[{id:"b", host:"", note:"", report:"b.json", runs}],
    benchmarks:[{name:"BM_A/1", fam:"BM_A", tmpl:"", arg:1, threads:1, group:"g",
      s:{b:{med, p25:med, p75:med, cv:[0,0], iters:1, reps:2}}}]};
}

/* The 90-day corpus is 91 reports on qemu-x86: day 85 (five from the end)
   has a re-run a few hours after its report, so columns and days part ways
   from there — column 86 is the re-run, columns 87..90 the last four days. */
const RERUN=86;

/* ---------------------------------------------------------------- controls */
test("option lists derive from the history length", ()=>{
  const m=loadModel(corpus(90).payload);
  assert.equal(m.get("NRUNS"), 91);
  assert.deepEqual(m.get("BASE_OPTS"), [7,14,30,45]);
  assert.deepEqual(m.get("BASEWIN_OPTS"), [1,3,5,9,15,21]);
  assert.deepEqual(m.get("WIN_OPTS"), [90,60,30,14,7,3,1]);   // 90 days is the limit
  assert.equal(m.get("baseBack"), 7);
  assert.equal(m.get("baseWin"), 5);
  assert.equal(m.get("winDays"), 14);                           // the default view: the last two weeks
});

test("display window counts calendar days and survives launch jitter", ()=>{
  // the corpus launches every day at a jittered hour; the window is by day —
  // and a day with two reports puts both of them inside any window that holds it
  const m=loadModel(corpus(90).payload);
  for(const d of m.get("WIN_OPTS")){
    m.run(`winDays=${d}; recomputeWindow();`);
    const wn=d+(d>=5?1:0);
    assert.equal(m.get("wn"), wn, "window "+d);
    assert.equal(m.get("w0"), 91-wn);
  }
});

test("a 1-day window holds every point of the latest day", ()=>{
  // a folder whose last day has two reports: the corpus's qemu-x86 up to and
  // including the re-run (filenames carry the time, so they sort by launch),
  // read by the utility like any other folder — the seam, not a trimmed model
  const {dir}=corpus(90), src=path.join(dir,"qemu-x86"), cut=tmpdir("bd-lastday-");
  fs.mkdirSync(path.join(cut,"qemu-x86"));
  for(const f of fs.readdirSync(src).sort().slice(0,RERUN+1)) fs.copyFileSync(path.join(src,f), path.join(cut,"qemu-x86",f));
  const m=loadModel(dumpPayload(["--board","qemu-x86="+path.join(cut,"qemu-x86")]));
  m.run("winDays=1; recomputeWindow();");
  assert.equal(m.get("NRUNS"), RERUN+1);
  assert.equal(m.get("runs")[RERUN-1].day, m.get("runs")[RERUN].day, "the last two columns are one day");
  assert.equal(m.get("wn"), 2);
  assert.equal(m.get("w0"), RERUN-1);
  m.run("winDays=3; recomputeWindow();");
  assert.equal(m.get("wn"), 4);                            // three days, one of them with two reports
});

test("the day of a report is the day it says, in its own offset, not the UTC day", ()=>{
  // a launch at 02:00+03:00 is 23:00 UTC the evening before: by UTC the two
  // reports share a day, by the report's own clock they are two days
  const m=loadModel(twoReports(["2026-09-10T03:00:00+03:00","2026-09-11T02:00:00+03:00"], [1,1]));
  assert.deepEqual(m.get("runs").map(r=>r.day), ["2026-09-10","2026-09-11"]);
  assert.deepEqual(m.get("WIN_OPTS"), [3,1]);              // a span of two days
  m.run("winDays=1; recomputeWindow();");
  assert.equal(m.get("wn"), 1, "one day, one point — the UTC day would give two");
  assert.equal(m.get("w0"), 1);
});

test("switching boards switches the timeline, not just the series", ()=>{
  const {payload}=corpus(40);
  const m=loadModel(payload);
  assert.ok(!("runs" in payload), "no shared axis in the payload");
  const expect={"qemu-x86":41, board1:39, board2:40};    // a re-run, a lost day, neither
  for(const [b,n] of Object.entries(expect)){
    m.run(`useBoard('${b}');`);
    assert.equal(m.get("NRUNS"), n, b);
    const runs=m.get("runs"), bd=m.get("BOARDS").find(x=>x.id===b);
    assert.equal(runs.length, n);
    assert.equal(runs[n-1].report, bd.report, b+": the header names the latest column's file");
    assert.ok(runs.every((r,i)=>!i||r.date>=runs[i-1].date), b+": launch order");
    assert.ok(runs.every(r=>/\.json$/.test(r.report)), b+": every point has its file");
    assert.equal(m.get("reportMoves").length, n, b+": movement bars follow the board");
    const A=m.get("A"), insts=m.get("insts");
    for(let i=0;i<m.get("NBM");i++) if(A[i]) assert.equal(insts[i].med.length, n, insts[i].name);
  }
  // qemu-x86's two reports of one day are two points, hours apart
  m.run("useBoard('qemu-x86');");
  const runs=m.get("runs");
  assert.equal(runs[35].day, runs[36].day);
  assert.ok(/^\d{4}-\d{2}-\d{2}$/.test(runs[36].day), "every point carries its calendar day");
  assert.ok(runs[36].date-runs[35].date>3*3600e3&&runs[36].date-runs[35].date<6*3600e3);
  assert.notEqual(runs[35].report, runs[36].report);
});

test("baseline: the reference report is a calendar-day offset, the window a count of reports", ()=>{
  const m=loadModel(corpus(90).payload);
  m.run("useBoard('qemu-x86');");
  const insts=m.get("insts"), ids=m.get("activeIds"), runs=m.get("runs"), last=runs[90].dn;
  const median=a=>{ const s=Array.from(a).sort((x,y)=>x-y), n=s.length; return n%2?s[(n-1)/2]:(s[n/2-1]+s[n/2])/2; };
  const before=sevCounts(m);
  // the reference is the board's last column at least baseBack days older
  // than its newest; column 86 is a re-run, so past it "−7 days" is column
  // 82 (day 82), not column 83 = NRUNS-1-7 — a day is not a column
  for(const [back,from,at] of [[7,78,82],[14,71,75],[30,55,59],[45,40,44]]){
    m.run(`baseBack=${back}; baseWin=5; recomputeBaseline();`);
    assert.deepEqual([m.get("baseFrom"),m.get("baseAt")], [from,at], "baseBack "+back);
    assert.notEqual(at, 91-1-back, "days, not columns");
    assert.ok(runs[at].dn<=last-back&&runs[at+1].dn>last-back, "the last column that is old enough");
  }
  // the window is baseWin columns ending at the reference, the median per benchmark
  for(const [w,from] of [[1,59],[3,57],[5,55],[9,51],[15,45],[21,39]]){
    m.run(`baseBack=30; baseWin=${w}; recomputeBaseline();`);
    assert.deepEqual([m.get("baseFrom"),m.get("baseAt")], [from,59], "baseWin "+w);
    const base=m.get("baseArr");
    for(const i of ids){ const e=median(insts[i].med.slice(from,60)); assert.ok(Math.abs(e-base[i])<=1e-9*e, insts[i].name+" at baseWin "+w); }
  }
  // neither selector touches the verdicts: the detector looks for a step, not
  // a deviation from a reference
  assert.deepEqual(sevCounts(m), before);
  // and a wider median calms Δ on the channels that have no step or drift:
  // the stable verdicts, minus the family the corpus plants a drift on
  // (BM_LzDecompress<snappy>, +0.31 %/report — a drift is not a step)
  const A=m.get("A");
  const quiet=ids.filter(i=>A[i].sev==="stable"&&insts[i].fam!=="BM_LzDecompress");
  assert.ok(quiet.length>=10, "enough quiet channels to average: "+quiet.length);
  const meanAbs=()=>{ const d=m.get("dBase"); let s=0; for(const i of quiet) s+=Math.abs(d[i]); return s/quiet.length; };
  m.run("baseWin=1; recomputeBaseline();"); const one=meanAbs();
  m.run("baseWin=21; recomputeBaseline();"); const wide=meanAbs();
  assert.ok(wide<0.8*one, `mean |Δ| on quiet channels: ${one} at 1 report, ${wide} at 21`);
  // the same rule over every board's own columns — board1 lost a day,
  // board2 has one report a day — and the same median
  for(const b of ["board1","board2"]){
    m.run(`useBoard('${b}'); baseBack=14; baseWin=3; recomputeBaseline();`);
    const rs=m.get("runs"), n=rs.length, l=rs[n-1].dn;
    let at=n-1; while(at>0&&rs[at].dn>l-14) at--;
    assert.deepEqual([m.get("baseFrom"),m.get("baseAt")], [at-2,at], b);
    const base=m.get("baseArr"), ins=m.get("insts");
    for(const i of m.get("activeIds")){ const e=median(ins[i].med.slice(at-2,at+1)); assert.ok(Math.abs(e-base[i])<=1e-9*e, b+" "+ins[i].name); }
  }
});

test("baseline offsets no report can reach are not offered", ()=>{
  // 40 days of history: nothing is 45 days older than the newest report
  let m=loadModel(corpus(40).payload);
  assert.deepEqual(m.get("BASE_OPTS"), [7,14,30]);
  // two reports on one day, hours apart: no offset is reachable, the
  // reference is the oldest report and the selector has nothing to offer
  m=loadModel(twoReports(["2026-09-10T03:00:00+03:00","2026-09-10T08:00:00+03:00"], [1,1.1]));
  m.run("useBoard('b');");
  assert.deepEqual(m.get("BASE_OPTS"), []);
  assert.deepEqual([m.get("baseFrom"),m.get("baseAt")], [0,0]);
  assert.ok(Math.abs(m.get("dBase")[0]-0.1)<1e-9, "Δ against the oldest report");
});

test("regression threshold: a bar the verdict, the chip and the dashed line all agree on", ()=>{
  const m=loadModel(corpus(90).payload);
  m.run("useBoard('qemu-x86');");
  assert.deepEqual(m.get("THR_OPTS"), [0.01,0.02,0.03,0.05,0.10]);
  assert.equal(m.get("THRESHOLD"), 0.03);
  const at=t=>{ m.run(`THRESHOLD=${t}; recomputeVerdicts();`); return sevCounts(m); };
  assert.deepEqual(at(0.03), {stable:15,regressed:18,improved:4});
  // nothing called a regression sits below the bar it is drawn against
  const A=m.get("A"), ids=m.get("activeIds");
  assert.equal(ids.filter(i=>A[i].sev==="regressed"&&Math.abs(A[i].cp.ratio-1)<0.03).length, 0);
  // the noise gate decides whether a step is real at all and does not move
  // with the bar: at 1 % every real step is in, and the noisy flag never changes
  const lo=at(0.01);
  assert.equal(lo.regressed+lo.improved, 23);
  assert.equal(noisyCount(m), 3);
  const hi=at(0.10);
  assert.deepEqual(hi, {stable:23,regressed:10,improved:4});
  assert.equal(noisyCount(m), 3);
  at(0.03);
  assert.deepEqual(sevCounts(m), {stable:15,regressed:18,improved:4});
  // the other boards, for the record — no re-run, no noisy channel on either
  m.run("useBoard('board1');"); assert.deepEqual(sevCounts(m), {stable:8,regressed:11,improved:3}); assert.equal(noisyCount(m), 0);
  m.run("useBoard('board2');"); assert.deepEqual(sevCounts(m), {stable:8,regressed:4,improved:3}); assert.equal(noisyCount(m), 0);
});

test("noisy is a flag, not a verdict: it survives on a regression", ()=>{
  const m=loadModel(corpus(90).payload);
  m.run("useBoard('qemu-x86');");
  const A=m.get("A"), ids=m.get("activeIds"), insts=m.get("insts");
  // BM_ThreadPool's thread multiplier raises the channel's CV; threads:32 is planted
  const onNoisy=ids.filter(i=>A[i].sev==="regressed"&&A[i].noisy);
  assert.deepEqual(onNoisy.map(i=>insts[i].name), ["BM_ThreadPool/threads:32/repeats:9"]);
  for(const i of onNoisy) assert.ok(A[i].noiseCv>0.03&&Math.abs(A[i].cp.ratio-1)>1.5*A[i].noiseCv);
  // and every flag is exactly the CV rule
  for(const i of ids) assert.equal(A[i].noisy, A[i].noiseCv>0.03);
});

/* ---------------------------------------------------------------- real data */
test("real data: the payload drives the model", ()=>{
  const {payload}=corpus(40);
  const m=loadModel(payload);
  assert.equal(m.get("NRUNS"), 41);                       // the first board's columns
  assert.deepEqual(payload.boards.map(b=>b.runs.length), [41,39,40]);
  assert.ok(payload.boards.every(b=>b.runs.every(r=>r.date&&r.report)));
  assert.equal(m.get("NBM"), payload.benchmarks.length);
  assert.ok(m.get("BOARDS").every(b=>b.report.endsWith(".json")), "every board names the report its numbers came from");
  assert.deepEqual(m.get("BOARDS").map(b=>b.id), ["qemu-x86","board1","board2"]);   // argument order
  assert.equal(m.get("METRIC"), "cpu_time");
  assert.deepEqual(m.get("WIN_OPTS"), [60,30,14,7,3,1]);   // 40 days: 60 covers, 90 dropped
  assert.deepEqual(m.get("BASE_OPTS"), [7,14,30]);          // 45 back leaves no room
});

test("real data: planted scenarios in the sample corpus are found", ()=>{
  const {payload}=corpus(40);
  const m=loadModel(payload);
  const insts=m.get("insts"), n=40;
  const sevOf=(board,pred)=>{ m.run(`useBoard('${board}');`); const A=m.get("A");
    return insts.map((x,i)=>pred(x)&&A[i]?A[i]:null).filter(Boolean); };
  const at=b=>Math.max(2,n-b);
  // Crc32 everywhere but board2, at n-16
  for(const a of sevOf("qemu-x86",i=>i.fam==="BM_Crc32")){ assert.equal(a.sev,"regressed"); assert.equal(a.cp.k, at(16)); }
  assert.ok(sevOf("board2",i=>i.fam==="BM_Crc32").every(a=>a.sev==="stable"));
  // FrameDecode only on board2, at n-11
  for(const a of sevOf("board2",i=>i.fam==="BM_FrameDecode")){ assert.equal(a.sev,"regressed"); assert.equal(a.cp.k, at(11)); }
  // one template only
  for(const a of sevOf("qemu-x86",i=>i.fam==="BM_FlatHashFind"&&i.tmpl==="<std::string>")) assert.equal(a.sev,"regressed");
  assert.ok(sevOf("qemu-x86",i=>i.fam==="BM_FlatHashFind"&&i.tmpl==="<int64_t>").every(a=>a.sev==="stable"));
  // a speed-up
  for(const a of sevOf("qemu-x86",i=>i.fam==="BM_Base64Decode")) assert.equal(a.sev,"improved");
  // the day with movement both ways
  m.run("useBoard('qemu-x86'); recomputeBaseline(); rollupFamilies(); computeOverview();");
  assert.deepEqual(m.get("reportMoves")[at(6)], {up:1, down:1});
  // the same-day re-run on qemu-x86: BM_LzDecompress<zstd> steps between the two
  // reports of day n-5, so the change point is the re-run's column — n-5+1 —
  // and no other board has it
  assert.deepEqual(m.get("reportMoves")[at(5)+1], {up:2, down:0});   // both sizes moved, between two reports of one day
  for(const a of sevOf("qemu-x86",i=>i.fam==="BM_LzDecompress"&&i.tmpl==="<zstd>")){ assert.equal(a.sev,"regressed"); assert.equal(a.cp.k, at(5)+1); }
  assert.ok(sevOf("board1",i=>i.fam==="BM_LzDecompress"&&i.tmpl==="<zstd>").every(a=>a.sev==="stable"));
});

test("real data: per-board facts follow useBoard and match the reports", ()=>{
  const {dir, payload}=corpus(12);
  const m=loadModel(payload);
  const insts=m.get("insts");
  const i=insts.findIndex(x=>x.name==="BM_Crc32<simd>/8192/repeats:9");
  assert.ok(i>=0, "names carry /repeats:N as google/benchmark writes them");
  for(const b of ["qemu-x86","board1","board2"]){
    m.run(`useBoard('${b}');`);
    const files=fs.readdirSync(path.join(dir,b)).sort();
    const doc=JSON.parse(fs.readFileSync(path.join(dir,b,files[files.length-1]),"utf8"));
    const rows=doc.benchmarks.filter(r=>r.run_name==="BM_Crc32<simd>/8192/repeats:9");
    const raw=rows.filter(r=>!r.aggregate_name);
    assert.equal(rows.length, raw.length+4, "raw rows followed by mean/median/stddev/cv");
    const med=a=>a.slice().sort((x,y)=>x-y)[a.length>>1];
    assert.equal(insts[i].iters, raw[0].iterations, b+" iterations from the raw rows, not the aggregates' 9");
    assert.equal(insts[i].reps, 9);
    assert.ok(Math.abs(insts[i].rt-med(raw.map(r=>r.real_time)))<1e-3*insts[i].rt, b+" real_time");
    assert.ok(Math.abs(insts[i].bps-med(raw.map(r=>r.bytes_per_second)))<1e-3*insts[i].bps, b+" bytes_per_second from the raw rows");
  }
});

test("real report: a history built from real_sample.json — step found, noisy channel flagged", ()=>{
  // the real report as the template: 14 reports a day apart, one planted +15 % step
  // from report 9, every other benchmark drifting within a percent
  const dir=makeHistory(14);
  const m=loadModel(dumpPayload(["--board","qemu-x86_64="+dir]));
  assert.equal(m.get("NRUNS"), 14);
  assert.equal(m.get("REPS"), 5);
  assert.deepEqual(m.get("BOARDS").map(b=>b.id), ["qemu-x86_64"]);   // one board, as the report is
  m.run("useBoard('qemu-x86_64');");
  const insts=m.get("insts"), A=m.get("A"), ids=m.get("activeIds");
  assert.equal(ids.length, 10);
  const by=Object.fromEntries(ids.map(i=>[insts[i].name, {inst:insts[i], a:A[i]}]));
  const step=by["BM_TestB/32768/repeats:5"].a;
  assert.equal(step.sev, "regressed"); assert.equal(step.cp.k, 9); assert.ok(Math.abs(step.cp.ratio-1.15)<0.02);
  const noisy=by["BM_TestA/2097152/repeats:5"].a;
  assert.equal(noisy.sev, "stable"); assert.equal(noisy.noisy, true); assert.ok(noisy.noiseCv>0.07);
  for(const [name,{a}] of Object.entries(by)) if(name!=="BM_TestB/32768/repeats:5"){
    assert.equal(a.sev, "stable", name);
    if(name!=="BM_TestA/2097152/repeats:5") assert.equal(a.noisy, false, name);
  }
  // per-board facts come from the raw rows of the latest file
  const files=fs.readdirSync(path.join(dir,"qemu-x86_64")).sort();
  const doc=JSON.parse(fs.readFileSync(path.join(dir,"qemu-x86_64",files[files.length-1]),"utf8"));
  const raw=doc.benchmarks.filter(x=>x.run_name==="BM_TestA/8192/repeats:5"&&x.run_type==="iteration");
  const med=a=>a.slice().sort((x,y)=>x-y)[a.length>>1];
  const t=by["BM_TestA/8192/repeats:5"];
  assert.equal(t.inst.iters, 516208); assert.equal(t.inst.reps, 5);
  assert.ok(Math.abs(t.a.last-med(raw.map(x=>x.cpu_time)))<1e-5*t.a.last);   // series are rounded to 6 significant digits
  assert.ok(Math.abs(t.inst.bps-med(raw.map(x=>x.bytes_per_second)))<1e-5*t.inst.bps);
  // 2 iterations of 457 ms and 516 208 of 1.1 µs sit in the same catalogue
  assert.equal(by["BM_TestB/8388608/repeats:5"].inst.iters, 2);
  assert.ok(by["BM_TestB/8388608/repeats:5"].a.last>4e8);
});

test("snapshot: one report is a page too — no comparison, no verdicts, no NaN", ()=>{
  const m=loadModel(dumpPayload(["--board","qemu-x86-64="+makeSnapshot()]));
  assert.equal(m.get("NRUNS"), 1);
  assert.equal(m.get("SNAPSHOT"), true);
  m.run("useBoard('qemu-x86-64'); recomputeBaseline(); rollupFamilies(); computeOverview();");
  const A=m.get("A"), ids=m.get("activeIds"), d=m.get("dBase"), insts=m.get("insts");
  assert.equal(ids.length, 10);
  for(const i of ids){
    assert.equal(A[i].sev, "stable"); assert.equal(A[i].cp, null);
    assert.ok(Number.isFinite(A[i].noiseCv)&&Number.isFinite(d[i]), insts[i].name);
    assert.ok(Number.isFinite(A[i].last)&&A[i].last>0);
  }
  // what one report does carry: spread, CV, the noisy flag, the facts
  const noisy=ids.find(i=>insts[i].name==="BM_TestA/2097152/repeats:5");
  assert.equal(A[noisy].noisy, true);
  assert.equal(insts[noisy].iters, 1496); assert.equal(insts[noisy].reps, 5); assert.ok(insts[noisy].bps>0);
  assert.deepEqual(m.get("WIN_OPTS"), [1]); assert.deepEqual(m.get("BASE_OPTS"), []); assert.deepEqual(m.get("BASEWIN_OPTS"), [1]);
  assert.equal(m.get("reportMoves").length, 1);
});

test("real data: a history of two reports does not read off the front of any array", ()=>{
  const {payload}=corpus(2);
  const m=loadModel(payload);
  m.run("useBoard('qemu-x86'); recomputeBaseline(); rollupFamilies(); computeOverview();");
  assert.equal(m.get("NRUNS"), 2);
  assert.deepEqual(m.get("BASE_OPTS"), []);                // no report a week older than the newest
  assert.deepEqual(m.get("BASEWIN_OPTS"), [1]);
  assert.deepEqual([m.get("baseFrom"), m.get("baseAt")], [0,0]);   // the oldest report, then
  const A=m.get("A"), d=m.get("dBase");
  for(let i=0;i<m.get("NBM");i++){ if(!A[i]) continue;
    assert.ok(Number.isFinite(A[i].noiseCv), "noiseCv");
    assert.ok(Number.isFinite(d[i]), "dBase");
    assert.equal(A[i].sev, "stable", "nothing to detect in 2 reports"); }
  for(const w of m.get("WIN_OPTS")){ m.run(`winDays=${w}; recomputeWindow();`); assert.ok(m.get("wn")>=1); }
});
