#!/usr/bin/env python3
"""Fabricate a corpus of --benchmark_format=json reports to feed the dashboard.

This is sample data, not a benchmark: the numbers come out of a fixed seed, so
the same corpus appears on every machine and a regression can be pointed at in
conversation. It exists so the tests — and a newcomer without reports of
their own — have something realistic to feed `bench-drift` — three boards on their own coverage, planted scenarios of every shape
the detector is supposed to catch, and a few awkward cases (a benchmark added
half-way through the history, a day one board never reported, a day with
two reports on one board) that exercise the reader rather than the happy path.

Every report is in the shape google/benchmark writes by default with
--benchmark_repetitions=N: the raw repetition rows first, then the mean /
median / stddev / cv aggregates in the same array, names carrying /repeats:N,
family_index and per_family_instance_index on every row, bytes_per_second on
the benchmarks that set it. That is the format the utility is fed.

    python3 tests/make_sample_runs.py            # -> sample-runs/ in the working directory
    python3 tests/make_sample_runs.py --days 30 --out /tmp/runs

Then:

    bench-drift --board qemu-x86=sample-runs/qemu-x86 --board board1=sample-runs/board1 \
                --board board2=sample-runs/board2 --serve
"""

import argparse
import json
import math
import os
import random
import shutil
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- the suite
# name, templates, arguments, threads, base CV (%), cost model
# families whose argument is a byte count report bytes_per_second, as a real
# benchmark would via state.SetBytesProcessed()
BYTE_FAMILIES = {"BM_Crc32", "BM_JsonParse", "BM_Base64Decode", "BM_LzDecompress"}
FAMILIES = [
    ("BM_Crc32",         ["<scalar>", "<simd>"],          [1024, 8192, 65536, 262144], None, 0.8, "lin"),
    ("BM_JsonParse",     [""],                            [512, 4096, 32768],          None, 1.3, "lin"),
    ("BM_SortInt",       ["<int32_t>", "<int64_t>"],      [1024, 16384, 262144],       None, 1.0, "nlogn"),
    ("BM_FlatHashFind",  ["<int64_t>", "<std::string>"],  [1024, 65536],               None, 1.1, "log"),
    ("BM_Base64Decode",  [""],                            [256, 4096, 65536],          None, 0.9, "lin"),
    ("BM_LzDecompress",  ["<snappy>", "<zstd>"],          [8192, 131072],              None, 1.2, "lin"),
    ("BM_RwLock",        [""],                            [1],            [1, 4, 16, 64], 2.4, "flat"),
    ("BM_ThreadPool",    [""],                            [1],            [1, 8, 32],     2.8, "flat"),
    ("BM_FrameDecode",   [""],                            [720, 1080],                 None, 1.4, "lin"),
]

COST = {
    "lin":   lambda n: 12 + 0.85 * n,
    "nlogn": lambda n: 20 + 0.30 * n * math.log2(max(2, n)),
    "log":   lambda n: 30 + 9.0 * math.log2(max(2, n)),
    "flat":  lambda n: 260.0,
}

# id -> (time multiplier, noise multiplier, argument ceiling, extra families kept)
BOARDS = {
    "qemu-x86": (1.00, 1.15, None,   None),
    "board1":   (6.40, 0.75, 131072, {"BM_Crc32", "BM_JsonParse", "BM_SortInt",
                                      "BM_FrameDecode", "BM_Base64Decode", "BM_FlatHashFind"}),
    "board2":   (11.2, 1.45, 65536,  {"BM_Crc32", "BM_SortInt", "BM_FrameDecode",
                                      "BM_Base64Decode"}),
}
HOSTS = {"qemu-x86": "ci-qemu-07", "board1": "lab-board1-02", "board2": "lab-board2-05"}

def day_back(days, back):
    """The day index `back` days from the end of a `days`-long history, never
    earlier than day 2 so a story on a short history still has a before."""
    return max(2, days - back)


