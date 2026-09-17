"""bench-drift: a dashboard out of folders of Google Benchmark reports.

One command. --board names each board and where its reports are; the path
is walked for *.json at any depth. Every report is one point of its board's
history, in context.date order — two reports on one day are two points. The
reports are folded into the four rows per benchmark per point the dashboard
works on (median / p25 / p75 / CV) and injected as `window.BENCH_DRIFT_DATA`
into the page, which is written to a file (-o) or served on a port (--serve).

    bench-drift --board qemu-x86_64=runs/qemu -o dashboard.html
    bench-drift --board qemu-x86_64=runs/qemu --board board1=runs/b1 --serve

A bench-drift.toml next to you (or --config) holds how to read — days,
metric, port, groups — never what to read. Stdlib only. See --help.
"""

import argparse
import glob
import tomllib
import gzip
import io
import json
import os
import re
import statistics
import sys
import webbrowser
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The one version string: --version, the HTTP Server header and the package
# metadata (pyproject.toml reads it from here) all derive from it.
__version__ = "0.1.0"

UNIT_NS = {"ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}
NUM = re.compile(r"^-?\d+$")

# The dashboard's detector looks for a step inside the last 22 reports using
# 8-report windows; below this it has nothing to chew on and every verdict
# comes back "stable". The page still renders — the trace and the catalogue
# are fine.
MIN_REPORTS_FOR_DETECTOR = 12


def warn(msg):
    print("warning: " + msg, file=sys.stderr)


def die(msg):
    print("error: " + msg, file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------- name parsing

def split_top(name):
    """Split a benchmark name on '/' outside template brackets."""
    parts, depth, cur = [], 0, []
    for ch in name:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        if ch == "/" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def parse_name(name):
    """BM_SortInt<int64_t>/65536/threads:16 -> (fam, tmpl, arg, threads)."""
    parts = split_top(name)
    head = parts[0]
    threads, args = 1, []
    for p in parts[1:]:
        if p.startswith("threads:"):
            try:
                threads = int(p[len("threads:"):])
            except ValueError:
                pass
        elif NUM.match(p):
            args.append(int(p))
    i = head.find("<")
    if i >= 0 and head.endswith(">"):
        fam, tmpl = head[:i], head[i:]
    else:
        fam, tmpl = head, ""
    return fam, tmpl, (args[0] if args else 0), threads


# ------------------------------------------------------------------ statistics

def pct(sorted_vals, q):
    """Linear-interpolation percentile over an already sorted list."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def summarise(times, agg, reps_hint):
    """One report of one benchmark -> (med, p25, p75, cv, reps).

    Prefers the raw repetition rows. Falls back to the aggregate rows, which is
    what --benchmark_report_aggregates_only leaves behind: there p25/p75 are
    reconstructed from the reported stddev assuming a normal channel, which is
    only ever used to draw the band, never by the detector.
    """
    if len(times) >= 2:
        s = sorted(times)
        med = pct(s, 0.5)
        sd = statistics.stdev(s)
        return med, pct(s, 0.25), pct(s, 0.75), (sd / med if med else 0.0), len(s)
    if len(times) == 1:
        v = times[0]
        return v, v, v, 0.0, 1
    med = agg.get("median", agg.get("mean"))
    if med is None:
        return None
    sd = agg.get("stddev", 0.0) or 0.0
    cv = agg.get("cv")
    if cv is None:
        cv = sd / med if med else 0.0
    # 0.6745 sigma is the quartile of a normal distribution
    return med, med - 0.6745 * sd, med + 0.6745 * sd, cv, reps_hint or 1


# ------------------------------------------------------------- report reading

def read_report(path, metric):
    """One --benchmark_format=json file -> (meta, {name: measurement}).

    Everything the header and the fact list show has to come from the report:
    the other time metric, the iteration count, the repetition count and the
    throughput counters, each read rather than reconstructed.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        warn("skipping %s: %s" % (path, exc))
        return None
    if not isinstance(doc, dict) or "benchmarks" not in doc:
        warn("skipping %s: not a Google Benchmark report" % path)
        return None

    ctx = doc.get("context") or {}
    when = None
    raw_date = ctx.get("date")
    if raw_date:
        # google/benchmark writes "2026-09-10T03:12:00+00:00", older ones
        # "2026-09-10 03:12:00" and some builds append a zone name
        txt = str(raw_date).strip().replace(" ", "T", 1)
        for cut in (txt, txt[:19]):
            try:
                when = datetime.fromisoformat(cut)
                break
            except ValueError:
                continue
    if when is None:
        when = datetime.fromtimestamp(os.path.getmtime(path))
        warn("%s: no usable context.date, using the file's mtime" % path)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    other = "real_time" if metric == "cpu_time" else "cpu_time"
    raw = defaultdict(list)        # name -> the metric the dashboard plots, ns
    raw_other = defaultdict(list)  # name -> the other one, ns, shown as a fact
    counters = defaultdict(lambda: defaultdict(list))
    counters_agg = defaultdict(dict)
    aggs = defaultdict(dict)
    aggs_other = defaultdict(dict)
    info = {}
    errors = 0
    for b in doc.get("benchmarks") or []:
        if not isinstance(b, dict):
            continue
        if b.get("error_occurred"):
            errors += 1
            continue
        name = b.get("run_name") or b.get("name")
        value = b.get(metric)
        if not name or value is None:
            continue
        scale = UNIT_NS.get(b.get("time_unit", "ns"), 1.0)
        slot = info.setdefault(name, {})
        if b.get("repetitions"):
            slot["repetitions"] = b["repetitions"]
        # a row with aggregate_name is an aggregate whatever run_type claims:
        # the default output (repetitions > 1) lists the raw rows first and the
        # mean/median/stddev/cv rows after them, in the same array
        kind = b.get("aggregate_name") or ""
        if kind or b.get("run_type") == "aggregate":
            # on an aggregate row `iterations` is the repetition count and the
            # counters are aggregates too (a cv row carries ratios) — neither is
            # the number the fact list wants, so they count only as a fallback
            if b.get("iterations"):
                slot.setdefault("iterations_agg", b["iterations"])
            if kind in ("median", "mean"):
                for key in ("bytes_per_second", "items_per_second"):
                    if b.get(key) is not None:
                        counters_agg[name].setdefault(key, {})[kind] = float(b[key])
            if b.get("aggregate_unit") == "percentage" or kind == "cv":
                aggs[name][kind] = float(value)          # already a ratio
            else:
                aggs[name][kind] = float(value) * scale
                if b.get(other) is not None:
                    aggs_other[name][kind] = float(b[other]) * scale
        else:
            if b.get("iterations"):
                slot["iterations"] = b["iterations"]
            # google/benchmark writes these two only when the benchmark sets them
            for key in ("bytes_per_second", "items_per_second"):
                if b.get(key) is not None:
                    counters[name][key].append(float(b[key]))
            raw[name].append(float(value) * scale)
            if b.get(other) is not None:
                raw_other[name].append(float(b[other]) * scale)
    if errors:
        warn("%s: %d benchmark(s) reported an error, dropped" % (path, errors))

    def one(values, agg):
        """The same median, for a metric that only ever needs its latest value."""
        if values:
            return pct(sorted(values), 0.5)
        return agg.get("median", agg.get("mean"))

    out = {}
    for name in set(raw) | set(aggs):
        got = summarise(raw.get(name, []), aggs.get(name, {}),
                        info.get(name, {}).get("repetitions", 0))
        if got is None:
            continue
        med, p25, p75, cv, reps = got
        if not med or med <= 0:
            continue
        c, ca, slot = counters.get(name, {}), counters_agg.get(name, {}), info.get(name, {})
        out[name] = {"med": med, "p25": p25, "p75": p75, "cv": cv, "reps": reps,
                     "iters": slot.get("iterations") or slot.get("iterations_agg", 0),
                     "other": one(raw_other.get(name, []), aggs_other.get(name, {})),
                     "bps": one(c.get("bytes_per_second", []), ca.get("bytes_per_second", {})),
                     "ips": one(c.get("items_per_second", []), ca.get("items_per_second", {}))}

    exe = ctx.get("executable") or ""
    meta = {
        "path": path,
        "when": when,
        "host": str(ctx.get("host_name") or ""),
        "exe": os.path.basename(exe) if exe else "",
    }
    return meta, out


# ------------------------------------------------------------------- discovery

def expand(where):
    """A directory -> every *.json under it at any depth; otherwise a glob."""
    if os.path.isdir(where):
        found = []
        for root, dirs, files in os.walk(where):
            dirs.sort()
            found += [os.path.join(root, f) for f in sorted(files) if f.endswith(".json")]
        return found
    return sorted(p for p in glob.glob(where, recursive=True) if p.endswith(".json"))


def discover(specs):
    """--board NAME=PATH, repeatable -> [(board, [file, ...]), ...] in argument
    order. A file belongs to a board only because the argument said so: no
    layout is read into anything, PATH is walked for *.json at any depth."""
    if not specs:
        die("no boards: pass --board NAME=PATH for each board (the path is walked for *.json)")
    boards, order = {}, []
    for spec in specs:
        if "=" not in spec:
            die("--board wants NAME=PATH, got %r" % spec)
        name, _, where = spec.partition("=")
        if not name or not where:
            die("--board wants NAME=PATH, got %r" % spec)
        files = expand(where)
        if not files:
            die("--board %s: no .json under %s" % (name, where))
        if name not in boards:
            boards[name] = []
            order.append(name)
        boards[name] += [f for f in files if f not in boards[name]]
    return [(name, boards[name]) for name in order]


# ------------------------------------------------------------------- assembly

def build_payload(boards, args):
    """-> the object the page reads out of window.BENCH_DRIFT_DATA."""
    # board -> [(meta, measurements), ...] in launch order: every report is a
    # column of that board's history, however many were written on one day
    per_board, order = {}, []
    for board, files in boards:
        reports = []
        for path in files:
            got = read_report(path, args.metric)
            if got is None:
                continue
            meta, vals = got
            if not vals:
                warn("%s: no usable benchmarks" % path)
                continue
            reports.append((meta, vals))
        if not reports:
            warn("board %s: nothing usable, dropped" % board)
            continue
        # by calendar day, then by instant, then by path. The day comes first
        # because the page filters and labels by it and assumes it never runs
        # backwards along the columns — with mixed offsets on one board (DST,
        # a relocated host) the instant order and the day order can disagree.
        # Two files with one context.date are both points, in a fixed order,
        # rather than one of them swallowed.
        reports.sort(key=lambda mv: (day_of(mv[0]), mv[0]["when"], mv[0]["path"]))
        per_board[board] = reports
        order.append(board)                    # argument order, nothing cleverer
    if not per_board:
        die("no usable reports")

    if args.days:
        # the last N calendar days back from the newest report of any board,
        # whatever landed inside them — a day may hold two reports, or none
        newest = max(day_of(meta) for b in order for meta, _ in per_board[b])
        cut = newest - timedelta(days=args.days - 1)
        for board in list(order):
            per_board[board] = [mv for mv in per_board[board] if day_of(mv[0]) >= cut]
            if not per_board[board]:
                warn("board %s: nothing within the last %s, dropped" % (board, plural(args.days, "day")))
                del per_board[board]; order.remove(board)
        if not per_board:
            die("no reports within the last %d days" % args.days)

    for board in order:
        n = len(per_board[board])
        if n == 1:
            # one report is a legitimate input: the dashboard becomes a snapshot
            # of it — times, spread across repetitions, CV, counters — with
            # nothing to compare against, and it says so instead of drawing zeros
            warn("board %s: one report only — the dashboard is a snapshot: no trend, no Δ, no verdicts" % board)
        elif n < MIN_REPORTS_FOR_DETECTOR:
            warn("board %s: only %d reports — the change-point detector needs about %d before it "
                 "reports anything, so every verdict will read 'stable'"
                 % (board, n, MIN_REPORTS_FOR_DETECTOR))

    # board -> name -> column index -> measurement
    grid = {b: defaultdict(dict) for b in order}
    facts, reps_seen = {}, Counter()
    for board in order:
        for ci, (meta, vals) in enumerate(per_board[board]):
            for name, m in vals.items():
                grid[board][name][ci] = m
                reps_seen[m["reps"]] += 1
                if name not in facts:
                    fam, tmpl, arg, threads = parse_name(name)
                    facts[name] = {"fam": fam, "tmpl": tmpl, "arg": arg, "threads": threads}

    groups = args.groups or {}

    r = lambda x: float("%.6g" % x)
    out, gaps, partial = [], 0, 0
    for name in sorted(facts):
        f = facts[name]
        series = {}
        for board in order:
            got = grid[board].get(name)
            if not got:
                continue
            # A benchmark added mid-history, or absent from one report (a
            # re-run of part of the suite, an error), leaves holes. The page's
            # model has no notion of a missing point, so a hole carries the
            # last known value forward (and the first known value backward).
            # Flat, not invented.
            n = len(per_board[board])
            med = [0.0] * n; p25 = [0.0] * n; p75 = [0.0] * n; cv = [0.0] * n
            last = None
            for i in range(n):
                m = got.get(i)
                if m is None:
                    gaps += 1
                    m = last
                    if m is None:
                        nxt = min(got)
                        m = got[nxt]
                else:
                    last = m
                med[i] = r(m["med"]); p25[i] = r(m["p25"])
                p75[i] = r(m["p75"]); cv[i] = r(m["cv"])
            if len(got) < n:
                partial += 1
            # iteration count, repetitions and the counters differ per board and
            # drift over the history — the header shows the latest report, so
            # that is the report these come from, not whichever was seen first
            newest = got[max(got)]
            series[board] = {"med": med, "p25": p25, "p75": p75, "cv": cv,
                             "iters": newest["iters"], "reps": newest["reps"]}
            for key, value in (("rt", newest["other"]), ("bps", newest["bps"]),
                               ("ips", newest["ips"])):
                if value:
                    series[board][key] = r(value)
        if not series:
            continue
        out.append({"name": name, "fam": f["fam"], "tmpl": f["tmpl"],
                    "arg": f["arg"], "threads": f["threads"],
                    # a label only when [groups] gives one
                    "group": groups.get(f["fam"], ""),
                    "s": series})

    if partial:
        warn("%d benchmark/board series had missing reports (%d points filled "
             "by carrying the neighbouring value)" % (partial, gaps))

    reps = reps_seen.most_common(1)[0][0] if reps_seen else 1
    board_meta = []
    for board in order:
        reports = per_board[board]
        host = ""
        for meta, _ in reports:
            if meta["host"]:
                host = meta["host"]
        covered = sum(1 for b in out if board in b["s"])
        n = len(reports)
        board_meta.append({"id": board, "host": host,
                           # the file this board's latest numbers were read from,
                           # so the header names something that actually exists
                           "report": display_path(reports[-1][0]["path"]),
                           "note": "%s · %s" % (plural(covered, "benchmark"), plural(n, "report")),
                           # the board's own axis: one point per report, in
                           # launch order, each with its timestamp, the
                           # calendar day it belongs to and its file
                           "runs": [{"date": meta["when"].isoformat(),
                                     "day": day_of(meta).isoformat(),
                                     "report": display_path(meta["path"])}
                                    for meta, _ in reports]})

    return {# which time is plotted, so the page can label it and the other one
            "metric": args.metric,
            "reps": reps, "boards": board_meta, "benchmarks": out}


def day_of(meta):
    """The calendar day a report belongs to: the date it wrote in context.date,
    in its own offset — a launch at 02:00+03:00 is that day on the operator's
    clock, not the UTC evening before. Nothing is converted."""
    return meta["when"].date()


def plural(n, word, many=None):
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))


