#!/usr/bin/env python3
"""Prime Video cookie validator and session export tool (local use only)."""

from __future__ import annotations

import json
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pvimport_codec import MAX_STANDALONE_LINK_LEN, encode_pvimport_payload

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

REQUEST_HEADERS = {
    "Host": "www.primevideo.com",
    "Connection": "keep-alive",
    "device-memory": "4",
    "sec-ch-device-memory": "4",
    "dpr": "1",
    "sec-ch-dpr": "1",
    "viewport-width": "1366",
    "sec-ch-viewport-width": "1366",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
}

AUTH_COOKIE_KEYS = (
    "session-token",
    "x-main-av",
    "x-main",
    "at-main-av",
    "at-main",
    "sess-at-main-av",
    "sess-at-main",
    "ubid-main-av",
    "ubid-main",
    "session-id",
)

PRIME_DOMAINS = (".primevideo.com", ".amazon.com", "www.primevideo.com", "www.amazon.com")

STOREFRONT_URL = "https://www.primevideo.com/region/eu/storefront"
CONFIG_URL = (
    "https://atv-ps.primevideo.com/acm/GetConfiguration/WebClient"
    "?deviceTypeID=AOAGZA014O5RE&deviceID=Web"
)

DEFAULT_LINKS = {
    "pc": "https://www.primevideo.com/",
    "mobile": "https://www.primevideo.com/storefront",
    "tv": "https://www.primevideo.com/livetv",
}

SESSION_TTL_SECONDS = 3600
LOGIN_BASE_URL = "http://127.0.0.1:5050"
SESSIONS: dict[str, dict[str, Any]] = {}
SESSIONS_LOCK = threading.Lock()


def links_for_region(region: str) -> dict[str, str]:
    code = (region or "").strip().lower()
    if len(code) == 2 and code.isalpha():
        base = f"https://www.primevideo.com/region/{code}"
        return {
            "pc": f"{base}/",
            "mobile": f"{base}/storefront",
            "tv": f"{base}/livetv",
        }
    return dict(DEFAULT_LINKS)


def is_netscape_line(line: str) -> bool:
    parts = line.strip().split("\t")
    if len(parts) < 7:
        return False
    if parts[1].upper() not in ("TRUE", "FALSE"):
        return False
    if parts[3].upper() not in ("TRUE", "FALSE"):
        return False
    return bool(re.fullmatch(r"-?\d+", parts[4].strip()))


def json_to_netscape(data) -> str:
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        data = data["cookies"]
    if not isinstance(data, list):
        raise ValueError("Invalid JSON cookie format")

    lines = []
    for cookie in data:
        domain = str(cookie.get("domain", ""))
        tail = "TRUE" if domain.startswith(".") else "FALSE"
        path = str(cookie.get("path", "/"))
        secure = "TRUE" if cookie.get("secure", False) else "FALSE"
        expires = cookie.get("expirationDate") or cookie.get("expires") or cookie.get("expiry") or 0
        name = str(cookie.get("name", ""))
        value = str(cookie.get("value", ""))
        lines.append(f"{domain}\t{tail}\t{path}\t{secure}\t{int(float(expires))}\t{name}\t{value}")
    return "\n".join(lines)


def parse_cookie_text(raw_text: str) -> tuple[dict[str, str], str, list[str]]:
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("Cookie input is empty")

    found_keys: list[str] = []
    cookies: dict[str, str] = {}
    netscape_lines: list[str] = []

    try:
        parsed = json.loads(raw_text)
        netscape_text = json_to_netscape(parsed)
    except (json.JSONDecodeError, ValueError):
        netscape_text = raw_text

    for line in netscape_text.splitlines():
        if not is_netscape_line(line):
            continue
        netscape_lines.append(line.strip())
        parts = line.split("\t")
        name, value = parts[5], parts[6]
        cookies[name] = value
        if name in AUTH_COOKIE_KEYS:
            found_keys.append(name)

    if not netscape_lines:
        for match in re.finditer(
            r"(?P<name>session-token|x-main(?:-av)?|at-main(?:-av)?|sess-at-main(?:-av)?|"
            r"ubid-main(?:-av)?|session-id)\s*=\s*(?P<value>[^;\s]+)",
            raw_text,
            re.IGNORECASE,
        ):
            name = match.group("name")
            value = match.group("value").strip().strip('"')
            cookies[name] = value
            if name not in found_keys:
                found_keys.append(name)
            domain = ".primevideo.com"
            netscape_lines.append(f"{domain}\tTRUE\t/\tTRUE\t2082758400\t{name}\t{value}")

    if not cookies:
        raise ValueError("No valid cookies found. Use Netscape or JSON format.")

    if not any(key in cookies for key in AUTH_COOKIE_KEYS):
        raise ValueError(
            "Missing Prime auth cookies. Need at least one of: "
            + ", ".join(AUTH_COOKIE_KEYS[:4])
        )

    return cookies, "\n".join(netscape_lines), sorted(set(found_keys))


