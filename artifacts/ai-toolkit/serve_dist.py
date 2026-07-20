#!/usr/bin/env python3
import os, sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = int(os.environ.get("PORT", 3000))
DIST = os.path.join(os.path.dirname(__file__), "dist", "public")

class SPAHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST, **kwargs)
    def do_GET(self):
        path = DIST + self.path.split("?")[0]
        if not os.path.exists(path) or os.path.isdir(path):
            self.path = "/index.html"
        super().do_GET()
    def log_message(self, *a): pass

print(f"[frontend] Serving {DIST} on port {PORT}", flush=True)
HTTPServer(("0.0.0.0", PORT), SPAHandler).serve_forever()
