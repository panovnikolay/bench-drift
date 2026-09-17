/* What the page actually draws, in headless Chrome.

     node --test tests/browser.test.js

   Skipped when no Chrome is on PATH. Every test is one Chrome launch: the
   probe walks a list of steps and snapshots after each, so a test can cycle
   through all window options, or switch boards, in a single render. */
const test=require("node:test"), assert=require("node:assert/strict");
const fs=require("fs"), path=require("path"), {spawnSync}=require("child_process");
const {ROOT, tmpdir, boardArgs, makeCorpus}=require("./model.js");

const CHROME=["google-chrome","google-chrome-stable","chromium","chromium-browser"]
  .find(c=>spawnSync("which",[c]).status===0);
const PROBE=fs.readFileSync(path.join(__dirname,"probe.js"),"utf8");
const tmp=tmpdir("bd-browser-");

function render(htmlPath, steps){
  const snaps=renderRaw(htmlPath, steps);
  snaps.forEach((s,i)=>assert.deepEqual(s.nan, [], "NaN/Infinity drawn at step "+i));
  return snaps;
}
function renderRaw(htmlPath, steps){
  const probed=htmlPath.replace(/\.html$/,".probe.html");
  fs.writeFileSync(probed, fs.readFileSync(htmlPath,"utf8").replace("</body>","<script>\n"+PROBE+"\n</script>\n</body>"));
  const url="file://"+probed+"#"+encodeURIComponent(JSON.stringify(steps||[[]]));
  // One process, no zygote, a capped JS heap: a headless Chrome otherwise takes
  // 400 MB and change, and on a small machine that is the difference. And no
  // network at all — the tests are about what the page draws.
  const argv=["--headless","--disable-gpu","--no-sandbox","--single-process","--no-zygote",
    "--disable-extensions","--disable-dev-shm-usage","--js-flags=--max-old-space-size=256",
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--window-size=1280,900","--dump-dom","--virtual-time-budget=20000",url];
  const dump=()=>spawnSync(CHROME,argv,{encoding:"utf8",maxBuffer:64<<20,timeout:120000});
  // the probe's own source sits in the dump too and mentions the id — take the last one
  const find=r=>(r.stdout||"").match(/[\s\S]*<pre id="probe">([\s\S]*?)<\/pre>/);
  let r=dump(), m=find(r);
  if(!m){
    // single-process Chrome occasionally dumps before the probe's timer chain
    // has finished; one more go, then it is a failure
    r=dump(); m=find(r);
  }
  assert.ok(m, "no probe output; chrome stderr: "+(r.stderr||"").slice(0,400));
  const out=JSON.parse(m[1].replace(/&quot;/g,'"').replace(/&lt;/g,"<").replace(/&gt;/g,">").replace(/&amp;/g,"&"));
  assert.deepEqual(out.errors, [], "page errors");
  return out.snaps;
}

/* a page over a generated corpus; built once per (days, args) — the
   90-day corpus is 27 MB and four tests share it */
const pages=new Map();
function realPage(days, extraArgs, mutate){
  const key=days+"-"+(extraArgs||[]).join("")+(mutate?"-m":"");
  if(pages.has(key)) return pages.get(key);
  const d=path.join(tmp,"real-"+key); fs.mkdirSync(d,{recursive:true});
  const runs=makeCorpus(days, path.join(d,"runs"));
  if(mutate) mutate(runs);
  const out=path.join(d,"page.html");
  const r=spawnSync("python3",[path.join(ROOT,"bench-drift"),"-o",out,...(extraArgs||[]),...boardArgs(runs)],{encoding:"utf8",cwd:d});
  assert.equal(r.status, 0, r.stderr);
  const built={page:out, runs};
  pages.set(key, built);
  return built;
}
/* ground truth for one benchmark's latest report on one board, from the raw rows */
function latest(runs, board, name){
  const dir=path.join(runs,board), f=fs.readdirSync(dir).sort().pop();
  const doc=JSON.parse(fs.readFileSync(path.join(dir,f),"utf8"));
  const rows=doc.benchmarks.filter(b=>b.run_name===name);
  // the default format: raw repetition rows first, aggregates after — every
  // fact must come from the raw rows (the aggregates' `iterations` is the
  // repetition count, their counters are aggregates too)
  const raw=rows.filter(b=>!b.aggregate_name&&b.run_type!=="aggregate");
  assert.ok(raw.length&&rows.length>raw.length, name+": expected raw rows followed by aggregates");
  const med=a=>{const s=a.slice().sort((x,y)=>x-y);return s[s.length>>1];};
  const bps=raw.some(b=>b.bytes_per_second!==undefined)?med(raw.map(b=>b.bytes_per_second)):undefined;
  return {file:f, ctx:doc.context, iterations:raw[0].iterations,
    cpu:med(raw.map(b=>b.cpu_time)), real:med(raw.map(b=>b.real_time)), bps};
}
/* the page's own fmtTime / unitOf, so expectations format the way it does */
function fmtTime(ns){
  if(ns<1) return ns.toFixed(3)+" ns";
  if(ns<1000) return (ns<10?ns.toFixed(2):ns.toFixed(1))+" ns";
  if(ns<1e6) return (ns/1e3).toFixed(ns<1e4?2:1)+" µs";
  if(ns<1e9) return (ns/1e6).toFixed(ns<1e8?2:1)+" ms";
  return (ns/1e9).toFixed(2)+" s";
}
// one time_unit per record, chosen by the plotted metric and applied to both
const kOf=ns=>ns<1000?1:ns<1e6?1e3:1e6;
const inUnit=(ns,base)=>+(ns/kOf(base===undefined?ns:base)).toFixed(4);
// the payload rounds series to 6 significant digits, so the raw JSON block
// agrees with the report to that precision, not to the last bit
const near=(a,b,what)=>assert.ok(Math.abs(a-b)<=Math.abs(b)*1e-5+1e-9, what+": "+a+" vs "+b);

const skip=CHROME?false:"no chrome on PATH";

/* the corpus page over the full 90 days: the header controls are tested on
   it. qemu-x86, the board it opens on, has 91 reports — day 85 carries a
   re-run, so every window of five days or more holds one point more than days */
test("every display window renders with exactly that many reports", {skip}, ()=>{
  const {page}=realPage(90);
  const opts=[90,60,30,14,7,3,1];
  const snaps=render(page, opts.map((w,i)=>[["select","#winsel",String(w)],...(i?[]:[["click","#g-toggle"]])]));
  snaps.forEach((s,i)=>{
    const wn=opts[i]+(opts[i]>=5?1:0), w0=91-wn;
    assert.deepEqual(s.sels.winsel.options, opts.map(String));
    assert.equal(s.sels.winsel.value, String(opts[i]));
    assert.match(s.ovsub, new RegExp("· "+wn+" report"+(wn===1?",":"s,")));
    assert.deepEqual(s.plotPaths, [2*wn, wn], "band + median paths at "+wn);
    assert.equal(s.sparkPts, Math.min(30,wn), "sparkline at "+wn);
    assert.ok(s.ovDates>=1&&s.ovDates<=7, "date labels at "+wn);
    // the baseline band (−7 days, the default, is column 82 on this board,
    // five columns ending there) is drawn only where the window reaches it
    const band=82>=w0?1:0;
    assert.equal(s.ovBand, band); assert.equal(s.plotBand, band);
  });
});

test("a point's tooltip tells two reports of one day apart: the time and the file", {skip}, ()=>{
  const {page, runs}=realPage(90);
  const files=fs.readdirSync(path.join(runs,"qemu-x86")).sort();
  // columns 85 and 86 are the two reports of Sep 6; with the whole history on
  // screen (the default view is 14 days) the trace places column k at k/90
  const [s85, s86, s90, sb]=render(page, [[["select","#winsel","90"],["hover","#plot rect[fill=transparent]","0.9444"]],
                                          [["hover","#plot rect[fill=transparent]","0.9556"]],
                                          [["hover","#plot rect[fill=transparent]","1"]],
                                          [["click","#v-boards"],["hover","#bplot rect[fill=transparent]","1"]]]);
  assert.ok(s85.tip.includes(files[85])&&!s85.tip.includes(files[86]), s85.tip);
  assert.ok(s86.tip.includes(files[86])&&!s86.tip.includes(files[85]), s86.tip);
  assert.ok(files[85].slice(0,10)===files[86].slice(0,10), "the two files are one day");
  for(const s of [s85,s86,s90]) assert.match(s.tip, /^[A-Z][a-z]{2} \d{2}, \d{2}:\d{2}/, "timestamp with time: "+s.tip);
  assert.ok(s90.tip.includes(files[90]), s90.tip);
  assert.match(s90.tip, /median.*p25 – p75.*CV of repetitions.*Δ vs baseline/);
  // All boards: every board's nearest point, each with its own time
  for(const b of ["qemu-x86","board1","board2"]) assert.ok(sb.tip.includes(b), b+" in "+sb.tip);
  assert.ok(sb.tip.includes(files[90]), sb.tip);
});

test("board dropdown drives the whole page, timeline included", {skip}, ()=>{
  const {page, runs}=realPage(90);
  // each board, in the chart and then in the All boards view, with the whole
  // history on screen so the column counts are the boards' own
  const snaps=render(page, [[["select","#winsel","90"]],[["click","#v-boards"]],
                            [["select","#boardsel","board1"],["click","#v-chart"]],[["click","#v-boards"]],
                            [["select","#boardsel","board2"],["click","#v-chart"]],[["click","#v-boards"]],
                            [["select","#boardsel","qemu-x86"],["click","#v-chart"]]]);
  const chart=[snaps[0],snaps[2],snaps[4],snaps[6]], boards=[snaps[1],snaps[3],snaps[5]];
  assert.deepEqual(chart.map(s=>s.ro.suite), ["37 in 9 families","22 in 6 families","15 in 4 families","37 in 9 families"]);
  assert.deepEqual(chart.map(s=>s.sels.boardsel.value), ["qemu-x86","board1","board2","qemu-x86"]);
  assert.deepEqual(chart[0].sels.boardsel.options, ["qemu-x86","board1","board2"]);
  // a board's own columns: 91 reports on qemu-x86 (a re-run), 89 on board1 (a lost day), 90 on board2
  const cols={"qemu-x86":91, board1:89, board2:90};
  for(const [s,b] of [[chart[0],"qemu-x86"],[chart[1],"board1"],[chart[2],"board2"]]){
    // the Report line names the file the board's latest numbers were read from
    const f=fs.readdirSync(path.join(runs,b)).sort().pop();
    assert.ok(s.ro.report.endsWith(path.join(b,f)), b+": "+s.ro.report);
    assert.deepEqual(s.plotPaths, [2*cols[b], cols[b]], b+": the trace has the board's columns");
    assert.match(s.ovsub, new RegExp("· "+cols[b]+" reports,"), b+": "+s.ovsub);
  }
  // All boards draws every board the benchmark runs on, each with its own points
  // — the benchmark it opens on, the worst regression, runs on all three
  assert.match(chart[0].tr.name, /^BM_Crc32</);
  for(const s of boards) assert.deepEqual(s.boardPaths.slice().sort(), [89,90,91], "three boards, their own columns each");
  assert.equal(chart[0].build, null, "no line under the title: the executable and host are not shown");
});

test("baseline offset and window controls", {skip}, ()=>{
  const {page}=realPage(90);
  const snaps=render(page, [[],[["select","#basewin","1"]],[["select","#basewin","21"]],[["select","#basesel","30"]],[["click","#v-boards"]],[["click","#v-table"]]]);
  // the defaults: the last 14 days on screen, the baseline a week back, five reports wide
  assert.deepEqual([snaps[0].sels.winsel.value, snaps[0].sels.basesel.value, snaps[0].sels.basewin.value], ["14","7","5"]);
  // the offset is in calendar days, the width a count of reports
  assert.deepEqual(snaps[0].sels.basesel.labels, ["−7 days","−14 days","−30 days","−45 days"]);
  assert.deepEqual(snaps[0].sels.basewin.labels, ["single report","median of 3 reports","median of 5 reports","median of 9 reports","median of 15 reports","median of 21 reports"]);
  assert.ok(snaps[0].plotLabels.includes("BASELINE · 7 DAYS BACK"), snaps[0].plotLabels.join("|"));
  assert.ok(snaps[0].legend.some(t=>t==="baseline — median of 5 reports, −7 days"));
  assert.ok(snaps[1].legend.some(t=>t==="baseline — single report, −7 days"));
  assert.ok(snaps[2].legend.some(t=>t==="baseline — median of 21 reports, −7 days"));
  assert.ok(snaps[3].legend.some(t=>t==="baseline — median of 21 reports, −30 days"));
  assert.ok(snaps[3].plotLabels.includes("BASELINE · 30 DAYS BACK"));
  assert.match(snaps[3].chips["Slower than baseline"].title, /vs baseline −30 days$/);
  assert.deepEqual([snaps[0].view, snaps[0].shown], ["chart", ["plot"]]);
  assert.equal(snaps[4].view, "boards"); assert.equal(snaps[4].boardPaths.length, 3);
  assert.deepEqual(snaps[4].shown, ["bplot"], "All boards is the view on screen, not just the pressed button");
  // the table shows the 14-day window: 14 days, one of them with two reports
  assert.equal(snaps[5].view, "table"); assert.equal(snaps[5].tableRows, 15);
  assert.deepEqual(snaps[5].shown, ["tablewrap"]);
  // two reports of one day are two rows, told apart by the time (the re-run on Sep 06)
  const dates=snaps[5].tableDates;
  assert.equal(new Set(dates).size, 15, "every row names its own report: "+dates.join("|"));
  assert.equal(dates.filter(d=>d.startsWith("Sep 06")).length, 2, dates.join("|"));
  assert.match(dates[0], /^[A-Z][a-z]{2} \d\d, \d\d:\d\d$/, "day with time");
});

test("the regression threshold is a control and every '3 %' follows it", {skip}, ()=>{
  const {page}=realPage(90);
  const [s0, s1, s2]=render(page, [[],[["select","#thrsel","0.1"]],[["select","#thrsel","0.1"],["click","#v-boards"]]]);
  assert.deepEqual(s0.sels.thrsel.labels, ["+1.0 %","+2 %","+3 %","+5 %","+10 %"]);
  assert.equal(s0.sels.thrsel.value, "0.03");
  assert.ok(s0.plotLabels.includes("REGRESSION +3 %"));
  assert.ok(s0.legend.includes("regression threshold +3 %"));
  assert.equal(s0.chips["Regressions"].n, "18");
  assert.match(s0.chips["Slower than baseline"].title, /^by over 3 % vs baseline/);
  assert.equal(s0.ro.sha, undefined, "no Commit readout anywhere");
  // at +10 % fewer steps clear the bar
  assert.ok(s1.plotLabels.includes("REGRESSION +10 %"));
  assert.ok(s1.legend.includes("regression threshold +10 %"));
  assert.equal(s1.chips["Regressions"].n, "10");
  assert.match(s1.chips["Slower than baseline"].title, /^by over 10 % vs baseline/);
  assert.ok(s2.legend.includes("±10 % regression threshold"));
});

test("real data: what the header and the fact list show is what the reports say", {skip}, ()=>{
  const {page, runs}=realPage(30, [], runs=>{
    // a real_time that is not cpu_time times any constant the page might know
    for(const b of fs.readdirSync(runs)) for(const f of fs.readdirSync(path.join(runs,b))){
      const p=path.join(runs,b,f), doc=JSON.parse(fs.readFileSync(p,"utf8"));
      for(const r of doc.benchmarks) if(r.aggregate_unit!=="percentage") r.real_time=r.cpu_time*(1.1+0.01*r.run_name.length);
      fs.writeFileSync(p, JSON.stringify(doc));
    }
  });
  const [s0, s1]=render(page, [[["click","#v-table"]],[["select","#boardsel","board2"],["click","#v-table"]]]);
  for(const [s,board] of [[s0,"qemu-x86"],[s1,"board2"]]){
    const t=latest(runs, board, s.tr.name);
    assert.equal(s.sels.boardsel.value, board);
    assert.equal(s.tr.metric, "Median cpu_time");
    assert.equal(s.tr.now, fmtTime(t.cpu), board+" median");
    assert.equal(s.facts.real_time, fmtTime(t.real), board+" real_time");
    if(t.bps===undefined) assert.equal(s.facts.bytes_per_second, undefined, "no counter in the report, no row");
    else assert.equal(s.facts.bytes_per_second, (t.bps/1e9).toFixed(2)+" GB/s", board+" bytes_per_second from the raw rows");
    assert.match(s.tr.sub, new RegExp("^\\d+ repetitions · "+t.iterations.toLocaleString("en-US")+" iterations$"), "no executable name in the sub-line: "+s.tr.sub);
    assert.equal(s.json.iterations, t.iterations);
    near(s.json.cpu_time, inUnit(t.cpu), "json cpu_time"); near(s.json.real_time, inUnit(t.real, t.cpu), "json real_time");
    assert.ok(s.ro.report.endsWith(path.join(board, t.file)), "Report line names the file read: "+s.ro.report);
    assert.ok(s.roLabels.includes("Report")&&!s.roLabels.includes("Artifact"), "header labels: "+s.roLabels);
    assert.ok(!s.vdLabels.some(l=>/artifact/i.test(l)), "verdict block labels: "+s.vdLabels);
    assert.equal(s.tableLatest[1], fmtTime(t.cpu));                         // Report | Median | p25 | p75 | CV | Δ
  }
  assert.match(s0.ro.suite, /^37 in 9 families$/); assert.match(s1.ro.suite, /^15 in 4 families$/);
  assert.deepEqual(s0.sels.winsel.options, ["30","14","7","3","1"]);
  // 30 days of history: no report is 30 or 45 days older than the newest
  assert.deepEqual(s0.sels.basesel.labels, ["−7 days","−14 days"]);
  assert.equal(s0.sels.basesel.value, "7");
});

test("real report: the page over a history built from real_sample.json", {skip}, ()=>{
  const d=path.join(tmp,"realsample"); fs.mkdirSync(d,{recursive:true});
  const runs=path.join(d,"runs");
  let r=spawnSync("python3",[path.join(ROOT,"tests","reports_from_sample.py"),runs,"--reports","14"],{encoding:"utf8"});
  assert.equal(r.status, 0, r.stderr);
  const page=path.join(d,"page.html");
  r=spawnSync("python3",[path.join(ROOT,"bench-drift"),"-o",page,"--board","qemu-x86_64="+runs],{encoding:"utf8",cwd:d});
  assert.equal(r.status, 0, r.stderr);
  const [s0, s1, s2]=render(page, [[["click","#v-table"]],
                                   [["click","#g-toggle"]],
                                   [["input","#q","BM_TestB/8388608/repeats:5"]],[["click",".vrow"]]]);
  // opens on the worst regression: the planted step
  assert.equal(s0.tr.name, "BM_TestB/32768/repeats:5");
  assert.match(s0.verdict, /^regression step located at the report of \w{3} \d\d, \d\d:\d\d/);   // date with time: two reports of one day are told apart
  assert.equal(s0.ro.suite, "10 in 2 families");
  assert.deepEqual(s0.sels.boardsel.options, ["qemu-x86_64"]); // one board, as the report is
  assert.deepEqual(s0.sels.winsel.options, ["14","7","3","1"]);
  const t=latest(runs, "qemu-x86_64", s0.tr.name);
  assert.equal(s0.tr.now, fmtTime(t.cpu));
  assert.equal(s0.facts.real_time, fmtTime(t.real));
  assert.equal(s0.facts.bytes_per_second, (t.bps/1e9).toFixed(2)+" GB/s");
  assert.match(s0.tr.sub, /^5 repetitions · 574 iterations$/);
  assert.equal(s0.tableRows, 14);
  assert.equal(s0.chips["Regressions"].n, "1");
  assert.equal(s0.chips["Noisy channels"].n, "1");
  // the noisy channel is marked in the catalogue once the families are open
  assert.ok(s1.catCount.startsWith("10 of 10"));
  // the slowest benchmark: 2 iterations of ~457 ms, units in ms
  const last=render(page, [[["input","#q","BM_TestB/8388608/repeats:5"]],[["click",".vrow"]]])[1];
  assert.equal(last.tr.name, "BM_TestB/8388608/repeats:5");
  assert.match(last.tr.now, / ms$/);
  assert.match(last.tr.sub, /· 2 iterations$/);
  assert.equal(last.json.time_unit, "ms");
});

test("snapshot: the page over one real report", {skip}, ()=>{
  const d=path.join(tmp,"one"); fs.mkdirSync(path.join(d,"q"),{recursive:true});
  fs.copyFileSync(path.join(ROOT,"tests","real_sample.json"), path.join(d,"q","real_sample.json"));
  const page=path.join(d,"page.html");
  const r=spawnSync("python3",[path.join(ROOT,"bench-drift"),"-o",page,"--board","qemu-x86-64="+d],{encoding:"utf8",cwd:d});
  assert.equal(r.status, 0, r.stderr);
  const [s0, s1]=render(page, [[["click","#g-toggle"]],[["click","#v-table"]]]);
  assert.equal(s0.ro.suite, "10 in 2 families");
  // the report's own clock (14:09+00:00), not the machine's zone
  assert.match(s0.ro.run, /Sep 09, 14:09/);
  assert.equal(s0.catCount, "10 of 10");
  // nothing to compare against: Δ is a dash, not +0.0 %, and the verdict says why
  assert.equal(s0.tr.delta, "—");
  assert.equal(s0.facts["Δ window 8×8"], "—"); assert.equal(s0.facts["p-value"], "—");
  assert.match(s0.verdict, /single report/i);
  assert.match(s0.ovsub, /one report/i);
  assert.equal(s0.chips["Regressions"].n, "0");
  assert.equal(s0.chips["Slower than baseline"], undefined, "no baseline chip without a baseline");
  // no report is any number of days older than the only one: the offset
  // selector has nothing to offer and says so
  assert.deepEqual([s0.sels.basesel.labels, s0.sels.basesel.disabled], [["oldest report"], true]);
  assert.deepEqual([s0.sels.basewin.labels, s0.sels.basewin.disabled], [["single report"], true]);
  // what the report does carry
  assert.equal(s0.chips["Noisy channels"].n, "1");
  assert.ok(s0.facts.bytes_per_second);
  assert.ok(s0.facts.real_time);
  assert.equal(s0.plotPaths.length, 2);                 // band and median, one point each
  assert.equal(s1.tableRows, 1);
  assert.equal(s1.tableLatest[5], "—");
});

test("real data: --metric real_time flips the labels, not just the numbers", {skip}, ()=>{
  const {page, runs}=realPage(14, ["--metric","real_time"]);
  const [s]=render(page, [[]]);
  const t=latest(runs, "qemu-x86", s.tr.name);
  assert.equal(s.tr.metric, "Median real_time");
  assert.equal(s.tr.now, fmtTime(t.real));
  assert.equal(s.facts.cpu_time, fmtTime(t.cpu));
  assert.equal(s.facts.real_time, undefined);
  near(s.json.real_time, inUnit(t.real), "json real_time"); near(s.json.cpu_time, inUnit(t.cpu, t.real), "json cpu_time");
});

test("real data: a throughput counter is shown only when the report carries it", {skip}, ()=>{
  const {page}=realPage(14, [], runs=>{
    for(const f of fs.readdirSync(path.join(runs,"qemu-x86"))){
      const p=path.join(runs,"qemu-x86",f), doc=JSON.parse(fs.readFileSync(p,"utf8"));
      // BM_SortInt sets no counter in the corpus; give it one on the raw rows only,
      // and a deliberately wrong one on the aggregates that must not leak through
      for(const b of doc.benchmarks) if(b.run_name.startsWith("BM_SortInt<int64_t>/16384"))
        b.bytes_per_second=(b.aggregate_name||b.run_type==="aggregate")?7e9:2.5e9;
      fs.writeFileSync(p, JSON.stringify(doc));
    }
  });
  const [,s]=render(page, [[["input","#q","BM_SortInt<int64_t>/16384/repeats:9"]],[["click",".vrow"]]]);
  assert.equal(s.tr.name, "BM_SortInt<int64_t>/16384/repeats:9");
  assert.equal(s.facts.bytes_per_second, "2.50 GB/s");
  assert.equal(s.json.bytes_per_second, 2.5e9);
});
