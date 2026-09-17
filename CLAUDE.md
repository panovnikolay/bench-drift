# bench-drift — working notes

`bench-drift` takes folders of Google Benchmark `--benchmark_format=json`
reports and builds a single-page dashboard (a file or a port). Everything —
the history per board, the regression detector, the baseline — is computed in
the browser over a payload the utility inlines into the page. The page
generates nothing itself.

The repository is the utility and its tests, nothing else. `README.md` is the
front door, `CONTEXT.md` the glossary (use its terms in code, tests and docs;
the words it says to avoid stay avoided — in particular a report is a
"report", not a run, a night or an artifact), `docs/design.md` the model and
the reasons behind the algorithms. This file is what an editor needs beyond
those: where things are, what is fixed, how to change the page safely.

## Files

| file | what it is |
|---|---|
| `bench_drift/__init__.py` | the utility: report reader, payload assembly, config, CLI, `--serve`. `__version__ = "0.1.0"` is the one version string (`--version`, the HTTP `Server` header, the package metadata via `dynamic = ["version"]`). Stdlib only, Python ≥ 3.11 for `tomllib` |
| `bench_drift/__main__.py` | `python -m bench_drift` |
| `bench_drift/bench-drift.html` | **the page's source**, shipped as package data: a pure renderer of `window.BENCH_DRIFT_DATA`. A fragment without `<!doctype>/<html>/<head>/<body>`: `<title>`, `<style>`, markup, one `<script>`. Not a single network request — no `<link>`, no external `src`; fonts are the system stacks in the CSS. Without a payload it writes "No data" and throws — deliberately |
| `bench-drift` | the launcher for a clone without installation: puts its own directory on `sys.path` and calls `main()` |
| `pyproject.toml` | setuptools, no dependencies; console script `bench-drift = "bench_drift:main"`; the page as package data. `default_page()` finds the fragment next to the module wherever it is installed, `--page` overrides |
| `tests/` | everything test-related, nothing of it in the root — see "Tests" |
| `docs/screenshot.png` | the README's picture, 1440×900. There is no script to renew it; if the page's look changes enough for the picture to lie, take a new one by hand over a generated corpus |
| `.github/workflows/bench-drift.yml` | CI: `tests/run-tests.sh` in full on Python 3.11 and 3.14 with Node 22 (Chrome is on the runner and the browser tier must run — it is the one that catches `NaN` in SVG attributes), then the install smoke test: a venv, `pip install .`, `--version`, `-o` on `tests/real_sample.json` |

## Fixed decisions

Ask before undoing any of these; the reasons are in `docs/design.md`.

- The repository is the utility and its tests: no demo, no synthetic history,
  no build step, no published page.
- One utility, no server: no scheduled ingest, no folder watching, no remote
  store, no server-side detector. The signals that would reopen each are in
  `docs/design.md`.
- The utility stays Python; the detector exists once, in the page. If the
  detector ever moves to the ingest side, it is rewritten in Python first.
- The dashboard only shows. No reaction layer: no statuses, assignments,
  comments; bugs are assumed to be filed elsewhere.
- Data comes from reports on disk. No manual JSON upload.
- A report is a timestamp and a file path. No commits, authors or messages —
  Google Benchmark writes none of that.
- Boards are named only on the command line (`--board NAME=PATH`), never
  inferred; a `[boards]` table in the config is an error.
- A report is the unit of history: two reports of a board on one day are two
  points, nothing merged, no warning. The history bound is `days`.
- The interface and all documentation are in English; number and date
  locales are `en-US`.
- The page loads no fonts and works without internet.
- Three verdicts (`regressed` / `improved` / `stable`) and one flag
  (`noisy`). No severity buckets, no Δ-distribution histogram, and the word
  "alert" is not on the page.
- Header selectors and their defaults: display window 14 days, baseline
  offset 7 days, baseline window 5 reports, regression threshold 3 %.
  `p < 0.05` is not configurable. Board selection is a dropdown.
- The catalogue is virtualised, collapsed by family by default, with its
  filters in its own panel; the heat map scrolls. Built to hold thousands of
  benchmarks — keep it that way.
- No line under the page title, and no label under the trace's title unless
  `[groups]` gives one.
- `tests/real_sample.json` is not edited: the tests check the reader against
  it to the last digit.

## The test corpus

`tests/make_sample_runs.py` → `sample-runs/<board>/<YYYY-MM-DDTHHMM>.json`,
fixed seed (`--days N`, default 90 — one report a day at a jittered hour).
37 benchmarks in 9 families, 9 repetitions; board1 carries 22 of them in 6
families, board2 15 in 4. Every report is in exactly the shape google/benchmark
writes by default with `--benchmark_repetitions=9`: raw rows then mean /
median / stddev / cv, `/repeats:9` in the names, `family_index` and
`per_family_instance_index`, the `cv` row in ratios, `bytes_per_second` on
`BYTE_FAMILIES`. `real_time` sits above `cpu_time` by a factor that depends on
family and threads — not a constant, or the tests could not tell a read value
from an invented one. Context cosmetics (`load_avg`) come from a separate
`Random` so they do not shift the shared noise stream.

