"""The ``localscreenshot`` command."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import client
from .client import ApiError, UsageError

HELP = f"""localscreenshot {client.VERSION}
Take a screenshot of a public web page.

Usage
  localscreenshot <url> [options]
  localscreenshot countries        list the countries a page can be loaded from
  localscreenshot credits          show the credits left on your account
  localscreenshot login            save your API key on this machine
  localscreenshot logout           remove the saved key

Options
  -o, --output <file>      where to write the image (default: <host>-<time>.<ext>)
      --country <code>     load the page from this country, two letters (BR, DE, JP)
      --full-page          capture the whole page, not only the first screen
      --device <name>      screen to render on (iphone_15, ipad, desktop_hd, ...)
      --format <format>    png (default), jpeg, webp or pdf
      --click-text <text>  click the one element showing this text, then capture
      --no-cookie-banners  hide cookie banners
      --no-ads             hide ads
      --dark               render in dark mode
      --json               print the API answer as JSON
      --force              overwrite the output file if it exists
  -h, --help               show this help
  -v, --version            show the version

Your API key
  Set {client.KEY_VARIABLE}, or run "localscreenshot login".
  Create a free account at https://localscreenshot.com/signup

Docs: https://docs.localscreenshot.com
"""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D102
        # The argparse message echoes the refused arguments, and someone may have put their key there. It is not repeated.
        raise UsageError('Unknown or incomplete option.\nRun "localscreenshot --help" to see the options.')


def _parser() -> argparse.ArgumentParser:
    # No option abbreviation: "--f" must not become "--force" by accident.
    parser = _Parser(prog="localscreenshot", add_help=False, allow_abbrev=False)
    parser.add_argument("target", nargs="*")
    parser.add_argument("-o", "--output")
    parser.add_argument("--country")
    parser.add_argument("--full-page", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--format")
    parser.add_argument("--click-text")
    parser.add_argument("--no-cookie-banners", "--block-cookie-banners", dest="block_cookie_banners", action="store_true")
    parser.add_argument("--no-ads", "--block-ads", dest="block_ads", action="store_true")
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("-v", "--version", action="store_true")
    return parser


def default_name(page_url: str, mime: str, now: datetime) -> str:
    """Named after the page host and the time. Nothing in it comes from the server."""
    host = (urllib.parse.urlsplit(page_url).hostname or "screenshot").lower()
    if host.startswith("www."):
        host = host[4:]
    host = "".join(char for char in host if char.isascii() and (char.isalnum() or char in ".-")).lstrip(".") or "screenshot"
    return f"{host[:80]}-{now.strftime('%Y%m%d-%H%M%S')}.{client.EXTENSIONS[mime]}"


def write_image(file: str, data: bytes, force: bool) -> Path:
    # abspath, not resolve, and lexists, not exists: a symbolic link is never followed, and a dangling one counts
    # as "already there".
    target = Path(os.path.abspath(file))
    refusal = "The output file already exists. Use --force to overwrite it, or -o to choose another name."
    if not force and os.path.lexists(target):
        raise UsageError(refusal)
    if force:
        temp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:12]}.tmp")
        try:
            with open(temp, "xb") as handle:
                handle.write(data)
            os.replace(temp, target)
        except OSError:
            if temp.exists():
                os.unlink(temp)
            raise
        return target
    try:
        # Exclusive creation: fails if the file appeared in the meantime, instead of overwriting it.
        with open(target, "xb") as handle:
            handle.write(data)
    except FileExistsError:
        raise UsageError(refusal) from None
    except OSError:
        # No half-written image is left behind.
        if os.path.lexists(target):
            os.unlink(target)
        raise
    return target


def scrub(text: str, keys) -> str:
    """Never let a key out in a message, whatever the server answers."""
    for key in keys:
        if key:
            text = text.replace(key, "[key]")
    return text


def _read_secret() -> str:
    if not sys.stdin.isatty():
        # Piped input (a secret manager, a file): one line, no prompt.
        return sys.stdin.readline()
    return getpass.getpass("API key: ")


def _take(args, out: Callable[[str], None], now: Callable[[], datetime], poll_every: float) -> None:
    url = args.target[0]
    shot = client.screenshot(
        url, country=args.country, full_page=args.full_page, device=args.device, format=args.format,
        click_text=args.click_text, block_cookie_banners=args.block_cookie_banners, block_ads=args.block_ads,
        dark=args.dark, _poll_every=poll_every,
    )
    file = write_image(args.output or default_name(url, shot.mime, now()), shot.image, args.force)
    if args.json:
        out(json.dumps({**shot.data, "file": str(file)}, indent=2))
        return
    lines = [f"Saved {file}", f"{shot.width} x {shot.height}, {len(shot.image):,} bytes"]
    country = shot.data.get("country")
    if country:
        lines.append(f"Country: asked {country.get('requested')}, loaded from {country.get('observed') or 'unknown'}")
    if shot.credits_charged is not None:
        left = "" if shot.credits_remaining is None else f", {shot.credits_remaining} left"
        lines.append(f"Credits: {shot.credits_charged} used{left}")
    out("\n".join(lines))


def _login(out: Callable[[str], None], read_secret: Callable[[], str], secrets: set) -> None:
    key = read_secret().strip()
    # Known from now on, so that no later message can show it, even if the API echoes it back.
    secrets.add(key)
    if not client.looks_like_key(key):
        raise UsageError("That does not look like an API key. Copy it from your dashboard: https://localscreenshot.com/dashboard")
    # Checked before it is written: a key that does not work is not saved.
    data = client.credits(key)
    file = client.save_key(key)
    out(f"Key saved in {file}\n{(data.get('credits') or {}).get('remaining', 'unknown')} credits left")


def main(argv=None, out=None, err=None, now=None, read_secret=None, poll_every: float = client.POLL_EVERY) -> int:
    """Run the command and return its exit code: 0 done, 1 the API refused or failed, 2 wrong usage."""
    out = out or (lambda text: print(text))
    err = err or (lambda text: print(text, file=sys.stderr))
    now = now or (lambda: datetime.now(timezone.utc))
    secrets: set = set()
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
        if args.version:
            out(client.VERSION)
            return 0
        if args.help or not args.target:
            out(HELP)
            return 0 if args.help else 2
        if len(args.target) > 1:
            raise UsageError("One page at a time: give a single address.")
        try:
            secrets.add(client.read_key())
        except UsageError:
            pass  # No key: it is only needed here to mask it, the command itself will say what is missing.
        command = args.target[0]
        if command == "countries":
            listed = client.countries()
            out(json.dumps({"countries": listed}, indent=2) if args.json else "\n".join(f"{c.get('code')}  {c.get('name')}" for c in listed))
        elif command == "credits":
            data = client.credits()
            limits = data.get("limits") or {}
            out(json.dumps(data, indent=2) if args.json else
                f"{(data.get('credits') or {}).get('remaining', 'unknown')} credits left\n"
                f"{limits.get('requests_per_minute')} requests a minute, {limits.get('concurrent_screenshots')} screenshots at a time")
        elif command == "login":
            _login(out, read_secret or _read_secret, secrets)
        elif command == "logout":
            file = client.config_file()
            if file.exists():
                file.unlink()
                out(f"Key removed from {file}")
            else:
                out("No saved key.")
        elif command.lower().startswith(("http://", "https://")):
            _take(args, out, now, poll_every)
        else:
            # What was typed is not echoed: someone may have put their key there by mistake.
            raise UsageError("That is not a command or a web address. An address starts with https://")
        return 0
    except UsageError as error:
        err(scrub(str(error), secrets))
        return 2
    except ApiError as error:
        err(scrub(f"Error {error.code}: {error.message}", secrets))
        return 1
    except KeyboardInterrupt:
        err("Cancelled.")
        return 130
    except Exception as error:  # noqa: BLE001  an unexpected error must never print a traceback holding the key
        err(scrub(f"Unexpected error: {error}", secrets))
        return 1


def run() -> None:
    sys.exit(main())
