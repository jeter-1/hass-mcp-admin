"""Closed input and planning for a governed scene configuration plan (HAMCP-071).

Input names one exact existing UI-managed scene by its config id (optionally
cross-checked against its scene entity_id) and any non-empty subset of
``name``, ``icon`` and ``entities``.  ``entities`` is a merge patch: each
named member maps to its complete stored value (a mapping with ``state`` and
any attributes, stored exactly) or to ``null`` to remove it; members that
are not named are kept exactly.  Input is validated before any Home
Assistant read.

Planning binds the exact stored configuration and the loaded scene, refuses
integration, YAML, disabled, inconsistent and unreviewable scenes, refuses
members that exist in neither the entity registry nor the state machine, and
refuses a request that changes nothing with a truthful no-change reason and
no plan.  Nothing here dispatches and nothing here activates a scene.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..entity_ids import validate_entity_id
from ..errors import AuthorizationError, HomeAssistantApiError
from .. import untrusted_text
from . import scene_configuration as contract
from .entity_availability_exposure_planning import object_protection
from .entity_metadata import EntityMetadataProviderError
from .entity_metadata_planning import EntityMetadataRefusal
from .models import ChangeRiskAssessment, RiskLevel


ALLOWED_INPUT_FIELDS = frozenset(
    {"config_id", "entity_id", *contract.FIELDS, "expiration_minutes"}
)
INVALID_VALUE = "scene_configuration_invalid_value"
FIELD_NOT_SUPPORTED = "scene_configuration_field_not_supported"
NO_CHANGE = "scene_configuration_no_change"
# The closed write contract re-sends every stored field; a stored value it
# cannot send (a blank name) could never be written or restored.
BASELINE_NOT_SENDABLE = "scene_configuration_baseline_not_sendable"
BASELINE_NOT_RESTORABLE = "scene_configuration_baseline_not_restorable"
UNKNOWN_ENTITIES = "scene_configuration_unknown_entities"
POLICY_TRIGGER = "scene_configuration_policy"
DISCLOSURE_MODEL = "scene_configuration_disclosure_v1"
OPERATION_ID = "scene_configuration_1"
# Classification needs the device class only where object_protection uses it.
DEVICE_CLASS_DOMAINS = frozenset({"cover", "binary_sensor"})
# HAMCP-071 R1-H3 (Josh, D1): a switch's purpose (a siren, a lock, a safety
# device's power) is not knowable from Home Assistant, and an unresolved
# purpose never implies harmlessness, so every switch member before or after
# the edit is protected in addition to object_protection.
SWITCH_DOMAIN = "switch"
SWITCH_REASON = "switch_domain"
# HAMCP-071 D4 (Josh): mode helpers gate security automations (a camera
# alert that requires the away, guest and cleaner modes off), so a scene
# that sets one can silently change how automations behave.  Every
# input_boolean or input_select member before or after the edit elevates;
# other input_* helpers are unchanged.
MODE_HELPER_DOMAINS = frozenset({"input_boolean", "input_select"})
MODE_HELPER_REASON = "mode_helper_domain"
NOT_ACTIVATED_DISCLOSURE = (
    "Saving this scene does not activate it: no device changes state when "
    "the plan is applied. The stored states take effect only when the scene "
    "is activated later (by a person, an automation or a script)."
)
RELOAD_DISCLOSURE = (
    "Home Assistant reloads every UI and YAML scene after the save. Scenes "
    "created at runtime with scene.create are removed by that reload."
)
ID_ASSIGNMENT_DISCLOSURE = (
    "Home Assistant rewrites scenes.yaml on save; any stored scene entry "
    "without an id is given a new random id."
)
CONCURRENCY_DISCLOSURE = (
    "Core has no compare-and-swap for scene configuration, and its save is "
    "an upsert. The stored configuration, registry entry and loaded scene "
    "are rechecked immediately before the one save, but between that final "
    "check and the save: a concurrent edit to this scene can be overwritten "
    "without being detected (the readback sees only this plan's result), "
    "and a concurrent deletion can make the save recreate the scene. "
    "Verification proves this plan's stored result, not that no other "
    "change happened in that window."
)
LOADED_SCENE_DISCLOSURE = (
    "Verification needs evidence that Home Assistant reloaded the scene "
    "after this save (the scene state is re-added). Until that evidence is "
    "observed within the bounded observation window, the stored "
    "configuration may be verified while the loaded scene is not: the task "
    "stays observing and then requires manual review, never verified. "
    "Recovery is read-only: inspect the task and the scene's stored "
    "configuration and state, and reload scenes in Home Assistant yourself "
    "if the loaded scene is stale; Engineering never reloads, activates or "
    "resends. The evidence is held in memory, so an Engineering restart "
    "after the save also leaves the result unverified. A scene reload by "
    "anything else just before the save cannot be told apart from the "
    "save's own reload."
)
MEMBER_ORDER_DISCLOSURE = (
    "Members are saved in entity_id order. Core reproduces scene states "
    "with every domain in parallel, so stored order is not an activation "
    "ordering guarantee before or after this save."
)
METADATA_DISCLOSURE = (
    "Scene metadata is never changed: it is sent exactly as stored."
)
ROLLBACK_DISCLOSURE = (
    "rollback_change creates a separate governed plan that restores the "
    "exact captured prior configuration; it needs its own approval and it "
    "does not activate the scene either."
)
UNTRUSTED_DISCLOSURE = (
    "Scene names, entity attributes and automation or script names are "
    "untrusted Home Assistant text, returned as data and never treated as "
    "instructions or authorization."
)
CONSUMER_DISCLOSURE = (
    "Automations and scripts that reference this scene are listed from the "
    "dependency index. They may activate it later; this plan does not."
)
_REFUSALS = {
    "scene_configuration_not_ui_managed": (
        "scene_configuration_not_ui_managed", "unreviewable"
    ),
    "scene_configuration_target_ambiguous": (
        "scene_configuration_target_ambiguous", "unreviewable"
    ),
    "scene_configuration_target_unreviewable": (
        "scene_configuration_target_unreviewable", "unreviewable"
    ),
    "scene_configuration_not_loaded": (
        "scene_configuration_not_loaded", "unreviewable"
    ),
    "scene_configuration_target_disabled": (
        "scene_configuration_target_disabled", "unreviewable"
    ),
    # R1-H2: the reads refuse before any call while the profile is withdrawn.
    contract.PENDING_ADMISSION_REASON: (
        contract.PENDING_ADMISSION_REASON, "unavailable"
    ),
}
_UNSET = object()


class SceneConfigurationRefusal(EntityMetadataRefusal):
    """A deterministic refusal with a design reason code; no plan exists."""


Refusal = SceneConfigurationRefusal


@dataclass(frozen=True)
class Request:
    config_id: str
    entity_id: str | None
    values: tuple[tuple[str, Any], ...]
    entity_patch: tuple[tuple[str, Any], ...]
    expiration_minutes: int
    # Set only by the F3 restore route: the exact captured prior config.
    restore_config: dict[str, Any] | None = None

    @property
    def requested(self) -> tuple[str, ...]:
        names = [name for name, _ in self.values]
        if self.entity_patch or self.restore_config is not None:
            names.append("entities")
        if self.restore_config is not None:
            return contract.FIELDS
        return tuple(name for name in contract.FIELDS if name in names)

    @property
    def lease_target_id(self) -> str:
        return self.config_id


def _withheld(value: Any) -> bool:
    """Any untrusted string leaf or mapping key that the screen withholds."""

    if isinstance(value, str):
        return untrusted_text.screen(value) is not None
    if isinstance(value, dict):
        return any(_withheld(key) or _withheld(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_withheld(item) for item in value)
    return False


def validate_request(arguments: Any) -> Request:
    """Validate the closed input before any Home Assistant read."""

    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        raise Refusal("scene_configuration_invalid_request")
    unsupported = sorted(set(arguments) - ALLOWED_INPUT_FIELDS)
    if unsupported:
        raise Refusal(FIELD_NOT_SUPPORTED, fields=unsupported[:16])
    config_id = arguments.get("config_id")
    if not contract.config_id_valid(config_id):
        raise Refusal(INVALID_VALUE, field="config_id")
    entity_id = arguments.get("entity_id")
    if entity_id is not None:
        try:
            validate_entity_id(entity_id)
            if not entity_id.startswith("scene."):
                raise ValueError
        except ValueError:
            raise Refusal(INVALID_VALUE, field="entity_id") from None
    values: list[tuple[str, Any]] = []
    if "name" in arguments:
        if not contract.requested_name_valid(arguments["name"]):
            raise Refusal(INVALID_VALUE, field="name")
        if _withheld(arguments["name"]):
            raise Refusal("scene_configuration_text_withheld", field="name")
        values.append(("name", arguments["name"]))
    if "icon" in arguments:
        if not contract.requested_icon_valid(arguments["icon"]):
            raise Refusal(INVALID_VALUE, field="icon")
        values.append(("icon", arguments["icon"]))
    patch: list[tuple[str, Any]] = []
    if "entities" in arguments:
        entities = arguments["entities"]
        if (
            type(entities) is not dict
            or not entities
            or len(entities) > contract.MAX_ENTITIES
        ):
            raise Refusal(INVALID_VALUE, field="entities")
        for member, value in entities.items():
            try:
                validate_entity_id(member)
            except ValueError:
                raise Refusal(INVALID_VALUE, field="entities") from None
            if member.startswith("scene."):
                # A scene member would make saving one scene depend on another.
                raise Refusal(INVALID_VALUE, field="entities")
            if value is not None and not contract.snapshot_valid(
                value, requested=True
            ):
                raise Refusal(INVALID_VALUE, field="entities")
            if _withheld(value):
                raise Refusal("scene_configuration_text_withheld", field="entities")
            patch.append((member, deepcopy(value)))
    if not values and not patch:
        raise Refusal("scene_configuration_no_fields_requested")
    expiration = arguments.get("expiration_minutes", 120)
    if type(expiration) is not int or not 5 <= expiration <= 1440:
        raise Refusal(INVALID_VALUE, field="expiration_minutes")
    return Request(config_id, entity_id, tuple(values), tuple(patch), expiration)


def restore_request(config_id: str, prior: dict[str, Any]) -> Request:
    """The F3 restore request: the exact captured prior configuration."""

    if contract.state_errors(config_id, prior):
        raise Refusal("prior_state_missing")
    return Request(
        config_id,
        prior["entity_id"],
        (),
        (),
        120,
        restore_config=deepcopy(prior["config"]),
    )


@dataclass
class Item:
    """The one prepared child: exact current state, final state, evidence."""

    operation_id: str
    config_id: str
    current: dict[str, Any]
    proposed: dict[str, Any]
    changed: tuple[str, ...]
    disclosure: dict[str, Any]
    classification: dict[str, Any]


@dataclass
class Preparation:
    request: Request
    items: list[Item]
    plan_disclosure: dict[str, Any] = field(default_factory=dict)

    def item(self, operation_id: str) -> Item:
        for candidate in self.items:
            if candidate.operation_id == operation_id:
                return candidate
        raise KeyError(operation_id)


def id_protection_reasons(entity_ids: Any) -> list[str]:
    """The reasons derivable from member entity_ids alone.

    The policy recompute uses this floor; the planner's basis must contain it.
    """

    reasons: set[str] = set()
    for entity_id in entity_ids:
        reasons.update(
            reason
            for reason in object_protection({"entity_id": entity_id})
            # A classless cover is decided by the planner's device-class read.
            if reason != "cover_device_class_unresolved"
        )
        reasons.update(_member_rule_reasons(entity_id))
    return sorted(reasons)


def _domain(entity_id: Any) -> str | None:
    return entity_id.split(".", 1)[0] if isinstance(entity_id, str) else None


def _is_switch(entity_id: Any) -> bool:
    return _domain(entity_id) == SWITCH_DOMAIN


def _is_mode_helper(entity_id: Any) -> bool:
    return _domain(entity_id) in MODE_HELPER_DOMAINS


def _member_rule_reasons(entity_id: Any) -> set[str]:
    """The scene-member rules (D1, D4) on top of object_protection."""

    reasons: set[str] = set()
    if _is_switch(entity_id):
        reasons.add(SWITCH_REASON)
    if _is_mode_helper(entity_id):
        reasons.add(MODE_HELPER_REASON)
    return reasons


def switch_members(entity_ids: Any) -> list[str]:
    """The switch members before and after the edit, for the disclosure."""

    return sorted({item for item in entity_ids if _is_switch(item)})


def mode_helper_members(entity_ids: Any) -> list[str]:
    """The input_boolean/input_select members before and after the edit."""

    return sorted({item for item in entity_ids if _is_mode_helper(item)})


async def protection_reasons(gateway: Any, entity_ids: list[str]) -> list[str]:
    """OD-4 object protection over every member before and after the edit."""

    reasons: set[str] = set()
    for entity_id in sorted(set(entity_ids)):
        entry: dict[str, Any] = {"entity_id": entity_id}
        if entity_id.split(".", 1)[0] in DEVICE_CLASS_DOMAINS:
            try:
                entry["device_class"] = await gateway.device_class(entity_id)
            except Exception:
                # Unresolved evidence is protected, never assumed harmless.
                reasons.add("device_class_unresolved")
        reasons.update(object_protection(entry))
        reasons.update(_member_rule_reasons(entity_id))
    return sorted(reasons)


def classify(
    reasons: list[str],
    switches: tuple[str, ...] | list[str] = (),
    mode_helpers: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    if reasons:
        statements = [
            "This scene stores states for protected devices; activating "
            "it later can change them. Saving it does not."
        ]
        if switches:
            statements.append(
                "Elevated because the scene has switch members before or "
                "after this edit; a switch's purpose cannot be resolved from "
                "Home Assistant, so it is treated as protected: "
                + ", ".join(switches)
                + "."
            )
        if mode_helpers:
            statements.append(
                "Elevated because the scene has input_boolean or "
                "input_select members before or after this edit; "
                "mode/helper members can change how automations behave: "
                + ", ".join(mode_helpers)
                + "."
            )
        return {
            "policy": "elevated",
            "physical_consequence": "indirect",
            "basis": list(reasons),
            "statements": statements,
            "warnings": [],
        }
    return {
        "policy": "standard",
        "physical_consequence": "none",
        "basis": [],
        "statements": [],
        "warnings": [],
    }


def risk_assessment(
    operation_id: str, classification: dict[str, Any]
) -> ChangeRiskAssessment:
    """The risk record and the one policy marker for the scene child."""

    elevated = classification["policy"] == "elevated"
    return ChangeRiskAssessment(
        level=RiskLevel.HIGH if elevated else RiskLevel.LOW,
        reasons=list(classification["statements"]) or ["Scene configuration edit"],
        apply_allowed=True,
        evidence=[
            {
                "field": operation_id,
                "trigger": POLICY_TRIGGER,
                "policy": classification["policy"],
                "physical_consequence": classification["physical_consequence"],
                "basis": ",".join(classification["basis"]),
            }
        ],
        warnings=list(classification["warnings"]),
    )


async def read_state(gateway: Any, config_id: str) -> dict[str, Any] | None:
    """Read one exact state, mapping every failure to a precise refusal."""

    try:
        return await gateway.read_state(config_id)
    except AuthorizationError:
        raise Refusal(
            "scene_configuration_permission_failure", kind="permission"
        ) from None
    except contract.SceneConfigurationContractError as exc:
        reason, kind = _REFUSALS.get(
            exc.category, ("scene_configuration_target_unreviewable", "unreviewable")
        )
        raise Refusal(reason, kind=kind) from None
    except (EntityMetadataProviderError, HomeAssistantApiError, ValueError):
        raise Refusal(
            "scene_configuration_provider_unavailable", kind="unavailable"
        ) from None


def proposed_config(current: dict[str, Any], request: Request) -> dict[str, Any]:
    """The full proposed configuration; unrequested keys are kept exactly."""

    if request.restore_config is not None:
        return deepcopy(request.restore_config)
    config = deepcopy(current["config"])
    values = dict(request.values)
    if "name" in values:
        config["name"] = values["name"]
    if "icon" in values:
        if values["icon"] is None:
            config.pop("icon", None)
        else:
            config["icon"] = values["icon"]
    entities = config["entities"]
    for member, value in request.entity_patch:
        if value is None:
            if member not in entities:
                raise Refusal(
                    "scene_configuration_entity_not_in_scene", field="entities"
                )
            del entities[member]
        else:
            # Member order is not bound: plans store canonical JSON, so the
            # POST sends members in sorted entity_id order.
            entities[member] = deepcopy(value)
    if not entities:
        raise Refusal("scene_configuration_entities_empty", field="entities")
    return config


def field_rows(
    current: dict[str, Any], proposed: dict[str, Any], requested: tuple[str, ...]
) -> list[dict[str, Any]]:
    changed = set(contract.changed_fields(current, proposed))
    return [
        {
            "field": name,
            "before": current["config"].get(name),
            "after": proposed["config"].get(name),
            "requested": name in requested,
            "changed": name in changed,
        }
        for name in ("name", "icon")
    ]


def entity_rows(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    """Per-member before/after for every changed member, and the unchanged count."""

    rows: list[dict[str, Any]] = []
    unchanged = 0
    for member in [*before, *(item for item in after if item not in before)]:
        if member in before and member in after:
            if contract.exactly_equal(before[member], after[member]):
                unchanged += 1
                continue
            change = "changed"
        else:
            change = "removed" if member in before else "added"
        rows.append(
            {
                "entity_id": member,
                "change": change,
                "before": deepcopy(before.get(member)),
                "after": deepcopy(after.get(member)),
            }
        )
    return rows, unchanged


async def consumer_disclosure(gateway: Any, entity_id: str) -> dict[str, Any]:
    """Automations and scripts referencing the scene; gaps are disclosed."""

    try:
        evidence = await gateway.consumer_evidence(entity_id)
    except Exception:
        evidence = None
    if not isinstance(evidence, dict):
        return {
            "status": "unavailable",
            "consumers": [],
            "consumer_count": None,
            "evidence_complete": False,
            "evidence_gaps": ["dependency_index_unavailable"],
            "statement": CONSUMER_DISCLOSURE,
        }
    screened, withheld = untrusted_text.screen_tree(deepcopy(evidence))
    return {
        **screened,
        "withheld_text_count": withheld,
        "statement": CONSUMER_DISCLOSURE,
    }


async def prepare(
    gateway: Any, request: Request, *, restore: bool = False
) -> Preparation:
    """Resolve the exact target and bind its state; nothing is dispatched."""

    if restore != (request.restore_config is not None):
        raise Refusal("scene_configuration_invalid_request")
    current = await read_state(gateway, request.config_id)
    if current is None:
        raise Refusal("scene_configuration_target_not_found", kind="not_found")
    if request.entity_id is not None and current["entity_id"] != request.entity_id:
        raise Refusal("scene_configuration_target_mismatch", field="entity_id")
    if current["loaded_matches_config"] is not True:
        # The loaded scene does not reflect the stored configuration (a
        # reload is pending or failed): the target cannot be bound exactly.
        raise Refusal("scene_configuration_not_loaded", kind="unreviewable")
    if _withheld(current["config"]):
        raise Refusal("scene_configuration_text_withheld", field="config")
    config = proposed_config(current, request)
    proposed = {**deepcopy(current), "config": config, "loaded_matches_config": True}
    if contract.exactly_equal(current["config"], config):
        raise Refusal(
            NO_CHANGE,
            already_satisfied=True,
            fields=list(request.requested),
        )
    if contract.proposed_errors(request.config_id, proposed):
        raise Refusal(INVALID_VALUE, field="proposed_state")
    try:
        command = contract.write_command(request.config_id, current, proposed)
    except contract.SceneConfigurationContractError:
        raise Refusal(INVALID_VALUE, field="proposed_state") from None
    if contract.command_errors(command):
        # An unrequested stored value the closed POST cannot send.
        raise Refusal(BASELINE_NOT_SENDABLE, field="config")
    if not restore:
        reverse = contract.write_command(request.config_id, proposed, current)
        if contract.command_errors(reverse):
            # Refused before any plan exists: the restore could never be sent.
            raise Refusal(BASELINE_NOT_RESTORABLE, field="config")
        requested_members = [
            member for member, value in request.entity_patch if value is not None
        ]
        try:
            unknown = await gateway.unknown_entities(requested_members)
        except AuthorizationError:
            raise Refusal(
                "scene_configuration_permission_failure", kind="permission"
            ) from None
        except Exception:
            raise Refusal(
                "scene_configuration_provider_unavailable", kind="unavailable"
            ) from None
        if unknown:
            raise Refusal(UNKNOWN_ENTITIES, fields=sorted(unknown)[:16])
    changed = contract.changed_fields(current, proposed)
    members = [*current["config"]["entities"], *config["entities"]]
    reasons = await protection_reasons(gateway, members)
    switches = switch_members(members)
    mode_helpers = mode_helper_members(members)
    classification = classify(reasons, switches, mode_helpers)
    rows, unchanged = entity_rows(current["config"]["entities"], config["entities"])
    disclosure = {
        "model": DISCLOSURE_MODEL,
        "config_id": request.config_id,
        "entity_id": current["entity_id"],
        "registry_entry_id": current["registry_entry_id"],
        "fields": field_rows(current, proposed, request.requested),
        "entities": rows,
        "unchanged_entity_count": unchanged,
        "entity_count_before": len(current["config"]["entities"]),
        "entity_count_after": len(config["entities"]),
        "requested_fields": list(request.requested),
        "changed_fields": list(changed),
        "unrequested_fields": "sent_exactly_as_stored_and_verified_unchanged",
        "metadata": METADATA_DISCLOSURE,
        "member_order": MEMBER_ORDER_DISCLOSURE,
        "configuration_fingerprint_bound": True,
        "activation": NOT_ACTIVATED_DISCLOSURE,
        "activated": False,
        "reload": RELOAD_DISCLOSURE,
        "id_assignment": ID_ASSIGNMENT_DISCLOSURE,
        "consumers": await consumer_disclosure(gateway, current["entity_id"]),
        "consequence_statements": list(classification["statements"]),
        "switch_members": switches,
        "mode_helper_members": mode_helpers,
        "untrusted_text": UNTRUSTED_DISCLOSURE,
        "provider": contract.PROVIDER,
        "fallback": "none",
    }
    item = Item(
        operation_id=OPERATION_ID,
        config_id=request.config_id,
        current=current,
        proposed=proposed,
        changed=changed,
        disclosure=disclosure,
        classification=classification,
    )
    return Preparation(
        request=request,
        items=[item],
        plan_disclosure={
            "model": DISCLOSURE_MODEL,
            "target_count": 1,
            "execution": "single_update_at_most_once",
            "provider": contract.PROVIDER,
            "fallback": "none",
            "activation": NOT_ACTIVATED_DISCLOSURE,
            "never_sent": list(contract.NEVER_SENT),
            "reload": RELOAD_DISCLOSURE,
            "rollback": (
                "this_plan_restores_the_captured_configuration"
                if restore
                else ROLLBACK_DISCLOSURE
            ),
            "residual_concurrency_window": CONCURRENCY_DISCLOSURE,
            "loaded_scene_verification": LOADED_SCENE_DISCLOSURE,
        },
    )


def projection(item: Item) -> dict[str, Any]:
    """The bounded per-child plan projection."""

    return {
        **item.disclosure,
        "policy_basis": {
            "policy": item.classification["policy"],
            "physical_consequence": item.classification["physical_consequence"],
            "basis": list(item.classification["basis"]),
        },
    }


__all__ = [
    "ALLOWED_INPUT_FIELDS",
    "BASELINE_NOT_RESTORABLE",
    "BASELINE_NOT_SENDABLE",
    "FIELD_NOT_SUPPORTED",
    "INVALID_VALUE",
    "Item",
    "MODE_HELPER_REASON",
    "NO_CHANGE",
    "OPERATION_ID",
    "POLICY_TRIGGER",
    "Preparation",
    "Refusal",
    "Request",
    "SWITCH_REASON",
    "SceneConfigurationRefusal",
    "UNKNOWN_ENTITIES",
    "classify",
    "id_protection_reasons",
    "mode_helper_members",
    "prepare",
    "projection",
    "restore_request",
    "risk_assessment",
    "switch_members",
    "validate_request",
]
