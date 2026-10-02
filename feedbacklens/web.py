"""Small local web server. It keeps the Gemini key out of browser code."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .core import AnalysisError, analyze_batch, build_report, load_config, load_env, parse_reviews


ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / "static"
ROUTES = {
    "/": (ASSETS / "index.html", "text/html; charset=utf-8"),
    "/styles.css": (ASSETS / "styles.css", "text/css; charset=utf-8"),
    "/app.js": (ASSETS / "app.js", "text/javascript; charset=utf-8"),
}
MAX_REQUEST_BYTES = 2_000_000


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # Only these three files make up the browser interface.
        path = urlsplit(self.path).path
        if path in ROUTES:
            file_path, content_type = ROUTES[path]
            try:
                body = file_path.read_bytes()
            except OSError:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "UI asset unavailable"})
                return
            self._send(HTTPStatus.OK, body, content_type)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        # The browser sends CSV text here after the user chooses a file.
        if urlsplit(self.path).path != "/api/analyze":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Expected JSON request"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_REQUEST_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "CSV must be under 2 MB"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            csv_text = payload["csv"]
            if not isinstance(csv_text, str):
                raise ValueError("CSV must be text")
        except (ValueError, KeyError, TypeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Request must contain a CSV string"})
            return
        load_env()
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Set GEMINI_API_KEY before starting the dashboard"})
            return
        try:
            # Reuse the same analysis functions as the terminal command.
            config = load_config(ROOT / "config.json")
            prompt = (ROOT / "prompts/review_analysis.txt").read_text(encoding="utf-8").strip()
            reviews = parse_reviews(csv_text, int(config["max_review_chars"]))
            if len(reviews) > 100:
                raise AnalysisError("The dashboard accepts at most 100 reviews per run")
            results = analyze_batch(reviews, prompt, config, key, ROOT / "cache")
            report = build_report(results)
            # Save locally so the teacher can inspect the generated JSON files.
            output = ROOT / "results"
            output.mkdir(parents=True, exist_ok=True)
            (output / "analysis.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except AnalysisError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except OSError:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Could not save results"})
            return
        self._json(HTTPStatus.OK, {"results": results, "report": report})

    def _json(self, status: HTTPStatus, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = 8765) -> None:
    # Loopback means this dashboard is only available on this computer.
    load_env()
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"FeedbackLens dashboard: http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
