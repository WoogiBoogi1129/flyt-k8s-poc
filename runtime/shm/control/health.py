"""Process liveness, recent API readiness, and bounded-cardinality metrics."""
import json
import threading
import time
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def log(event, level='INFO', **fields):
    levels={'DEBUG':10,'INFO':20,'WARNING':30,'ERROR':40}
    configured=os.getenv('FLYT_LOG_LEVEL','INFO').upper()
    if levels[level]>=levels.get(configured,20):
        print(json.dumps({'level':level,'event': event, **fields}, sort_keys=True), flush=True)


class Health:
    def __init__(self, stale_after=60):
        self.last_api = 0
        self.stale_after = stale_after
        self.stopping = False
        self.reconciles = 0
        self.errors = 0
        self.duration = 0.0

    def api_ok(self):
        self.last_api = time.monotonic()

    def ready(self):
        return not self.stopping and self.last_api > 0 and time.monotonic() - self.last_api < self.stale_after

    def serve(self, port):
        state = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path == '/metrics':
                    body = (f'flyt_reconcile_total {state.reconciles}\n'
                            f'flyt_reconcile_errors_total {state.errors}\n'
                            f'flyt_reconcile_duration_seconds_sum {state.duration}\n').encode()
                    code = 200
                elif self.path in ('/livez', '/readyz'):
                    ok = not state.stopping if self.path == '/livez' else state.ready()
                    code, body = (200, b'ok\n') if ok else (503, b'not ready\n')
                else:
                    code, body = 404, b'not found\n'
                self.send_response(code)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
