/* Load the dashboard's data model into Node, without a browser.

   Slice the pure-computation half of the page's script (detector, baseline,
   rollup, overview) and evaluate it as a function that is handed a payload —
   the same object the utility inlines into a page. Returns { get, run }:
   get("NRUNS") reads a binding live, run("useBoard('board1')") executes in
   the scope. Handy from a shell too:

     node -e 'const m=require("./tests/model.js").loadModel(require("./payload.json")); m.run("useBoard(\"board1\"); console.log(activeIds.length)")'
*/
const fs=require("fs"), os=require("os"), path=require("path");
const ROOT=path.join(__dirname,"..");

/* A temp directory that goes away when the process does. The tests build
   corpora and pages there — 20 MB per browser run — and /tmp on the dev
   machine is a tmpfs with a per-user quota: eight leaked runs filled it,
   Chrome could not write its dump, and every test in the tier failed with
   "no probe output". */
const tmpdirs=[];
function tmpdir(prefix){
  const d=fs.mkdtempSync(path.join(os.tmpdir(),prefix));
  tmpdirs.push(d);
  return d;
}
process.on("exit",()=>{ for(const d of tmpdirs) fs.rmSync(d,{recursive:true,force:true}); });

function loadModel(payload){
  const src=fs.readFileSync(path.join(ROOT,"bench_drift","bench-drift.html"),"utf8");
  const js=src.match(/<script>\n([\s\S]*)\n<\/script>/)[1];
  const START="/* ============================ helpers", END="/* ============================ movement overview";
  const core=js.slice(js.indexOf(START), js.indexOf(END));
  if(!core) throw new Error("could not slice the model out of the page");
  // direct eval inside the function sees its let/const bindings, so the
  // accessors below stay live as useBoard() and friends rebind things
  const fn=new Function("window","document",
    core+"\nreturn { get:n=>eval(n), run:c=>eval(c) };");
  return fn({BENCH_DRIFT_DATA:payload}, {querySelector:()=>null});
}

/* The utility and the two generators as subprocesses. Each throws on a
   non-zero exit with the process's stderr, so a broken build fails the test
   that needed it rather than the first assertion after. */
const {spawnSync}=require("child_process");
function py(script, args, opts){
  const r=spawnSync("python3",[path.join(ROOT,script),...args],{encoding:"utf8",cwd:ROOT,...opts});
  if(r.status!==0) throw new Error(script+" failed: "+r.stderr);
  return r;
}
/* bench-drift with the given arguments — -o, --dump, --board … — in cwd
   (the repository root by default; the page prints report paths relative to it) */
function benchDrift(args, cwd){ return py("bench-drift", args, cwd?{cwd}:{}); }
/* the payload bench-drift builds from the given --board (and other) arguments */
function dumpPayload(args){
  const out=path.join(tmpdir("bd-payload-"),"payload.json");
  benchDrift(["--dump",out,...args]);
  return JSON.parse(fs.readFileSync(out,"utf8"));
}
/* a history built from tests/real_sample.json by tests/reports_from_sample.py:
   N reports a day apart, a +15 % step planted on BM_TestB/32768 from report
   N−5, one board — qemu-x86_64 — that --board must still name */
function makeHistory(reports, dir){
  dir=dir||tmpdir("bd-real-");
  py("tests/reports_from_sample.py", [dir,"--reports",String(reports)]);
  return dir;
}
/* one report: a folder holding tests/real_sample.json and nothing else */
function makeSnapshot(dir){
  dir=dir||tmpdir("bd-one-");
  fs.mkdirSync(path.join(dir,"q"),{recursive:true});
  fs.copyFileSync(path.join(ROOT,"tests","real_sample.json"), path.join(dir,"q","real_sample.json"));
  return dir;
}

/* A corpus of reports from tests/make_sample_runs.py, N days, three boards,
   in a temp directory of its own; and the --board arguments that feed it to
   the utility. Boards are named on the command line and nowhere else — one
   --board per subfolder — and the page opens on the first board given, so
   qemu-x86 (every benchmark) goes first. */
const BOARD_ORDER=["qemu-x86","board1","board2"];
function boardArgs(dir){
  return fs.readdirSync(dir).filter(d=>fs.statSync(path.join(dir,d)).isDirectory())
    .sort((a,b)=>BOARD_ORDER.indexOf(a)-BOARD_ORDER.indexOf(b))
    .flatMap(d=>["--board", d+"="+path.join(dir,d)]);
}
function makeCorpus(days, dir){
  dir=dir||tmpdir("bd-corpus-");
  py("tests/make_sample_runs.py", ["--days",String(days),"--out",dir]);
  return dir;
}

function sevCounts(m){
  const A=m.get("A"), NBM=m.get("NBM"), c={};
  for(let i=0;i<NBM;i++){ if(!A[i]) continue; c[A[i].sev]=(c[A[i].sev]||0)+1; }
  return c;
}
function noisyCount(m){
  const A=m.get("A"), NBM=m.get("NBM"); let n=0;
  for(let i=0;i<NBM;i++) if(A[i]&&A[i].noisy) n++;
  return n;
}

module.exports={loadModel, sevCounts, noisyCount, tmpdir, ROOT,
  benchDrift, dumpPayload, boardArgs, makeCorpus, makeHistory, makeSnapshot};
