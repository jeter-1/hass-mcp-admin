"""HAMCP-477: the dashboard-write sensitive-data guard.

Governed dashboard plan creation refuses a value an operation adds, replaces
or removes when the one dashboard sensitivity rule
(``f3_dashboard/sensitivity.py``) calls it sensitive, and the semantic-diff
preview withholds exactly those values. Before HAMCP-477 the guard used only
the shared exact-key detector, so a credential under ``apiKey``, ``api_token``,
``pin``, an ``X-API-Key`` header or an iframe ``?key=`` reached the stored
plan and its on-disk artifact.

Synthetic values only; secret-shaped text is assembled at runtime.
"""

from __future__ import annotations

from copy import deepcopy
from itertools import product
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qsl, quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

import hamcp162_synthetic as synthetic  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_dashboard.patch import compile_dashboard_patch  # noqa: E402
from ha_mcp_engineering.f3_dashboard.risk import analyze_dashboard_risk  # noqa: E402
from ha_mcp_engineering.f3_dashboard.semantic_diff import (  # noqa: E402
    REDACTED_PREVIEW,
    build_semantic_diff,
    summarize_value,
)
from ha_mcp_engineering.f3_dashboard.sensitivity import (  # noqa: E402
    MAX_SENSITIVE_PATHS,
    sensitive_key,
    sensitive_paths,
    sensitive_string,
)
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from test_dashboard_update_mvp import (  # noqa: E402
    _DashboardGateway,
    _UnusedConfigurationGateway,
    _unrelated_lifecycle_provider_identity,
)

REASON = "dashboard_contains_prohibited_sensitive_data"
# Shapeless synthetic key material: no detector shape matches it by value.
RAW = "Zx9synth" + "K3yAbCdEf" + "0123456789qwertyUIOP"
BASE = {
    "title": "Synthetic",
    "views": [{"title": "One", "cards": [{"type": "markdown", "content": "hi"}]}],
}
URL = "https" + synthetic.SCHEME_SEPARATOR + "example.invalid/view"


def add_card(value):
    return [
        {"operation_id": "c", "operation": "add", "path": "/views/0/cards/1", "value": value}
    ]


# Every case the guard admitted before HAMCP-477 (source re-test,
# 2026-11-27), with the pointer the refusal must name.
ADMITTED_BEFORE = (
    ("apiKey", {"type": "custom:x", "apiKey": RAW}, "/views/0/cards/1/apiKey"),
    ("api_token", {"type": "custom:x", "api_token": RAW}, "/views/0/cards/1/api_token"),
    ("auth_token", {"type": "custom:x", "auth_token": RAW}, "/views/0/cards/1/auth_token"),
    ("bearer_token", {"type": "custom:x", "bearer_token": RAW}, "/views/0/cards/1/bearer_token"),
    ("access_key", {"type": "custom:x", "access_key": RAW}, "/views/0/cards/1/access_key"),
    ("secret_key", {"type": "custom:x", "secret_key": RAW}, "/views/0/cards/1/secret_key"),
    ("private_key", {"type": "custom:x", "private_key": RAW}, "/views/0/cards/1/private_key"),
    (
        "X-API-Key header",
        {"type": "custom:x", "headers": {"X-API-Key": RAW}},
        "/views/0/cards/1/headers/X-API-Key",
    ),
    ("passcode", {"type": "custom:x", "passcode": RAW}, "/views/0/cards/1/passcode"),
    ("pin string", {"type": "custom:x", "pin": "4821"}, "/views/0/cards/1/pin"),
    ("pin number", {"type": "custom:x", "pin": 4821}, "/views/0/cards/1/pin"),
    (
        "alarm-panel code",
        {"type": "alarm-panel", "entity": "alarm_control_panel.synthetic", "code": "4821"},
        "/views/0/cards/1/code",
    ),
    ("iframe ?apikey=", {"type": "iframe", "url": URL + "?apikey=" + RAW}, "/views/0/cards/1/url"),
    ("iframe ?key=", {"type": "iframe", "url": URL + "?mode=dark&key=" + RAW}, "/views/0/cards/1/url"),
    ("camelCase accessToken", {"type": "custom:x", "accessToken": RAW}, "/views/0/cards/1/accessToken"),
    ("hyphenated Api-Token", {"type": "custom:x", "Api-Token": RAW}, "/views/0/cards/1/Api-Token"),
)

