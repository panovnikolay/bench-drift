# bench-drift

A dashboard over a history of Google Benchmark reports: folders of JSON
reports in, one page with a regression detector out. The project has one name,
`bench-drift`; the Python module is `bench_drift` because Python cannot
spell a hyphen.
_Avoid_: Benchmark Drift, BenchDrift, benchmark-drift

## Language

**Report**:
One JSON file written by a Google Benchmark executable with
`--benchmark_format=json`; one launch of a board's benchmarks writes one.
The unit of input and the unit of history: every report is one point of
every series it carries, however many were written that day. The header's
`Report` field names the active board's latest one.
_Avoid_: run, night, nightly, artifact, dump

**Board**:
A machine or emulator on which the benchmarks are run; named only on the
command line, never inferred. Every series belongs to exactly one board,
and every board has its own sequence of reports — boards share no timeline.
_Avoid_: host, target, platform

**Snapshot**:
A history of a single report. Shows its numbers, compares nothing.

**Benchmark**:
One named row of a report on one board, e.g. `BM_SortInt<int64_t>/65536`;
its series is one value per report of its board.
_Avoid_: instance, test, case

**Suite**:
The set of benchmarks that exist on the active board, counted as "N in M
families". Nothing else is a suite.
_Avoid_: store, set, corpus

**Family**:
The benchmarks that share a name before the template and argument, e.g.
`BM_SortInt`. The unit of the heat map.
_Avoid_: group (a group is a user label over families)

**Baseline**:
The median of a benchmark over a few reports ending at the reference
report; Δ is measured against it. It moves with the history, never with a
tag. Its width is a count of reports — a sample size.
_Avoid_: reference, tag, release

**Reference report**:
The report the baseline window ends at: the board's latest report that is
at least a fixed number of calendar days older than the board's newest one.
Measured in days, not reports — "a week ago", whatever ran that week.
_Avoid_: reference day, anchor

**Display window**:
How many recent calendar days the charts show, whatever number of reports
fell into them. A view only: the detector and the baseline ignore it.

**Change point**:
The report at which a benchmark's level stepped, as located by the detector.
_Avoid_: shift point, event, break

**Step**:
The size of a change at the change point, in percent of the level before it.
_Avoid_: shift, jump, delta (Δ is the distance from the baseline, a different number)

**Verdict**:
The detector's word for a benchmark: `regressed`, `improved` or `stable`.
Severity is the number next to it, never a fourth word.
_Avoid_: alert, status, severity, bucket

**Noisy**:
A flag on a benchmark whose channel scatters too much to trust small steps;
it sits on any verdict and is not one itself.
_Avoid_: noise (as a verdict), unstable

**Movement**:
How many benchmarks of a board changed between two adjacent reports,
counted in both directions.
_Avoid_: churn, activity
