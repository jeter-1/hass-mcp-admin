"""Validate that Josh's latest PR lifecycle decision authorizes the current head."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


AUTHORIZING_ACTOR = "jeter-1"
HEAD_LIFECYCLE_EVENTS = {
    "committed",
    "convert_to_draft",
    "head_ref_force_pushed",
    "ready_for_review",
}


class AuthorizationError(ValueError):
    """The timeline does not prove current-head Ready authorization."""


def validate_timeline(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    lifecycle = [
        item for item in timeline if item.get("event") in HEAD_LIFECYCLE_EVENTS
    ]
    if not lifecycle:
        raise AuthorizationError("pull-request timeline has no Ready lifecycle evidence")

    latest = lifecycle[-1]
    actor = latest.get("actor")
    actor_login = actor.get("login") if isinstance(actor, dict) else None
    if latest.get("event") != "ready_for_review" or actor_login != AUTHORIZING_ACTOR:
        raise AuthorizationError(
            "the latest head lifecycle event is not a Josh-authored Ready action"
        )
    return {
        "status": "authorized",
        "event_id": latest.get("id"),
        "actor": actor_login,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        timeline = json.loads(args.timeline.read_text(encoding="utf-8"))
        if not isinstance(timeline, list) or not all(
            isinstance(item, dict) for item in timeline
        ):
            raise AuthorizationError("pull-request timeline must be an array of objects")
        result = validate_timeline(timeline)
    except (AuthorizationError, OSError, json.JSONDecodeError) as exc:
        print(f"Ready authorization evidence is invalid: {exc}", file=sys.stderr)
        return 1

    args.output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    print("Current pull-request head has a fresh Josh-authored Ready action.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
