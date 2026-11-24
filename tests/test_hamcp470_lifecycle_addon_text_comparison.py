"""HAMCP-470: the lifecycle add-on text comparison under upstream redaction.

ha-mcp 9.2.0 with ``redact_secrets`` on scrubs the add-on read's text content
as a raw string and its structuredContent as an object (``src/ha_mcp/
redaction.py``: ``scrub_text`` and ``scrub_obj``). The two can then differ in
form: the text may carry duplicate members or stop parsing, while the
structured object renames colliding keys ``<key>#N``. A large detail (a
9.2.0 add-on with its translations) can also exceed the old fixed bound.

These tests pin the narrow hotfix: compare only a bounded, cleanly parsed
text; otherwise decide from structuredContent and record the comparison as
skipped, never as verified equal; keep every structuredContent check; and
refuse an unreadable upstream add-on identity before F3 dispatch as
``operational_contract_mismatch``.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))
if str(ROOT / "tests") not in sys.path:
    sys.path.append(str(ROOT / "tests"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    MAX_GENERIC_UPSTREAM_RESPONSE_BYTES,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from ha_mcp_engineering.governance.operational_lifecycle import (  # noqa: E402
    LifecycleGatewayError,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.providers import operational_lifecycle as lifecycle  # noqa: E402
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    MAX_LIFECYCLE_ADDON_STRUCTURED_TEXT_BYTES,
    OperationalLifecycleProviderError,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    Clock,
    LegacyGateway,
    UPSTREAM_ADDON_SLUG,
)
from tests.test_beta15_live_lifecycle_addon_response import (  # noqa: E402
    DetailTransport,
    call_result,
    configured_provider,
)
from tests import test_f3_runtime_integration as f3_fixtures  # noqa: E402


LOCALES = ("de", "en", "es", "fr", "it", "ru", "zh-Hans")
# Synthetic, unmistakable values; none is a real credential.
SYNTHETIC_SECRET = "hamcp470-synthetic-secret-value"
SYNTHETIC_NUMERIC_SECRET = "470470470"
SYNTHETIC_SENTINEL = "hamcp470-value-sentinel"
REDACTED = "<redacted>"
TEXT_READS = {"inventory", "detail"}
TEXT_COMPARISONS = {"verified_equal", "skipped", "mismatch", "not_reached"}
TEXT_BUCKETS = {"<64K", "64K-128K", "128K-256K", "256K-512K", "512K-1M"}
TEXT_FAILURE_CLASSES = {
    "oversize",
    "invalid_json",
    "duplicate_member",
    "nonfinite",
    "recursion",
    "unicode",
    "none",
}


def sized_detail_payload(target_bytes: int) -> dict:
    """Return a secret-free 9.2.0-shaped detail of exactly ``target_bytes``.

    Seven translation documents carry the bulk, like a 9.2.0 add-on detail
    (9.2.0 translations measured 228,284 bytes; the dev build 370,011). The
    filler is synthetic and spread across every locale.
    """

    addon = {
        "slug": UPSTREAM_ADDON_SLUG,
        "name": "Home Assistant MCP Server",
        "version": "9.2.0",
        "state": "started",
        "repository": "abcdef12",
        "update_available": False,
        "translations": {
            locale: {"configuration": {"synthetic_contract_text": ""}}
            for locale in LOCALES
        },
    }
    payload = {"success": True, "addon": addon}

    def size() -> int:
        return len(_compact(payload).encode("utf-8"))

    padding = target_bytes - size()
    if padding <= 0:
        raise AssertionError("sized detail baseline exceeded target")
    share, remainder = divmod(padding, len(LOCALES))
    for index, locale in enumerate(LOCALES):
        addon["translations"][locale]["configuration"][
            "synthetic_contract_text"
        ] = "t" * (share + (1 if index < remainder else 0))
    assert size() == target_bytes
    return payload


def _compact(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def scrubbed_result(payload: dict, secrets: tuple[str, ...]) -> dict:
    """Model ha-mcp 9.2.0 ``RedactSecretsMiddleware`` on one reply.

    The text block is scrubbed by raw substring replacement (``scrub_text``)
    and structuredContent by ``scrub_obj``: string values and keys are
    scrubbed, and a later colliding key is renamed ``<key>#N`` (N >= 2).
    Numbers are never scrubbed in the object.
    """

    ordered = tuple(sorted(secrets, key=len, reverse=True))

    def scrub_text(text: str) -> str:
        for secret in ordered:
            text = text.replace(secret, REDACTED)
        return text

    def scrub_obj(value):
        if isinstance(value, str):
            return scrub_text(value)
        if isinstance(value, dict):
            out: dict = {}
            for key, item in value.items():
                new_key = scrub_text(key)
                if new_key in out:
                    suffix = 2
                    while f"{new_key}#{suffix}" in out:
                        suffix += 1
                    new_key = f"{new_key}#{suffix}"
                out[new_key] = scrub_obj(item)
            return out
        if isinstance(value, list):
            return [scrub_obj(item) for item in value]
        return value

    return {
        "content": [{"type": "text", "text": scrub_text(_compact(payload))}],
        "structuredContent": scrub_obj(deepcopy(payload)),
        "isError": False,
    }


def text_entries(provider) -> dict[str, dict[str, str]]:
    entries = provider.health_snapshot()["lifecycle_addon_text_comparison"]
    assert entries is not None
    return {entry["read"]: entry for entry in entries}


class TextComparisonBoundTests(unittest.TestCase):
    def test_bound_is_derived_from_the_exchange_bound(self):
        self.assertEqual(MAX_GENERIC_UPSTREAM_RESPONSE_BYTES, 1_000_000)
        self.assertEqual(
            MAX_LIFECYCLE_ADDON_STRUCTURED_TEXT_BYTES,
            MAX_GENERIC_UPSTREAM_RESPONSE_BYTES // 3,
        )

    def test_an_equal_pair_at_the_bound_fits_one_exchange(self):
        # The exchange carries the text escaped once and the decoded object
        # once, serialized as upstream_read measures it.
        payload = sized_detail_payload(
            MAX_LIFECYCLE_ADDON_STRUCTURED_TEXT_BYTES
        )
        encoded = json.dumps(
            call_result(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), MAX_GENERIC_UPSTREAM_RESPONSE_BYTES)

    def test_parse_classes_are_exact_and_bounded(self):
        parse = lifecycle._parse_lifecycle_addon_text
        bound = MAX_LIFECYCLE_ADDON_STRUCTURED_TEXT_BYTES
        at_bound = '"' + "a" * (bound - 2) + '"'
        over_bound = '"' + "a" * (bound - 1) + '"'
        cases = {
            "none": ('{"success":true}', "none"),
            "at_bound": (at_bound, "none"),
            "over_bound": (over_bound, "oversize"),
            "invalid_json": ('{"success":tru', "invalid_json"),
            "redaction_broken_number": (
                '{"port":<redacted>}',
                "invalid_json",
            ),
            "duplicate_member": (
                '{"<redacted>":1,"<redacted>":2}',
                "duplicate_member",
            ),
            "nonfinite": ('{"value":NaN}', "nonfinite"),
            "infinity": ('{"value":-Infinity}', "nonfinite"),
            "exponent_overflow": ('{"value":1e999}', "nonfinite"),
            "negative_exponent_overflow": (
                '{"a":[{"value":-1e999}]}',
                "nonfinite",
            ),
            "exponent_underflow_is_finite": ('{"value":1e-999}', "none"),
            "finite_float": ('{"value":-1.5e3}', "none"),
            "recursion": ("[" * 100_000 + "]" * 100_000, "recursion"),
            "lone_surrogate": ('{"a":"\ud800"}', "unicode"),
        }
        for name, (text, expected) in cases.items():
            with self.subTest(name=name):
                parsed, failure_class = parse(text)
                self.assertEqual(failure_class, expected)
                if expected == "none":
                    self.assertEqual(parsed, json.loads(text))
                else:
                    self.assertIsNone(parsed)

    def test_bytes_buckets_are_fixed_and_exhaustive(self):
        bucket = lifecycle._lifecycle_text_bytes_bucket
        cases = {
            0: "<64K",
            65_535: "<64K",
            65_536: "64K-128K",
            131_071: "64K-128K",
            131_072: "128K-256K",
            262_143: "128K-256K",
            262_144: "256K-512K",
            524_287: "256K-512K",
            524_288: "512K-1M",
            999_999: "512K-1M",
        }
        for size, expected in cases.items():
            with self.subTest(size=size):
                self.assertEqual(bucket("a" * size), expected)
        # Multi-byte text is measured in UTF-8 bytes, not characters.
        self.assertEqual(bucket("é" * 32_768), "64K-128K")
        # A lone surrogate still gets a bucket rather than an exception.
        self.assertEqual(bucket("\ud800"), "<64K")
        self.assertEqual(
            lifecycle._LIFECYCLE_TEXT_BUCKET_LABELS, frozenset(TEXT_BUCKETS)
        )

    def test_renamed_key_matcher_is_exact(self):
        renamed = lifecycle._is_redaction_renamed_key
        fields = lifecycle._ADDON_IDENTITY_FIELDS
        self.assertTrue(renamed("slug#2", fields))
        self.assertTrue(renamed("name#17", fields))
        self.assertFalse(renamed("slug", fields))
        self.assertFalse(renamed("<redacted>#2", fields))
        self.assertFalse(renamed("translations#2", fields))
        self.assertFalse(renamed("slug#", fields))
        self.assertFalse(renamed("slug#x", fields))
        self.assertFalse(renamed(2, fields))

    def test_unreviewed_diagnostic_values_are_refused(self):
        provider = lifecycle.ReviewedOperationalLifecycleProvider()
        valid = {
            "read": "detail",
            "text_comparison": "skipped",
            "text_bytes_bucket": "<64K",
            "text_failure_class": "oversize",
        }
        for field, value in (
            ("read", "options"),
            ("text_comparison", "equal"),
            ("text_bytes_bucket", "12345"),
            ("text_failure_class", SYNTHETIC_SENTINEL),
        ):
            with self.subTest(field=field):
                with self.assertRaises(AssertionError):
                    provider._record_addon_text_comparison(
                        **{**valid, field: value}
                    )
        self.assertIsNone(
            provider.health_snapshot()["lifecycle_addon_text_comparison"]
        )


class LifecycleAddonTextComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def _read(self, detail_result: dict, **transport_kwargs):
        provider = configured_provider(
            DetailTransport(detail_result, **transport_kwargs)
        )
        await provider.probe("restart_addon")
        return provider, await provider.get_addon(UPSTREAM_ADDON_SLUG)

    async def _refuse(self, detail_result: dict, **transport_kwargs):
        provider = configured_provider(
            DetailTransport(detail_result, **transport_kwargs)
        )
        await provider.probe("restart_addon")
        with self.assertRaises(OperationalLifecycleProviderError) as caught:
            await provider.get_addon(UPSTREAM_ADDON_SLUG)
        self.assertFalse(caught.exception.dispatched)
        health = provider.health_snapshot()
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)
        return provider, caught.exception

    def _assert_no_dispatch(self, provider) -> None:
        health = provider.health_snapshot()
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_9_2_0_sized_detail_is_compared_and_verified_equal(self):
        payload = sized_detail_payload(235_000)
        result = call_result(payload)
        text_bytes = len(result["content"][0]["text"].encode("utf-8"))
        self.assertEqual(text_bytes, 235_000)
        self.assertLess(text_bytes, MAX_LIFECYCLE_ADDON_STRUCTURED_TEXT_BYTES)

        provider, addon = await self._read(result)

        self.assertEqual(addon["slug"], UPSTREAM_ADDON_SLUG)
        self.assertEqual(addon["version"], "9.2.0")
        self.assertNotIn("translations", addon)
        entries = text_entries(provider)
        self.assertEqual(
            entries["detail"],
            {
                "read": "detail",
                "text_comparison": "verified_equal",
                "text_bytes_bucket": "128K-256K",
                "text_failure_class": "none",
            },
        )
        self.assertEqual(entries["inventory"]["text_comparison"], "verified_equal")
        self.assertEqual(entries["inventory"]["text_failure_class"], "none")
        self._assert_no_dispatch(provider)

    async def test_compared_text_still_refuses_a_structured_divergence(self):
        # The equality check is kept exactly for a bounded, clean text.
        result = call_result(sized_detail_payload(235_000))
        result["structuredContent"]["addon"]["translations"]["en"][
            "configuration"
        ]["synthetic_contract_text"] += "!"

        provider, error = await self._refuse(result)

        self.assertEqual(error.category, "addon_response_contract_mismatch")
        entry = text_entries(provider)["detail"]
        self.assertEqual(entry["text_comparison"], "mismatch")
        self.assertEqual(entry["text_failure_class"], "none")
        diagnostics = provider.health_snapshot()[
            "lifecycle_addon_response_diagnostics"
        ]
        self.assertIn("/content/0/text", diagnostics["invalid_semantic_field_paths"])

    async def test_over_bound_dev_detail_is_decided_from_structured(self):
        payload = sized_detail_payload(370_011)
        result = call_result(payload)
        self.assertGreater(
            len(result["content"][0]["text"].encode("utf-8")),
            MAX_LIFECYCLE_ADDON_STRUCTURED_TEXT_BYTES,
        )

        provider, addon = await self._read(result)

        self.assertEqual(addon["slug"], UPSTREAM_ADDON_SLUG)
        self.assertEqual(addon["version"], "9.2.0")
        self.assertNotIn("translations", addon)
        entries = text_entries(provider)
        self.assertEqual(
            entries["detail"],
            {
                "read": "detail",
                "text_comparison": "skipped",
                "text_bytes_bucket": "256K-512K",
                "text_failure_class": "oversize",
            },
        )
        self.assertNotEqual(entries["detail"]["text_comparison"], "verified_equal")
        self.assertEqual(entries["inventory"]["text_comparison"], "verified_equal")
        self._assert_no_dispatch(provider)

    async def test_skipped_comparison_keeps_every_structured_check(self):
        # An oversize text is not compared, so a tampered structuredContent
        # must still be refused by the structured identity checks.
        base = call_result(sized_detail_payload(370_011))
        cases = {}
        for field, value in (
            ("version", "9.2.1"),
            ("name", "Tampered MCP Server"),
            ("state", "stopped"),
            ("repository", "fedcba21"),
            ("update_available", True),
        ):
            tampered = deepcopy(base)
            tampered["structuredContent"]["addon"][field] = value
            cases[f"identity_{field}"] = tampered
        wrong_slug = deepcopy(base)
        wrong_slug["structuredContent"]["addon"]["slug"] = "local_other"
        cases["slug"] = wrong_slug
        success_missing = deepcopy(base)
        success_missing["structuredContent"].pop("success")
        cases["success_missing"] = success_missing
        truncated = deepcopy(base)
        truncated["structuredContent"]["truncated"] = True
        cases["truncated"] = truncated
        no_addon = deepcopy(base)
        no_addon["structuredContent"].pop("addon")
        cases["addon_missing"] = no_addon

        for name, result in cases.items():
            with self.subTest(name=name):
                provider, error = await self._refuse(result)
                self.assertEqual(
                    error.category, "addon_response_contract_mismatch"
                )
                entry = text_entries(provider)["detail"]
                self.assertEqual(entry["text_comparison"], "skipped")
                self.assertEqual(entry["text_failure_class"], "oversize")
                diagnostics = provider.health_snapshot()[
                    "lifecycle_addon_response_diagnostics"
                ]
                if name == "identity_repository":
                    # A repository that disagrees with the slug prefix is
                    # not an identity at all.
                    self.assertEqual(
                        diagnostics["invalid_semantic_field_paths"], ["/addon"]
                    )
                elif name.startswith("identity_"):
                    field = name.removeprefix("identity_")
                    self.assertEqual(
                        diagnostics["identity_mismatch_fields"], [field]
                    )

    async def test_scrubbed_text_with_duplicate_member_is_skipped(self):
        payload = sized_detail_payload(80_000)
        # Two option keys that each carry a secret scrub to the same key: the
        # text gains a duplicate member and the object renames one "#2".
        payload["addon"]["options_schema_keys"] = {
            f"{SYNTHETIC_SECRET}-a": "first",
            f"{SYNTHETIC_SECRET}-b": "second",
        }
        result = scrubbed_result(
            payload,
            (f"{SYNTHETIC_SECRET}-a", f"{SYNTHETIC_SECRET}-b"),
        )
        self.assertEqual(
            sorted(result["structuredContent"]["addon"]["options_schema_keys"]),
            [REDACTED, f"{REDACTED}#2"],
        )
        with self.assertRaises(ValueError):
            lifecycle._parse_lifecycle_addon_text(
                result["content"][0]["text"]
            ) and None
            json.loads(
                result["content"][0]["text"],
                object_pairs_hook=lifecycle._reject_duplicate_members,
            )

        provider, addon = await self._read(result)

        self.assertEqual(addon["version"], "9.2.0")
        entry = text_entries(provider)["detail"]
        self.assertEqual(entry["text_comparison"], "skipped")
        self.assertEqual(entry["text_failure_class"], "duplicate_member")
        self.assertEqual(entry["text_bytes_bucket"], "64K-128K")
        self._assert_no_dispatch(provider)
        self.assertNotIn(SYNTHETIC_SECRET, json.dumps(provider.health_snapshot()))

    async def test_scrubbed_text_that_stops_parsing_is_skipped(self):
        payload = sized_detail_payload(80_000)
        # A number that contains a known secret: the object keeps the number
        # (numbers are never scrubbed), the raw text scrub breaks the JSON.
        payload["addon"]["ingress_port"] = int(SYNTHETIC_NUMERIC_SECRET)
        result = scrubbed_result(payload, (SYNTHETIC_NUMERIC_SECRET,))
        self.assertIn(
            f'"ingress_port":{REDACTED}', result["content"][0]["text"]
        )
        self.assertEqual(
            result["structuredContent"]["addon"]["ingress_port"],
            int(SYNTHETIC_NUMERIC_SECRET),
        )

        provider, addon = await self._read(result)

        self.assertEqual(addon["version"], "9.2.0")
        entry = text_entries(provider)["detail"]
        self.assertEqual(entry["text_comparison"], "skipped")
        self.assertEqual(entry["text_failure_class"], "invalid_json")
        self._assert_no_dispatch(provider)

    async def test_scrubbed_text_that_still_parses_is_compared(self):
        # A secret inside a string value is scrubbed the same way in both
        # forms, so the pair stays equal and is still verified.
        payload = sized_detail_payload(80_000)
        payload["addon"]["description"] = f"uses {SYNTHETIC_SECRET} here"
        result = scrubbed_result(payload, (SYNTHETIC_SECRET,))

        provider, _addon = await self._read(result)

        entry = text_entries(provider)["detail"]
        self.assertEqual(entry["text_comparison"], "verified_equal")
        self.assertEqual(entry["text_failure_class"], "none")

    async def test_renamed_identity_keys_are_not_identity(self):
        cases = {}
        for field in ("slug", "name", "version", "state"):
            payload = sized_detail_payload(80_000)
            payload["addon"][f"{field}#2"] = payload["addon"][field]
            cases[f"detail_{field}#2"] = call_result(payload)
        oversize = sized_detail_payload(370_011)
        oversize["addon"]["version#2"] = "9.2.0"
        cases["oversize_detail_version#2"] = call_result(oversize)

        for name, result in cases.items():
            with self.subTest(name=name):
                provider, error = await self._refuse(result)
                self.assertEqual(
                    error.category, "addon_response_contract_mismatch"
                )
                diagnostics = provider.health_snapshot()[
                    "lifecycle_addon_response_diagnostics"
                ]
                self.assertEqual(
                    diagnostics["invalid_semantic_field_paths"], ["/addon"]
                )

    async def test_renamed_identity_key_in_inventory_is_not_identity(self):
        transport = DetailTransport(call_result(sized_detail_payload(80_000)))
        for addon in transport.addons:
            if addon["slug"] == UPSTREAM_ADDON_SLUG:
                addon["slug#2"] = UPSTREAM_ADDON_SLUG
        provider = configured_provider(transport)
        await provider.probe("restart_addon")

        with self.assertRaises(OperationalLifecycleProviderError) as caught:
            await provider.get_addon(UPSTREAM_ADDON_SLUG)

        self.assertEqual(
            caught.exception.category, "addon_response_contract_mismatch"
        )
        self.assertFalse(caught.exception.dispatched)
        diagnostics = provider.health_snapshot()[
            "lifecycle_addon_response_diagnostics"
        ]
        # The installed-inventory projection rejects the renamed record, so
        # the inventory (and its summary counts) is not read as identity.
        self.assertIn("/addons", diagnostics["invalid_semantic_field_paths"])
        self._assert_no_dispatch(provider)

    async def test_renamed_envelope_keys_refuse(self):
        cases = {}
        payload = sized_detail_payload(80_000)
        renamed_addon = call_result(payload)
        renamed_addon["structuredContent"]["addon#2"] = deepcopy(
            renamed_addon["structuredContent"]["addon"]
        )
        cases["addon#2/structured_only"] = renamed_addon
        consistent = deepcopy(payload)
        consistent["addon#2"] = deepcopy(consistent["addon"])
        cases["addon#2/equal_text"] = call_result(consistent)
        success = deepcopy(payload)
        success["success#2"] = False
        cases["success#2/equal_text"] = call_result(success)
        oversize = sized_detail_payload(370_011)
        oversize["truncated#2"] = True
        cases["truncated#2/oversize"] = call_result(oversize)

        for name, result in cases.items():
            with self.subTest(name=name):
                provider, error = await self._refuse(result)
                self.assertEqual(
                    error.category, "addon_response_contract_mismatch"
                )
                diagnostics = provider.health_snapshot()[
                    "lifecycle_addon_response_diagnostics"
                ]
                key = name.split("/", 1)[0]
                field = key.split("#", 1)[0]
                self.assertEqual(
                    diagnostics["invalid_semantic_field_paths"],
                    [f"/structuredContent/{field}#N"],
                )
                self.assertNotIn(key, json.dumps(provider.health_snapshot()))
                self.assertNotIn(key, f"{error!s} {error!r}")
                entry = text_entries(provider)["detail"]
                self.assertNotEqual(entry["text_comparison"], "verified_equal")

    async def test_renamed_envelope_key_diagnostic_is_fixed_and_bounded(self):
        # SEC-483-01: the observed key is upstream text; only the reviewed
        # field name and a literal "#N" reach the diagnostic.
        long_suffix = "7" * 100_000
        cases = {
            "short": ("success#2",),
            "long": (f"success#{long_suffix}",),
            "several": ("success#2", "success#3", f"addon#{long_suffix}"),
        }
        for name, keys in cases.items():
            with self.subTest(name=name):
                result = call_result(sized_detail_payload(80_000))
                for key in keys:
                    result["structuredContent"][key] = False
                provider, error = await self._refuse(result)
                self.assertEqual(
                    error.category, "addon_response_contract_mismatch"
                )
                diagnostics = provider.health_snapshot()[
                    "lifecycle_addon_response_diagnostics"
                ]
                paths = diagnostics["invalid_semantic_field_paths"]
                expected = list(
                    dict.fromkeys(
                        f"/structuredContent/{key.split('#', 1)[0]}#N"
                        for key in keys
                    )
                )
                self.assertEqual(paths, expected)
                self.assertTrue(all(len(path) <= 64 for path in paths))
                health = json.dumps(provider.health_snapshot())
                self.assertLess(len(health), 20_000)
                rendered = f"{error!s} {error!r}"
                for key in keys:
                    self.assertNotIn(key, health)
                    self.assertNotIn(key, rendered)
                self.assertNotIn("7" * 16, health)
                self.assertNotIn("7" * 16, rendered)

    def _overflow_result(self, tokens: dict[str, str]) -> dict:
        # The text carries exponent-overflow numbers; structuredContent
        # carries finite strings, so an unclassified text would compare
        # unequal and refuse.
        payload = sized_detail_payload(80_000)
        payload["addon"]["overflow_probe"] = {
            "value": "__OVERFLOW_0__",
            "items": [{"value": "__OVERFLOW_1__"}, ["__OVERFLOW_2__"]],
        }
        result = call_result(payload)
        text = result["content"][0]["text"]
        for placeholder, token in tokens.items():
            text = text.replace(f'"{placeholder}"', token)
        result["content"][0]["text"] = text
        return result

    _OVERFLOW_CASES = {
        "positive_object": {"__OVERFLOW_0__": "1e999"},
        "negative_object": {"__OVERFLOW_0__": "-1e999"},
        "nested_array_object": {"__OVERFLOW_1__": "1E+400"},
        "nested_array": {"__OVERFLOW_2__": "-1e999"},
        "several": {
            "__OVERFLOW_0__": "1e999",
            "__OVERFLOW_1__": "-1e999",
            "__OVERFLOW_2__": "1e309",
        },
    }

    async def test_exponent_overflow_text_is_skipped_nonfinite(self):
        # SEC-483-02: an exponent overflow parses to infinity without
        # reaching parse_constant; it is a nonfinite text, not a mismatch.
        for name, tokens in self._OVERFLOW_CASES.items():
            with self.subTest(name=name):
                provider, addon = await self._read(self._overflow_result(tokens))
                self.assertEqual(addon["slug"], UPSTREAM_ADDON_SLUG)
                self.assertEqual(addon["version"], "9.2.0")
                self.assertEqual(
                    text_entries(provider)["detail"],
                    {
                        "read": "detail",
                        "text_comparison": "skipped",
                        "text_bytes_bucket": "64K-128K",
                        "text_failure_class": "nonfinite",
                    },
                )
                self._assert_no_dispatch(provider)

    async def test_exponent_overflow_text_keeps_structured_identity_checks(self):
        for name, tokens in self._OVERFLOW_CASES.items():
            for field, value in (("version", "9.2.1"), ("state", "stopped")):
                with self.subTest(name=name, field=field):
                    result = self._overflow_result(tokens)
                    result["structuredContent"]["addon"][field] = value
                    provider, error = await self._refuse(result)
                    self.assertEqual(
                        error.category, "addon_response_contract_mismatch"
                    )
                    entry = text_entries(provider)["detail"]
                    self.assertEqual(entry["text_comparison"], "skipped")
                    self.assertEqual(entry["text_failure_class"], "nonfinite")
                    diagnostics = provider.health_snapshot()[
                        "lifecycle_addon_response_diagnostics"
                    ]
                    self.assertEqual(
                        diagnostics["identity_mismatch_fields"], [field]
                    )

    async def test_redaction_marker_in_identity_value_is_not_identity(self):
        # Pins the existing _safe_text marker rejection for identity values.
        for field in ("name", "version"):
            with self.subTest(field=field):
                payload = sized_detail_payload(80_000)
                payload["addon"][field] = f"Home {REDACTED} Server"
                transport = DetailTransport(call_result(payload))
                for addon in transport.addons:
                    if addon["slug"] == UPSTREAM_ADDON_SLUG:
                        addon[field] = f"Home {REDACTED} Server"
                provider = configured_provider(transport)
                await provider.probe("restart_addon")
                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as caught:
                    await provider.get_addon(UPSTREAM_ADDON_SLUG)
                self.assertEqual(
                    caught.exception.category,
                    "addon_response_contract_mismatch",
                )
                self._assert_no_dispatch(provider)

    async def test_diagnostic_is_value_free_and_reset_per_read(self):
        payload = sized_detail_payload(370_011)
        payload["addon"]["description"] = SYNTHETIC_SENTINEL
        payload["addon"]["translations"]["de"][SYNTHETIC_SENTINEL] = "x"
        transport = DetailTransport(call_result(payload))
        provider = configured_provider(transport)
        await provider.probe("restart_addon")
        self.assertIsNone(
            provider.health_snapshot()["lifecycle_addon_text_comparison"]
        )

        await provider.get_addon(UPSTREAM_ADDON_SLUG)

        health = provider.health_snapshot()
        entries = health["lifecycle_addon_text_comparison"]
        self.assertEqual([entry["read"] for entry in entries], ["inventory", "detail"])
        for entry in entries:
            self.assertEqual(
                set(entry),
                {
                    "read",
                    "text_comparison",
                    "text_bytes_bucket",
                    "text_failure_class",
                },
            )
            self.assertIn(entry["read"], TEXT_READS)
            self.assertIn(entry["text_comparison"], TEXT_COMPARISONS)
            self.assertIn(entry["text_bytes_bucket"], TEXT_BUCKETS)
            self.assertIn(entry["text_failure_class"], TEXT_FAILURE_CLASSES)
        self.assertEqual(
            lifecycle._LIFECYCLE_TEXT_FAILURE_CLASSES,
            frozenset(TEXT_FAILURE_CLASSES),
        )
        self.assertEqual(
            lifecycle._LIFECYCLE_TEXT_COMPARISONS, frozenset(TEXT_COMPARISONS)
        )
        self.assertNotIn(SYNTHETIC_SENTINEL, json.dumps(health))
        # The snapshot is a copy.
        entries[0]["read"] = SYNTHETIC_SENTINEL
        self.assertNotIn(
            SYNTHETIC_SENTINEL, json.dumps(provider.health_snapshot())
        )

        # The next read starts a fresh record: a failed inventory read
        # leaves only its own entry, not the previous detail.
        transport.inventory_result = {
            "content": [{"type": "text", "text": "{"}],
            "structuredContent": {"success": True},
            "isError": False,
        }
        with self.assertRaises(OperationalLifecycleProviderError):
            await provider.get_addon(UPSTREAM_ADDON_SLUG)
        entries = provider.health_snapshot()["lifecycle_addon_text_comparison"]
        self.assertEqual(
            entries,
            [
                {
                    "read": "inventory",
                    "text_comparison": "skipped",
                    "text_bytes_bucket": "<64K",
                    "text_failure_class": "invalid_json",
                }
            ],
        )

    async def test_missing_structured_records_not_reached(self):
        result = call_result(sized_detail_payload(80_000))
        result.pop("structuredContent")

        provider, error = await self._refuse(result)

        self.assertEqual(error.category, "addon_response_contract_mismatch")
        entry = text_entries(provider)["detail"]
        self.assertEqual(entry["text_comparison"], "not_reached")
        self.assertEqual(entry["text_failure_class"], "none")


def _unreadable_identity_reader(*, succeed_first: int = 0):
    calls = {"count": 0}

    async def reader():
        calls["count"] += 1
        if calls["count"] <= succeed_first:
            return {"slug": "core_ha_mcp", "evidence_hash": "a" * 64}
        raise LifecycleGatewayError("upstream_addon_identity_unavailable")

    return reader, calls


class F3UnreadableProviderIdentityTests(f3_fixtures._OperationalActivationBase):
    async def _setup(self, reader):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.lifecycle = f3_fixtures._F3LifecycleGateway()
        self.lifecycle.now = self.clock
        self.service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "plans"),
            LegacyGateway(),
            AuditLogger(
                str(Path(self.temp.name) / "audit.jsonl"), "f3-test-secret"
            ),
            now=self.clock,
            lifecycle_gateway=self.lifecycle,
        )
        self.telemetry, self.context = begin_request("hamcp470-f3")
        self.telemetry.caller_id = "mcp-requester"
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(Path(self.temp.name) / "plans"),
            configuration_gateway=f3_fixtures._UnusedExactConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=self.lifecycle,
            provider_identity_reader=reader,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self):
        if hasattr(self, "context"):
            end_request(self.context)
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def _assert_contract_refusal(self, caught: GovernanceError) -> None:
        self.assertEqual(caught.code, ErrorCode.OPERATIONAL_CONTRACT_MISMATCH)
        self.assertNotEqual(caught.code, ErrorCode.INTERNAL_SERVER_ERROR)
        details = caught.details
        self.assertEqual(
            details["failure_category"], "upstream_addon_identity_unavailable"
        )
        self.assertEqual(details["failure_stage"], "pre_dispatch")
        self.assertIs(details["provider_dispatch_occurred"], False)
        self.assertIs(details["action_attempted"], False)
        self.assertIs(details["fallback_occurred"], False)
        self.assertIs(details["redispatch_performed"], False)
        self.assertEqual(details["fallback"], "none")
        self.assertIsNone(caught.__cause__)

    async def test_unreadable_identity_at_prepare_refuses_before_dispatch(self):
        reader, calls = _unreadable_identity_reader()
        await self._setup(reader)
        plan = await self._grant(
            await self.service.create_reload_plan(reload_target="automation")
        )

        with self.assertRaises(GovernanceError) as caught:
            await self.service.apply(plan["plan_id"], plan["plan_hash"])

        self._assert_contract_refusal(caught.exception)
        self.assertGreaterEqual(calls["count"], 1)
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        task = self.service.task_repository.get_for_plan(plan["plan_id"])
        if task is not None:
            for declaration in self.runtime.children.declarations_for_task(
                task.task_id
            ):
                record = self.runtime.children.get(declaration["child_id"])
                self.assertTrue(record is None or record.dispatch_count == 0)

    async def test_unreadable_identity_at_recheck_refuses_before_dispatch(self):
        # A task initialized with a readable identity, then re-applied
        # before any dispatch intent, re-reads the identity in
        # _load_prepared (the pre-dispatch recheck).
        reader, calls = _unreadable_identity_reader(succeed_first=1)
        await self._setup(reader)
        approved = await self._grant(
            await self.service.create_reload_plan(reload_target="automation")
        )
        plan = self.service._load(approved["plan_id"])
        task, _prepared, _requests = await self.runtime._initialize(
            plan, approved["plan_hash"]
        )
        self.assertEqual(calls["count"], 1)

        with self.assertRaises(GovernanceError) as direct:
            await self.runtime._load_prepared(plan, task)
        self._assert_contract_refusal(direct.exception)

        with self.assertRaises(GovernanceError) as caught:
            await self.service.apply(approved["plan_id"], approved["plan_hash"])

        self._assert_contract_refusal(caught.exception)
        self.assertEqual(calls["count"], 3)
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        for declaration in self.runtime.children.declarations_for_task(
            task.task_id
        ):
            record = self.runtime.children.get(declaration["child_id"])
            self.assertTrue(
                record is None
                or (record.dispatch_count == 0 and record.dispatch_intent is None)
            )

    async def test_readable_identity_still_dispatches_once(self):
        reader, calls = _unreadable_identity_reader(succeed_first=100)
        await self._setup(reader)
        plan = await self._grant(
            await self.service.create_reload_plan(reload_target="automation")
        )

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])

        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        self.assertEqual(calls["count"], 1)


if __name__ == "__main__":
    unittest.main()
