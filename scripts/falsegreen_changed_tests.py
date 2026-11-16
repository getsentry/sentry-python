"""
Run falsegreen on the test lines that a pull request adds or changes.

High-confidence findings fail the run, low-confidence findings are reported as
warnings. Findings on lines the pull request didn't touch are ignored.

In CI, HEAD is the pull request merge commit, so its first parent is the base
branch. Locally, commit your changes and pass the commit to compare against:

    uv run --group falsegreen python scripts/falsegreen_changed_tests.py \
        "$(git merge-base origin/master HEAD)"
"""

import json
import re
import subprocess
import sys
from collections import defaultdict

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# falsegreen exit codes: 0 clean, 10 low-confidence findings only,
# 20 at least one high-confidence finding
FALSEGREEN_EXIT_CODES = (0, 10, 20)


def changed_test_lines(base_ref):
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--unified=0",
            "--diff-filter=AM",
            "--no-color",
            base_ref,
            "HEAD",
            "--",
            "tests/*.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    lines = defaultdict(set)
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[len("+++ b/") :]
            continue

        match = HUNK_RE.match(line)
        if match and path is not None:
            start = int(match.group(1))
            count = int(match.group(2) or 1)
            lines[path].update(range(start, start + count))

    return lines


def main():
    base_ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD^1"
    lines = changed_test_lines(base_ref)
    if not lines:
        print("No changed test files.")
        return 0

    result = subprocess.run(
        [sys.executable, "-m", "falsegreen", "--json", *sorted(lines)],
        capture_output=True,
        text=True,
    )
    if result.returncode not in FALSEGREEN_EXIT_CODES:
        sys.stderr.write(result.stderr)
        return result.returncode

    findings = [
        finding
        for finding in json.loads(result.stdout)
        if finding["line"] in lines[finding["file"]]
    ]

    for finding in findings:
        level = "error" if finding["confidence"] == "high" else "warning"
        print(
            "::{} file={},line={},title=falsegreen {}::{}. {}".format(
                level,
                finding["file"],
                finding["line"],
                finding["code"],
                finding["title"],
                finding.get("fix", ""),
            )
        )

    high = sum(finding["confidence"] == "high" for finding in findings)
    print(
        "falsegreen: {} high, {} low finding(s) on changed lines.".format(
            high, len(findings) - high
        )
    )
    if high:
        print(
            "If a finding is a false positive, silence it with "
            "`# falsegreen: ignore[CODE]` on that line."
        )
    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main())