# Ordinary configuration that must still create a plan.
ORDINARY = (
    ("plain entity card", {"type": "entity", "entity": "sensor.synthetic_temperature"}),
    ("entity id with token word", {"type": "entity", "entity": "sensor.synthetic_token_balance"}),
    (
        "entity id with password word",
        {"type": "entity", "entity": "binary_sensor.synthetic_password_expired"},
    ),
    ("plain iframe url", {"type": "iframe", "url": URL + "?mode=dark&theme=1"}),
    ("camera picture", {"type": "picture-entity", "entity": "camera.synthetic_door"}),
    ("code outside an alarm panel", {"type": "custom:x", "code": "python"}),
    ("pin as a boolean", {"type": "custom:x", "pin": True}),
    (
        "button with tap action",
        {
            "type": "button",
            "entity": "light.synthetic_lamp",
            "tap_action": {"action": "toggle"},
            "hold_action": {"action": "more-info"},
        },
    ),
    ("markdown with a monkey", {"type": "markdown", "content": "keyboard and monkey"}),
)

# Codex r1 F1 (PR #543): URL forms the first query rule missed (an encoded
# parameter name, a parenthesized path, a relative URL with no slash, an
# encoded separator, a doubly encoded name), each with the pointer to name.
URL_FORMS = (
    ("encoded name %6bey", URL + "?%6bey=" + RAW),
    ("parenthesized path", "https" + synthetic.SCHEME_SEPARATOR + "example.invalid/(view)?key=" + RAW),
    ("relative without a slash", "view?key=" + RAW),
    ("encoded separators", "view%3Fkey%3D" + RAW),
    ("doubly encoded name", URL + "?%256bey=" + RAW),
    # Architect class sweep (same batch).
    ("scheme-relative", "//example.invalid/view?key=" + RAW),
    ("bare query", "?key=" + RAW),
    ("fragment parameter", URL + "#key=" + RAW),
    ("fragment route query", URL + "#/route?key=" + RAW),
    ("semicolon parameter", URL + ";key=" + RAW),
    ("html-escaped ampersand", URL + "?mode=dark&amp;key=" + RAW),
    ("triply encoded name", URL + "?%25256bey=" + RAW),
    ("triply encoded separators", "view%25253Fkey%25253D" + RAW),
    ("encoded more deeply than three rounds", "view%2525253Fkey%2525253D" + RAW),
    ("uppercase percent hex", URL + "?%6B%45%59=" + RAW),
    ("encoded equals", URL + "?key%3D" + RAW),
    ("plus inside a name", URL + "?api+key=" + RAW),
    ("encoded space inside a name", URL + "?api%20key=" + RAW),
    ("mixed case name", URL + "?ApI_KeY=" + RAW),
    ("userinfo password", "https" + synthetic.SCHEME_SEPARATOR + "user:" + RAW + "@example.invalid/view"),
    ("scheme-relative userinfo", "//user:" + RAW + "@example.invalid/view"),
    ("encoded userinfo", "https%3A%2F%2Fuser%3A" + RAW + "%40example.invalid"),
    # Cursor r2 F3 and F4 (PR #543): a user name holding "@", and tab, line
    # feed, carriage return or a space inside a name or a password.
    ("user name holding @", "https" + synthetic.SCHEME_SEPARATOR + "x@user:" + RAW + "@example.invalid/view"),
    ("scheme-relative user name holding @", "//x@user:" + RAW + "@example.invalid/view"),
    ("encoded user name holding @", "https%3A%2F%2Fx%40user%3A" + RAW + "%40example.invalid/view"),
    ("tab inside a name", URL + "?ke\ty=" + RAW),
    ("tab ending a name", URL + "?key\t=" + RAW),
    ("line break inside a name", URL + "?ke\r\ny=" + RAW),
    ("tab inside a password", "https" + synthetic.SCHEME_SEPARATOR + "user:" + RAW[:8] + "\t" + RAW[8:] + "@example.invalid"),
    ("space inside a password", "https" + synthetic.SCHEME_SEPARATOR + "user:" + RAW[:8] + " " + RAW[8:] + "@example.invalid"),
    ("space inside a name", URL + "?api key=" + RAW),
    ("encoded separator inside a name", URL + "?ap%26i_key=" + RAW),
    ("question mark inside a name", URL + "?k?ey=" + RAW),
    ("hash inside a fragment name", URL + "#k#ey=" + RAW),
    ("question mark inside a later name", URL + "?mode=dark&k?ey=" + RAW),
    # Broader than the parser, which reads the names ``a?key``, ``a#key``,
    # ``a?ke y`` and ``a?ke\x0by`` and no separator in the last form: the scan
    # also judges each name after its nearest separator, and deletes a line
    # break that a decoding produces.
    ("name after a second question mark", "view?a?key=" + RAW),
    ("name after a second hash", "view#a#key=" + RAW),
    ("space inside a name after a second question mark", "view?a?ke y=" + RAW),
    ("control character inside a name after a second question mark", "view?a?ke\x0by=" + RAW),
    ("line break encoded inside an encoded separator", "view%3%0AFkey%3D" + RAW),
    ("tab between the authority slashes", "/\t/user:" + RAW + "@example.invalid"),
    ("tab inside the scheme", "ht\ttps:user:" + RAW + "@example.invalid"),
    ("line break inside an encoded separator", "view%3\nFkey%3D" + RAW),
    ("space ending a name", URL + "?key =" + RAW),
    # The same class in browser URL parsing: a special scheme needs no
    # slashes, and a backslash or a third slash stands for one.
    ("special scheme without slashes", "https:user:" + RAW + "@example.invalid"),
    ("backslashes for slashes", "https:\\\\user:" + RAW + "@example.invalid"),
    ("three slashes", "https:///user:" + RAW + "@example.invalid"),
    # Cursor r3 F5 (PR #543): every special scheme, in either case, with no
    # slash or with one. Python's ``urlsplit`` reads no password in these, so
    # only these forms lock the scheme list, the optional slashes and the
    # case-insensitive match.
    *(
        (
            f"{scheme} with {count} slash before userinfo",
            scheme + ":" + slashes + "user:" + RAW + "@example.invalid/view",
        )
        for scheme in ("http", "https", "ws", "wss", "ftp", "HTTP", "HTTPS", "WS", "WSS", "FTP")
        for count, slashes in (("no", ""), ("one", "/"))
    ),
)
# The same forms with an ordinary parameter must still plan.
ORDINARY_URL_FORMS = (
    "https" + synthetic.SCHEME_SEPARATOR + "example.invalid/(view)?mode=dark",
    "view?mode=dark&theme=1",
    URL + "?%6dode=dark",
    "view%3Fmode%3Ddark",
    "view%25253Fmode%25253Ddark",
    URL + "?q=100%25",
    "//example.invalid/view?mode=dark",
    URL + "#/route?mode=dark",
    URL + "?q=a+b&page=2",
    "https" + synthetic.SCHEME_SEPARATOR + "example.invalid:8123/view?mode=dark",
    "https" + synthetic.SCHEME_SEPARATOR + "user@example.invalid/view",
    "https" + synthetic.SCHEME_SEPARATOR + "x@user@example.invalid/view",
    "https" + synthetic.SCHEME_SEPARATOR + "user:@example.invalid/view",
    "mailto:someone@example.invalid",
    URL + "?mode=dark mode&theme=1",
    # Special schemes with no slash or one: a user name alone, an empty
    # password and a port are no credential.
    "http:user@example.invalid/view",
    "WSS:/user:@example.invalid/view",
    "FTP:example.invalid:21/view",
    "ws:/example.invalid:8123/view",
)
# Codex r1 F2 (PR #543): custom cards may nest data whose ``type`` is not a
# string; it is no card type, and it must not break the guard.
NON_STRING_TYPES = ({"mode": "text"}, ["a", "b"])