def planted(days):
    """The scenarios, placed relative to the end of the history.

    Each is (predicate over a benchmark, boards it applies to or None for all,
    day index, step factor, per-day drift).
    """
    def at(back):
        return day_back(days, back)
    return [
        # a plain regression, everywhere — the one to find first
        (lambda f, t, a, th: f == "BM_Crc32", None, at(16), 1.128, 0.0),
        # only one template of a family moved: the family median hides this,
        # the worst-decile cell in the heat map does not
        (lambda f, t, a, th: f == "BM_FlatHashFind" and t == "<std::string>",
         None, at(19), 1.115, 0.0),
        # only the large sizes
        (lambda f, t, a, th: f == "BM_SortInt" and a >= 16384, None, at(13), 1.072, 0.0),
        # only the contended thread counts
        (lambda f, t, a, th: f == "BM_ThreadPool" and th >= 32, None, at(22), 1.094, 0.0),
        # one board only — the reason boards exist
        (lambda f, t, a, th: f == "BM_FrameDecode", ("board2",), at(11), 1.112, 0.0),
        # everywhere except one board
        (lambda f, t, a, th: f == "BM_Crc32", ("board2",), at(16), 1 / 1.128, 0.0),
        # a slow drift rather than a step
        (lambda f, t, a, th: f == "BM_LzDecompress" and t == "<snappy>",
         None, at(25), 1.0, 0.0031),
        # a speed-up
        (lambda f, t, a, th: f == "BM_Base64Decode", None, at(20), 0.842, 0.0),
        # both directions on one day, so the movement bars show up and down
        (lambda f, t, a, th: f == "BM_JsonParse" and a == 32768, None, at(6), 1.096, 0.0),
        # -14 %, not -9.5 %: the movement bar on this channel is ~8 % (3·CV),
        # and a plant within a percent of the bar came and went with the noise draw
        (lambda f, t, a, th: f == "BM_RwLock" and th == 64, None, at(6), 0.86, 0.0),
        # a re-run a few hours after the day's first report, on one board only: the
        # second report of that day is its own point, and the level shifts from
        # that point on — a step between two reports of one day, not between days
        (lambda f, t, a, th: f == "BM_LzDecompress" and t == "<zstd>",
         (RERUN_BOARD,), at(RERUN_BACK) + 0.5, 1.09, 0.0),
    ]


# The day with two reports: RERUN_BACK days from the end, RERUN_BOARD gets
# a second report RERUN_HOURS later, on the same day. Its noise comes from a
# stream of its own so the extra launch does not shift every other series.
RERUN_BOARD, RERUN_BACK, RERUN_HOURS = "qemu-x86", 5, 4.6


def instances():
    for fam_ix, (fam, tmpls, args, threads, cv, shape) in enumerate(FAMILIES):
        inst_ix = 0
        for t in tmpls:
            for a in args:
                for th in (threads or [None]):
                    name = fam + t
                    if not (args == [1] and threads):
                        name += "/" + str(a)
                    if th:
                        name += "/threads:%d" % th
                    cost = COST[shape](a) * (1 + 0.45 * math.log2(th)) if th else COST[shape](a)
                    yield {"name": name, "fam": fam, "tmpl": t, "arg": a,
                           "threads": th or 1, "cv": cv, "cost": max(8.0, cost),
                           "family_index": fam_ix, "instance_index": inst_ix}
                    inst_ix += 1


