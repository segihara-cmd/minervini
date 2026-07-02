"""Vercel serverless — Investing 목표가 + 네이버 현재가 괴리율 Top N."""

from __future__ import annotations

import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gap_pipeline"))

from pipeline.gap_dashboard import build_gap_payload  # noqa: E402

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
            payload = build_gap_payload(
                refresh_investing=True,
                investing_parallel=6,
            )
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
