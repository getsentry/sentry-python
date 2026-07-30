#!/usr/bin/env python3
"""Analyze .coverage DB with per-test contexts: find coverage-redundant tests.

A test is "coverage-redundant" iff every line and every branch arc it covers
is also covered by at least one other test. Deleting it cannot reduce coverage
totals (advisory only — checks.sh is the real guard).

Outputs:
  .auto/redundant_tests.txt  — ranked candidates (redundant first)
  .auto/attribution.json     — per-test covered/unique entity counts
"""
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from coverage.numbits import numbits_to_nums

DB = ".coverage"
OUT_DIR = Path(".auto")

con = sqlite3.connect(DB)
files = dict(con.execute("select id, path from file").fetchall())
contexts = dict(con.execute("select id, context from context").fetchall())


def nodeid(ctx: str) -> str:
    # "tests/x.py::test_y[1]|setup" -> "tests/x.py::test_y[1]"
    return ctx.rsplit("|", 1)[0] if "|" in ctx else ctx


# test -> set((file, lineno)), test -> set((file, from, to))
test_lines = defaultdict(set)
test_arcs = defaultdict(set)

# With branch=true, coverage stores arcs only; line_bits is empty.
# Derive per-test lines from arcs: arc fromno->tono covers fromno (and tono if >0).
for file_id, ctx_id, numbits in con.execute(
    "select file_id, context_id, numbits from line_bits"
):
    t = nodeid(contexts[ctx_id])
    path = files[file_id]
    for ln in numbits_to_nums(numbits):
        test_lines[t].add((path, ln))

for file_id, ctx_id, fromno, tono in con.execute(
    "select file_id, context_id, fromno, tono from arc"
):
    t = nodeid(contexts[ctx_id])
    path = files[file_id]
    test_arcs[t].add((path, fromno, tono))
    test_lines[t].add((path, fromno))
    if tono > 0:
        test_lines[t].add((path, tono))

# coverage counts per entity
line_count = defaultdict(int)
for lines in test_lines.values():
    for e in lines:
        line_count[e] += 1
arc_count = defaultdict(int)
for arcs in test_arcs.values():
    for e in arcs:
        arc_count[e] += 1

rows = []
all_tests = sorted(set(test_lines) | set(test_arcs))
for t in all_tests:
    lines = test_lines.get(t, set())
    arcs = test_arcs.get(t, set())
    uniq_lines = sum(1 for e in lines if line_count[e] == 1)
    uniq_arcs = sum(1 for e in arcs if arc_count[e] == 1)
    rows.append(
        {
            "test": t,
            "lines": len(lines),
            "arcs": len(arcs),
            "unique_lines": uniq_lines,
            "unique_arcs": uniq_arcs,
            "redundant": uniq_lines == 0 and uniq_arcs == 0,
        }
    )

redundant = [r for r in rows if r["redundant"]]
redundant.sort(key=lambda r: -(r["lines"] + r["arcs"]))
keepers = [r for r in rows if not r["redundant"]]
keepers.sort(key=lambda r: (r["unique_lines"] + r["unique_arcs"]))

with open(OUT_DIR / "attribution.json", "w") as f:
    json.dump(rows, f, indent=1)

with open(OUT_DIR / "redundant_tests.txt", "w") as f:
    f.write(f"# {len(redundant)} fully coverage-redundant tests "
            f"(of {len(rows)} tests with attribution)\n")
    f.write("# NOTE: map excludes tests/profiler/test_continuous_profiler.py and\n")
    f.write("# 16 tests that fail under --cov-context=test (conservative).\n\n")
    for r in redundant:
        f.write(f"{r['test']}  (lines={r['lines']}, arcs={r['arcs']})\n")

print(f"tests with attribution: {len(rows)}")
print(f"fully redundant: {len(redundant)}")
print(f"lines covered: {len(line_count)}, arcs covered: {len(arc_count)}")

# Greedy maximal deletable set. Deletion only decreases entity counts, so a
# test can never become deletable later; a single heap-ordered pass suffices:
# pop the most-specialized deletable candidate, recheck against live counts,
# delete (decrement) or skip permanently.
import heapq

heap = [
    (len(test_lines.get(t, ())) + len(test_arcs.get(t, ())), t) for t in all_tests
]
heapq.heapify(heap)
cur_line_count = dict(line_count)
cur_arc_count = dict(arc_count)
removed = set()
deletable = []


def is_deletable(t):
    return all(cur_line_count.get(e, 0) >= 2 for e in test_lines.get(t, ())) and all(
        cur_arc_count.get(e, 0) >= 2 for e in test_arcs.get(t, ())
    )


while heap:
    _, t = heapq.heappop(heap)
    if t in removed or not is_deletable(t):
        continue
    removed.add(t)
    deletable.append(t)
    for e in test_lines.get(t, ()):
        cur_line_count[e] -= 1
    for e in test_arcs.get(t, ()):
        cur_arc_count[e] -= 1

print(f"greedy maximal deletable set: {len(deletable)} tests")
print(f"coverage preserved: all {sum(1 for v in cur_line_count.values() if v > 0)} lines "
      f"and {sum(1 for v in cur_arc_count.values() if v > 0)} arcs still covered")

with open(OUT_DIR / "deletable_set.txt", "w") as f:
    f.write(f"# greedy maximal deletable set: {len(deletable)} tests\n")
    f.write("# deleting ALL of these leaves every attributed line/arc covered (advisory)\n\n")
    for t in deletable:
        f.write(t + "\n")
print("\nsmallest unique-coverage tests (near-redundant, kept):")
for r in keepers[:15]:
    print(f"  uniq_l={r['unique_lines']:3d} uniq_a={r['unique_arcs']:3d}  {r['test']}")