# Config keys of the built-in cards, rows, elements, features, badges, views
# and sections of the pinned frontend (home-assistant/frontend 380e9b5a81ad,
# the dashboard_analysis pin): the interface field names of its lovelace
# config types (src/panels/lovelace/{cards,entity-rows,elements,card-features,
# badges,heading-badges}/types.ts and src/data/lovelace/config/*.ts).
BUILT_IN_KEYS = """
action action_name actions alert_classes alignment allow allow_open_top_navigation
alt_text appearance area area_id aspect_ratio attachment attribute auto_fit
background back_path backup badges badges_position badges_wrap border buttons
calendar camera_image camera_view card cards card_size chart_type clock_size
clock_style cluster collection_key color columns column_span commands conditions
config confirmation confirm_text content content_label content_only controls
dark_mode dark_mode_filter dark_mode_image dashboard data date_format days_to_show
default_zoom dense_section_placement description detail disable_compare disabled
disable_sandbox dismiss_text display_type double_tap_action download
due_date_period effects elements energy_date_selection entities entity entity_id
entity_ids error event_type exclude_entities exemptions expand_legend face_style
fan_modes fan_speeds features features_position filter fit_mode fit_y_data
fit_zones fixed_period focus footer force forecast_slots forecast_type from
geo_location_sources graph green grid_options group_by_area group_by_floor header
heading heading_style hide_background hide_completed hide_compound_stats
hide_create hide_disabled_controls hide_empty hide_if_unavailable hide_legend
hide_section_headers hide_state hold_action hours_to_show hvac_modes icon
icon_color icon_double_tap_action icon_height icon_hold_action icon_tap_action
image image_entity image_tap_action index initial_view label label_mode layout
layout_options limits link_dashboard logarithmic_scale max max_columns
max_devices max_width max_y_axis message min min_y_axis mode modes name
name_detail narrow navigation_path navigation_replace needle new_tab
no_background opacity opening_direction operation_modes options panel path
perform_action period pipeline_id precipitation_type prefix preset_modes preview
pulse rect red repeat resources rolling_window round_temperature row row_span
scale_ruler secondary_info secondary_info_attribute seconds_motion section
sections segments sensor_classes service service_data severity show_all
show_camera show_current show_current_as_primary show_empty show_entity_picture
show_forecast show_header_toggle show_icon show_icon_and_title show_labels
show_last_changed show_legend show_mute_button show_name show_names
show_only_totals show_seconds show_state sidebar sidebar_label size sort
sound_modes source sources split_device_classes square start_listening
state_color state_content state_filter state_image states stat_type stat_types
step strategy style subview suffix summary swing_horizontal_modes swing_modes
tap_action target text text_only theme theme_mode ticks time_format time_zone
title top_margin type types unit url url_path user value variant vertical
vertical_opening_direction view view_index view_layout views visibility visible
yellow
""".split()


