"""Vercel serverless — 관세청 API 실시간 반도체 분기 수출."""

from __future__ import annotations

import json
import sys
import traceback
from datetime import date
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nowcast_pipeline"))

from pipeline.export_dashboard import build_export_payload  # noqa: E402

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Content-Type": "application/json; charset=utf-8",
}


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        try:
            payload = build_export_payload(as_of=date.today(), use_cache=True)
            payload["_live"] = True
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            for k, v in CORS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            err = {"error": str(exc), "detail": traceback.format_exc()}
            body = json.dumps(err, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            for k, v in CORS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
