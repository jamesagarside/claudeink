"""Tiny status web server for claudeink.

Serves the most recently rendered frame and usage rows, and lets the
browser force an immediate refresh. Stdlib only, so nothing new to
install on the Pi.

Endpoints:
  GET  /           status page
  GET  /frame.png  latest rendered frame
  GET  /status     JSON: rows, updated timestamp, stale flag
  POST /refresh    wake the main loop for an immediate fetch + render
"""

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    """Shared between the main render loop and the request handlers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._png = b""
        self._rows = []
        self._updated = None
        self._stale = False
        self.refresh_event = threading.Event()

    def update(self, png, rows, stale):
        payload = [
            {
                "label": label,
                "percent": pct,
                "resets_at": reset.isoformat() if reset else None,
                "weekly": weekly,
            }
            for label, pct, reset, weekly in rows
        ]
        with self._lock:
            self._png = png
            self._rows = payload
            self._updated = datetime.now().astimezone()
            self._stale = stale

    def png(self):
        with self._lock:
            return self._png

    def status(self):
        with self._lock:
            return {
                "updated": self._updated.isoformat() if self._updated else None,
                "stale": self._stale,
                "rows": self._rows,
            }


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>claudeink</title>
<style>
  body { background: #14161a; color: #e8e8e8; font: 16px/1.5 system-ui, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; margin: 0; padding: 24px 16px; box-sizing: border-box; }
  h1 { font-size: 20px; font-weight: 600; margin: 0 0 16px; }
  img { width: 100%; max-width: 500px; image-rendering: pixelated;
        border: 1px solid #333; border-radius: 6px; background: #fff; }
  #meta { color: #9a9a9a; font-size: 13px; margin: 12px 0 20px; min-height: 1.5em; }
  button { background: #e8e8e8; color: #14161a; border: 0; border-radius: 6px;
           padding: 10px 22px; font: inherit; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: wait; }
</style>
</head>
<body>
<h1>claudeink</h1>
<img id="frame" src="/frame.png" alt="current panel frame">
<div id="meta">loading&hellip;</div>
<button id="btn">Refresh now</button>
<script>
const meta = document.getElementById("meta");
const frame = document.getElementById("frame");
const btn = document.getElementById("btn");
let lastUpdated = null;

async function poll() {
  try {
    const s = await (await fetch("/status")).json();
    if (s.updated && s.updated !== lastUpdated) {
      lastUpdated = s.updated;
      frame.src = "/frame.png?t=" + encodeURIComponent(s.updated);
    }
    const when = s.updated ? new Date(s.updated).toLocaleTimeString() : "never";
    meta.textContent = "updated " + when + (s.stale ? " (stale data)" : "");
  } catch (e) {
    meta.textContent = "unreachable";
  }
}

btn.addEventListener("click", async () => {
  btn.disabled = true;
  const before = lastUpdated;
  try { await fetch("/refresh", { method: "POST" }); } catch (e) {}
  // e-paper refresh takes a few seconds; poll until a new frame lands
  for (let i = 0; i < 20 && lastUpdated === before; i++) {
    await new Promise(r => setTimeout(r, 1000));
    await poll();
  }
  btn.disabled = false;
});

poll();
setInterval(poll, 15000);
</script>
</body>
</html>
"""


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif path == "/frame.png":
                png = state.png()
                if png:
                    self._send(200, "image/png", png)
                else:
                    self._send(503, "text/plain", b"no frame rendered yet\n")
            elif path == "/status":
                self._send(200, "application/json", json.dumps(state.status()).encode())
            else:
                self._send(404, "text/plain", b"not found\n")

        def do_POST(self):
            if self.path.split("?")[0] == "/refresh":
                state.refresh_event.set()
                self._send(202, "application/json", b'{"refreshing": true}\n')
            else:
                self._send(404, "text/plain", b"not found\n")

        def log_message(self, fmt, *args):
            pass  # keep journald to the render loop's own log lines

    return Handler


def start(port):
    """Run the server on a daemon thread; returns the shared State."""
    state = State()
    server = ThreadingHTTPServer(("", port), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return state