def normalize_cookie_value(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def store_session(editor_json: list[dict[str, Any]], region: str) -> str:
    token = secrets.token_urlsafe(10)
    with SESSIONS_LOCK:
        SESSIONS[token] = {
            "cookies": editor_json,
            "region": region,
            "expires": time.time() + SESSION_TTL_SECONDS,
        }
    return token


def get_session(token: str) -> dict[str, Any] | None:
    with SESSIONS_LOCK:
        data = SESSIONS.get(token)
        if not data:
            return None
        if data["expires"] < time.time():
            SESSIONS.pop(token, None)
            return None
        return data


def netscape_to_editor_json(netscape_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in netscape_text.splitlines():
        if not is_netscape_line(line):
            continue
        domain, tail, path, secure, expires, name, value = line.split("\t")[:7]
        items.append(
            {
                "domain": domain,
                "hostOnly": tail.upper() == "FALSE",
                "httpOnly": name in AUTH_COOKIE_KEYS,
                "name": name,
                "path": path,
                "sameSite": "lax",
                "secure": secure.upper() == "TRUE",
                "session": False,
                "storeId": "0",
                "value": normalize_cookie_value(value),
                "expirationDate": int(expires),
            }
        )
    return items


def generate_login_links(netscape_text: str, region: str = "") -> tuple[dict[str, str], dict[str, str], bool]:
    editor_json = netscape_to_editor_json(netscape_text)
    if not editor_json:
        raise ValueError("No cookies available for login link.")

    payload = encode_pvimport_payload(editor_json)
    targets = links_for_region(region)
    standalone: dict[str, str] = {}
    local: dict[str, str] = {}
    token = store_session(editor_json, region)

    for key, base_url in targets.items():
        link = f"{base_url}#pvimport={payload}"
        if len(link) <= MAX_STANDALONE_LINK_LEN:
            standalone[key] = link
        local[key] = f"{LOGIN_BASE_URL}/login/{token}?target={key}"

    return standalone, local, len(standalone) > 0


def netscape_to_playwright_cookies(netscape_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in netscape_text.splitlines():
        if not is_netscape_line(line):
            continue
        domain, _, path, secure, expires, name, value = line.split("\t")[:7]
        expiry = int(expires)
        entry: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": secure.upper() == "TRUE",
            "httpOnly": name in AUTH_COOKIE_KEYS,
            "sameSite": "Lax",
        }
        if expiry > 0:
            entry["expires"] = expiry
        items.append(entry)
    return items


def launch_prime_browser(netscape_text: str, target: str = "pc", region: str = "") -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError(
            "Browser launch needs Playwright. Run:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        ) from exc

    cookies = netscape_to_playwright_cookies(netscape_text)
    if not cookies:
        raise ValueError("No cookies available to inject.")

    url = links_for_region(region).get(target, DEFAULT_LINKS["pc"])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        print(f"Opened Prime Video: {url}")
        print("Close the browser window when finished, then press Enter here.")
        while browser.is_connected():
            page.wait_for_timeout(500)
        browser.close()


def find_first_value(obj, target_keys: set[str]):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in target_keys and value not in (None, ""):
                return value
            nested = find_first_value(value, target_keys)
            if nested not in (None, ""):
                return nested
    elif isinstance(obj, list):
        for item in obj:
            nested = find_first_value(item, target_keys)
            if nested not in (None, ""):
                return nested
    return None


def extract_pattern(text: str, patterns: list[str], default: str = "") -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return default


def cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def http_get(url: str, cookies: dict[str, str], extra_headers: dict[str, str] | None = None) -> tuple[int, str, str]:
    headers = dict(REQUEST_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    headers["Cookie"] = cookie_header(cookies)

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, response.geturl(), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, exc.geturl(), body


def get_configuration(cookies: dict[str, str]) -> tuple[dict, int, str]:
    headers = {
        "Host": "atv-ps.primevideo.com",
        "Accept": "application/json, text/plain, */*",
        "Referer": STOREFRONT_URL,
    }
    status, final_url, text = http_get(CONFIG_URL, cookies, headers)
    final_url = final_url.lower()
    if "signin" in final_url:
        return {}, 401, text
    if status != 200:
        return {}, status, text
    try:
        return json.loads(text) or {}, status, text
    except json.JSONDecodeError:
        fallback = {}
        territory = extract_pattern(text, [r'"recordTerritory"\s*:\s*"([^"]+)"'])
        customer = extract_pattern(text, [r'"customerID"\s*:\s*"([^"]+)"'])
        if territory:
            fallback["recordTerritory"] = territory
        if customer:
            fallback["customerID"] = customer
        return fallback, status, text


def analyze_storefront(cookies: dict[str, str]) -> tuple[str, int, dict]:
    status, final_url, text = http_get(STOREFRONT_URL, cookies)
    final_url = final_url.lower()
    if status != 200:
        return text, status, {}
    if "signin" in final_url or "ap/signin" in final_url:
        return text, 401, {}

    config, config_status, _ = get_configuration(cookies)
    if config_status == 401:
        return text, 401, config
    customer_id = str(find_first_value(config, {"customerID", "customerId"}) or "").strip()
    if not customer_id:
        customer_id = extract_pattern(text, [r'"customerID"\s*:\s*"([^"]+)"'])
    if customer_id == "":
        return text, 412, config
    return text, 200, config


def infer_account(text: str, config: dict) -> dict:
    watchlist = extract_pattern(
        text,
        [r'"watchlistAction"\s*:\s*\{\s*"ajaxEnabled"\s*:\s*(true|false|null)'],
    ).lower()

    if watchlist == "true":
        plan = "Prime (Paid)"
        is_paid = True
    elif watchlist == "false":
        plan = "Free / No subscription"
        is_paid = False
    elif re.search(r"subscribe now", text, re.IGNORECASE):
        plan = "Free / No subscription"
        is_paid = False
    else:
        plan = "Unknown"
        is_paid = None

    profile = extract_pattern(
        text,
        [
            r'data-testid="active-profile-([^"]+)"',
            r'"profiles"\s*:\s*\[\{"name":"([^"]+)"',
            r'"displayName":"([^"]+)"',
        ],
    )

    region = str(find_first_value(config, {"recordTerritory"}) or "").strip()
    if not region:
        region = extract_pattern(text, [r'"recordTerritory"\s*:\s*"([^"]+)"'])

    customer_id = str(find_first_value(config, {"customerID", "customerId"}) or "").strip()
    if not customer_id:
        customer_id = extract_pattern(text, [r'"customerID"\s*:\s*"([^"]+)"'])

    return {
        "profile": profile or "Unknown",
        "region": region or "Unknown",
        "plan": plan,
        "is_paid": is_paid,
        "customer_id": customer_id or "Unknown",
    }


def check_cookies(raw_text: str) -> dict[str, Any]:
    cookies, netscape_config, found_keys = parse_cookie_text(raw_text)
    text, status, config = analyze_storefront(cookies)
    account = infer_account(text, config)

    alive = status == 200 and account["customer_id"] not in ("", "Unknown")
    status_label = "ALIVE" if alive else "DEAD"

    if status == 401:
        status_label = "DEAD"
        message = "Session expired or cookies are invalid (redirected to sign-in)."
    elif status == 412:
        status_label = "DEAD"
        message = "Cookies loaded but customerID is empty (logged out)."
    elif status == 403:
        status_label = "DEAD"
        message = "Amazon blocked the request (403). Try again later."
    elif alive:
        message = "Valid Prime Video session detected."
    else:
        message = f"Could not fully verify session (HTTP {status})."

    region = account.get("region", "")
    editor_json = netscape_to_editor_json(netscape_config)
    login_links, local_links, has_standalone = (
        generate_login_links(netscape_config, region) if alive else ({}, {}, False)
    )
    primary_links = login_links if has_standalone else local_links
    first_line = raw_text.splitlines()[0] if raw_text.splitlines() else ""
    return {
        "status": status_label,
        "alive": alive,
        "message": message,
        "format": "NETSCAPE" if is_netscape_line(first_line) else "MIXED",
        "total_cookies": len(cookies),
        "found_auth_cookies": found_keys,
        "account": account,
        "links": primary_links,
        "login_links": primary_links,
        "standalone_links": login_links,
        "local_links": local_links,
        "has_standalone": has_standalone,
        "requires_server": not has_standalone,
        "netscape_config": netscape_config,
        "cookie_editor_json": editor_json,
        "can_launch_browser": _playwright_available(),
        "requires_extension": True,
        "note": (
            "Use Standalone link (primevideo.com) — no terminal needed. "
            "If that fails, use Local link with python3 prime_checker.py running. "
            "Extension v1.4.0 required. Allow in incognito."
        ),
    }


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def run_cli(cookie_text: str) -> int:
    try:
        result = check_cookies(cookie_text)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc}")
        return 1

    print(f"Status: {result['status']}")
    print(result["message"])
    print()
    account = result["account"]
    print(f"Profile : {account['profile']}")
    print(f"Region  : {account['region']}")
    print(f"Plan    : {account['plan']}")
    print(f"Customer: {account['customer_id']}")
    print()
    print("Prime Video login links:")
    for name, url in result.get("login_links", result["links"]).items():
        print(f"  {name.upper():6}: {url}")
    print()
    print(result["note"])
    print("Install extension once from the extension/ folder (Chrome → Load unpacked).")
    return 0 if result["alive"] else 2


class PrimeHandler(BaseHTTPRequestHandler):
    server_version = "PrimeCookieTool/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        if path.startswith("/login/"):
            token = path[len("/login/") :].split("/")[0].strip()
            if not token or not get_session(token):
                self.send_error(HTTPStatus.NOT_FOUND, "Login link expired or invalid.")
                return
            self._send_file(STATIC_DIR / "login.html", "text/html; charset=utf-8")
            return

        if path.startswith("/api/session/"):
            token = path.rsplit("/", 1)[-1].strip()
            session = get_session(token)
            if not session:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Session expired. Generate a new link."})
                return
            region = str(session.get("region") or "")
            self._send_json(
                HTTPStatus.OK,
                {
                    "cookies": session["cookies"],
                    "region": region,
                    "redirects": links_for_region(region),
                },
            )
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def _read_json_body(self) -> tuple[dict | None, str | None]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw) if raw else {}, None
        except json.JSONDecodeError:
            return None, "Invalid JSON body."

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload, parse_error = self._read_json_body()
        if parse_error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": parse_error})
            return

        if parsed.path == "/api/check":
            cookie_text = str(payload.get("cookies", "")).strip()
            if not cookie_text:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "No cookie data provided."})
                return
            try:
                result = check_cookies(cookie_text)
                self._send_json(HTTPStatus.OK, result)
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except urllib.error.URLError as exc:
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": f"Network error: {exc}"})
            return

        if parsed.path == "/api/launch":
            cookie_text = str(payload.get("cookies", "")).strip()
            target = str(payload.get("target", "pc")).strip().lower() or "pc"
            region = str(payload.get("region", "")).strip()
            if not cookie_text:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "No cookie data provided."})
                return
            try:
                _, netscape_config, _ = parse_cookie_text(cookie_text)
                threading.Thread(
                    target=launch_prime_browser,
                    args=(netscape_config, target, region),
                    daemon=True,
                ).start()
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "message": "Opening Prime Video in a new browser window..."},
                )
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        open_browser = "--open" in sys.argv
        args = [a for a in sys.argv[2:] if a != "--open"]
        if args:
            cookie_text = Path(args[0]).read_text(encoding="utf-8", errors="ignore")
        else:
            cookie_text = sys.stdin.read()
        if open_browser:
            try:
                _, netscape_config, _ = parse_cookie_text(cookie_text)
                result = check_cookies(cookie_text)
                region = result.get("account", {}).get("region", "")
                launch_prime_browser(netscape_config, "pc", region)
            except (ValueError, urllib.error.URLError) as exc:
                print(f"Error: {exc}")
                raise SystemExit(1) from exc
            raise SystemExit(0)
        raise SystemExit(run_cli(cookie_text))

    if not STATIC_DIR.exists():
        print("Static UI folder missing. Run from project directory.")
        raise SystemExit(1)

    host, port = "127.0.0.1", 5050
    server = ThreadingHTTPServer((host, port), PrimeHandler)
    print("Prime Video Cookie Tool")
    print(f"Open http://{host}:{port} in your browser")
    print("Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
