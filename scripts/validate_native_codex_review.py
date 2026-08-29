"""Validate native Codex GitHub review evidence for an exact pull-request head."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


CODEX_LOGIN = "chatgpt-codex-connector[bot]"
SUMMARY_MARKER = "<!-- codex-pull-request-review-summary -->"
SUMMARY_ROW = re.compile(
    r"\|\s*📝\s*\*\*Code Review\*\*\s*\|"
    r"\s*(?P<status>.*?)\s*\|\s*`(?P<commit>[0-9a-fA-F]{7,40})`\s*\|"
)
TERMINAL_SUCCESS = "✅ **Completed**"
PENDING_MARKERS = ("🔄 **Running**", "Queued", "Pending")
FAILURE_MARKERS = ("Failed", "Error", "Cancelled", "Canceled", "Timed out")
SUBMITTED_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
NON_REVIEW_MARKERS = (
    "chatgpt.com/codex/cloud/settings/environments",
    "to use codex here, [create an environment for this repo]",
)


class EvidenceError(ValueError):
    """The connector evidence is malformed or reports a failed review."""


def _load_array(path: Path, label: str) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise EvidenceError(f"{label} must be a JSON array of objects")
    return value


def inspect_evidence(
    *,
    head_sha: str,
    comments: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    review_comments: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = head_sha.lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise EvidenceError("head SHA must be 40 lowercase hexadecimal characters")

    for review in reversed(reviews):
        user = review.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        commit_id = str(review.get("commit_id", "")).lower()
        state = str(review.get("state", "")).upper()
        if (
            login == CODEX_LOGIN
            and commit_id == expected
            and state in SUBMITTED_REVIEW_STATES
        ):
            review_id = review.get("id")
            if not isinstance(review_id, int) or review_id <= 0:
                raise EvidenceError("native Codex submitted review has no valid review id")
            attached_bodies = [
                str(item.get("body", ""))
                for item in review_comments
                if item.get("pull_request_review_id") == review_id
                and item.get("commit_id") == expected
                and isinstance(item.get("user"), dict)
                and item["user"].get("login") == CODEX_LOGIN
            ]
            evidence_text = "\n".join(
                [str(review.get("body", "")), *attached_bodies]
            ).lower()
            if any(marker in evidence_text for marker in NON_REVIEW_MARKERS):
                raise EvidenceError(
                    "native Codex submitted an operational notice instead of a code review"
                )
            return {
                "status": "complete",
                "evidence_kind": "submitted_review",
                "commit_ref": commit_id,
                "exact": True,
                "review_state": state,
            }

    matching_summary: tuple[str, str] | None = None
    for comment in reversed(comments):
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        body = comment.get("body")
        if login != CODEX_LOGIN or not isinstance(body, str) or SUMMARY_MARKER not in body:
            continue
        match = SUMMARY_ROW.search(body)
        if match is None:
            raise EvidenceError("native Codex summary has an unknown format")
        commit_ref = match.group("commit").lower()
        if not expected.startswith(commit_ref):
            continue
        matching_summary = (match.group("status").strip(), commit_ref)
        break

    if matching_summary is None:
        return {
            "status": "pending",
            "reason": "no native Codex summary exists for the current head",
        }

    status_text, commit_ref = matching_summary
    if TERMINAL_SUCCESS in status_text:
        return {
            "status": "complete",
            "evidence_kind": "completed_summary",
            "commit_ref": commit_ref,
            "exact": len(commit_ref) == 40,
            "review_state": "COMPLETED",
        }
    if any(marker in status_text for marker in PENDING_MARKERS):
        return {
            "status": "pending",
            "reason": "native Codex review is still running",
            "commit_ref": commit_ref,
        }
    if any(marker.lower() in status_text.lower() for marker in FAILURE_MARKERS):
        raise EvidenceError(f"native Codex review did not complete successfully: {status_text}")
    raise EvidenceError(f"native Codex summary has an unknown status: {status_text}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--comments", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--review-comments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        result = inspect_evidence(
            head_sha=args.head,
            comments=_load_array(args.comments, "comments"),
            reviews=_load_array(args.reviews, "reviews"),
            review_comments=_load_array(args.review_comments, "review comments"),
        )
    except (EvidenceError, OSError, json.JSONDecodeError) as exc:
        print(f"Native Codex review evidence is invalid: {exc}", file=sys.stderr)
        return 1

    args.output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    if result["status"] == "pending":
        print(result["reason"])
        return 75
    print(
        "Native Codex review receipt is complete for "
        f"{result['commit_ref']} ({result['evidence_kind']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