# The differential class guard (architect, after Cursor r2 on PR #543): a
# generated corpus of URL shapes, judged by Python's own URL parser. Whatever
# the parser reads as a userinfo password or a credential-named parameter,
# the rule must refuse; the rule may be broader than the parser, never
# narrower.
CREDENTIAL_PARAMETER_NAMES = frozenset({"key", "auth", "sig", "signature"})
DIFFERENTIAL_NAMES = (
    "key", "apikey", "api_key", "token", "access_token", "auth", "sig",
    "signature", "password", "X-API-Key", "mode", "monkey", "keyboard",
)
# Each control and whitespace character, and each character a URL gives a
# meaning to, inserted at the start, middle and end (encoded, a separator
# becomes part of a name or password).
DIFFERENTIAL_INSERTS = (
    "\t", "\n", "\r", "\r\n", " ", "\x0b", "\x0c", "\x00", "\x1f", "\x7f",
    "\u00a0", "\u2028", "+", "@", ":", "&", "=", ";", "#", "?", "%",
)
DIFFERENTIAL_PREFIXES = (URL, "//example.invalid/view", "view", "")
DIFFERENTIAL_SEPARATORS = (
    "?",
    "?mode=dark&",
    "#",
    "#/route?",
    "?mode=dark#/route?",
    "?debug#/route?",
    ";",
    "?mode=dark;",
)
DIFFERENTIAL_USERS = ("user", "x@user", "us:er", "", "a b")
DIFFERENTIAL_AUTHORITIES = (
    "https" + synthetic.SCHEME_SEPARATOR + "{}@example.invalid/view",
    "HTTPS" + synthetic.SCHEME_SEPARATOR + "{}@example.invalid:8123/view?mode=dark",
    "//{}@example.invalid/view",
    "view/{}@example.invalid",
)


def _squashed(name):
    return "".join(character for character in name.lower() if character.isascii() and character.isalnum())


def _credential_name(name):
    squashed = _squashed(name)
    return (
        sensitive_key(name)
        or squashed in CREDENTIAL_PARAMETER_NAMES
        or "signature" in squashed
    )


def _parser_finds_credential(value):
    """Whether ``urlsplit`` and ``parse_qsl`` read a credential in ``value``.

    Each of the string and its first three percent-decodings is split; the
    query, the fragment, a query inside the fragment and the ``;`` parameters
    of the path are each parsed with both ``&`` and ``;`` as separators. A
    parameter counts when its name is a credential name and its value is not
    empty (``?key&=value`` gives ``key`` no value and ``value`` no name).
    """

    form = value
    for _ in range(4):
        split = urlsplit(form)
        if split.password:
            return True
        parts = [split.query, split.fragment, split.fragment.partition("?")[2]]
        parts.extend(split.path.split(";")[1:])
        for part in parts:
            for piece in part.split(";"):
                for name, value in parse_qsl(piece, keep_blank_values=True):
                    if value and _credential_name(name):
                        return True
        form = unquote(form)
    return False


def _variants(text):
    """``text`` with each insert at its start, middle and end, and case mixes."""

    middle = len(text) // 2
    yield text
    yield text.upper()
    yield "".join(c.upper() if i % 2 else c for i, c in enumerate(text))
    for insert in DIFFERENTIAL_INSERTS:
        for at in (0, middle, len(text)):
            yield text[:at] + insert + text[at:]


def _encodings(assemble, component):
    """The string with no, one, two and three levels of percent-encoding, of
    the whole string and of the component alone."""

    for level in range(4):
        whole = assemble(component)
        encoded = component
        for _ in range(level):
            whole = quote(whole, safe="")
            encoded = quote(encoded, safe="")
        yield whole
        if level:
            yield assemble(encoded)


