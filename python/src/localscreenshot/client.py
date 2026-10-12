"""Calls to the localscreenshot API. Standard library only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

VERSION = "0.1.0"
DEFAULT_API = "https://api.localscreenshot.com/v1"
KEY_VARIABLE = "LOCALSCREENSHOT_API_KEY"
USER_AGENT = f"localscreenshot-cli/{VERSION} (python)"
TIMEOUT = 120.0
TOTAL = 180.0
POLL_EVERY = 2.0
MAX_IMAGE_BYTES = 50 * 1024 * 1024
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "application/pdf": "pdf"}
FORMATS = ("png", "jpeg", "webp", "pdf")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class UsageError(Exception):
    """Something is wrong in what was asked, before any request is sent."""


class ApiError(Exception):
    """The API refused, or the screenshot failed. ``code`` is the error code of the API."""

    def __init__(self, code: str, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class Screenshot:
    """A finished screenshot: the file itself and what the API says about it."""

    image: bytes
    mime: str
    width: Optional[int]
    height: Optional[int]
    data: dict = field(repr=False, default_factory=dict)

    @property
    def extension(self) -> str:
        return EXTENSIONS[self.mime]

    @property
    def credits_charged(self) -> Optional[int]:
        return (self.data.get("credits") or {}).get("charged")

    @property
    def credits_remaining(self) -> Optional[int]:
        return (self.data.get("credits") or {}).get("remaining")

    @property
    def country_observed(self) -> Optional[str]:
        return (self.data.get("country") or {}).get("observed")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect is never followed: the key only goes to the chosen host, and the signed link of an image is read
    # where the API points. urllib would follow them by default, hence this handler.
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def api_base(env: Optional[dict] = None) -> str:
    """The API address. It can be overridden for tests; outside this machine it must be https."""
    env = os.environ if env is None else env
    raw = (env.get("LOCALSCREENSHOT_API_URL") or DEFAULT_API).rstrip("/")
    url = urllib.parse.urlsplit(raw)
    if not url.hostname:
        raise UsageError("LOCALSCREENSHOT_API_URL is not a valid address.")
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in LOCAL_HOSTS):
        raise UsageError("LOCALSCREENSHOT_API_URL must use https.")
    if url.username or url.password or url.query or url.fragment:
        raise UsageError("LOCALSCREENSHOT_API_URL must be a plain address.")
    return raw


def config_file(env: Optional[dict] = None) -> Path:
    env = os.environ if env is None else env
    if sys.platform == "win32" and env.get("APPDATA"):
        return Path(env["APPDATA"]) / "localscreenshot" / "config.json"
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME") or Path.home()) / ".config")
    return Path(base) / "localscreenshot" / "config.json"


def looks_like_key(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[\x21-\x7e]{8,200}", value) is not None


def read_key(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    from_env = (env.get(KEY_VARIABLE) or "").strip()
    if from_env:
        if not looks_like_key(from_env):
            raise UsageError(f"{KEY_VARIABLE} does not look like an API key.")
        return from_env
    try:
        saved = json.loads(config_file(env).read_text(encoding="utf-8")).get("api_key")
        if looks_like_key(saved):
            return saved
    except (OSError, ValueError, AttributeError):
        pass  # No file, or unreadable: treated as "no key".
    raise UsageError(
        f'No API key. Set {KEY_VARIABLE} or run "localscreenshot login".\n'
        "Create a free account at https://localscreenshot.com/signup"
    )


def save_key(key: str, env: Optional[dict] = None) -> Path:
    file = config_file(env)
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = file.with_name(f"{file.name}.{uuid.uuid4().hex[:12]}.tmp")
    # Created with mode 0600 from the start: the key is never readable by another account, not even briefly.
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"api_key": key}) + "\n")
        os.replace(temp, file)
    except OSError:
        # The key must not stay anywhere but in the announced file.
        if temp.exists():
            os.unlink(temp)
        raise
    try:
        os.chmod(file, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows: permissions there are those of the user's folder.
    return file


def call(method: str, url: str, key: Optional[str] = None, body: Optional[dict] = None,
         idempotency_key: Optional[str] = None, timeout: float = TIMEOUT) -> dict:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(url, data=payload, method=method, headers=headers)
    if timeout <= 0:
        raise ApiError("network_error", "The request timed out.")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return _json(response.read())
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            error.close()
            raise ApiError("unexpected_redirect", "The API answered with a redirect, which this tool does not follow.", error.code) from None
        detail = _json(error.read()).get("error") or {}
        error.close()
        # Nothing is retried automatically: a screenshot costs credits, so retrying is the user's call.
        wait = error.headers.get("Retry-After", "") if error.headers else ""
        hint = f" Try again in {wait} s." if error.code == 429 and wait.isdigit() and int(wait) > 0 else ""
        message = (detail.get("message") or f"The API answered {error.code}.") + hint
        raise ApiError(detail.get("code") or f"http_{error.code}", message, error.code) from None
    except TimeoutError:
        raise ApiError("network_error", "The request timed out.") from None
    except (urllib.error.URLError, OSError):
        raise ApiError("network_error", "Could not reach the API.") from None


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def download(url: str, image: dict, timeout: float = TIMEOUT) -> bytes:
    target = urllib.parse.urlsplit(url)
    if target.scheme != "https" and not (target.scheme == "http" and target.hostname in LOCAL_HOSTS):
        raise ApiError("bad_image_link", "The API returned an image link that is not https.")
    # Signed link: it is read WITHOUT the key, which therefore never leaves the API host.
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if timeout <= 0:
        raise ApiError("network_error", "Could not download the image.")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        raise ApiError("image_unavailable", f"The image could not be downloaded ({error.code}).", error.code) from None
    except (urllib.error.URLError, OSError):
        raise ApiError("network_error", "Could not download the image.") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError("image_too_large", "The image is larger than this tool accepts.")
    # The file returned is the one the API describes: same size, same fingerprint.
    if isinstance(image.get("bytes"), int) and image["bytes"] != len(data):
        raise ApiError("image_mismatch", "The downloaded image does not have the size the API announced.")
    announced = image.get("sha256")
    if isinstance(announced, str) and announced and hashlib.sha256(data).hexdigest() != announced.lower():
        raise ApiError("image_mismatch", "The downloaded image does not match the fingerprint the API announced.")
    return data


def request_body(url: str, country: Optional[str] = None, full_page: bool = False, device: Optional[str] = None,
                 format: Optional[str] = None, click_text: Optional[str] = None, block_cookie_banners: bool = False,
                 block_ads: bool = False, dark: bool = False) -> dict:
    if not re.match(r"https?://", url or "", re.IGNORECASE):
        raise UsageError("A web address starts with https://")
    body: dict = {"url": url, "response": "json"}
    if country is not None:
        if not re.fullmatch(r"[A-Za-z]{2}", country):
            raise UsageError("country takes a two-letter country code, for example BR.")
        body["country"] = country.upper()
    if full_page:
        body["full_page"] = True
    if device is not None:
        body["device"] = device
    if format is not None:
        if format not in FORMATS:
            raise UsageError("format is png, jpeg, webp or pdf.")
        body["format"] = format
    if click_text is not None:
        body["click_text"] = click_text
    if block_cookie_banners:
        body["block_cookie_banners"] = True
    if block_ads:
        body["block_ads"] = True
    if dark:
        body["color_scheme"] = "dark"
    return body


def screenshot(url: str, *, country: Optional[str] = None, full_page: bool = False, device: Optional[str] = None,
               format: Optional[str] = None, click_text: Optional[str] = None, block_cookie_banners: bool = False,
               block_ads: bool = False, dark: bool = False, api_key: Optional[str] = None,
               _poll_every: float = POLL_EVERY, _total: float = TOTAL) -> Screenshot:
    """Take a screenshot of a public web page and return it.

    Raises ``ApiError`` when the API refuses or the screenshot fails (a failed screenshot is not charged).
    """
    base = api_base()
    body = request_body(url, country, full_page, device, format, click_text, block_cookie_banners, block_ads, dark)
    key = api_key if api_key is not None else read_key()
    if not looks_like_key(key):
        raise UsageError("api_key does not look like an API key.")
    # One deadline for the whole operation (request, wait, download), on a clock that never goes backwards.
    started = time.monotonic()
    left = lambda: _total - (time.monotonic() - started)  # noqa: E731
    shot = call("POST", f"{base}/screenshot", key, body, str(uuid.uuid4()), min(TIMEOUT, left()))
    # The follow-up address is built here from the id: never an address returned by the server.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(shot.get("id") or "")):
        raise ApiError("bad_answer", "The API answered without a screenshot id.")
    while shot.get("status") not in ("succeeded", "failed"):
        if left() <= _poll_every:
            raise ApiError("still_running", f"The screenshot is still being taken (id {shot['id']}). It will appear in your dashboard.")
        time.sleep(_poll_every)
        shot = call("GET", f"{base}/screenshots/{shot['id']}", key, timeout=left())
    image = shot.get("image") or {}
    if shot.get("status") != "succeeded" or not image.get("url"):
        error = shot.get("error") or {}
        raise ApiError(error.get("code") or "failed", error.get("message") or "The screenshot failed.")
    if image.get("mime") not in EXTENSIONS:
        raise ApiError("bad_answer", "The API returned a file type this tool does not know.")
    data = download(image["url"], image, min(TIMEOUT, left()))
    return Screenshot(image=data, mime=image["mime"], width=image.get("width"), height=image.get("height"), data=shot)


def countries() -> list:
    """The countries a page can be loaded from: a list of ``{"code": ..., "name": ...}``. Needs no key."""
    return list(call("GET", f"{api_base()}/countries").get("countries") or [])


def credits(api_key: Optional[str] = None) -> dict:
    """The credits left on the account, and its limits."""
    return call("GET", f"{api_base()}/credits", api_key if api_key is not None else read_key())
