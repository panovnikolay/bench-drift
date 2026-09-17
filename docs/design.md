# How bench-drift is built, and why

The utility (`bench_drift/__init__.py`) reads the reports and folds them into a
payload; the page (`bench_drift/bench-drift.html`) renders that payload and
computes everything else — the change-point detector, the verdicts, the
baseline, the family rollup — in the browser. This document is the model
behind both and the reasons for the choices that are not obvious.

## Four decisions

**The detector runs in the browser over a payload the utility inlines.** The
viewer turns the regression threshold and the baseline in the header, so the
verdict has to be computed where those controls are. A full payload for 90
days of a real-sized corpus (under 1 000 benchmarks) is a few MB, well within
a page's reach. `--serve` is a static file on a port, nothing more: no
server-side verdicts, no server-side aggregates.
*What would reverse it:* someone needs verdicts without a browser (a CI gate,
automatic bugs, notifications) — then a server-side detector; or noticeably
more than 1 000 benchmarks on a real corpus — then series fetched on demand.

**A board is named only on the command line, never inferred.** `--board
NAME=PATH`, repeatable, is the only way a report belongs to a board: PATH is
walked for `*.json` at any depth, or is a glob, and everything found is that
board's. Nothing is read into the layout — not a subdirectory name, not a path
component, not `context.host_name`. Every guess would be a rule a user has to
learn and a layout the pipeline has to keep. Board order is argument order and
the page opens on the first. `bench-drift.toml` may say how to read (`days`,
`metric`, `port`, `[groups]`) but never what — a `[boards]` table in it is an
error.
*What would reverse it:* boards appear more often than the launch command
gets edited.

**The utility is Python, the page is JavaScript, the detector exists once,
the source is a local folder.** The utility is stdlib Python (≥ 3.11 for
`tomllib`) because it runs on CI runners and lab machines where `python3` is
almost always present and `node` is not, and it shares almost nothing with the
page: it does not parse names (the payload arrives parsed) and its statistics
run over raw repetitions while the page's run over ready series. The detector
therefore exists exactly once, in the page; `tests/model.js` reaches it from
Node by slicing the page's script, not by keeping a copy. The utility is run
by hand, reads a local folder once and exits (or serves what it read).
*What would reverse it:* someone tired of restarting by hand (a watcher); the
folder stopped being local (S3); a second pipeline the same people look at (a
switcher in one page). If the detector ever moves to the ingest side, it is
rewritten in Python **first**, before any server exists — a numerically tuned
algorithm in two languages that must agree to the verdict is the one thing
this layout is built to avoid.