Three awkward cases are planted for the reader: `BM_FrameDecode/1080` appears
only from day 45; board1 wrote no report on one day (nine from the end — 89
columns, not a hole); qemu-x86 has two reports on day 85 (five from the end,
a re-run 4.6 h later, `2026-09-06T0324.json` and `2026-09-06T0743.json`), and
`BM_LzDecompress<zstd>` is +9 % from the re-run on. The re-run draws its noise
from a `Random` of its own, so every other report is unaffected by it. On
qemu-x86 that makes 91 columns for 90 days: the re-run is column 86, and from
there column index and day part ways — the tests use that to tell "−7 days"
from "seven columns" (the reference at −7 days is column 82 of 91, not 83).

Planted scenarios (days counted from the end):

| what | where | effect |
|---|---|---|
| `BM_Crc32` | every board **except** board2 | +12.8 % |
| `BM_FlatHashFind<std::string>` | everywhere, one template only | +11.5 % |
| `BM_SortInt` (arg ≥ 16384) | everywhere, large sizes only | +7.2 % |
| `BM_ThreadPool` (threads ≥ 32) | qemu | +9.4 % |
| `BM_FrameDecode` | **board2 only** | +11.2 % |
| `BM_LzDecompress<snappy>` | everywhere | drift +0.31 %/day |
| `BM_Base64Decode` | everywhere | −15.8 % |
| `BM_JsonParse/32768` ↑ and `BM_RwLock/threads:64` ↓ | everywhere, **one day** | +9.5 % / −14 % |
| `BM_LzDecompress<zstd>` | **qemu-x86 only, from the same-day re-run** | +9 % |

84 of 90 days are completely quiet; six carry events, the re-run a seventh
column with movement on qemu-x86. A plant sits well clear of the movement bar
(`max(3 %, 3·CV)`) — a plant at the bar is not a plant.

**Numbers the tests pin on the 90-day corpus, qemu-x86** (if one legitimately
changes, change it in `tests/model.test.js` and here): verdicts at 3 % — 18
regressed, 4 improved, 15 stable; at 1 % — 23 real steps (19 + 4); at 10 % —
10 regressed; the `noisy` flag on 3 (`BM_ThreadPool` at 1, 8 and 32 threads),
one of them on a regression (`threads:32`). board1: 11 / 3 / 8, board2:
4 / 3 / 8, no noisy channel on either. The two `BM_LzDecompress<snappy>` sizes
— the drift — measure 2.58 % and 2.34 %: `stable` at +3 %, and at +1 % one is
`regressed` while the other sits under its own `1.5·CV` gate (2.37 %) — that
is the rule, not a defect.

`tests/reports_from_sample.py` builds a history out of `real_sample.json`: N
reports a day apart (`--reports N`), every repetition scaled by one per-report
factor (CV preserved exactly — the noisy channel `BM_TestA/2097152`, CV 7.9 %,
stays noisy), aggregates recomputed; a +15 % step planted on `BM_TestB/32768`
from report N−5. One board, `qemu-x86_64`.

## Tests

`tests/run-tests.sh` — about 20 s; `--quick` skips the browser, 5 s. Runs
from any directory. Nothing to install: `unittest`, `node:test`, and
`google-chrome` on PATH for the browser tier (without it the tier is skipped
locally; CI fails). 91 tests.

| tier | what it checks | count |
|---|---|---|
| `tests/test_serve.py` | name parsing; statistics over repetitions and over aggregates; report reading (units, both times, counters, errors, dates, raw + aggregates in one file); `real_sample.json` as is, and its self-consistency; `--board` (recursive walk, argument order, glob, repeated board, no layout guessing); a report is a column (two reports of one day, identical timestamps ordered by path, mixed offsets ordered by day first, absent benchmarks carried flat, boards with their own column counts, no top-level `runs`); config and `settings()`; the calendar day; the CLI as a subprocess; the package (`--version`, `python -m`, the page found next to the module, the `Server` header); the page skeleton | 64 |
| `tests/page.test.js` | the page as a file: a fragment, no network access; the tests' temp directories are gone when the process exits | 2 |
| `tests/model.test.js` | the page's model in Node over the 90-day corpus: option lists, the calendar-day window, the reference report and the baseline, the threshold as a bar, the noisy flag; over a 40-day corpus: the payload drives the model, switching boards switches the timeline, planted scenarios by day, the same-day re-run at its column; a history from `real_sample.json`; a snapshot; a two-report history | 15 |
| `tests/browser.test.js` | what is actually drawn in headless Chrome: every window, tooltips, the board dropdown, both baseline settings, the threshold; the audit — header, facts, JSON block and table against raw reports; the page over a real history and over one real report; `--metric real_time`; a counter only when the report carries it; `NaN` in SVG attributes | 10 |

