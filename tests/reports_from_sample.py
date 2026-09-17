#!/usr/bin/env python3
"""Turn real_sample.json into a history: N reports one day apart, each the
sample report with every repetition scaled by a small per-report factor, and
the aggregates recomputed so the file stays self-consistent.

The real report is one board, qemu-x86_64 — that is the default, and the
tests use only that. --boards can still name more, each scaled by a
different constant, but that is fabrication — the real report has one.

Scaling all five repetitions of a benchmark by the same factor keeps its CV
exactly — so the noisy channel in the sample (BM_TestA/2097152, CV 7.9 %)
stays noisy, and the quiet ones stay quiet. One benchmark gets a planted step
so the detector has something to find.

    python3 tests/reports_from_sample.py OUT_DIR --reports 14

Used by test_serve.py (imported), model.test.js and browser.test.js (spawned).
"""
import argparse
import json
import os
import random
import statistics
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "real_sample.json")
PLANT = ("BM_TestB/32768/repeats:5", 1.15)     # benchmark, step factor, from report N-5


BOARD = "qemu-x86_64"                           # the board real_sample.json came from


def build(out_dir, reports=14, boards=(BOARD,), seed=1, last="2026-09-10"):
    template = json.load(open(SAMPLE, encoding="utf-8"))
    rnd = random.Random(seed)
    end = datetime.strptime(last, "%Y-%m-%d").replace(hour=3, minute=7, tzinfo=timezone.utc)
    names = []
    for r in template["benchmarks"]:
        if r["run_name"] not in names:
            names.append(r["run_name"])
    written = []
    for bi, board in enumerate(boards):
        os.makedirs(os.path.join(out_dir, board), exist_ok=True)
        board_mul = 1.0 + 0.5 * bi                       # boards differ in absolute time
        for k in range(reports):
            when = end - timedelta(days=reports - 1 - k)
            factor = {n: board_mul * (1 + 0.01 * rnd.gauss(0, 1)) for n in names}
            if k >= reports - 5:
                factor[PLANT[0]] *= PLANT[1]
            rows = []
            for name in names:
                raw = [dict(r) for r in template["benchmarks"]
                       if r["run_name"] == name and r["run_type"] == "iteration"]
                f = factor[name]
                for r in raw:
                    r["real_time"] *= f
                    r["cpu_time"] *= f
                    if "bytes_per_second" in r:
                        r["bytes_per_second"] /= f
                    rows.append(r)
                cpu = [r["cpu_time"] for r in raw]
                rt = [r["real_time"] for r in raw]
                bps = [r["bytes_per_second"] for r in raw if "bytes_per_second" in r]
                agg = {"mean": (statistics.mean(cpu), statistics.mean(rt), statistics.mean(bps) if bps else None, "time"),
                       "median": (statistics.median(cpu), statistics.median(rt), statistics.median(bps) if bps else None, "time"),
                       "stddev": (statistics.stdev(cpu), statistics.stdev(rt), statistics.stdev(bps) if bps else None, "time"),
                       "cv": (statistics.stdev(cpu) / statistics.mean(cpu), statistics.stdev(rt) / statistics.mean(rt),
                              (statistics.stdev(bps) / statistics.mean(bps)) if bps else None, "percentage")}
                for r in template["benchmarks"]:
                    if r["run_name"] == name and r["run_type"] == "aggregate":
                        a = dict(r)
                        c, w, b, _ = agg[a["aggregate_name"]]
                        a["cpu_time"], a["real_time"] = c, w
                        if b is not None:
                            a["bytes_per_second"] = b
                        rows.append(a)
            doc = {"context": dict(template["context"], date=when.isoformat()),
                   "benchmarks": rows}
            path = os.path.join(out_dir, board, when.strftime("%Y-%m-%d") + ".json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            written.append(path)
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--reports", type=int, default=14)
    ap.add_argument("--boards", default=BOARD)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    files = build(a.out, a.reports, tuple(a.boards.split(",")), a.seed)
    print("%d reports from real_sample.json in %s" % (len(files), a.out))
