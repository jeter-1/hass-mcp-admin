"""Bounded mixed-source collection and pure native-inventory projection."""

from ..sanitization import sanitize_untrusted_data
from . import contracts as c
from .models import Inventory


def safe_entity(value, known_secrets=()):
    if type(value) is not str or len(value) > 256 or not c.ENTITY.fullmatch(value):
        return None
    cleaned = sanitize_untrusted_data(value, known_secrets=known_secrets, max_string=256)
    return value if cleaned.value == value and not cleaned.failed_closed else None


def project_inventory(kind, value, *, known_secrets=()):
    """Retain valid positives while incomplete identity evidence forbids absence."""
    if kind not in {"states", "registry"}:
        raise c.AnalysisError("invalid_arguments")
    if type(value) is not list:
        return Inventory(kind, (), False, 0, 0, 0, 0, "malformed_response")
    records, seen = {}, set()
    invalid = duplicate = 0
    examined = min(len(value), c.INVENTORY_ENTRIES)
    for row in value[:examined]:
        entity = safe_entity(row.get("entity_id"), known_secrets) if type(row) is dict else None
        if not entity:
            invalid += 1
            continue
        if entity in seen:
            duplicate += 1
            records.pop(entity, None)
            continue
        seen.add(entity)
        if kind == "states":
            state = row.get("state")
            if type(state) is not str:
                invalid += 1
                continue
            category = ("unavailable" if state == "unavailable" else
                        "state_unknown" if state == "unknown" else "present")
        else:
            if "disabled_by" not in row or (row["disabled_by"] is not None
                                             and type(row["disabled_by"]) is not str):
                invalid += 1
                continue
            category = "registry_only" if row["disabled_by"] is None else "registry_disabled"
        records[entity] = category
    omitted = len(value) - len(records)
    complete = not invalid and not duplicate and examined == len(value)
    return Inventory(kind, tuple(sorted(records.items())), complete, examined, omitted,
                     invalid, duplicate)


def failed_inventory(kind, reason):
    if kind not in {"states", "registry"}:
        raise c.AnalysisError("invalid_arguments")
    failure = c.AnalysisError(reason)
    if failure.reason in {"access_denied", "authority_unavailable", "authority_drift",
                          "identity_mismatch", "hash_mismatch", "source_rejected"}:
        raise failure
    return Inventory(kind, (), False, 0, 0, 0, 0, failure.reason)


def availability(entity, states, registry):
    if states.kind != "states" or registry.kind != "registry":
        raise c.AnalysisError("identity_mismatch")
    state_map, registry_map = dict(states.records), dict(registry.records)
    if entity in state_map:
        return state_map[entity]
    if entity in registry_map:
        return registry_map[entity]
    if states.complete and registry.complete:
        return "absent_from_observed_inventories"
    return "unassessed"


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _freeze(path, dashboard, inventories, sources, authority, upstream_metadata,
            started, finished, transport, known_secrets):
    from .rules import scan, FRONTEND_COMMIT, CORE_VERSION
    from .models import FrozenReport

    result = scan(dashboard["configuration"], dashboard["engineering_config_hash"],
                  inventories["states"], inventories["registry"],
                  core_version=authority[1], known_secrets=known_secrets)
    header = {"model": c.MODEL, "requested_path": path, "canonical_path": path,
              "core_version": authority[1], "frontend_commit": FRONTEND_COMMIT,
              "default_rules_applicable": authority[1] == CORE_VERSION,
              "config_hash": dashboard["config_hash"],
              "engineering_config_hash": dashboard["engineering_config_hash"],
              "projection_hash": result["projection_hash"], "coverage": result["coverage"],
              "counts": result["counts"], "sources": sources,
              "collection_started_at": started, "collection_finished_at": finished,
              "non_atomic": True, "transport": transport,
              "authority": {"core_generation": authority[0],
                            "core_authority_hash": c.digest(authority[:3]), **upstream_metadata}}
    return FrozenReport(c.canonical(header), tuple(c.canonical(x) for x in result["items"]),
                        authority, "")


class DashboardAnalysisProvider:
    """Three logical reads with a conjunctive authority check at every boundary."""

    def __init__(self, client, core_runtime, upstream, *, known_secrets=()):
        self.client, self.core_runtime, self.upstream = client, core_runtime, upstream
        self.known_secrets = tuple(known_secrets)

    def authority(self):
        from ..request_context import current_telemetry

        status = self.core_runtime.route_status(c.REQUIREMENTS)
        observation = self.core_runtime.current_observation
        telemetry = current_telemetry()
        if (status.get("available") is not True or observation is None or telemetry is None
                or telemetry.core_dispatch_authorizer is None or not telemetry.authorize_core_dispatch()):
            raise c.AnalysisError("authority_unavailable")
        # Observation fingerprint and generation bind current signed profiles;
        # upstream identity additionally binds the configured transport instance.
        return (status["generation"], observation.version, observation.fingerprint,
                self.upstream.analysis_authority())

    async def collect(self, path):
        from ..clients.dashboard_analysis import CollectionBudget

        authority = self.authority()
        budget, started, sources, inventories = CollectionBudget(), _now(), [], {}

        def authorize():
            if self.authority() != authority:
                raise c.AnalysisError("authority_drift")

        before = _now()
        dashboard, upstream_metadata = await self.upstream.get_analysis_configuration(
            url_path=path, authorize=authorize, budget=budget)
        sources.append({"kind": "dashboard", "provider": "upstream_dashboard", "complete": True,
                        "failure": None, "started_at": before, "finished_at": _now(), "sanitized": True,
                        "examined": 1, "retained": 1, "omitted": 0, "invalid": 0, "duplicate": 0})
        authorize()
        async with self.client.collection(authority[1], authorize, budget) as peer:
            for kind in ("states", "registry"):
                authorize()
                before = _now()
                try:
                    raw = await peer.read(kind)
                    authorize()
                    inventory = await c.worker(project_inventory, kind, raw, known_secrets=self.known_secrets)
                    del raw
                except c.AnalysisError as error:
                    inventory = failed_inventory(kind, error.reason)
                authorize()
                inventories[kind] = inventory
                sources.append({**inventory.metadata(), "started_at": before,
                                "finished_at": _now(), "sanitized": True})
        authorize()
        report = await c.worker(_freeze, path, dashboard, inventories, sources, authority,
            upstream_metadata, started, _now(), {"requests": budget.requests, "bytes": budget.bytes,
                                               "logical_reads": 3, "retries": 0}, self.known_secrets)
        del dashboard
        authorize()
        return report
