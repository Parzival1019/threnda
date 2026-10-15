"""Manual check that the landing page's demo-request form really delivers.

Not part of the pytest suite (needs Playwright + a local Chrome):

    pip install playwright
    python landing/check_form.py

Serves ``landing/`` on a local port, injects a config pointing ``formEndpoint``
at a local receiver, submits the form in headless Chrome, and verifies the
receiver got every field. Also checks the receiver-error and unconfigured
paths. Exit code 0 = all good.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from playwright.sync_api import Page, sync_playwright

SITE = Path(__file__).resolve().parent
received: list[dict[str, list[str]]] = []
receiver_status = {"code": 200}


class Receiver(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Accept")

    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server API
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        ctype = self.headers.get("content-type", "")
        fields: dict[str, list[str]] = {}
        if "multipart/form-data" in ctype:
            boundary = ctype.split("boundary=")[1].encode()
            for part in body.split(b"--" + boundary):
                if b'name="' in part:
                    header, _, value = part.partition(b"\r\n\r\n")
                    name = header.split(b'name="')[1].split(b'"')[0].decode()
                    fields.setdefault(name, []).append(value.rstrip(b"\r\n").decode())
        else:
            fields = parse_qs(body.decode())
        received.append(fields)
        self.send_response(receiver_status["code"])
        self._cors()
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())

    def log_message(self, *args: object) -> None:
        pass


class Site(SimpleHTTPRequestHandler):
    endpoint = ""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(SITE), **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.startswith("/config.js"):
            payload = (
                "window.THRENDA_SITE_CONFIG = "
                + json.dumps({"formEndpoint": Site.endpoint, "contactEmail": ""})
                + ";"
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/javascript")
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def log_message(self, *args: object) -> None:
        pass


def serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def fill(page: Page) -> None:
    page.fill("#f-name", "Teszt Elek")
    page.fill("#f-email", "teszt@example.com")
    page.fill("#f-company", "Fiktív Kft.")
    page.select_option("#f-size", "6–20 fő")
    page.fill("#f-msg", "Demót kérünk.")
    page.check("input[name=consent]")


def main() -> int:
    receiver = serve(Receiver)
    site = serve(Site)
    base = f"http://127.0.0.1:{site.server_address[1]}"
    failures: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()

        Site.endpoint = f"http://127.0.0.1:{receiver.server_address[1]}/submit"
        page.goto(base + "/index.html")
        page.click("#demo-form button[type=submit]")
        if "Kérjük" not in page.inner_text("#form-status"):
            failures.append("empty form was not rejected client-side")
        fill(page)
        page.click("#demo-form button[type=submit]")
        page.wait_for_function("document.getElementById('form-status').textContent.length > 0")
        status = page.inner_text("#form-status")
        if "Megkaptuk" not in status:
            failures.append(f"success message missing: {status!r}")
        expected = {"name", "email", "company", "team_size", "message", "consent"}
        if not received or set(received[-1]) != expected:
            failures.append(f"receiver got {received[-1] if received else None!r}")

        receiver_status["code"] = 500
        page.goto(base + "/index.html")
        fill(page)
        page.click("#demo-form button[type=submit]")
        page.wait_for_function("document.getElementById('form-status').textContent.length > 0")
        if "nem sikerült" not in page.inner_text("#form-status"):
            failures.append("receiver error was not shown to the visitor")

        Site.endpoint = ""
        page.goto(base + "/index.html")
        fill(page)
        page.click("#demo-form button[type=submit]")
        if "nincs beállítva" not in page.inner_text("#form-status"):
            failures.append("unconfigured form did not say so")
        browser.close()

    for failure in failures:
        print("FAIL:", failure)
    print("OK — form delivery verified against the local receiver" if not failures else "")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
