#!/bin/sh
# Runs every test tier. Python and the model tests take seconds; the browser
# tier needs Chrome on PATH and takes about a dozen seconds — skip it with
#   tests/run-tests.sh --quick
set -e
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ litter from the test run

# Keep the machine breathing. Each tier runs in its own cgroup with a memory
# cap when systemd-run is available, so a runaway test dies alone instead of
# taking the biggest process on the box with it; Node's heap is bounded and
# test files run one at a time. A failing assertion on a huge object once
# pretty-printed 5 447 benchmarks into a 2 GB error message — hence the caps.
export NODE_OPTIONS="--max-old-space-size=512"
if command -v systemd-run >/dev/null 2>&1; then
  capped() { cap=$1; shift; systemd-run --user --scope -q -p "MemoryMax=$cap" -- "$@"; }
else
  capped() { shift; "$@"; }
fi
NODE_TEST="node --test --test-concurrency=1"

echo "== bench_drift (python unittest)"
capped 1000M python3 -m unittest discover -s tests
echo "== the page as a file (node:test)"
capped 1000M $NODE_TEST tests/page.test.js
echo "== page model (node:test)"
capped 1500M $NODE_TEST tests/model.test.js
if [ "$1" != "--quick" ]; then
  echo "== rendered page (headless chrome)"
  capped 2500M $NODE_TEST tests/browser.test.js
fi
