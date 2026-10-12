"""Tests of the command and of the importable function, against a fake API on this machine."""

import hashlib
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import localscreenshot  # noqa: E402
from localscreenshot import client  # noqa: E402
from localscreenshot.cli import HELP, default_name, main  # noqa: E402

KEY = "lss_test_SECRETSECRETSECRET"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")
NOW = datetime(2026, 10, 12, 9, 8, 7, tzinfo=timezone.utc)


class FakeApi:
    """A fake API: it records what it receives and answers what the test tells it."""

    def __init__(self, handler):
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                entry = {"method": self.command, "url": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": json.loads(raw) if raw else None}
                outer.seen.append(entry)
                answer = handler(entry, outer) or {"status": 404, "json": {"error": {"code": "not_found", "message": "Not found."}}}
                body = answer["raw"] if "raw" in answer else json.dumps(answer.get("json", {})).encode()
                self.send_response(answer["status"])
                self.send_header("Content-Type", "image/png" if "raw" in answer else "application/json")
                self.send_header("Content-Length", str(len(body)))
                for name, value in (answer.get("headers") or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _serve

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def shot(self, **extra):
        image = {"url": f"{self.origin}/files/shot.png?signature=abc", "mime": "image/png", "bytes": len(PNG), "width": 1440, "height": 900, "sha256": hashlib.sha256(PNG).hexdigest()}
        image.update(extra.pop("image", {}))
        return {"id": "01TESTSHOT", "status": "succeeded", "image": image, "country": None, "credits": {"charged": 1, "reserved": 1, "remaining": 49}, "error": None, **extra}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ls-cli-")
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        self.saved = dict(os.environ)
        os.environ.update({"LOCALSCREENSHOT_API_KEY": KEY, "HOME": self.dir, "XDG_CONFIG_HOME": os.path.join(self.dir, "cfg")})
        os.environ.pop("LOCALSCREENSHOT_API_URL", None)

    def tearDown(self):
        os.chdir(self.cwd)
        os.environ.clear()
        os.environ.update(self.saved)

    def run_cli(self, argv, api=None, **more):
        if api is not None:
            os.environ["LOCALSCREENSHOT_API_URL"] = f"{api.origin}/v1"
        out, err = [], []
        code = main(argv, out=out.append, err=err.append, now=lambda: NOW, poll_every=0.01, **more)
        files = sorted(name for name in os.listdir(self.dir) if name != "cfg")
        return code, "\n".join(out), "\n".join(err), files

    def test_options_map_to_the_real_api_parameters(self):
        self.assertEqual(client.request_body("https://example.com"), {"url": "https://example.com", "response": "json"})
        self.assertEqual(
            client.request_body("https://example.com", "br", True, "iphone_15", "jpeg", "Product", True, True, True),
            {"url": "https://example.com", "response": "json", "country": "BR", "full_page": True, "device": "iphone_15", "format": "jpeg",
             "click_text": "Product", "block_cookie_banners": True, "block_ads": True, "color_scheme": "dark"},
        )
        with self.assertRaises(client.UsageError):
            client.request_body("https://example.com", country="Brazil")
        with self.assertRaises(client.UsageError):
            client.request_body("https://example.com", format="gif")

    def test_the_default_name_comes_from_the_page_and_the_time(self):
        self.assertEqual(default_name("https://www.example.com/a?b=1", "image/png", NOW), "example.com-20261012-090807.png")
        self.assertEqual(default_name("https://example.com", "application/pdf", NOW), "example.com-20261012-090807.pdf")

    def test_the_api_address_must_be_https_except_on_this_machine(self):
        self.assertEqual(client.api_base({}), "https://api.localscreenshot.com/v1")
        self.assertEqual(client.api_base({"LOCALSCREENSHOT_API_URL": "http://127.0.0.1:9/v1/"}), "http://127.0.0.1:9/v1")
        for bad in ("http://example.com/v1", "https://user:pass@example.com/v1"):
            with self.assertRaises(client.UsageError):
                client.api_base({"LOCALSCREENSHOT_API_URL": bad})

    def test_a_screenshot_is_requested_once_downloaded_without_the_key_and_written(self):
        api = FakeApi(lambda req, api: {"status": 200, "json": api.shot(country={"requested": "BR", "observed": "BR"})} if req["method"] == "POST" else {"status": 200, "raw": PNG})
        code, out, err, files = self.run_cli(["https://example.com", "--country", "BR"], api)
        api.close()
        self.assertEqual(code, 0, err)
        self.assertEqual(files, ["example.com-20261012-090807.png"])
        self.assertEqual(Path(self.dir, files[0]).read_bytes(), PNG)
        self.assertIn("1440 x 900", out)
        self.assertIn("asked BR, loaded from BR", out)
        self.assertIn("Credits: 1 used, 49 left", out)
        post, image = api.seen
        self.assertEqual(post["headers"]["authorization"], f"Bearer {KEY}")
        self.assertEqual(post["headers"]["user-agent"], client.USER_AGENT)
        self.assertRegex(post["headers"]["idempotency-key"], r"^[0-9a-f-]{36}$")
        self.assertEqual(post["body"], {"url": "https://example.com", "response": "json", "country": "BR"})
        self.assertNotIn("authorization", image["headers"])

    def test_an_existing_file_is_never_overwritten_without_force(self):
        api = FakeApi(lambda req, api: {"status": 200, "json": api.shot()} if req["method"] == "POST" else {"status": 200, "raw": PNG})
        file = Path(self.dir, "home.png")
        file.write_text("mine")
        code, _, err, _ = self.run_cli(["https://example.com", "-o", str(file)], api)
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(file.read_text(), "mine")
        # Refused before anything is asked of the API: no screenshot taken, no credit used.
        self.assertEqual(api.seen, [])
        # The same for a path that cannot be written at all: a folder, or a folder that does not exist.
        code, _, err, _ = self.run_cli(["https://example.com", "-o", str(self.dir), "--force"], api)
        self.assertEqual((code, "is a folder" in err), (2, True))
        code, _, err, _ = self.run_cli(["https://example.com", "-o", str(Path(self.dir, "nowhere", "home.png"))], api)
        self.assertEqual((code, "does not exist" in err), (2, True))
        self.assertEqual(api.seen, [])
        # A dangling symbolic link is "already there" too: nothing is written through it.
        if sys.platform != "win32":
            link = Path(self.dir, "link.png")
            os.symlink(Path(self.dir, "elsewhere.png"), link)
            self.assertEqual(self.run_cli(["https://example.com", "-o", str(link)], api)[0], 2)
            self.assertFalse(Path(self.dir, "elsewhere.png").exists())
            os.unlink(link)
        code, _, err, files = self.run_cli(["https://example.com", "-o", str(file), "--force"], api)
        api.close()
        self.assertEqual(code, 0, err)
        self.assertEqual(file.read_bytes(), PNG)
        self.assertEqual(files, ["home.png"])

    def test_an_api_error_prints_its_code_and_never_the_key(self):
        api = FakeApi(lambda req, api: {"status": 402, "json": {"error": {"code": "insufficient_credits", "message": f"Not enough credits for key {KEY}."}}})
        code, out, err, files = self.run_cli(["https://example.com"], api)
        api.close()
        self.assertEqual(code, 1)
        self.assertIn("Error insufficient_credits: Not enough credits", err)
        self.assertNotIn(KEY, out + err)
        self.assertIn("[key]", err)
        self.assertEqual(files, [])

        limited = FakeApi(lambda req, api: {"status": 429, "headers": {"Retry-After": "7"}, "json": {"error": {"code": "rate_limited", "message": "Too many requests."}}})
        _, _, err, _ = self.run_cli(["https://example.com"], limited)
        limited.close()
        self.assertIn("Error rate_limited: Too many requests. Try again in 7 s.", err)
        self.assertEqual(len(limited.seen), 1)

    def test_an_unfinished_screenshot_is_followed_by_id_and_a_failed_one_says_why(self):
        def handler(req, api):
            if req["method"] == "POST":
                return {"status": 202, "json": {"id": "01PENDING", "status": "running", "links": {"self": "https://evil.example/steal"}}}
            polls = sum(1 for entry in api.seen if entry["url"] == "/v1/screenshots/01PENDING")
            if polls < 2:
                return {"status": 200, "json": {"id": "01PENDING", "status": "running"}}
            return {"status": 200, "json": {"id": "01PENDING", "status": "failed", "error": {"code": "click_target_invalid", "message": "No element shows that text."}}}

        api = FakeApi(handler)
        code, _, err, _ = self.run_cli(["https://example.com", "--click-text", "Nope"], api)
        api.close()
        self.assertEqual(code, 1)
        self.assertIn("Error click_target_invalid: No element shows that text.", err)
        polls = [entry for entry in api.seen if entry["method"] == "GET"]
        self.assertEqual(len(polls), 2)
        self.assertTrue(all(entry["url"] == "/v1/screenshots/01PENDING" and entry["headers"]["authorization"] == f"Bearer {KEY}" for entry in polls))

    def test_a_redirect_a_wrong_size_a_wrong_fingerprint_or_an_unknown_type_is_refused(self):
        redirect = FakeApi(lambda req, api: {"status": 302, "headers": {"Location": "https://evil.example/"}})
        self.assertIn("unexpected_redirect", self.run_cli(["https://example.com"], redirect)[2])
        redirect.close()
        self.assertEqual(len(redirect.seen), 1)

        for image, expected in (({"bytes": 5}, "image_mismatch"), ({"sha256": "a" * 64}, "image_mismatch"), ({"mime": "text/html"}, "bad_answer")):
            api = FakeApi(lambda req, api, image=image: {"status": 200, "json": api.shot(image=image)} if req["method"] == "POST" else {"status": 200, "raw": PNG})
            code, _, err, files = self.run_cli(["https://example.com"], api)
            api.close()
            self.assertEqual(code, 1)
            self.assertIn(expected, err)
            self.assertEqual(files, [])

        moved = FakeApi(lambda req, api: {"status": 200, "json": api.shot()} if req["method"] == "POST" else {"status": 302, "headers": {"Location": "https://evil.example/x.png"}})
        self.assertIn("image_unavailable", self.run_cli(["https://example.com"], moved)[2])
        moved.close()

    def test_json_prints_the_final_document_and_still_writes_the_file(self):
        api = FakeApi(lambda req, api: {"status": 200, "json": api.shot()} if req["method"] == "POST" else {"status": 200, "raw": PNG})
        _, out, _, files = self.run_cli(["https://example.com", "--json"], api)
        api.close()
        printed = json.loads(out)
        self.assertEqual(printed["status"], "succeeded")
        self.assertEqual(os.path.basename(printed["file"]), "example.com-20261012-090807.png")
        self.assertNotIn(KEY, out)
        self.assertEqual(len(files), 1)

    def test_countries_needs_no_key_and_credits_does(self):
        def handler(req, api):
            if req["url"] == "/v1/countries":
                return {"status": 200, "json": {"count": 2, "countries": [{"code": "BR", "name": "Brazil"}, {"code": "DE", "name": "Germany"}]}}
            if req["url"] == "/v1/credits":
                return {"status": 200, "json": {"credits": {"remaining": 42}, "limits": {"requests_per_minute": 10, "concurrent_screenshots": 2}}}

        api = FakeApi(handler)
        os.environ["LOCALSCREENSHOT_API_KEY"] = ""
        code, out, err, _ = self.run_cli(["countries"], api)
        self.assertEqual((code, out), (0, "BR  Brazil\nDE  Germany"), err)
        self.assertNotIn("authorization", api.seen[0]["headers"])
        code, _, err, _ = self.run_cli(["credits"], api)
        self.assertEqual(code, 2)
        self.assertIn("No API key", err)
        os.environ["LOCALSCREENSHOT_API_KEY"] = KEY
        self.assertIn("42 credits left\n10 requests a minute, 2 screenshots at a time", self.run_cli(["credits"], api)[1])
        api.close()

    def test_login_checks_the_key_saves_it_for_the_owner_only_and_logout_removes_it(self):
        api = FakeApi(lambda req, api: {"status": 200, "json": {"credits": {"remaining": 50}}} if req["headers"].get("authorization") == f"Bearer {KEY}"
                      else {"status": 401, "json": {"error": {"code": "invalid_api_key", "message": f"The key {req['headers'].get('authorization', '')[7:]} is not valid."}}})
        os.environ["LOCALSCREENSHOT_API_KEY"] = ""
        file = Path(self.dir, "cfg", "localscreenshot", "config.json")
        code, _, err, _ = self.run_cli(["login"], api, read_secret=lambda: "lss_test_WRONGWRONGWRONG")
        self.assertEqual(code, 1)
        self.assertIn("invalid_api_key", err)
        # The API echoes the refused key in its message: it is masked all the same.
        self.assertNotIn("lss_test_WRONGWRONGWRONG", err)
        self.assertFalse(file.exists())

        code, out, err, _ = self.run_cli(["login"], api, read_secret=lambda: f" {KEY}\n")
        self.assertEqual(code, 0, err)
        self.assertNotIn(KEY, out + err)
        self.assertEqual(json.loads(file.read_text())["api_key"], KEY)
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(file.parent.stat().st_mode), 0o700)
        self.assertIn("50 credits left", self.run_cli(["credits"], api)[1])
        self.assertIn("Key removed", self.run_cli(["logout"], api)[1])
        api.close()
        self.assertFalse(file.exists())

    def test_usage_errors_exit_2_and_the_help_never_shows_a_key(self):
        self.assertEqual(self.run_cli([])[0], 2)
        self.assertEqual(self.run_cli(["--help"])[0], 0)
        self.assertNotIn("Bearer", HELP)
        self.assertIn("starts with https://", self.run_cli(["example.com"])[2])
        # A key typed where the address goes is not echoed back.
        os.environ["LOCALSCREENSHOT_API_KEY"] = ""
        self.assertNotIn(KEY, self.run_cli([KEY])[2])
        os.environ["LOCALSCREENSHOT_API_KEY"] = KEY
        self.assertIn("One page at a time", self.run_cli(["https://a.com", "https://b.com"])[2])
        code, _, err, _ = self.run_cli(["https://a.com", "--api-key", KEY])
        self.assertEqual(code, 2)
        self.assertIn("--help", err)
        self.assertNotIn(KEY, err)
        self.assertEqual(self.run_cli(["--version"])[1], localscreenshot.__version__)
        # The version the command prints is the one in the manifest, hence the one of the published tag.
        manifest = Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'\nversion = "{localscreenshot.__version__}"\n', manifest)

    def test_the_importable_function_returns_the_image_and_what_it_cost(self):
        api = FakeApi(lambda req, api: {"status": 200, "json": api.shot(country={"requested": "DE", "observed": "DE"})} if req["method"] == "POST" else {"status": 200, "raw": PNG})
        os.environ["LOCALSCREENSHOT_API_URL"] = f"{api.origin}/v1"
        os.environ["LOCALSCREENSHOT_API_KEY"] = ""
        shot = localscreenshot.screenshot("https://example.com", country="de", full_page=True, api_key=KEY)
        api.close()
        self.assertEqual((shot.image, shot.mime, shot.extension, shot.width, shot.height), (PNG, "image/png", "png", 1440, 900))
        self.assertEqual((shot.credits_charged, shot.credits_remaining, shot.country_observed), (1, 49, "DE"))
        self.assertEqual(api.seen[0]["body"], {"url": "https://example.com", "response": "json", "country": "DE", "full_page": True})
        self.assertNotIn(KEY, repr(shot))
        with self.assertRaises(localscreenshot.ApiError):
            localscreenshot.screenshot("https://example.com", api_key=KEY)  # server stopped


if __name__ == "__main__":
    unittest.main()
