#!/usr/bin/env python3
import argparse
import json
import os
from urllib.parse import quote
from urllib.request import Request, urlopen

LABEL = "Trigger: tests using secrets"


def _has_write(repo_id: int, username: str, *, token: str) -> bool:
    req = Request(
        f"https://api.github.com/repositories/{repo_id}/collaborators/{username}/permission",
        headers={"Authorization": f"token {token}"},
    )
    contents = json.load(urlopen(req, timeout=10))

    return contents["permission"] in {"admin", "write"}


def _remove_label(repo_id: int, pr: int, label: str, *, token: str) -> None:
    quoted_label = quote(label)
    req = Request(
        f"https://api.github.com/repositories/{repo_id}/issues/{pr}/labels/{quoted_label}",
        method="DELETE",
        headers={"Authorization": f"token {token}"},
    )
    urlopen(req)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", type=int, required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--label-names", type=json.loads, required=True)
    args = parser.parse_args()

    token = os.environ["GITHUB_TOKEN"]

    write_permission = _has_write(args.repo_id, args.username, token=token)

    if (
        not write_permission
        # `reopened` is included here due to close => push => reopen
        and args.event in {"synchronize", "reopened"}
        and LABEL in args.label_names
    ):
        print(f"Invalidating label [{LABEL}] due to code change...")
        _remove_label(args.repo_id, args.pr, LABEL, token=token)
        args.label_names.remove(LABEL)

    if write_permission or LABEL in args.label_names:
        print("Permissions passed!")
        print(f"- has write permission: {write_permission}")
        print(f"- has [{LABEL}] label: {LABEL in args.label_names}")
        return 0
    else:
        print("Permissions failed!")
        print(f"- has write permission: {write_permission}")
        print(f"- has [{LABEL}] label: {LABEL in args.label_names}")
        print(f"- args.label_names: {args.label_names}")
        print(
            f"Please have a collaborator add the [{LABEL}] label once they "
            f"have reviewed the code to trigger tests."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
