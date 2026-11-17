"""R1 and R2 comparison of the S6 recapture run (HAMCP-371, 2026-11-14).

Reads capture files as data only (run with ``python3 -I``).  Prints JSON: the
forced case on both versions, the listing and page order of every overview and
services call across versions after the one Core restart, the same for the
order group across that restart, and a per-call payload table.  Stores no
captured value other than service and domain names, error codes and counts.
"""
import hashlib
import json
import sys
from pathlib import Path

VERSIONS = ("9.1.2", "9.2.0")
BEFORE, AFTER = "order-before-core-restart", "calls-after-core-restart"
ORDER_CASES = ("o-overview-standard", "o-services-first-five", "o-services-default-page",
               "o-services-page-1", "o-services-page-2")
LISTING_CASES = ORDER_CASES + ("r17-overview", "v17-overview-standard", "r24-services",
                               "t-large-services", "v24-services-domain-full",
                               "v24-services-domain-summary", "v24-services-response")


def load(root, version, folder, case):
    return json.loads((root / version / folder / f"{case}.json").read_text("utf-8"))


def payload(call):
    """The text item, parsed in wire order.

    The call files are stored canonically (sorted keys), so the order of any
    stored object, ``structuredContent`` included, is the storage order.  Only
    the text item, a string, keeps the order the peer sent.
    """
    for message in (call.get("exchange") or {}).get("messages") or []:
        result = message.get("result")
        if isinstance(result, dict):
            for item in result.get("content") or []:
                if item.get("type") == "text":
                    return json.loads(item["text"])
    return None


def text_matches_structured(call):
    for message in (call.get("exchange") or {}).get("messages") or []:
        result = message.get("result")
        if isinstance(result, dict) and result.get("structuredContent") is not None:
            return payload(call) == result["structuredContent"]
    return None


def listing(case, data):
    if "overview" in case:
        stats = data["domain_stats"]
        return {"domain_order": list(stats),
                "per_domain": {domain: {"count": row.get("count"),
                                        "listed": len(row.get("entities") or []),
                                        "truncated": row.get("truncated")}
                               for domain, row in stats.items()},
                "listed_entities": {domain: [entity.get("entity_id") or entity.get("friendly_name")
                                             for entity in row.get("entities") or []]
                                    for domain, row in stats.items()},
                "most_common_domains": data["ai_insights"]["most_common_domains"],
                "pagination": data["pagination"]}
    return {"order": list(data["services"]), "total_count": data.get("total_count"),
            "has_more": data.get("has_more"), "next_offset": data.get("next_offset"),
            "domains": data.get("domains")}


def contrast(case, old, new):
    row = {"listing_equal": old == new}
    if "overview" in case:
        row["domains_with_a_different_listing"] = sorted(
            domain for domain in set(old["listed_entities"]) | set(new["listed_entities"])
            if old["listed_entities"].get(domain) != new["listed_entities"].get(domain)
            or old["per_domain"].get(domain) != new["per_domain"].get(domain))
        row["most_common_domains"] = [old["most_common_domains"], new["most_common_domains"]]
    else:
        row["order_equal"] = old["order"] == new["order"]
        row["same_services"] = set(old["order"]) == set(new["order"])
        row["services"] = [len(old["order"]), len(new["order"])]
        row["total_count"] = [old["total_count"], new["total_count"]]
    return row


def domain_runs(order):
    runs = []
    for name in order:
        domain = name.split(".", 1)[0]
        if not runs or runs[-1] != domain:
            runs.append(domain)
    return runs


def catalogue(root, version, folder):
    order = []
    for page in ("o-services-page-1", "o-services-page-2"):
        order += listing(page, payload(load(root, version, folder, page)))["order"]
    runs = domain_runs(order)
    slices = {}
    for case in ("o-services-first-five", "o-services-default-page"):
        page = listing(case, payload(load(root, version, folder, case)))["order"]
        slices[case] = {"services": len(page), "equals_catalogue_head": page == order[:len(page)],
                        "page_sorted": page == sorted(page)}
    return {"services": len(order), "distinct": len(set(order)), "domain_runs": len(runs),
            "catalogue_sorted": order == sorted(order),
            "pages_sorted": [listing(page, payload(load(root, version, folder, page)))["order"]
                             == sorted(listing(page, payload(load(root, version, folder, page)))["order"])
                             for page in ("o-services-page-1", "o-services-page-2")],
            "first_pages_as_slices": slices,
            "domain_runs_sorted": runs == sorted(runs),
            "domains_in_one_run_each": len(runs) == len(set(runs)),
            "names_sorted_within_each_run": all(
                part == sorted(part) for part in
                ([name for name in order if name.split(".", 1)[0] == domain] for domain in set(runs))),
            "order_sha256": hashlib.sha256("\n".join(order).encode()).hexdigest(),
            "domain_runs_in_order": runs}


def forced(root):
    out = {}
    for version in VERSIONS:
        call = load(root, version, AFTER, "f-invalid-json")
        data = payload(call)
        result = call["exchange"]["messages"][0]["result"]
        out[version] = {"is_error": result.get("isError"), "error_code": data["error"]["code"],
                        "details": data["error"].get("details"),
                        "response_sha256": hashlib.sha256(
                            json.dumps(call["exchange"]["response"], sort_keys=True).encode()).hexdigest(),
                        "body_bytes": call["exchange"]["body_bytes"]}
    out["responses_equal"] = out["9.1.2"]["response_sha256"] == out["9.2.0"]["response_sha256"]
    out["validation_invalid_json_observed"] = any(
        out[version]["error_code"] == "VALIDATION_INVALID_JSON" for version in VERSIONS)
    return out


def payload_table(root):
    names = sorted(path.name[:-5] for path in (root / VERSIONS[0] / AFTER).iterdir()
                   if not path.name.startswith("_"))
    rows = []
    for name in names:
        old, new = (load(root, version, AFTER, name) for version in VERSIONS)
        rows.append({"id": name, "upstream_tool": old["exchange"]["request"]["json"]["params"]["name"],
                     "same_wire_arguments": old["exchange"]["request"]["json"]["params"]
                     == new["exchange"]["request"]["json"]["params"],
                     "body_bytes": [old["exchange"]["body_bytes"], new["exchange"]["body_bytes"]],
                     "payload_identical": payload(old) == payload(new),
                     "text_equals_structured_content": [text_matches_structured(old),
                                                        text_matches_structured(new)]})
    return rows


def main():
    root = Path(sys.argv[1])
    table = payload_table(root)
    out = {
        "r1_forced_case": forced(root),
        "r2_across_versions_after_core_restart": {
            case: contrast(case, *(listing(case, payload(load(root, version, AFTER, case)))
                                   for version in VERSIONS)) for case in LISTING_CASES},
        "r2_across_versions_before_core_restart": {
            case: contrast(case, *(listing(case, payload(load(root, version, BEFORE, case)))
                                   for version in VERSIONS)) for case in ORDER_CASES},
        "r2_across_core_restart_per_version": {
            f"{version}:{case}": contrast(case, listing(case, payload(load(root, version, BEFORE, case))),
                                          listing(case, payload(load(root, version, AFTER, case))))
            for version in VERSIONS for case in ORDER_CASES},
        "r2_service_catalogue": {f"{version}:{folder}": catalogue(root, version, folder)
                                 for version in VERSIONS for folder in (BEFORE, AFTER)},
        "payloads_after_core_restart": {"calls": len(table),
                                        "identical": sum(row["payload_identical"] for row in table),
                                        "rows": table},
    }
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


main()
