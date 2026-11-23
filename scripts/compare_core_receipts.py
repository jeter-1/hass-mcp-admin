#!/usr/bin/env python3
"""Compare a new local Core lane receipt with its committed receipt, field by field (HAMCP-443).

The comparison is offline: it reads two JSON files and the committed exclusion
declaration, and touches nothing else. Every dict key set, list length and leaf
type is compared everywhere. Only a leaf that a declared exclusion pattern
matches skips value equality, and only a declared expected change may hold a
different value; any other difference fails the comparison.

A leaf that a declared suffix mask matches is neither excluded nor compared
raw: both values must be strings ending in ``_`` and exactly eight lowercase
hex digits (a per-run ``secrets.token_hex(4)``), and everything before that
ending must be equal. A value without that ending fails the comparison.

Patterns are lists of segments. A string matches that dict key, a list of
strings matches any of them, ``*`` matches one segment and ``**`` zero or more.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DECLARATION = ROOT / "docs/evidence/hamcp443/receipt-exclusions.json"
MODEL = "hamcp443-receipt-comparison-v1"
REPORT_MODEL = "hamcp443-receipt-comparison-report-v2"
LANE_KEYS = {"harness", "committed_receipt", "committed_receipt_sha256", "excluded", "expected_changes"}
OPTIONAL_LANE_KEYS = {"suffix_masked"}
CLASS_KEYS = {"class", "reason", "paths"}
CHANGE_KEYS = {"path", "committed", "observed", "reason"}
SUFFIX = re.compile(r"_[0-9a-f]{8}\Z")


class Refusal(Exception):
    """The declaration or an input cannot be compared."""


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise Refusal("duplicate_key")
        value[key] = item
    return value


def _constant(name):
    raise Refusal("non_finite_number")


def load_json(data):
    try:
        value = json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise Refusal("invalid_json") from exc
    return value


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def pointer(path):
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in path)


def _segment(value):
    if isinstance(value, str) and value:
        return value
    if (isinstance(value, list) and value and len(set(value)) == len(value)
            and all(isinstance(item, str) and item and item not in ("*", "**") for item in value)):
        return tuple(value)
    raise Refusal("invalid_pattern_segment")


def _pattern(value):
    if not isinstance(value, list) or not value:
        raise Refusal("invalid_pattern")
    return tuple(_segment(item) for item in value)


def matches(pattern, path):
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(matches(rest, path[index:]) for index in range(len(path) + 1))
    if not path:
        return False
    part = path[0]
    if head == "*":
        hit = True
    elif isinstance(head, tuple):
        hit = isinstance(part, str) and part in head
    else:
        hit = isinstance(part, str) and part == head
    return hit and matches(rest, path[1:])


def same(left, right):
    """Equality that also requires the same JSON type (True is not 1, 1 is not 1.0)."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and (math.isnan(left) or math.isnan(right)):
        return False
    return left == right


def leaves(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, path + (index,))
    else:
        yield path, value


def lane_declaration(declaration, lane):
    if not isinstance(declaration, dict) or set(declaration) != {"model", "lanes"} or declaration["model"] != MODEL:
        raise Refusal("declaration_shape")
    lanes = declaration["lanes"]
    if not isinstance(lanes, dict) or lane not in lanes:
        raise Refusal("lane_not_declared")
    entry = lanes[lane]
    if not isinstance(entry, dict) or not LANE_KEYS <= set(entry) <= LANE_KEYS | OPTIONAL_LANE_KEYS:
        raise Refusal("lane_shape")
    names = set()

    def declared(key, refusal):
        items = entry.get(key, [])
        if not isinstance(items, list):
            raise Refusal("lane_shape")
        result = []
        for item in items:
            if (not isinstance(item, dict) or set(item) != CLASS_KEYS or not isinstance(item["class"], str)
                    or not item["class"] or item["class"] in names or not isinstance(item["reason"], str)
                    or not item["reason"] or not isinstance(item["paths"], list) or not item["paths"]):
                raise Refusal(refusal)
            names.add(item["class"])
            result.append((item["class"], tuple(_pattern(path) for path in item["paths"])))
        return result

    classes = declared("excluded", "exclusion_class_shape")
    masks = declared("suffix_masked", "suffix_mask_shape")
    changes = {}
    if not isinstance(entry["expected_changes"], list):
        raise Refusal("lane_shape")
    for item in entry["expected_changes"]:
        if (not isinstance(item, dict) or set(item) != CHANGE_KEYS or not isinstance(item["path"], list)
                or not item["path"] or not all(isinstance(part, str) and part not in ("*", "**") for part in item["path"])
                or not isinstance(item["reason"], str) or not item["reason"] or same(item["committed"], item["observed"])):
            raise Refusal("expected_change_shape")
        path = tuple(item["path"])
        if path in changes:
            raise Refusal("expected_change_duplicate")
        changes[path] = (item["committed"], item["observed"])
    for path in changes:
        if any(matches(pattern, path) for _, patterns in classes for pattern in patterns):
            raise Refusal("expected_change_excluded")
        if any(matches(pattern, path) for _, patterns in masks for pattern in patterns):
            raise Refusal("expected_change_masked")
    return entry, classes, changes, masks