def differential_corpus():
    for name in DIFFERENTIAL_NAMES:
        for variant in _variants(name):
            for prefix, separator in product(DIFFERENTIAL_PREFIXES, DIFFERENTIAL_SEPARATORS):
                yield from _encodings(
                    lambda part: prefix + separator + part + "=" + RAW, variant
                )
    for password in _variants(RAW[:8] + RAW[-4:]):
        for user, authority in product(DIFFERENTIAL_USERS, DIFFERENTIAL_AUTHORITIES):
            yield from _encodings(
                lambda part: authority.format(user + ":" + part), password
            )
        # Look-alikes: a user alone, an empty password, a port.
        for authority in DIFFERENTIAL_AUTHORITIES:
            yield authority.format(password)
            yield authority.format(password + ":")
    # The characters URL parsers delete, at every position of a whole
    # credential-carrying string, raw and encoded once.
    carriers = [
        prefix + separator + "key=" + RAW
        for prefix, separator in product(DIFFERENTIAL_PREFIXES, DIFFERENTIAL_SEPARATORS)
    ]
    carriers += [authority.format("user:" + RAW) for authority in DIFFERENTIAL_AUTHORITIES]
    carriers += [quote(carrier, safe="") for carrier in carriers]
    for carrier, deleted in product(carriers, ("\t", "\n", "\r")):
        for at in range(len(carrier) + 1):
            yield carrier[:at] + deleted + carrier[at:]