def display_path(path):
    """How a report's path is shown in the header — relative while that stays
    readable, absolute once it would start climbing out of the directory."""
    if not path:
        return ""
    rel = os.path.relpath(path)
    return rel if not rel.startswith("..") else os.path.abspath(path)


# ---------------------------------------------------------------------- config

CONFIG_KEYS = {"days": int, "metric": str, "port": int, "groups": dict}
DEFAULTS = {"days": 90, "metric": "cpu_time", "port": 8777, "groups": {}}


def load_config(path, required=None):
    """bench-drift.toml -> {days, metric, port, groups}, only the keys present.

    The file says how to read, never what: boards live on the command line
    (--board NAME=PATH), so a [boards] table here is refused with that hint.
    A missing file is empty unless it was named explicitly."""
    if required is None:
        required = True
    if not os.path.exists(path):
        if required:
            die("no config at %s" % path)
        return {}
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        die("%s: %s" % (path, exc))
    if "boards" in raw:
        die("%s: boards are named on the command line, not in the config: "
            "--board NAME=PATH for each" % path)
    cfg = {}
    for key, value in raw.items():
        want = CONFIG_KEYS.get(key)
        if want is None:
            warn("%s: unknown key %r ignored" % (path, key))
            continue
        if not isinstance(value, want) or isinstance(value, bool):
            die("%s: %s must be %s" % (path, key, want.__name__))
        if key == "metric" and value not in ("cpu_time", "real_time"):
            die("%s: metric must be cpu_time or real_time" % path)
        cfg[key] = value
    return cfg