**A report is the unit of history.** Every report of a board is one point of
every series it carries, however many were written that day. Reports are
sorted by their own calendar day, then timestamp, then path, and each is a
column; nothing is merged, nothing is pooled, no warning is given — two
reports a day is the normal case (a re-run after a hiccup, a second
measurement on demand), and the second launch is usually the interesting one.
Each board has its own sequence of reports and boards share no timeline:
picking a board switches the whole axis together with the series; only `All
boards` places points by time. The baseline offset is in calendar days ("−7
days" is a week ago, whatever ran that week); the baseline's width is a count
of reports — a sample size; the detector is ordinal (8×8 windows over the last
22 reports) because a statistic over samples needs no calendar. The history
the page gets is bounded in calendar days (`days`, default 90) from the newest
report across all boards. A benchmark absent from a report is carried flat
from its neighbour with a warning — the one remaining hole.
*What would reverse it:* shards — several executables on one board, each
writing a report with a disjoint set of benchmarks — would call for sparse
series; a real history where the scatter between launches of one day is
larger than the scatter from day to day would call for pooling as an option.

## The payload

`window.BENCH_DRIFT_DATA`, inlined into `<body>` before the page's script.
Exactly what the model needs, nothing more:

```
{ metric, reps,
  boards:[{id, host, note, report,
           runs:[{date, day, report}]}],
  benchmarks:[{name, fam, tmpl, arg, threads, group,
               s:{ boardId:{ med:[], p25:[], p75:[], cv:[],
                             iters, reps, rt?, bps?, ips? } }}] }
```

- **Each board carries its own `runs`**: one entry per report, in launch
  order — `date` the full ISO timestamp, `day` its calendar day (`YYYY-MM-DD`,
  in the report's own offset), `report` the displayed path of the file. There
  is no top-level `runs` and no shared axis. `boards[].report` is the path of
  the report the board's latest numbers came from — the header's `Report`
  field.
- Every series of a board has that board's `runs.length`. A board absent
  from `s` does not exist for that benchmark.
- `iters`, `reps`, `rt` (the other time metric), `bps`/`ips` are **scalars
  for the latest report of that board**, not series; they sit inside
  `s[board]` because all five depend on the board. `bps`/`ips` appear only
  when the benchmark set them; without them the throughput row is not shown —
  a guess from the argument is not a measurement.
- `group` is empty unless `[groups]` in the config names one.

### How the utility reads a report

- A report's calendar day is the day it wrote in `context.date`, in its own
  offset — nothing is converted to UTC; a launch at 02:00+03:00 belongs to
  that day on the operator's clock. The page counts the display window in
  `run.day` and shows every timestamp as the report's own wall clock, so the
  day a point is labelled with is the day it is filtered by. The day comes
  before the instant in the sort: with mixed offsets on one board the two
  orders can disagree, and the page assumes the day never runs backwards.
- Median/p25/p75/CV are computed over the raw repetitions. If there are none
  (`--benchmark_report_aggregates_only`), the aggregates are used and p25/p75
  are reconstructed as `med ∓ 0.6745·stddev`. CV is `stdev/median`.
- The default format with `--benchmark_repetitions=N` is raw rows and
  aggregates in one array, raw first. `iterations` and the counters come from
  raw rows only; from aggregates only when there are no raw rows, and then
  from the `median` row (else `mean`). A row with `aggregate_name` is an
  aggregate whatever `run_type` says.
- Gaps (a benchmark added later, absent from one report) are filled by
  carrying the neighbouring value — flat, nothing invented — with a warning
  ("missing reports"). A report a board never wrote is not a gap: it is
  simply not a column.
- One report is a snapshot: the utility warns ("one report only —
  snapshot"); the page sets `SNAPSHOT=NRUNS<2` and, wherever it would compare
  reports, shows a dash — Δ, the "Δ window" and "p-value" facts, the
  "Slower than baseline" chip; the verdict is "single report — nothing to
  compare against"; the baseline and window selectors are disabled. What the
  report does carry is shown: median, p25–p75, CV, the `noisy` flag,
  `iterations`, the other metric, the counters. Zeros instead of dashes would
  be a lie: `Δ = +0.0 %` reads as "did not change".

## The page's data model

- `insts[]` — instances: `BM_SortInt<int64_t>/65536`,
  `BM_RwLockContended/threads:16`. Each has `s = {boardId: {med, p25, p75,
  cvA, iters, reps, rt, bps, ips}}` and `boards[]`, which boards it runs on.
- `useBoard(id)` re-points `inst.med/p25/p75/cvA` and the scalars at the
  chosen board's series, sets `runs`/`NRUNS`, and recomputes everything
  derived. **All the code below it knows nothing about boards** —
  deliberately, so `board` is not dragged through every function.
- `A[i]` — the detector's verdict, `null` if the instance does not run on
  the active board. Every `for(i<NBM)` loop must check `if(!A[i]) continue`.
- `activeIds` — indices of the instances available on the active board. The
  header's `Suite` counter is `activeIds.length` in `famIndex.size` families —
  the set on the active board, not the total.
- `recomputeVerdicts()` is the tail of `useBoard` without the board switch —
  what a threshold change runs.

## Algorithms, and why exactly these

Each of these came from a measurement that showed the previous one lying.
Do not simplify them without re-measuring through `tests/model.js` over a
generated corpus.

**Change-point detector** (`analyse`). An 8×8 sliding window over the
reports' medians, within the last 22 reports — ordinal, a statistic over
samples: a day with two reports is two samples. The change point is located
by the **standardised two-sample statistic** (`t`), not by the ratio of
medians: the median ratio saturates as soon as more than half the window is
past the shift, and the point drifts by 1–3 reports; `t` peaks where both
windows are homogeneous, i.e. exactly at the shift. Every planted family in
the test corpus is found at its report; per benchmark — within ±1 report on
the noisiest channels (thread-pool benchmarks with many threads). That is the
detector's resolution on such a channel, not a defect.

Significance — the Mann–Whitney test (normal approximation with tie
correction). Effect gate: `|Δ| > max(1.2 %, 1.5·channel CV)` — otherwise
noisy benchmarks produce a stream of false regressions.

**Verdicts.** Three, plus a flag:

```
real      = p < 0.05  &&  |Δ| > max(1.2 %, 1.5·channel CV)     ← the gate, does not move
regressed = real && Δ > 0 && |Δ| ≥ THRESHOLD
improved  = real && Δ < 0 && |Δ| ≥ THRESHOLD
stable    = otherwise
noisy     = median CV of the last 20 reports > 3 %              ← a flag on any verdict
```

Severity is the Δ number, not a word: the catalogue is sorted by |Δ|, Δ is
printed on every row, the heat map paints continuously. `noisy` is a flag and
not a verdict so that a noisy channel with a regression shows the regression
and the caution at once. The noise gate stays because it works: most noisy
channels with a significant step ≥ 3 % are not planted — that is the
p-value's optimism when picking the best of ~11 change points on a channel
with CV 8 %.

**The regression threshold `THRESHOLD`** (a selector in the header: +1 / +2 /
+3 / +5 / +10 %, default 3 %) is a bar, not a scale: the same one for the
verdict, the dashed line in the trace, the "Slower than baseline" chip and
the movement bars `max(THRESHOLD, 3·CV)`. The `real` gate does not depend on
it, so at +1 % you get every real step and not one more; the `noisy` flag
does not change at any threshold. `p < 0.05` is not configurable and is in
the label. A plant near the bar measures on either side of it on individual
benchmarks — that is the rule, not a defect.

How the flag is shown: a `noisy channel · CV n %` badge next to the verdict,
a `· noisy` suffix on the catalogue row, a hollow sparkline dot. The family
bar has three colours; the flag does not take part in it. The "Noisy
channels" chip filters by the flag.

**Movement bars** (`reportMoves`). How many benchmarks moved between adjacent
reports — a re-run that moved is a bar of its own. Threshold — `max(3 %,
3·channel CV)`: with a fixed 3 % a quiet report gave about half as many false
moves as the report of a real regression gave true ones; with the noise
correction quiet reports give exactly 0. Minimum height of a non-zero bar is
2.5 units — one benchmark at a scale maximum of a few dozen drew as a sliver.

**A heat-map cell** is the shift of the family's **worst decile**, not the
median. The median hides regressions of subsets, and that is the common case:
a family where only one template regressed (the corpus's
`BM_FlatHashFind<std::string>`) shows a median step of a fraction of a
percent against a real double-digit one.

**The cell's neutrality floor is per row** (`famFloorBase/Prev`): twice the
median `|Δ|` over all reports of that row. The worst-decile noise grows with
family size, so a global floor does not work: on a large set the map was half
painted; with a per-row floor only the families that moved are.

**The display window** (`recomputeWindow`): 90 / 60 / 30 / 14 / 7 / 3 / 1
day, default 14 (or the whole history when shorter). `winDays` → `w0` (first
visible report) and `wn` (how many). Selection goes **by calendar day**, not
by array index: `runs[i].dn >= runs[NRUNS-1].dn − (winDays−1)`, where `dn` is
the day number of `run.day`. A day may hold two reports and a board may have
skipped one — "7 days" must mean seven days, whatever landed inside them; a
1-day window shows every report of the latest day. A view only: the
detector, the baseline and the family rollup always run over the whole
history. The option list is everything shorter than the history plus the
smallest that covers it whole; 90 days is the limit.

A 1-day window with one report in it is one point: in all three charts `X`
divides by `wn−1`, so at `wn===1` the point is placed mid-field. Points are
spaced by index, not by time: a cluster of re-runs stays readable and a
skipped week does not become empty canvas; only `All boards` has a time axis.
At a short window the baseline runs off the left edge — then its band and
caption are not drawn (`baseAt >= w0`); the change point likewise.

**The baseline** (`baseIndices`, `recomputeBaseline`). The reference report
is the active board's **last column at least `baseBack` calendar days older
than its newest** (`runs[k].dn <= runs[NRUNS-1].dn − baseBack`), `baseBack` ∈
{7, 14, 30, 45}, default 7. Baseline = the median of `baseWin` **reports**
ending at the reference, `baseWin` ∈ {1, 3, 5, 9, 15, 21}, default 5 — per
benchmark and per board separately; `baseIndices(rs)` holds the rule so `All
boards` applies it to each board's own columns. An offset the board cannot
reach is not offered (`BASE_OPTS` is filtered by calendar reach); with none
reachable — a snapshot, two launches of one day — the selector is disabled,
reads "oldest report", and column 0 is the reference. At the start of the
history the window is simply shorter, `from` clamped at zero.

A median of several reports rather than one so that one unlucky launch does
not shift every Δ: mean `|Δ|` over a set falls from one report to three to
five to nine and flattens after that — hence 5, the main gain taken and the
window still narrow. The baseline affects only the Δ numbers; **it does not
affect the detector's verdicts** — that one looks for a step, not a
deviation from a reference point.

## Page structure

Header (`Latest` — the active board's newest timestamp, `Report` — its file,
the board dropdown, the suite counter, then four selectors: baseline offset,
baseline window, display window, regression threshold) → the **Movement
between reports** panel (bars + scrollable heat map) → the **catalogue |
trace** grid → footer (the detector in a sentence).

The trace has three views: `Chart` (absolute time, p25–p75 band, change
point), `All boards` (each board's Δ against its own baseline, in percent, on
a time axis), `Table`. `showView()` is the only place that decides what is
visible.

The catalogue is virtualised: ~20 rows in the DOM regardless of set size, the
array `flat` (group headers + rows) and `offs` (cumulative offsets), binary
search over `offs` on scroll. Filters live in the catalogue panel; it opens
collapsed by family. The heat map scrolls. Both were built for thousands of
benchmarks and should stay that way.

An `<option>` has no tooltip of its own, so the board's coverage and host
hang as `title` on the `<select>`. The word on the page is "regressed", not
"alert" — it is a verdict, nobody is alerted; and "report", not "run" or
"night" — the unit of history.
