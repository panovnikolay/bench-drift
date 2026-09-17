/* The page as a file: a renderer of window.BENCH_DRIFT_DATA and nothing
   else — no generator inside, no network. */
const test=require("node:test"), assert=require("node:assert/strict");
const fs=require("fs"), path=require("path"), {spawnSync}=require("child_process");
const {ROOT, tmpdir}=require("./model.js");

test("the page renders the payload and fetches nothing", ()=>{
  const page=fs.readFileSync(path.join(ROOT,"bench_drift","bench-drift.html"),"utf8");
  // a fragment: the utility wraps it into a document and inlines the payload before it
  assert.ok(page.includes("BENCH_DRIFT_DATA"));
  assert.ok(!/<!doctype|<html\b|<head\b|<body\b/i.test(page), "a fragment, not a document");
  // nothing is fetched from anywhere: the utility works with no network at all
  assert.ok(!/<link\b/.test(page), "no <link> in the page");
  assert.ok(!/\b(src|href)=["']https?:/.test(page), "no external resource");
  assert.ok(!page.includes("googleapis"), "no font service");
});

test("the tests' temp directories do not outlive the process", ()=>{
  // /tmp on the dev machine is a quota'd tmpfs; a leaked corpus per run fills it
  const r=spawnSync("node",["-e",'const {tmpdir}=require("./tests/model.js"); const d=tmpdir("bd-gone-"); require("fs").writeFileSync(d+"/x","x"); console.log(d);'],{cwd:ROOT,encoding:"utf8"});
  assert.equal(r.status, 0, r.stderr);
  const d=r.stdout.trim();
  assert.ok(d.includes("bd-gone-"), d);
  assert.ok(!fs.existsSync(d), d+" survived the process");
});
