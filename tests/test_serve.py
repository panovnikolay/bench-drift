"""Unit tests for the bench_drift package — the report reader and the payload.

    python3 -m unittest discover -s tests -v

Stdlib only. Reports are fabricated inline, small and to the point; the
90-day sample corpus is exercised by the Node tests, not here.
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bench_drift as bds  # noqa: E402


# ------------------------------------------------------------------ fixtures

def report(rows, date="2026-09-10T03:07:00+00:00", exe="./libbench_suite",
           host="ci-qemu-07", **extra_ctx):
    ctx = {"date": date, "host_name": host, "executable": exe}
    ctx.update(extra_ctx)
    return {"context": ctx, "benchmarks": rows}


def iteration_rows(name, values, unit="ns", reps=None, iterations=1000, **more):
    """The raw repetition rows, shaped as google/benchmark writes them."""
    reps = reps or len(values)
    out = []
    for i, v in enumerate(values):
        row = {"name": name, "family_index": 0, "per_family_instance_index": 0,
               "run_name": name, "run_type": "iteration",
               "repetitions": reps, "repetition_index": i, "threads": 1,
               "iterations": iterations, "real_time": v * 1.1, "cpu_time": v,
               "time_unit": unit}
        row.update(more)
        out.append(row)
    return out


def aggregate_rows(name, mean, median, stddev, reps=9, iterations=None, unit="ns", **counters):
    """The four aggregate rows google/benchmark appends: on them `iterations`
    is the repetition count, and the cv row carries ratios in every numeric
    field — the counters included."""
    out = []
    for kind, v, agg_unit in (("mean", mean, "time"), ("median", median, "time"),
                              ("stddev", stddev, "time"), ("cv", stddev / mean, "percentage")):
        row = {"name": "%s_%s" % (name, kind), "family_index": 0,
               "per_family_instance_index": 0, "run_name": name,
               "run_type": "aggregate", "repetitions": reps, "threads": 1,
               "aggregate_name": kind, "aggregate_unit": agg_unit,
               "iterations": iterations if iterations is not None else reps,
               "real_time": v * 1.1, "cpu_time": v, "time_unit": unit}
        for k, x in counters.items():
            row[k] = (stddev / mean) if agg_unit == "percentage" else (x * stddev / mean if kind == "stddev" else x)
        out.append(row)
    return out


def gb_rows(name, values, unit="ns", iterations=1000, **counters):
    """One benchmark exactly as --benchmark_repetitions=N reports it by
    default: the raw rows first, then its aggregates, in one array. This is
    the shape the utility is fed. Keyword counters (bytes_per_second, …) go on
    every row the way the real thing puts them."""
    import statistics
    s = sorted(values)
    med = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return (iteration_rows(name, values, unit=unit, iterations=iterations, **counters)
            + aggregate_rows(name, statistics.mean(values), med, sd, reps=len(values),
                             unit=unit, **counters))


def args(**over):
    base = dict(metric="cpu_time", days=0, groups=None)
    base.update(over)
    return argparse.Namespace(**base)


class Corpus:
    """A temp directory of reports: corpus.write(board, day_offset, rows)."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="bds-test-")
        self.t0 = datetime(2026, 8, 1, 3, 0, tzinfo=timezone.utc)

    def write(self, board, day, rows, **ctx):
        d = os.path.join(self.root, board)
        os.makedirs(d, exist_ok=True)
        when = self.t0 + timedelta(days=day)
        doc = report(rows, date=when.isoformat(), **ctx)
        path = os.path.join(d, "n%03d.json" % day)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return path

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def quiet(fn, *a, **kw):
    """Run fn with stderr captured; returns (result, stderr_text)."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


# ----------------------------------------------------------------- the tests

class ParseName(unittest.TestCase):
    def check(self, name, fam, tmpl, arg, threads):
        self.assertEqual(bds.parse_name(name), (fam, tmpl, arg, threads), name)

    def test_shapes(self):
        self.check("BM_Base64Decode/256", "BM_Base64Decode", "", 256, 1)
        self.check("BM_Crc32<simd>/8192", "BM_Crc32", "<simd>", 8192, 1)
        self.check("BM_FlatHashFind<std::string>/65536", "BM_FlatHashFind", "<std::string>", 65536, 1)
        self.check("BM_RwLock/threads:16", "BM_RwLock", "", 0, 16)
        self.check("BM_Sort<int64_t>/1024/threads:4", "BM_Sort", "<int64_t>", 1024, 4)
        self.check("BM_Nested<std::map<int, int>>/8", "BM_Nested", "<std::map<int, int>>", 8, 1)
        self.check("BM_Two/8/16", "BM_Two", "", 8, 1)              # first arg wins
        self.check("BM_Manual/64/real_time", "BM_Manual", "", 64, 1)
        self.check("BM_Plain", "BM_Plain", "", 0, 1)


class Summarise(unittest.TestCase):
    def test_raw_repetitions(self):
        vals = [100, 90, 110, 95, 105, 98, 102, 101, 99]
        med, p25, p75, cv, reps = bds.summarise(vals, {}, 0)
        self.assertEqual(reps, 9)
        self.assertEqual(med, 100)
        self.assertEqual((p25, p75), (98, 102))
        import statistics
        self.assertAlmostEqual(cv, statistics.stdev(vals) / 100, places=9)   # stdev/median, not /mean

    def test_aggregates_only(self):
        med, p25, p75, cv, reps = bds.summarise([], {"mean": 101, "median": 100, "stddev": 4}, 9)
        self.assertEqual(med, 100)
        self.assertEqual(reps, 9)
        self.assertAlmostEqual(p25, 100 - 0.6745 * 4)
        self.assertAlmostEqual(p75, 100 + 0.6745 * 4)
        self.assertAlmostEqual(cv, 0.04)                            # sd/median without a cv row

    def test_aggregates_cv_row_preferred(self):
        _, _, _, cv, _ = bds.summarise([], {"median": 100, "stddev": 4, "cv": 0.0396}, 9)
        self.assertEqual(cv, 0.0396)

    def test_single_repetition(self):
        self.assertEqual(bds.summarise([42.0], {}, 0), (42.0, 42.0, 42.0, 0.0, 1))

    def test_nothing_usable(self):
        self.assertIsNone(bds.summarise([], {"stddev": 1}, 0))


class ReadReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bds-rr-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, doc, name="r.json"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return p

    def test_units_and_both_times(self):
        rows = gb_rows("BM_A/1", [1.0, 2.0, 3.0], unit="us", iterations=77)
        meta, vals = bds.read_report(self.path(report(rows)), "cpu_time")
        self.assertEqual(vals["BM_A/1"]["med"], 2000.0)            # us -> ns
        self.assertAlmostEqual(vals["BM_A/1"]["other"], 2200.0)     # real_time, same scaling
        self.assertEqual(vals["BM_A/1"]["iters"], 77)
        self.assertEqual(vals["BM_A/1"]["reps"], 3)
        self.assertIsNone(vals["BM_A/1"]["bps"])
        self.assertEqual(meta["exe"], "libbench_suite")

    def test_metric_swap(self):
        rows = iteration_rows("BM_A/1", [10.0, 10.0, 10.0])
        _, vals = bds.read_report(self.path(report(rows)), "real_time")
        self.assertAlmostEqual(vals["BM_A/1"]["med"], 11.0)
        self.assertAlmostEqual(vals["BM_A/1"]["other"], 10.0)

    def test_counters_read_not_guessed(self):
        rows = iteration_rows("BM_A/1", [10.0, 10.0, 10.0], bytes_per_second=5e9)
        _, vals = bds.read_report(self.path(report(rows)), "cpu_time")
        self.assertEqual(vals["BM_A/1"]["bps"], 5e9)
        self.assertIsNone(vals["BM_A/1"]["ips"])

    def test_aggregates_only_keeps_iterations(self):
        rows = aggregate_rows("BM_A/1", 101, 100, 4, iterations=555)   # aggregates-only: no raw row to prefer
        _, vals = bds.read_report(self.path(report(rows)), "cpu_time")
        self.assertEqual(vals["BM_A/1"]["iters"], 555)
        self.assertEqual(vals["BM_A/1"]["reps"], 9)
        self.assertAlmostEqual(vals["BM_A/1"]["other"], 110.0)     # median real_time row

    def test_default_output_raw_rows_followed_by_aggregates(self):
        """--benchmark_repetitions=5 without report_aggregates_only: the raw
        rows come first and mean/median/stddev/cv follow in the same array.
        Everything must come from the raw rows; the aggregates' `iterations`
        is the repetition count and their counters are aggregates too."""
        rows = iteration_rows("BM_A/8192", [1353.0, 1367.0, 1340.0, 1360.0, 1349.0],
                              iterations=516208, bytes_per_second=2.4e10)
        for kind, v, unit in (("mean", 1353.8, "time"), ("median", 1353.0, "time"),
                              ("stddev", 10.3, "time"), ("cv", 0.0076, "percentage")):
            rows.append({"name": "BM_A/8192_" + kind, "run_name": "BM_A/8192",
                         "run_type": "aggregate", "repetitions": 5, "aggregate_name": kind,
                         "aggregate_unit": unit, "iterations": 5, "threads": 1,
                         "real_time": v * 1.1, "cpu_time": v, "time_unit": "ns",
                         "bytes_per_second": 2.4e10 if unit == "time" else 0.0076})
        _, vals = bds.read_report(self.path(report(rows)), "cpu_time")
        v = vals["BM_A/8192"]
        self.assertEqual(v["reps"], 5)
        self.assertEqual(v["med"], 1353.0)
        self.assertEqual(v["iters"], 516208)            # not the aggregates' 5
        self.assertEqual(v["bps"], 2.4e10)              # not polluted by the cv row's 0.0076
        self.assertLess(v["cv"], 0.02)                  # the cv row is not a repetition

    def test_aggregate_row_mislabelled_as_iteration(self):
        """A row that carries aggregate_name is an aggregate even if run_type
        says otherwise — seen in a hand-written sample, cheap to be safe about."""
        rows = iteration_rows("BM_A/1", [10.0, 12.0, 11.0])
        rows.append({"name": "BM_A/1_cv", "run_name": "BM_A/1", "run_type": "iteration",
                     "aggregate_name": "cv", "aggregate_unit": "percentage",
                     "iterations": 3, "threads": 1, "real_time": 0.09, "cpu_time": 0.09,
                     "time_unit": "ns"})
        _, vals = bds.read_report(self.path(report(rows)), "cpu_time")
        self.assertEqual(vals["BM_A/1"]["reps"], 3)
        self.assertEqual(vals["BM_A/1"]["med"], 11.0)

    def test_aggregates_only_counters_from_median_row(self):
        rows = aggregate_rows("BM_A/1", 101, 100, 4, iterations=555)
        for r in rows:
            r["bytes_per_second"] = {"mean": 5.1e9, "median": 5.0e9, "stddev": 2e8, "cv": 0.04}[r["aggregate_name"]]
        _, vals = bds.read_report(self.path(report(rows)), "cpu_time")
        self.assertEqual(vals["BM_A/1"]["bps"], 5.0e9)
        self.assertEqual(vals["BM_A/1"]["iters"], 555)

    def test_error_rows_dropped_with_warning(self):
        rows = iteration_rows("BM_A/1", [10.0, 10.0])
        rows.append({"name": "BM_B/1", "run_name": "BM_B/1", "error_occurred": True,
                     "error_message": "boom"})
        (meta, vals), err = quiet(bds.read_report, self.path(report(rows)), "cpu_time")
        self.assertNotIn("BM_B/1", vals)
        self.assertIn("1 benchmark(s) reported an error", err)

    def test_missing_date_falls_back_to_mtime(self):
        doc = report(iteration_rows("BM_A/1", [1.0, 1.0]))
        del doc["context"]["date"]
        (meta, _), err = quiet(bds.read_report, self.path(doc), "cpu_time")
        self.assertIn("mtime", err)
        self.assertIsNotNone(meta["when"].tzinfo)

    def test_not_a_report(self):
        (got), err = quiet(bds.read_report, self.path({"nope": 1}), "cpu_time")
        self.assertIsNone(got)
        self.assertIn("not a Google Benchmark report", err)


class Discover(unittest.TestCase):
    """Work 1 of the plan: a file belongs to a board only because --board
    said so. No layout is read into anything: --board NAME=PATH walks PATH
    for *.json at any depth, and that is the whole rule."""

    def setUp(self):
        self.c = Corpus()

    def tearDown(self):
        self.c.cleanup()

    def spec(self, name, sub=""):
        return "%s=%s" % (name, os.path.join(self.c.root, sub) if sub else self.c.root)

    def test_board_path_is_walked_recursively(self):
        # the pipeline folder: <run>/<board>/<job>/report.json, depth unknown in advance
        deep = os.path.join(self.c.root, "run-42", "qemu-x86_64", "job-7")
        os.makedirs(deep)
        with open(os.path.join(deep, "report.json"), "w") as fh:
            json.dump(report(gb_rows("BM_A/1", [1.0, 1.0])), fh)
        top = os.path.join(self.c.root, "report.json")
        with open(top, "w") as fh:
            json.dump(report(gb_rows("BM_A/1", [1.0, 1.0])), fh)
        boards = bds.discover(["qemu-x86_64=" + self.c.root])
        self.assertEqual([b for b, _ in boards], ["qemu-x86_64"])
        self.assertEqual(sorted(os.path.relpath(f, self.c.root) for f in boards[0][1]),
                         ["report.json", os.path.join("run-42", "qemu-x86_64", "job-7", "report.json")])

    def test_board_order_is_argument_order(self):
        for b in ("zeta", "alpha"):
            self.c.write(b, 0, gb_rows("BM_A/1", [1.0, 1.0]))
        boards = bds.discover([self.spec("zeta", "zeta"), self.spec("alpha", "alpha")])
        self.assertEqual([b for b, _ in boards], ["zeta", "alpha"])

    def test_glob_still_works_as_a_path(self):
        self.c.write("b", 0, gb_rows("BM_A/1", [1.0, 1.0]))
        self.c.write("b", 1, gb_rows("BM_A/1", [1.0, 1.0]))
        boards = bds.discover(["b=" + os.path.join(self.c.root, "b", "n00*.json")])
        self.assertEqual(len(boards[0][1]), 2)

    def test_same_board_twice_merges_its_files(self):
        self.c.write("x", 0, gb_rows("BM_A/1", [1.0, 1.0]))
        self.c.write("y", 1, gb_rows("BM_A/1", [1.0, 1.0]))
        boards = bds.discover([self.spec("one", "x"), self.spec("one", "y")])
        self.assertEqual(len(boards), 1)
        self.assertEqual(len(boards[0][1]), 2)

    def test_no_layout_guessing(self):
        # a folder of board-named subfolders is NOT a set of boards any more
        self.c.write("qemu-x86_64", 0, gb_rows("BM_A/1", [1.0, 1.0]))
        with self.assertRaises(SystemExit):
            bds.discover([self.c.root])                    # a bare path is not an argument
        boards = bds.discover(["all=" + self.c.root])
        self.assertEqual([b for b, _ in boards], ["all"])  # it is whatever --board calls it

    def test_malformed_and_empty(self):
        with self.assertRaises(SystemExit):
            bds.discover(["qemu-x86_64"])                  # no '='
        empty = os.path.join(self.c.root, "empty"); os.makedirs(empty)
        with self.assertRaises(SystemExit):
            bds.discover(["b=" + empty])                   # nothing to read
        with self.assertRaises(SystemExit):
            bds.discover([])                               # no boards at all


class PayloadCase(unittest.TestCase):
    """A temp corpus and the payload the utility builds from it — the seam
    the tests below judge the utility by."""

    def setUp(self):
        self.c = Corpus()

    def tearDown(self):
        self.c.cleanup()

    def build(self, *names, **over):
        boards = bds.discover(["%s=%s" % (b, os.path.join(self.c.root, b)) for b in names])
        return quiet(bds.build_payload, boards, args(**over))


class ReportsArePoints(PayloadCase):
    """A report is one column of its board's history. Two reports of one day
    are two points, in launch order, each with its own file: nothing is
    merged, nothing is said about it. Every board has its own sequence of
    reports — the payload carries them per board, never a shared axis."""

    def write_at(self, board, day, hour, rows, name=None, **ctx):
        d = os.path.join(self.c.root, board); os.makedirs(d, exist_ok=True)
        when = self.c.t0 + timedelta(days=day, hours=hour)
        path = os.path.join(d, name or "n%03d-%02d.json" % (day, hour))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report(rows, date=when.isoformat(), **ctx), fh)
        return path

    def series(self, p, board, name):
        return [x for x in p["benchmarks"] if x["name"] == name][0]["s"][board]

    def besides_detector(self, err):
        """stderr minus the short-history notice these tiny corpora always earn."""
        return [l for l in err.splitlines() if "detector" not in l]

    def test_two_reports_of_one_day_are_two_columns(self):
        self.write_at("b", 0, 0, gb_rows("BM_A/1", [1.0, 1.0]))
        self.write_at("b", 1, 0, gb_rows("BM_A/1", [2.0, 2.0]))
        self.write_at("b", 1, 3, gb_rows("BM_A/1", [9.0, 9.0]))       # a re-run, three hours later
        p, err = self.build("b")
        runs = p["boards"][0]["runs"]
        self.assertEqual([r["date"][:16] for r in runs],
                         ["2026-08-01T03:00", "2026-08-02T03:00", "2026-08-02T06:00"])
        self.assertEqual([os.path.basename(r["report"]) for r in runs],
                         ["n000-00.json", "n001-00.json", "n001-03.json"])
        self.assertEqual(sorted(runs[0]), ["date", "day", "report"])   # a point is a time, its day and a file
        self.assertEqual(self.series(p, "b", "BM_A/1")["med"], [1.0, 2.0, 9.0])
        self.assertEqual(p["boards"][0]["report"], runs[-1]["report"])  # the header names the latest
        self.assertEqual(self.besides_detector(err), [])                 # two reports a day is normal

    def test_no_shared_axis(self):
        self.write_at("b", 0, 0, gb_rows("BM_A/1", [1.0, 1.0]))
        self.write_at("b", 1, 0, gb_rows("BM_A/1", [1.0, 1.0]))
        p, _ = self.build("b")
        self.assertNotIn("runs", p)

    def test_launch_order_is_the_timestamp_not_the_file_name(self):
        self.write_at("b", 0, 0, gb_rows("BM_A/1", [1.0, 1.0]), name="z.json")
        self.write_at("b", 0, 2, gb_rows("BM_A/1", [9.0, 9.0]), name="a-rerun.json")
        p, _ = self.build("b")
        self.assertEqual([os.path.basename(r["report"]) for r in p["boards"][0]["runs"]],
                         ["z.json", "a-rerun.json"])
        self.assertEqual(self.series(p, "b", "BM_A/1")["med"], [1.0, 9.0])
        self.assertTrue(p["boards"][0]["report"].endswith("a-rerun.json"))

    def test_identical_timestamps_are_ordered_by_path(self):
        # a copied file is a point too — visible, not swallowed
        self.write_at("b", 0, 0, gb_rows("BM_A/1", [1.0, 1.0]), name="b.json")
        self.write_at("b", 0, 0, gb_rows("BM_A/1", [5.0, 5.0]), name="a.json")
        p, err = self.build("b")
        self.assertEqual([os.path.basename(r["report"]) for r in p["boards"][0]["runs"]], ["a.json", "b.json"])
        self.assertEqual(self.series(p, "b", "BM_A/1")["med"], [5.0, 1.0])
        self.assertEqual(self.besides_detector(err), [])

    def test_a_benchmark_absent_from_one_report_is_carried(self):
        self.write_at("b", 0, 0, gb_rows("BM_A/1", [1.0, 1.0]) + gb_rows("BM_B/1", [7.0, 7.0]))
        self.write_at("b", 0, 3, gb_rows("BM_A/1", [2.0, 2.0]))          # the re-run ran only BM_A
        self.write_at("b", 1, 0, gb_rows("BM_A/1", [3.0, 3.0]) + gb_rows("BM_B/1", [8.0, 8.0]))
        p, err = self.build("b")
        self.assertEqual(self.series(p, "b", "BM_A/1")["med"], [1.0, 2.0, 3.0])
        self.assertEqual(self.series(p, "b", "BM_B/1")["med"], [7.0, 7.0, 8.0])   # flat, not invented
        self.assertIn("missing", err)

    def test_boards_have_their_own_column_counts(self):
        for day in range(3):
            self.write_at("b", day, 0, gb_rows("BM_A/1", [1.0, 1.0]))
        self.write_at("b", 2, 4, gb_rows("BM_A/1", [1.0, 1.0]))
        for day in range(5):
            self.write_at("c", day, 0, gb_rows("BM_A/1", [6.0, 6.0]))
        p, err = self.build("b", "c")
        by = {x["id"]: x for x in p["boards"]}
        self.assertEqual((len(by["b"]["runs"]), len(by["c"]["runs"])), (4, 5))
        s = [x for x in p["benchmarks"] if x["name"] == "BM_A/1"][0]["s"]
        self.assertEqual((len(s["b"]["med"]), len(s["c"]["med"])), (4, 5))
        self.assertEqual(by["b"]["note"], "1 benchmark · 4 reports")
        self.assertNotIn("missing", err)                                  # no column was invented for b


class CalendarDay(PayloadCase):
    """A report's day is the day it says it was written, in its own offset —
    not the UTC day of the same instant. The history the page gets is bounded
    in those days: `days` back from the newest report across all boards."""

    def write_dated(self, board, name, date, rows=None):
        d = os.path.join(self.c.root, board); os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report(rows or gb_rows("BM_A/1", [1.0, 1.0]), date=date), fh)
        return path

    def days_of(self, p, board):
        return [r["day"] for r in [x for x in p["boards"] if x["id"] == board][0]["runs"]]

    def test_late_evening_and_early_morning_are_two_days(self):
        self.write_dated("b", "a.json", "2026-08-01T23:30:00+03:00")
        self.write_dated("b", "b.json", "2026-08-02T05:00:00+03:00")
        p, _ = self.build("b")
        self.assertEqual(self.days_of(p, "b"), ["2026-08-01", "2026-08-02"])

    def test_the_day_is_the_reports_own_not_utc(self):
        # 02:00+03:00 is 23:00 UTC the evening before; the report says the 2nd
        self.write_dated("b", "a.json", "2026-08-01T12:00:00+03:00")
        self.write_dated("b", "b.json", "2026-08-02T02:00:00+03:00")
        p, _ = self.build("b")
        self.assertEqual(self.days_of(p, "b"), ["2026-08-01", "2026-08-02"])
        self.assertEqual(p["boards"][0]["runs"][1]["date"], "2026-08-02T02:00:00+03:00")   # the timestamp is untouched
        # and west of Greenwich the other way round: 22:00-05:00 is 03:00 UTC next day
        self.write_dated("c", "a.json", "2026-08-01T22:00:00-05:00")
        p, _ = self.build("c")
        self.assertEqual(self.days_of(p, "c"), ["2026-08-01"])

    def test_days_bounds_by_calendar_day_from_the_newest_report_across_boards(self):
        for d in range(6):
            self.write_dated("b", "d%d.json" % d, "2026-08-%02dT03:00:00+00:00" % (d + 1))
        self.write_dated("b", "d5-rerun.json", "2026-08-06T07:00:00+00:00")   # two reports on the cut-off day
        self.write_dated("c", "d7.json", "2026-08-08T03:00:00+00:00")         # the newest report of all
        p, err = self.build("b", "c", days=3)                                  # Aug 6, 7, 8
        by = {x["id"]: x for x in p["boards"]}
        self.assertEqual([os.path.basename(r["report"]) for r in by["b"]["runs"]], ["d5.json", "d5-rerun.json"])
        self.assertEqual(self.days_of(p, "c"), ["2026-08-08"])
        self.assertEqual(len([x for x in p["benchmarks"]][0]["s"]["b"]["med"]), 2)
        self.assertNotIn("nothing within", err)
        p, err = self.build("b", "c", days=1)                                  # only Aug 8: b has nothing there
        self.assertEqual([x["id"] for x in p["boards"]], ["c"])
        self.assertIn("nothing within the last 1 day", err)
        p, _ = self.build("b", "c")                                            # days=0: unbounded
        self.assertEqual(len(by["b"]["runs"]) + 5, len([x for x in p["boards"] if x["id"] == "b"][0]["runs"]))

    def test_columns_follow_the_day_when_offsets_disagree_with_instants(self):
        # 01:00+03:00 on Aug 2 is the earlier instant (22:00 UTC Aug 1), but it
        # is the later day: columns go by day first, so the page's day sequence
        # never runs backwards, then by instant within the day, then by path
        self.write_dated("b", "a.json", "2026-08-02T01:00:00+03:00", gb_rows("BM_A/1", [2.0, 2.0]))
        self.write_dated("b", "b.json", "2026-08-01T23:00:00+00:00", gb_rows("BM_A/1", [1.0, 1.0]))
        self.write_dated("b", "c.json", "2026-08-01T22:30:00+00:00", gb_rows("BM_A/1", [3.0, 3.0]))
        p, _ = self.build("b")
        self.assertEqual(self.days_of(p, "b"), ["2026-08-01", "2026-08-01", "2026-08-02"])
        self.assertEqual([r["report"].rsplit("/", 1)[-1] for r in p["boards"][0]["runs"]],
                         ["c.json", "b.json", "a.json"])
        self.assertEqual(p["benchmarks"][0]["s"]["b"]["med"], [3.0, 1.0, 2.0])

    def test_days_counts_the_reports_own_days(self):
        # 23:00-01:00 on Aug 7 is 00:00 UTC Aug 8, but the report says Aug 7:
        # with days=1 from a report of Aug 8 it is outside the window
        self.write_dated("b", "a.json", "2026-08-07T23:00:00-01:00")
        self.write_dated("b", "b.json", "2026-08-08T03:00:00+00:00")
        p, _ = self.build("b", days=1)
        self.assertEqual(self.days_of(p, "b"), ["2026-08-08"])


class BuildPayload(unittest.TestCase):
    def setUp(self):
        self.c = Corpus()

    def tearDown(self):
        self.c.cleanup()

    def fill(self, board, days, names, base=100.0, **kw):
        for d in days:
            rows = []
            for n in names:
                rows += gb_rows(n, [base * (1 + 0.01 * k) for k in range(5)], **kw)
            self.c.write(board, d, rows)

    def build(self, **over):
        subs = sorted(d for d in os.listdir(self.c.root) if os.path.isdir(os.path.join(self.c.root, d)))
        boards = bds.discover(["%s=%s" % (d, os.path.join(self.c.root, d)) for d in subs])
        return quiet(bds.build_payload, boards, args(**over))

    def test_metric_and_runs(self):
        self.fill("small", range(3), ["BM_A/1"])
        self.fill("big", range(3), ["BM_A/1", "BM_B/1", "BM_C/1"])
        p, _ = self.build()
        self.assertEqual([b["id"] for b in p["boards"]], ["big", "small"])   # argument order (sorted here)
        self.assertEqual(p["metric"], "cpu_time")
        self.assertEqual([len(b["runs"]) for b in p["boards"]], [3, 3])

    def test_a_lost_report_is_no_column(self):
        self.fill("b", [0, 1, 2, 4], ["BM_A/1"])          # report 3 lost
        p, err = self.build()
        self.assertEqual(len(p["boards"][0]["runs"]), 4)  # 4 reports, 4 columns — nothing invented
        self.assertNotIn("missing", err)
        # another board that has report 3 is its own history; b gains no column
        self.fill("c", [3], ["BM_A/1"], base=500.0)
        p, err = self.build()
        self.assertEqual([len(b["runs"]) for b in p["boards"]], [4, 1])
        self.assertNotIn("missing", err)

    def test_missing_benchmark_is_carried_forward(self):
        self.fill("b", [0, 1, 3, 4], ["BM_A/1", "BM_B/1"])
        self.fill("b", [2], ["BM_A/1"])                   # BM_B is not in that report
        p, err = self.build()
        b = [x for x in p["benchmarks"] if x["name"] == "BM_B/1"][0]["s"]["b"]["med"]
        self.assertEqual(len(b), 5)
        self.assertEqual(b[2], b[1])                      # carried from the report before
        self.assertIn("missing reports", err)

    def test_late_benchmark_is_backfilled(self):
        self.fill("b", range(4), ["BM_A/1"])
        for d in (2, 3):
            self.c.write("b", d, iteration_rows("BM_A/1", [1.0, 1.0]) +
                         iteration_rows("BM_LATE/1", [7.0 + d, 7.0 + d, 7.0 + d]))
        p, _ = self.build()
        late = [x for x in p["benchmarks"] if x["name"] == "BM_LATE/1"][0]
        med = late["s"]["b"]["med"]
        self.assertEqual(med[0], med[2])                  # first known value, backwards
        self.assertEqual(med[1], med[2])
        self.assertNotEqual(med[2], med[3])

    def test_days_trims(self):
        self.fill("b", range(6), ["BM_A/1"])
        p, _ = self.build(days=4)
        self.assertEqual(len(p["boards"][0]["runs"]), 4)
        self.assertEqual(len([x for x in p["benchmarks"]][0]["s"]["b"]["med"]), 4)

    def test_per_board_facts_come_from_latest_report(self):
        for d in range(3):
            self.c.write("b", d, iteration_rows("BM_A/1", [1.0, 1.0], iterations=1000 + d))
        for d in range(3):
            self.c.write("c", d, iteration_rows("BM_A/1", [6.0, 6.0], iterations=100 + d))
        p, _ = self.build()
        a = [x for x in p["benchmarks"] if x["name"] == "BM_A/1"][0]
        self.assertEqual(a["s"]["b"]["iters"], 1002)
        self.assertEqual(a["s"]["c"]["iters"], 102)
        self.assertAlmostEqual(a["s"]["b"]["rt"], 1.1)
        self.assertNotIn("iters", a)                       # no board-blind copy

    def test_report_path_names_the_latest_file(self):
        self.fill("b", range(3), ["BM_A/1"])
        p, _ = self.build()
        self.assertTrue(p["boards"][0]["report"].endswith("n002.json"))

    def test_groups_file_and_no_default_group(self):
        # a group is only what [groups] says; the executable's name is not one
        self.fill("b", range(2), ["BM_A/1", "BM_B/1"])
        p, _ = self.build(groups={"BM_A": "hashing"})
        g = {x["fam"]: x["group"] for x in p["benchmarks"]}
        self.assertEqual(g, {"BM_A": "hashing", "BM_B": ""})

    def test_one_report_is_a_snapshot(self):
        """A single report is a legitimate input: the dashboard shows what the
        report measured — times, spread, CV, counters — and nothing to compare
        against. It says so instead of refusing."""
        self.fill("b", [0], ["BM_A/1", "BM_B/2"])
        p, err = self.build()
        self.assertEqual(len(p["boards"][0]["runs"]), 1)
        self.assertIn("one report only", err); self.assertIn("snapshot", err)
        a = [x for x in p["benchmarks"] if x["name"] == "BM_A/1"][0]
        self.assertEqual(len(a["s"]["b"]["med"]), 1)
        self.assertEqual(a["s"]["b"]["reps"], 5)

    def test_the_real_report_alone_builds(self):
        d = os.path.join(self.c.root, "one"); os.makedirs(d)
        shutil.copy(RealSample.SAMPLE, d)
        boards = bds.discover(["qemu-x86-64=" + d])
        p, err = quiet(bds.build_payload, boards, args())
        self.assertEqual(len(p["boards"][0]["runs"]), 1)
        self.assertEqual(len(p["benchmarks"]), 10)
        self.assertEqual(p["boards"][0]["note"], "10 benchmarks · 1 report")

    def test_short_history_warns_about_detector(self):
        self.fill("b", range(3), ["BM_A/1"])
        _, err = self.build()
        self.assertIn("only 3 reports", err); self.assertIn("detector", err)


class RealSample(unittest.TestCase):
    """tests/real_sample.json is a real report with the
    numbers altered — the shape is the contract, the digits are not the
    author's: ten benchmarks, five repetitions each, followed by their
    aggregates, exactly as google/benchmark writes them. The reader must
    return exactly what the raw rows say, for every benchmark. The pins below
    were computed from the file with the reader's own formulas."""

    SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "real_sample.json")

    def test_every_benchmark_matches_the_raw_rows(self):
        doc = json.load(open(self.SAMPLE, encoding="utf-8"))
        meta, vals = bds.read_report(self.SAMPLE, "cpu_time")
        self.assertEqual(len(vals), 10)
        import statistics
        for name, v in vals.items():
            raw = [r for r in doc["benchmarks"] if r["run_name"] == name and r["run_type"] == "iteration"]
            self.assertEqual(len(raw), 5, name)
            cpu = sorted(r["cpu_time"] for r in raw)
            self.assertEqual(v["reps"], 5)
            self.assertAlmostEqual(v["med"], bds.pct(cpu, 0.5), places=9)
            self.assertAlmostEqual(v["p25"], bds.pct(cpu, 0.25), places=9)
            self.assertAlmostEqual(v["p75"], bds.pct(cpu, 0.75), places=9)
            self.assertAlmostEqual(v["cv"], statistics.stdev(cpu) / bds.pct(cpu, 0.5), places=12)
            self.assertEqual(v["iters"], raw[0]["iterations"])             # never the aggregates' 5
            self.assertAlmostEqual(v["other"], bds.pct(sorted(r["real_time"] for r in raw), 0.5), places=9)
            self.assertAlmostEqual(v["bps"], bds.pct(sorted(r["bytes_per_second"] for r in raw), 0.5), places=6)
            self.assertIsNone(v["ips"])
        # anchors, so a silent change in the file shows up as numbers
        a = vals["BM_TestA/8192/repeats:5"]
        self.assertEqual(a["iters"], 516208)
        self.assertAlmostEqual(a["med"], 1104.2770100870193)
        self.assertAlmostEqual(a["cv"], 0.006404496602930922, places=12)
        self.assertAlmostEqual(a["bps"], 29673713842.3427, places=3)
        self.assertAlmostEqual(vals["BM_TestA/2097152/repeats:5"]["cv"], 0.07906504089404033, places=12)   # the noisy channel
        heavy = vals["BM_TestB/8388608/repeats:5"]
        self.assertEqual(heavy["iters"], 2)                                  # 457 ms per iteration
        self.assertAlmostEqual(heavy["med"], 456948114.4494994, places=3)
        self.assertEqual(bds.parse_name("BM_TestA/8388608/repeats:5"), ("BM_TestA", "", 8388608, 1))
        self.assertEqual(meta["exe"], "GoogleBenchmarkExample")
        self.assertEqual(meta["host"], "")                                   # empty host_name is fine
        self.assertEqual(meta["when"].isoformat(), "2026-09-09T14:09:10+00:00")

    def test_the_file_is_self_consistent(self):
        """The aggregate rows are google/benchmark's own definitions over the
        raw rows (mean, median, sample stddev, cv = stddev/mean), and the
        throughput on every raw row is the declared byte count over that
        row's wall time — so the altered numbers still make one report."""
        import statistics
        doc = json.load(open(self.SAMPLE, encoding="utf-8"))
        rows = doc["benchmarks"]
        names = list(dict.fromkeys(r["run_name"] for r in rows))
        self.assertEqual(len(names), 10)
        for name in names:
            raw = [r for r in rows if r["run_name"] == name and r["run_type"] == "iteration"]
            aggs = {r["aggregate_name"]: r for r in rows if r["run_name"] == name and r["run_type"] == "aggregate"}
            self.assertEqual(sorted(aggs), ["cv", "mean", "median", "stddev"], name)
            nbytes = 4 * bds.parse_name(name)[2]                       # sizeof(int) × arg, as the binary declares
            for col in ("real_time", "cpu_time", "bytes_per_second"):
                v = [r[col] for r in raw]
                self.assertAlmostEqual(aggs["mean"][col] / statistics.mean(v), 1.0, places=12, msg=(name, col))
                self.assertAlmostEqual(aggs["median"][col] / statistics.median(v), 1.0, places=12, msg=(name, col))
                self.assertAlmostEqual(aggs["stddev"][col] / statistics.stdev(v), 1.0, places=9, msg=(name, col))
                self.assertAlmostEqual(aggs["cv"][col] / (statistics.stdev(v) / statistics.mean(v)), 1.0, places=9, msg=(name, col))
            for r in raw:   # to the precision google/benchmark itself writes the rate with
                self.assertAlmostEqual(r["bytes_per_second"] * r["real_time"] * 1e-9 / nbytes, 1.0, places=5, msg=name)
                self.assertEqual(r["iterations"], raw[0]["iterations"])

    def test_history_built_from_the_sample(self):
        """reports_from_sample.py turns the real report into N reports; the
        payload must keep the real CVs, show the planted step, and take
        per-board facts from the raw rows of the latest report."""
        import reports_from_sample as rfs
        tmp = tempfile.mkdtemp(prefix="bds-real-")
        try:
            rfs.build(tmp, reports=14)                     # one board: the one the report came from
            boards = bds.discover(["qemu-x86_64=" + tmp])   # named here, not read off the folder
            p, err = quiet(bds.build_payload, boards, args())
            self.assertEqual([b["id"] for b in p["boards"]], ["qemu-x86_64"])
            self.assertEqual(len(p["boards"][0]["runs"]), 14)
            by = {b["name"]: b for b in p["benchmarks"]}
            self.assertEqual(len(by), 10)
            noisy = by["BM_TestA/2097152/repeats:5"]["s"]["qemu-x86_64"]["cv"]
            self.assertTrue(all(c > 0.07 for c in noisy), "scaling repetitions together keeps the CV")
            step = by[rfs.PLANT[0]]["s"]["qemu-x86_64"]["med"]
            self.assertGreater(step[-1] / step[0], 1.12)
            self.assertLess(step[8] / step[0], 1.03)
            q = by["BM_TestA/8192/repeats:5"]["s"]["qemu-x86_64"]
            self.assertEqual(q["iters"], 516208)
            self.assertEqual(q["reps"], 5)
            self.assertIn("bps", q)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Config(unittest.TestCase):
    """Work 2: bench-drift.toml holds how to read — days, metric, port,
    groups — and never what to read. Arguments beat the file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bds-cfg-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def toml(self, text):
        p = os.path.join(self.tmp, "bench-drift.toml")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_reads_the_settings(self):
        cfg = bds.load_config(self.toml('days = 30\nmetric = "real_time"\nport = 9000\n[groups]\nBM_A = "hashing"\n'))
        self.assertEqual(cfg, {"days": 30, "metric": "real_time", "port": 9000, "groups": {"BM_A": "hashing"}})

    def test_boards_in_the_file_are_an_error(self):
        with self.assertRaises(SystemExit):
            bds.load_config(self.toml('[boards]\nqemu = "runs/qemu"\n'))

    def test_unknown_key_warns_and_is_ignored(self):
        _, err = quiet(bds.load_config, self.toml('days = 5\nwhatever = 1\n'))
        self.assertIn("whatever", err)

    def test_missing_file_is_empty_when_default_and_an_error_when_named(self):
        self.assertEqual(bds.load_config(os.path.join(self.tmp, "nope.toml"), required=False), {})
        with self.assertRaises(SystemExit):
            bds.load_config(os.path.join(self.tmp, "nope.toml"), required=True)

    def test_arguments_beat_the_file_and_defaults_fill_the_rest(self):
        cfg = {"days": 30, "metric": "real_time", "port": 9000, "groups": {"BM_A": "x"}}
        eff = bds.settings(cfg, argparse.Namespace(days=None, metric=None, port=None, groups=None))
        self.assertEqual((eff.days, eff.metric, eff.port, eff.groups), (30, "real_time", 9000, {"BM_A": "x"}))
        eff = bds.settings(cfg, argparse.Namespace(days=7, metric="cpu_time", port=None, groups=None))
        self.assertEqual((eff.days, eff.metric, eff.port), (7, "cpu_time", 9000))
        eff = bds.settings({}, argparse.Namespace(days=None, metric=None, port=None, groups=None))
        self.assertEqual((eff.days, eff.metric, eff.port, eff.groups), (90, "cpu_time", 8777, {}))

    def test_days_is_a_setting_in_calendar_days(self):
        # default 90 days back from the newest report; the config sets it; an argument beats the config
        self.assertEqual(bds.DEFAULTS["days"], 90)
        self.assertEqual(bds.settings({}, argparse.Namespace()).days, 90)
        self.assertEqual(bds.settings({"days": 14}, argparse.Namespace()).days, 14)
        self.assertEqual(bds.settings({"days": 14}, argparse.Namespace(days=3)).days, 3)


class Cli(unittest.TestCase):
    """Work 2: one command. --board for the data, -o for a file or --serve
    for a port, --config for the rest; neither -o nor --serve is an error."""

    HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def setUp(self):
        self.c = Corpus()
        for d in range(4):
            self.c.write("q", d, gb_rows("BM_A/1", [1.0, 1.0]) + gb_rows("BM_B/2", [3.0, 3.0]))

    def tearDown(self):
        self.c.cleanup()

    def run_cli(self, *argv):
        import subprocess
        # a hang is a failure, not a freeze: the old CLI served a port when
        # given nothing to write, and a red test waited on it forever
        try:
            return subprocess.run([sys.executable, os.path.join(self.HERE, "bench-drift")] + list(argv),
                                  capture_output=True, text=True, cwd=self.c.root, timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("bench-drift did not exit in 20 s: %r" % (argv,))

    def test_writes_a_dashboard(self):
        out = os.path.join(self.c.root, "d.html")
        r = self.run_cli("--board", "qemu-x86_64=" + os.path.join(self.c.root, "q"), "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        html = open(out, encoding="utf-8").read()
        self.assertIn("window.BENCH_DRIFT_DATA=", html)
        self.assertIn('"id":"qemu-x86_64"', html)

    def test_neither_output_nor_serve_is_an_error(self):
        r = self.run_cli("--board", "qemu-x86_64=" + os.path.join(self.c.root, "q"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("-o", r.stderr); self.assertIn("--serve", r.stderr)

    def test_config_is_read_and_arguments_beat_it(self):
        cfg = os.path.join(self.c.root, "bench-drift.toml")
        with open(cfg, "w") as fh:
            fh.write('days = 2\n[groups]\nBM_A = "hashing"\n')
        out = os.path.join(self.c.root, "p.json")
        r = self.run_cli("--board", "qemu-x86_64=" + os.path.join(self.c.root, "q"), "--dump", out)   # bench-drift.toml next to us is picked up
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.load(open(out))
        self.assertEqual(len(p["boards"][0]["runs"]), 2)
        self.assertEqual({b["fam"]: b["group"] for b in p["benchmarks"]}["BM_A"], "hashing")
        r = self.run_cli("--board", "qemu-x86_64=" + os.path.join(self.c.root, "q"), "--dump", out, "--days", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.load(open(out))["boards"][0]["runs"]), 3)

    def test_help_names_the_settings(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--days", r.stdout); self.assertIn("days = 90", r.stdout)

    def test_version_is_the_one_string(self):
        r = self.run_cli("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "bench-drift 0.1.0")

    def test_module_runs_as_python_m(self):
        import subprocess
        r = subprocess.run([sys.executable, "-m", "bench_drift", "--help"],
                           capture_output=True, text=True, cwd=self.HERE, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--board", r.stdout)

    def test_page_is_found_next_to_the_module(self):
        # no --page: the fragment ships inside the package, wherever it is installed
        page = bds.default_page()
        self.assertTrue(os.path.isfile(page), page)
        self.assertEqual(os.path.dirname(page), os.path.dirname(os.path.abspath(bds.__file__)))
        out = os.path.join(self.c.root, "d.html")
        r = self.run_cli("--board", "qemu-x86_64=" + os.path.join(self.c.root, "q"), "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<title>bench-drift</title>", open(out, encoding="utf-8").read())

    def test_boards_in_config_refused_with_a_hint(self):
        cfg = os.path.join(self.c.root, "bench-drift.toml")
        with open(cfg, "w") as fh:
            fh.write('[boards]\nqemu = "q"\n')
        r = self.run_cli("--board", "qemu-x86_64=" + os.path.join(self.c.root, "q"), "--dump", os.path.join(self.c.root, "p.json"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--board", r.stderr)


class Serve(unittest.TestCase):
    def test_server_header_carries_the_version(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import urlopen
        import threading
        bds.Handler.page = b"<!doctype html>"
        bds.Handler.payload = b"{}"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), bds.Handler)
        httpd.verbose = False
        t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
        try:
            with urlopen("http://127.0.0.1:%d/" % httpd.server_address[1], timeout=5) as r:
                self.assertEqual(r.headers["Server"], "bench-drift/0.1.0")
                self.assertEqual(r.headers["Server"], "bench-drift/" + bds.__version__)
        finally:
            httpd.shutdown(); httpd.server_close()


class BuildPage(unittest.TestCase):
    def test_skeleton_and_injection(self):
        html = bds.build_page(bds.default_page(), {"x": "</script><b>"}).decode("utf-8")
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("[hidden]{ display:none !important; }", html)
        self.assertLess(html.index("BENCH_DRIFT_DATA"), html.index("(function(){"))
        self.assertIn('<\\/script>', html)                 # cannot close the tag early
        self.assertNotIn("</script><b>", html.split("BENCH_DRIFT_DATA")[1].split("</script>")[0])


class Helpers(unittest.TestCase):
    def test_display_path(self):
        cwd = os.getcwd()
        self.assertEqual(bds.display_path(os.path.join(cwd, "a", "b.json")), os.path.join("a", "b.json"))
        up = os.path.abspath(os.path.join(cwd, "..", "z.json"))
        self.assertEqual(bds.display_path(up), up)
        self.assertEqual(bds.display_path(""), "")


if __name__ == "__main__":
    unittest.main()