def settings(cfg, args):
    """Effective settings: an argument given beats the config, which beats
    the default. --groups on the command line is a JSON file; in the config
    it is a table — either way it ends up a dict."""
    eff = argparse.Namespace()
    for key, default in DEFAULTS.items():
        given = getattr(args, key, None)
        if key == "groups" and isinstance(given, str):
            with open(given, "r", encoding="utf-8") as fh:
                given = json.load(fh)
        eff.__dict__[key] = given if given is not None else cfg.get(key, default)
    return eff


# --------------------------------------------------------------------- serving

# The page is a fragment; this is the document skeleton it is wrapped in —
# [hidden]{display:none} in particular, without which the Chart / All boards /
# Table toggles stop hiding anything.
RESET = """<style>
  :root{ color-scheme: light; }
  html,body{ margin:0; }
  body{ font:14px system-ui,-apple-system,"Segoe UI",sans-serif; }
  img{ max-width:100%; }
  [hidden]{ display:none !important; }
</style>"""


def default_page():
    """The dashboard fragment ships inside the package, next to this module —
    the same place whether the clone runs ./bench-drift or pip installed it."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench-drift.html")


def build_page(fragment_path, payload):
    with open(fragment_path, "r", encoding="utf-8") as fh:
        frag = fh.read()
    cut = frag.find("</style>")
    if cut < 0:
        die("%s does not look like the dashboard fragment" % fragment_path)
    cut += len("</style>")
    head, body = frag[:cut].strip(), frag[cut:].strip()
    blob = json.dumps(payload, separators=(",", ":"))
    # </script> inside the data would close the tag early
    blob = blob.replace("</", "<\\/")
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            + RESET + "\n" + head + "\n</head>\n<body>\n"
            "<script>window.BENCH_DRIFT_DATA=" + blob + ";</script>\n"
            + body + "\n</body>\n</html>\n").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    page = b""
    payload = b""
    server_version = "bench-drift/" + __version__

    def version_string(self):
        return self.server_version           # without the Python version tag

    def log_message(self, fmt, *a):
        if self.server.verbose:
            sys.stderr.write("  %s %s\n" % (self.address_string(), fmt % a))

    def _send(self, body, ctype):
        enc = None
        if "gzip" in (self.headers.get("Accept-Encoding") or ""):
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
                gz.write(body)
            body, enc = buf.getvalue(), "gzip"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if enc:
            self.send_header("Content-Encoding", enc)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(self.page, "text/html; charset=utf-8")
        elif path == "/data.json":
            self._send(self.payload, "application/json; charset=utf-8")
        else:
            self.send_error(404, "nothing here but / and /data.json")


# ------------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="bench-drift",
        description="A dashboard out of folders of Google Benchmark reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""boards are named on the command line and nowhere else:
  --board qemu-x86_64=runs/qemu --board board1=runs/b1
PATH is walked for *.json at any depth (the layout inside does not matter),
or is a glob. Every file is one point of the board's history, in context.date
order; two files on one day — a re-run, a second measurement — are two points.

bench-drift.toml next to you, or --config, holds how to read:
  days = 90              # how much history the page gets: calendar days back
                         # from the newest report, whatever ran inside them
  metric = "cpu_time"    # or "real_time"
  port = 8777
  [groups]
  BM_Crc32 = "hashing"
An argument given here beats the file.
""")
    ap.add_argument("--board", action="append", default=[], metavar="NAME=PATH",
                    help="a board and where its reports are (repeatable)")
    out = ap.add_argument_group("what to produce (one of)")
    out.add_argument("-o", "--output", metavar="FILE", help="write a standalone dashboard")
    out.add_argument("--serve", action="store_true", help="serve the dashboard on a port")
    out.add_argument("--dump", metavar="FILE", help="write only the payload as JSON")
    ap.add_argument("--config", metavar="FILE",
                    help="settings file (default: bench-drift.toml in the working directory, if present)")
    ap.add_argument("--days", type=int, metavar="N",
                    help="keep only the last N calendar days, back from the newest report; 0 keeps everything (config: days)")
    ap.add_argument("--metric", choices=["cpu_time", "real_time"], help="which time to show (config: metric)")
    ap.add_argument("--port", type=int, help="port for --serve (config: port)")
    ap.add_argument("--host", default="127.0.0.1", help="bind address for --serve (default 127.0.0.1)")
    ap.add_argument("--groups", metavar="FILE", help="JSON object mapping family -> group (config: [groups])")
    ap.add_argument("--page", default=default_page(),
                    metavar="FILE", help="the dashboard fragment to build on (default: the one in the package)")
    ap.add_argument("--version", action="version", version="bench-drift " + __version__)
    ap.add_argument("--open", action="store_true", help="open a browser after --serve")
    ap.add_argument("-v", "--verbose", action="store_true", help="log requests")
    args = ap.parse_args(argv)

    if not args.board:
        ap.error("give me boards: --board NAME=PATH for each (the path is walked for *.json)")
    if not (args.output or args.serve or args.dump):
        ap.error("say what to produce: -o FILE for a dashboard file, or --serve for a port")
    if not os.path.exists(args.page):
        die("no dashboard fragment at %s (pass --page)" % args.page)

    if args.config:
        cfg = load_config(args.config, required=True)
    else:
        cfg = load_config(os.path.join(os.getcwd(), "bench-drift.toml"), required=False)
    eff = settings(cfg, args)

    boards = discover(args.board)
    payload = build_payload(boards, eff)

    nb = len(payload["benchmarks"])
    fams = len({b["fam"] for b in payload["benchmarks"]})
    print("%s · %s · %s (%s) · %d repetitions"
          % (plural(nb, "benchmark"), plural(fams, "family", "families"),
             plural(len(payload["boards"]), "board"),
             ", ".join("%s: %s" % (x["id"], plural(len(x["runs"]), "report")) for x in payload["boards"]),
             payload["reps"]))

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        print("payload written to %s (%.1f MB)" % (args.dump, os.path.getsize(args.dump) / 1e6))
        if not (args.output or args.serve):
            return 0

    page = build_page(args.page, payload)
    if args.output:
        with open(args.output, "wb") as fh:
            fh.write(page)
        print("dashboard written to %s (%.1f MB)" % (args.output, os.path.getsize(args.output) / 1e6))
        if not args.serve:
            return 0

    Handler.page = page
    Handler.payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    try:
        httpd = ThreadingHTTPServer((args.host, eff.port), Handler)
    except OSError as exc:
        die("cannot listen on %s:%d — %s" % (args.host, eff.port, exc))
    httpd.verbose = args.verbose
    url = "http://%s:%d/" % (args.host, eff.port)
    print("serving %s  (%.1f MB page, Ctrl-C to stop)" % (url, len(page) / 1e6))
    sys.stdout.flush()      # so the address shows up even when stdout is a pipe
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0