class SensitivityRuleTests(unittest.TestCase):
    def test_every_admitted_key_is_sensitive(self):
        for key in (
            "apiKey", "api_token", "auth_token", "bearer_token", "access_key",
            "secret_key", "private_key", "X-API-Key", "passcode", "pin", "otp",
            "totp", "client_secret", "Authorization", "passwd", "passphrase",
            "credentials", "APIKEY", "Api-Key", "accessToken",
        ):
            with self.subTest(key=key):
                self.assertTrue(sensitive_key(key))

    def test_code_is_sensitive_only_directly_inside_an_alarm_panel(self):
        self.assertTrue(sensitive_key("code", card_type="alarm-panel"))
        self.assertFalse(sensitive_key("code"))
        self.assertFalse(sensitive_key("code", card_type="custom:x"))

    def test_no_built_in_card_key_is_sensitive(self):
        self.assertGreater(len(BUILT_IN_KEYS), 250)
        flagged = [
            key
            for key in BUILT_IN_KEYS
            if sensitive_key(key) or sensitive_key(key, card_type="alarm-panel")
        ]
        self.assertEqual(flagged, [])

    def test_url_credential_parameters_and_prefixes(self):
        for value in (
            URL + "?apikey=" + RAW,
            URL + "?api_key=" + RAW,
            URL + "?x=1&key=" + RAW,
            URL + "?auth=" + RAW,
            URL + "?access_key=" + RAW,
            URL + "?api%5Fkey=" + RAW,
            URL + "?token=" + RAW,
            URL + "#access_token=" + RAW,
            "/local/view?sig=" + RAW,
            "see " + URL + "?apiKey=" + RAW + " here",
            *(value for _label, value in URL_FORMS),
            synthetic.bearer(RAW),
            "Basic " + RAW,
        ):
            with self.subTest(value=value[:40]):
                self.assertTrue(sensitive_string(value))
        for value in (
            URL + "?mode=dark",
            URL + "?monkey=1&keyboard=2",
            "Token: 5",
            "a key to the door",
            "sensor.synthetic_token_balance",
            *ORDINARY_URL_FORMS,
        ):
            with self.subTest(value=value):
                self.assertFalse(sensitive_string(value))

    def test_a_type_that_is_not_a_string_is_no_card_type(self):
        for holder in NON_STRING_TYPES:
            with self.subTest(holder=type(holder).__name__):
                self.assertFalse(sensitive_key("code", card_type=holder))
                self.assertEqual(
                    sensitive_paths(
                        {"type": holder, "code": "4821", "label": "x"},
                        path="/views/0/cards/0/options",
                    ),
                    (),
                )
                self.assertEqual(
                    sensitive_paths(
                        {"type": holder, "apiKey": RAW}, path="/views/0/cards/0/options"
                    ),
                    ("/views/0/cards/0/options/apiKey",),
                )
        self.assertTrue(sensitive_key("code", card_type="alarm-panel"))

    def test_the_rule_refuses_whatever_the_url_parser_reads_as_a_credential(self):
        flagged = missed = total = 0
        misses = []
        for value in differential_corpus():
            total += 1
            if not _parser_finds_credential(value):
                continue
            flagged += 1
            if not sensitive_string(value):
                missed += 1
                if len(misses) < 5:
                    misses.append(value)
        self.assertEqual((missed, misses), (0, []))
        # The corpus has teeth: the parser reads a credential in most strings.
        self.assertGreater(total, 50000)
        self.assertGreater(flagged, total // 2)

    def test_the_parser_reads_no_credential_in_the_look_alikes(self):
        # The parser is no stricter than the rule on ordinary URLs, so the
        # differential test is not passing on an oracle that flags everything.
        for value in (*ORDINARY_URL_FORMS, URL + "?mode=dark", URL + "?monkey=1&keyboard=2"):
            with self.subTest(value=value):
                self.assertFalse(_parser_finds_credential(value))

    def test_pointers_never_carry_a_value_or_an_unusual_key(self):
        value = {"headers": {"X-API-Key": RAW}, RAW + "-token": "x", "a b token": "y"}
        paths = sensitive_paths(value, path="/views/0/cards/1")
        self.assertIn("/views/0/cards/1/headers/X-API-Key", paths)
        self.assertIn("/views/0/cards/1/*", paths)
        self.assertNotIn(RAW, json.dumps(paths))

    def test_pointers_are_bounded(self):
        value = {f"token_{index}": "x" for index in range(MAX_SENSITIVE_PATHS * 3)}
        self.assertEqual(
            len(sensitive_paths(value, path="/views/0/cards/1")), MAX_SENSITIVE_PATHS
        )


class PreviewAgreementTests(unittest.TestCase):
    """The preview withholds exactly the values plan creation refuses."""

    @staticmethod
    def _leaves(value, segments=()):
        if isinstance(value, dict):
            for key, child in value.items():
                yield from PreviewAgreementTests._leaves(child, (*segments, key))
        else:
            yield segments, value

    def _withheld(self, value):
        """Pointers of the card's leaves the preview withholds."""

        withheld = []
        for segments, leaf in self._leaves(value):
            pointer = "/views/0/cards/1/" + "/".join(segments)
            holder = value
            for segment in segments[:-1]:
                holder = holder[segment]
            summary = summarize_value(
                present=True, value=leaf, path=pointer, holder_type=holder.get("type")
            )
            if summary.redacted:
                self.assertEqual(summary.preview, REDACTED_PREVIEW)
                withheld.append(pointer)
        return withheld

    def test_preview_and_guard_name_the_same_values(self):
        for label, value, pointer in ADMITTED_BEFORE:
            with self.subTest(label=label):
                guarded = sensitive_paths(value, path="/views/0/cards/1")
                self.assertEqual(self._withheld(value), list(guarded))
                self.assertEqual(list(guarded), [pointer])
        for label, value in ORDINARY:
            with self.subTest(label=label):
                self.assertEqual(sensitive_paths(value, path="/views/0/cards/1"), ())
                self.assertEqual(self._withheld(value), [])

    def test_the_semantic_diff_resolves_the_card_type_of_a_leaf(self):
        base = {"views": [{"cards": [{"type": "alarm-panel"}, {"type": "custom:x"}]}]}
        for index, expected in ((0, True), (1, False)):
            with self.subTest(card=index):
                compilation = compile_dashboard_patch(
                    deepcopy(base),
                    [{"operation_id": "c", "operation": "add",
                      "path": f"/views/0/cards/{index}/code", "value": "4821"}],
                )
                risk = analyze_dashboard_risk(
                    deepcopy(base), compilation.resulting_configuration
                )
                entry = build_semantic_diff(compilation, risk).entries[0]
                self.assertIs(entry.proposed.redacted, expected)


class DashboardWriteGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dashboard = _DashboardGateway()
        self.dashboard.configuration = deepcopy(BASE)
        self.repository = ChangePlanRepository(self.root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            _UnusedConfigurationGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), "test-access-secret"),
            dashboard_gateway=self.dashboard,
            provider_identity_reader=_unrelated_lifecycle_provider_identity,
        )
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            dashboard_gateway=self.dashboard,
            provider_identity_reader=_unrelated_lifecycle_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def _create(self, operations):
        return await self.service.create_dashboard_update_plan(
            title="Synthetic card",
            description="Add one synthetic card.",
            url_path="dashboard-synthetic",
            patch_operations=operations,
        )

    def _persisted_bytes(self) -> bytes:
        return b"".join(
            path.read_bytes() for path in sorted(self.root.rglob("*")) if path.is_file()
        )

    async def _assert_refused(self, operations, pointer):
        with self.assertRaises(GovernanceError) as raised:
            await self._create(operations)
        error = raised.exception
        self.assertEqual(error.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.assertEqual(error.details["reason"], REASON)
        self.assertIn(pointer, error.details["sensitive_paths"])
        self.assertNotIn(RAW, json.dumps(error.details))
        self.assertNotIn(RAW, str(error))
        self.assertEqual(self.dashboard.write_count, 0)
        self.assertEqual(self.repository.list(), [])
        self.assertNotIn(RAW.encode(), self._persisted_bytes())

    async def test_each_admitted_case_now_refuses_with_zero_writes(self):
        for label, value, pointer in ADMITTED_BEFORE:
            with self.subTest(label=label):
                await self._assert_refused(add_card(value), pointer)
        # The guard ran after the one pre-read; nothing was persisted.
        self.assertEqual(self.dashboard.preread_count, len(ADMITTED_BEFORE))

    async def test_a_leaf_replace_judges_the_resulting_card_type(self):
        self.dashboard.configuration = {
            "title": "Synthetic",
            "views": [{"cards": [{"type": "alarm-panel", "entity": "alarm_control_panel.synthetic"}]}],
        }
        await self._assert_refused(
            [{"operation_id": "c", "operation": "add", "path": "/views/0/cards/0/code",
              "value": "4821"}],
            "/views/0/cards/0/code",
        )

    async def test_a_previous_sensitive_value_refuses_its_replace_and_remove(self):
        self.dashboard.configuration = {
            "title": "Synthetic",
            "views": [{"cards": [{"type": "custom:x", "apiKey": RAW}]}],
        }
        for operation in (
            {"operation_id": "r", "operation": "replace",
             "path": "/views/0/cards/0/apiKey", "value": "rotated-later"},
            {"operation_id": "r", "operation": "remove", "path": "/views/0/cards/0/apiKey"},
            {"operation_id": "r", "operation": "remove", "path": "/views/0/cards/0"},
        ):
            with self.subTest(operation=operation["operation"], path=operation["path"]):
                with self.assertRaises(GovernanceError) as raised:
                    await self._create([operation])
                self.assertEqual(raised.exception.details["reason"], REASON)
                self.assertEqual(
                    raised.exception.details["sensitive_paths"],
                    ["/views/0/cards/0/apiKey"],
                )
                self.assertEqual(self.dashboard.write_count, 0)
                self.assertEqual(self.repository.list(), [])
        self.assertNotIn(RAW.encode(), self._persisted_bytes())

    async def test_a_value_removed_from_a_card_whose_type_changes_judges_the_current_type(self):
        # The same plan turns the alarm panel into another card type and
        # removes its code. The removed code is shown for approval, so it is
        # judged under the current card type too, not only the resulting one.
        self.dashboard.configuration = {
            "title": "Synthetic",
            "views": [{"cards": [{"type": "alarm-panel",
                                  "entity": "alarm_control_panel.synthetic",
                                  "code": "4821"}]}],
        }
        await self._assert_refused(
            [
                {"operation_id": "t", "operation": "replace",
                 "path": "/views/0/cards/0/type", "value": "custom:x"},
                {"operation_id": "r", "operation": "remove", "path": "/views/0/cards/0/code"},
            ],
            "/views/0/cards/0/code",
        )

    async def test_a_pointer_the_shared_detector_flags_is_withheld(self):
        # Defense in depth: a key holding a known secret is refused earlier by
        # the shared detector (unchanged reason, no pointer list), and if a
        # pointer ever reached the guard's list it is withheld, not shown.
        known = "QuxSynthKnownValue"
        self.service.sensitive_values = (known,)
        value = {"type": "custom:x", known + "_token": "x", "apiKey": RAW}
        with self.assertRaises(GovernanceError) as raised:
            await self._create(add_card(value))
        self.assertEqual(raised.exception.details, {"reason": REASON})
        self.assertEqual(self.dashboard.preread_count, 0)

        def guarded(card):
            operations = add_card(card)
            compilation = compile_dashboard_patch(deepcopy(BASE), operations)
            proposal = SimpleNamespace(
                compilation=compilation,
                raw_evidence=SimpleNamespace(configuration=deepcopy(BASE)),
            )
            return self.service._dashboard_changed_sensitive_paths(proposal)

        self.assertEqual(guarded(value), ["/views/0/cards/1/apiKey"])
        self.assertEqual(guarded({"type": "custom:x", known + "_token": "x"}), ["*"])
        self.assertNotIn(known, json.dumps(guarded(value)))

    async def test_unchanged_sensitive_values_elsewhere_do_not_lock_the_dashboard(self):
        # HAMCP-477 judges only changed values; an unrelated edit still plans.
        self.dashboard.configuration = {
            "title": "Synthetic",
            "views": [{"cards": [{"type": "custom:x", "apiKey": RAW}]}],
        }
        created = await self._create(
            [{"operation_id": "t", "operation": "replace", "path": "/title", "value": "After"}]
        )
        self.assertEqual(created["status"], "awaiting_approval")
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_a_list_insert_or_remove_judges_every_shifted_sibling(self):
        # An array add that displaces items, and an array remove, put the
        # shifted suffix in the approval projection, so a sensitive sibling
        # that shifts refuses the plan and is named at both indexes.
        self.dashboard.configuration = {
            "title": "Synthetic",
            "views": [{"cards": [
                {"type": "entity", "entity": "sensor.synthetic_a"},
                {"type": "custom:x", "apiKey": RAW},
            ]}],
        }
        plain = {"type": "entity", "entity": "sensor.synthetic_b"}
        for operation, expected in (
            ({"operation_id": "i", "operation": "add", "path": "/views/0/cards/0", "value": plain},
             ["/views/0/cards/1/apiKey", "/views/0/cards/2/apiKey"]),
            ({"operation_id": "d", "operation": "remove", "path": "/views/0/cards/0"},
             ["/views/0/cards/1/apiKey", "/views/0/cards/0/apiKey"]),
        ):
            with self.subTest(operation=operation["operation"]):
                with self.assertRaises(GovernanceError) as raised:
                    await self._create([operation])
                self.assertEqual(raised.exception.details["reason"], REASON)
                self.assertEqual(raised.exception.details["sensitive_paths"], expected)
                self.assertNotIn(RAW, str(raised.exception.details))
        self.assertEqual(self.dashboard.write_count, 0)
        self.assertEqual(self.repository.list(), [])
        # An append after the sensitive card shifts nothing, so it plans.
        created = await self._create(
            [{"operation_id": "a", "operation": "add", "path": "/views/0/cards/2", "value": plain}]
        )
        self.assertEqual(created["status"], "awaiting_approval")
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_ordinary_cards_still_create_plans(self):
        for label, value in ORDINARY:
            with self.subTest(label=label):
                created = await self._create(add_card(value))
                self.assertEqual(created["status"], "awaiting_approval")
        self.assertEqual(len(self.repository.list()), len(ORDINARY))
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_url_forms_refuse_before_persistence(self):
        # A form the shared detector already refuses is refused at the
        # pre-check (no pointer list); every other form by this rule, which
        # the unit test shows covers each form on its own.
        for label, url in URL_FORMS:
            with self.subTest(label=label):
                with self.assertRaises(GovernanceError) as raised:
                    await self._create(add_card({"type": "iframe", "url": url}))
                error = raised.exception
                self.assertEqual(error.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
                self.assertEqual(error.details["reason"], REASON)
                if "sensitive_paths" in error.details:
                    self.assertEqual(error.details["sensitive_paths"], ["/views/0/cards/1/url"])
                else:
                    self.assertEqual(error.details, {"reason": REASON})
                self.assertNotIn(RAW, json.dumps(error.details))
                self.assertNotIn(RAW, str(error))
                self.assertEqual(self.dashboard.write_count, 0)
                self.assertEqual(self.repository.list(), [])
                self.assertNotIn(RAW.encode(), self._persisted_bytes())
        for url in ORDINARY_URL_FORMS:
            with self.subTest(url=url):
                created = await self._create(add_card({"type": "iframe", "url": url}))
                self.assertEqual(created["status"], "awaiting_approval")
        self.assertEqual(len(self.repository.list()), len(ORDINARY_URL_FORMS))
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_a_nested_type_that_is_not_a_string_still_plans(self):
        for holder in NON_STRING_TYPES:
            with self.subTest(holder=type(holder).__name__):
                self.dashboard.configuration = {
                    "title": "Synthetic",
                    "views": [{"cards": [{
                        "type": "custom:x",
                        "options": {"type": holder, "label": "before", "code": "python"},
                    }]}],
                }
                for operation in (
                    {"operation_id": "r", "operation": "replace",
                     "path": "/views/0/cards/0/options/label", "value": "after"},
                    {"operation_id": "c", "operation": "replace",
                     "path": "/views/0/cards/0/options/code", "value": "yaml"},
                    {"operation_id": "a", "operation": "add",
                     "path": "/views/0/cards/0/options/extra", "value": {"type": holder}},
                ):
                    created = await self._create([operation])
                    self.assertEqual(created["status"], "awaiting_approval")
                # A credential key under that holder still refuses.
                await self._assert_refused_keeping_plans(
                    [{"operation_id": "k", "operation": "add",
                      "path": "/views/0/cards/0/options/apiKey", "value": RAW}],
                    "/views/0/cards/0/options/apiKey",
                )
        self.assertEqual(self.dashboard.write_count, 0)

    async def _assert_refused_keeping_plans(self, operations, pointer):
        before = len(self.repository.list())
        with self.assertRaises(GovernanceError) as raised:
            await self._create(operations)
        self.assertEqual(raised.exception.details["reason"], REASON)
        self.assertIn(pointer, raised.exception.details["sensitive_paths"])
        self.assertNotIn(RAW, json.dumps(raised.exception.details))
        self.assertEqual(len(self.repository.list()), before)
        self.assertNotIn(RAW.encode(), self._persisted_bytes())

    async def test_the_shared_detector_refusals_are_unchanged(self):
        # Pre-read refusals keep their reason and carry no pointer list.
        for value in (
            {"type": "custom:x", "token": RAW},
            {"type": "markdown", "content": "x " + synthetic.bearer(RAW)},
        ):
            with self.subTest(value=sorted(value)):
                with self.assertRaises(GovernanceError) as raised:
                    await self._create(add_card(value))
                self.assertEqual(raised.exception.details, {"reason": REASON})
        self.assertEqual(self.dashboard.preread_count, 0)
        self.assertEqual(self.dashboard.write_count, 0)


if __name__ == "__main__":
    unittest.main()