def exclusion_class(classes, path):
    for name, patterns in classes:
        if any(matches(pattern, path) for pattern in patterns):
            return name
    return None


def unmasked(value):
    """The value without its per-run suffix, or None when it does not end in exactly that suffix."""
    if isinstance(value, str) and SUFFIX.search(value):
        return value[:-9]
    return None


def compare(committed, observed, classes, changes, masks=()):
    """Return the comparison report body for two parsed receipts."""
    unused = [pointer(pattern) for _, patterns in (*classes, *masks) for pattern in patterns
              if not any(matches(pattern, path) for path, _ in leaves(committed))]
    if unused:
        raise Refusal("exclusion_pattern_unused: " + ", ".join(unused))
    differences, excluded, masked, seen, applied = [], {}, {}, set(), set()
    excluded_leaves = masked_leaves = 0

    def walk(left, right, path):
        nonlocal excluded_leaves, masked_leaves
        if path in changes:
            seen.add(path)
            before, after = changes[path]
            if same(left, before) and same(right, after):
                applied.add(path)
            else:
                differences.append({"path": pointer(path), "kind": "expected_change_not_observed",
                                    "committed": left, "observed": right})
            return
        if type(left) is not type(right):
            differences.append({"path": pointer(path), "kind": "type", "committed": left, "observed": right})
            return
        if isinstance(left, dict):
            for key in left:
                if key not in right:
                    differences.append({"path": pointer(path + (key,)), "kind": "missing"})
                else:
                    walk(left[key], right[key], path + (key,))
            for key in right:
                if key not in left:
                    differences.append({"path": pointer(path + (key,)), "kind": "unexpected"})
            return
        if isinstance(left, list):
            if len(left) != len(right):
                differences.append({"path": pointer(path), "kind": "length",
                                    "committed": len(left), "observed": len(right)})
                return
            for index, (a, b) in enumerate(zip(left, right)):
                walk(a, b, path + (index,))
            return
        name, mask = exclusion_class(classes, path), exclusion_class(masks, path)
        if name is not None and mask is not None:
            raise Refusal("masked_and_excluded: " + pointer(path))
        if mask is not None:
            masked_leaves += 1
            before, after = unmasked(left), unmasked(right)
            if before is None or after is None:
                differences.append({"path": pointer(path), "kind": "mask_shape", "committed": left, "observed": right})
            elif before != after:
                differences.append({"path": pointer(path), "kind": "masked_value", "committed": left, "observed": right})
            elif left != right:
                masked.setdefault(mask, []).append({"path": pointer(path), "committed": left, "observed": right})
        elif name is not None:
            excluded_leaves += 1
            if not same(left, right):
                excluded.setdefault(name, []).append({"path": pointer(path), "committed": left, "observed": right})
        elif not same(left, right):
            differences.append({"path": pointer(path), "kind": "value", "committed": left, "observed": right})

    walk(committed, observed, ())
    for path in changes:
        if path not in seen:
            differences.append({"path": pointer(path), "kind": "expected_change_path_absent"})
    return {"equal": not differences, "differences": differences,
            "expected_changes": [{"path": pointer(path), "applied": path in applied} for path in changes],
            "excluded_leaves": excluded_leaves,
            "excluded_differences": {name: excluded[name] for name, _ in classes if name in excluded},
            "masked_leaves": masked_leaves,
            "masked_differences": {name: masked[name] for name, _ in masks if name in masked}}


def run(lane, observed_path, declaration_path=DECLARATION, root=ROOT):
    declaration_bytes = Path(declaration_path).read_bytes()
    entry, classes, changes, masks = lane_declaration(load_json(declaration_bytes), lane)
    if not isinstance(entry["harness"], str) or not (root / entry["harness"]).is_file():
        raise Refusal("harness_not_found")
    if not isinstance(entry["committed_receipt"], str):
        raise Refusal("lane_shape")
    committed_bytes = (root / entry["committed_receipt"]).read_bytes()
    if sha256(committed_bytes) != entry["committed_receipt_sha256"]:
        raise Refusal("committed_receipt_sha256_mismatch")
    observed_bytes = Path(observed_path).read_bytes()
    body = compare(load_json(committed_bytes), load_json(observed_bytes), classes, changes, masks)
    return {"model": REPORT_MODEL, "lane": lane, "harness": entry["harness"],
            "declaration_sha256": sha256(declaration_bytes),
            "committed_receipt": entry["committed_receipt"], "committed_receipt_sha256": sha256(committed_bytes),
            "observed_receipt_sha256": sha256(observed_bytes), **body}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lane", required=True)
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--declaration", type=Path, default=DECLARATION)
    parser.add_argument("--output", type=Path, help="also write the JSON report to this new file")
    args = parser.parse_args(argv)
    try:
        report = run(args.lane, args.observed, args.declaration)
        text = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            with open(args.output, "x", encoding="utf-8") as stream:
                stream.write(text)
    except (OSError, Refusal) as exc:
        print(json.dumps({"model": REPORT_MODEL, "lane": args.lane, "refusal": str(exc) or type(exc).__name__}))
        return 2
    sys.stdout.write(text)
    return 0 if report["equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