def on_board(inst, board):
    mul, cvm, cap, keep = BOARDS[board]
    if cap is None:
        return True
    if inst["arg"] > cap:
        return False
    return inst["fam"] in keep


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="sample-runs")      # in the working directory
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--reps", type=int, default=9)
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--last", default="2026-09-10",
                    help="date of the most recent report (YYYY-MM-DD)")
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    cosmetic = random.Random(args.seed ^ 0x5EED)
    rerun_rnd = random.Random(args.seed ^ 0x2E2)
    insts = list(instances())
    stories = planted(args.days)
    last = datetime.strptime(args.last, "%Y-%m-%d").replace(hour=3, minute=7, tzinfo=timezone.utc)

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)

    # a benchmark that only shows up half-way through, and a day board1 lost:
    # the reader has to carry values across both without inventing anything
    late_name = "BM_FrameDecode/1080"
    late_from = args.days // 2
    lost_day, lost_board = args.days - 9, "board1"
    rerun_day = day_back(args.days, RERUN_BACK)

    total_files = total_bytes = 0
    for board in BOARDS:
        mul, cvm, cap, keep = BOARDS[board]
        here = [i for i in insts if on_board(i, board)]
        d = os.path.join(args.out, board)
        os.makedirs(d, exist_ok=True)
        # one steady "true" level per benchmark, so days differ only by noise
        truth0 = {i["name"]: i["cost"] * mul * (1 + 0.02 * rnd.gauss(0, 1)) for i in here}

        # one launch per day, plus the re-run: a second launch on the same
        # day, a few hours later, numbered day + 0.5 so a story planted at
        # that half-day starts with the re-run and not with the morning report
        launches = [(day, 0.0) for day in range(args.days)]
        if board == RERUN_BOARD and rerun_day < args.days:
            launches.insert(rerun_day + 1, (rerun_day + 0.5, RERUN_HOURS))
        for day, hours in launches:
            if day == lost_day and board == lost_board:
                continue
            rerun = hours > 0
            draw = rerun_rnd if rerun else rnd
            when = last - timedelta(days=args.days - 1 - int(day)) + timedelta(
                hours=hours, minutes=draw.randint(-18, 18))
            rows = []
            for inst in here:
                if inst["name"] == late_name and day < late_from:
                    continue
                factor, drift = 1.0, 0.0
                for pred, boards, at, f, dr in stories:
                    if boards is not None and board not in boards:
                        continue
                    if not pred(inst["fam"], inst["tmpl"], inst["arg"], inst["threads"]):
                        continue
                    if day >= at:
                        factor *= f
                        drift += (day - at) * dr
                truth = truth0[inst["name"]] * factor * (1 + drift) * (1 + 0.0013 * draw.gauss(0, 1))
                sigma = inst["cv"] * cvm / 100.0
                vals = []
                for _ in range(args.reps):
                    v = truth * (1 + sigma * draw.gauss(0, 1))
                    if draw.random() < 0.03:          # the occasional slow repetition
                        v *= 1 + sigma * 2.4 * abs(draw.gauss(0, 1))
                    vals.append(max(v, truth * 0.6))
                vals.sort()
                iters = max(64, int(4.2e9 / truth))

                # wall time sits above cpu time by a margin that depends on the
                # benchmark — contended ones wait more — and never by a constant,
                # so a stand-in leaking through the page would show.
                # (sum of code points, not hash(): that one is salted per process)
                wall = 1.02 + 0.06 * math.log2(inst["threads"]) + 0.004 * (sum(map(ord, inst["fam"])) % 7)
                run_name = "%s/repeats:%d" % (inst["name"], args.reps)
                fam_ix, inst_ix = inst["family_index"], inst["instance_index"]
                bytes_done = inst["arg"] if inst["fam"] in BYTE_FAMILIES else None
                bps = lambda ns: round(bytes_done / (ns / 1e9), 3)   # bytes per second
                # the raw repetitions, in the order they ran
                for r, v in enumerate(vals):
                    row = {"name": run_name, "family_index": fam_ix,
                           "per_family_instance_index": inst_ix, "run_name": run_name,
                           "run_type": "iteration", "repetitions": args.reps,
                           "repetition_index": r, "threads": inst["threads"],
                           "iterations": iters,
                           "real_time": round(v * wall, 4), "cpu_time": round(v, 4),
                           "time_unit": "ns"}
                    if bytes_done:
                        row["bytes_per_second"] = bps(v)
                    rows.append(row)
                # then the aggregates google/benchmark appends after them: on
                # these rows `iterations` is the repetition count, and the cv
                # row holds ratios in every numeric field, counters included
                mean = sum(vals) / len(vals)
                sd = (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5
                med = vals[len(vals) // 2]
                for kind, value, unit in (("mean", mean, "time"), ("median", med, "time"),
                                          ("stddev", sd, "time"), ("cv", sd / mean, "percentage")):
                    row = {"name": "%s_%s" % (run_name, kind), "family_index": fam_ix,
                           "per_family_instance_index": inst_ix, "run_name": run_name,
                           "run_type": "aggregate", "repetitions": args.reps,
                           "threads": inst["threads"], "aggregate_name": kind,
                           "aggregate_unit": unit, "iterations": args.reps,
                           "real_time": round(value * (1 if unit == "percentage" else wall), 6 if unit == "percentage" else 4),
                           "cpu_time": round(value, 6 if unit == "percentage" else 4),
                           "time_unit": "ns"}
                    if bytes_done:
                        if unit == "percentage":
                            row["bytes_per_second"] = round(sd / mean, 6)
                        elif kind == "stddev":
                            row["bytes_per_second"] = round(bps(mean) * (sd / mean), 3)
                        else:
                            row["bytes_per_second"] = bps(value)
                    rows.append(row)

            doc = {"context": {
                       "date": when.isoformat(), "host_name": HOSTS[board],
                       "executable": "./libbench_suite", "num_cpus": 8,
                       "mhz_per_cpu": 2800, "cpu_scaling_enabled": False,
                       "caches": [{"type": "Data", "level": 1, "size": 32768, "num_sharing": 2},
                                  {"type": "Instruction", "level": 1, "size": 32768, "num_sharing": 2},
                                  {"type": "Unified", "level": 2, "size": 1048576, "num_sharing": 2},
                                  {"type": "Unified", "level": 3, "size": 33554432, "num_sharing": 16}],
                       # cosmetic, and off the measurement stream: a context field
                       # must not shift the noise every series is drawn from
                       "load_avg": [round(0.4 + (rerun_rnd if rerun else cosmetic).random(), 2) for _ in range(3)],
                       "library_version": "v1.9.1", "library_build_type": "release"},
                   "benchmarks": rows}
            # the time is in the name so both reports of one day coexist
            path = os.path.join(d, when.strftime("%Y-%m-%dT%H%M") + ".json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, separators=(",", ":"))
            total_files += 1
            total_bytes += os.path.getsize(path)

    print("%d reports in %s (%.1f MB)" % (total_files, args.out, total_bytes / 1e6))
    for board in BOARDS:
        here = [i for i in insts if on_board(i, board)]
        print("  %-9s %3d benchmarks, %d days%s"
              % (board, len(here), args.days,
                 "  (one day missing, on purpose)" if board == lost_board else ""))
    print("  '%s' only appears from day %d, also on purpose" % (late_name, late_from))
    if rerun_day < args.days:
        print("  %s has two reports on day %d: a re-run %.1f h later, BM_LzDecompress<zstd> +9 %% from it"
              % (RERUN_BOARD, rerun_day, RERUN_HOURS))
    print("\nnow: bench-drift %s --serve" % " ".join("--board %s=%s" % (b, os.path.join(args.out, b)) for b in BOARDS))


if __name__ == "__main__":
    main()
