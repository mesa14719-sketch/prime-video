import base64
import gzip
import json
from typing import Any

ESSENTIAL_COOKIE_NAMES = {
    "session-token",
    "at-main-av",
    "at-main",
    "sess-at-main-av",
    "sess-at-main",
    "ubid-main-av",
    "ubid-main",
    "x-main-av",
    "x-main",
    "session-id",
    "session-id-time",
    "lc-main-av",
    "i18n-prefs",
    "av-timezone",
}

MAX_STANDALONE_LINK_LEN = 7500


def filter_essential_cookies(editor_json: list[dict[str, Any]]) -> list[dict[str, Any]]:
    essential = [c for c in editor_json if c.get("name") in ESSENTIAL_COOKIE_NAMES]
    return essential if essential else editor_json


def encode_pvimport_payload(editor_json: list[dict[str, Any]]) -> str:
    filtered = filter_essential_cookies(editor_json)
    raw = json.dumps(filtered, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9)
    return base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