Tooling under `tests/`: `model.js` — `loadModel(payload)` slices the page's
script and evaluates it in Node with the payload as `window`; `makeCorpus(days)`
/ `boardArgs(dir)` shared by the two Node tiers; `tmpdir()` removes what it
made at process exit. `probe.js` is injected into a built page before
`</body>`, reads a list of steps (`select` / `click` / `input`) from
`location.hash`, runs them with a 250 ms pause (the catalogue search is
debounced by 120 ms) and after each step puts a snapshot into `<pre
id="probe">`; the test pulls the **last** occurrence out of `--dump-dom` (the
probe's own source mentions the id too). Network is off in the tests
(`--host-resolver-rules=MAP * ~NOTFOUND`). The corpus page is built once per
process.

**Memory.** `run-tests.sh` runs each tier in its own cgroup (`systemd-run
--user --scope -p MemoryMax=…`; without `systemd-run` — plainly), test files
one at a time, Node's heap capped at 512 MB, Chrome `--single-process
--no-zygote` with a capped heap. Two rules that follow from big payloads:
assert on a number, not on an object (`assert.equal(hugeObject, x)`
pretty-prints the whole object on a mismatch); and the `eval` in `run()` keeps
the model's scope alive as long as the accessor lives — with a big payload,
share one model per process.

**What the tests are known to catch** (mutation-checked): `NBM` instead of
`activeIds` in the counter, a wrong clamp in the baseline, `iterations` from
the wrong report or board, an invented `real_time` ratio, division by zero at
a 1-day window, the detector's gate without `1.5·CV`. Two lessons: `NaN` in
an SVG attribute is not an error to the browser — the path is silently not
drawn, so the probe looks for `NaN|Infinity` in the attributes of `#plot`,
`#bplot`, `#ov`, `#ovheat`; and the corpus must not coincide with the page's
constants, or an invented value is indistinguishable from a read one.

## How to edit safely

The page is large and is edited pointwise:

1. Write a Node or Python patch script with `sub(a,b)` / `cut(a,b)` functions
   collecting the edits into an array. Write it to a file via `cat > patch.js
   <<'EOF'`, not `node -e '...'` — the code is full of single quotes.
2. **Dry run before writing**: check that every anchor is found exactly once,
   only then write the file. Otherwise the patch applies halfway.
3. Check syntax: extract the `<script>` of `bench_drift/bench-drift.html`
   into `check.js`, then `node --check check.js`.
4. Check the logic through `tests/model.js` over a payload:
   `python3 tests/make_sample_runs.py --out /tmp/bd-c && ./bench-drift --dump
   /tmp/bd-c/payload.json --board qemu-x86=/tmp/bd-c/qemu-x86 …`, then
   `node -e 'const m=require("./tests/model.js").loadModel(require("/tmp/bd-c/payload.json")); m.run("useBoard(\"qemu-x86\"); console.log(activeIds.length)")'`.
   The snippet runs inside the model's scope: `BOARDS, insts, NBM, NRUNS, A,
   useBoard(), activeIds, analyse(), recomputeVerdicts(), baseArr, dBase,
   recomputeBaseline(), recomputeWindow(), famIndex, rollupFamilies(),
   reportMoves, computeOverview(), runs`. Nothing that touches the DOM is
   there — do not call `render*()`.
5. `tests/run-tests.sh`. If a pinned number changes legitimately, change it
   in the test and in this file.

## Rakes

- **Inline elements ignore `overflow`/`text-overflow`.** `.nm` and `.sm` need
  `display:block`, or long names print over the sparklines.
- **`hidden` works only thanks to `[hidden]{display:none}`.** The page is a
  fragment; `bench_drift/__init__.py` adds the rule when it wraps the fragment
  into a document. Assemble a document by hand and forget it — every view
  toggle breaks.
- **`el.hidden` is an `HTMLElement` property; on an `<svg>` it is an
  expando.** SVGs take `toggleAttribute("hidden", …)`. The probe reports
  what is displayed (`shown`, by computed style), not which button is pressed.
- **SVG labels collide silently.** When changing geometry, compute
  coordinates rather than hope.
- **A `1fr` grid track does not shrink below its content** — needs
  `minmax(0,1fr)`.
- **`NaN` in an SVG attribute is not an error.** See the tests section.
- **`/tmp` may be a quota'd tmpfs.** If Chrome fails with "no probe output"
  and the shell goes silent: `df -h /tmp`, then `rm -rf /tmp/bd-*`. Every
  temp directory the tests make has a `bd-` prefix for that sweep.
- In heredoc check scripts it is easy to leave a template string unclosed —
  the error reads as `Unterminated template`.

## Not done, and could be

- A link to the automatically filed bug in the verdict block (no tracker key
  to build it from).
- Comparing boards to each other at set level rather than per benchmark
  (`All boards` currently works only for the selected benchmark).
- Everything in the "what would reverse it" lines of `docs/design.md`, each
  waiting for its signal.
