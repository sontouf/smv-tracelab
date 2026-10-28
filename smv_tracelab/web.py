"""HTTP server: serves the single-page UI and a small JSON API over one Session."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STATIC = Path(__file__).resolve().parent / 'static'


def make_handler(session):
    s = session

    post_routes = {
        '/api/load': lambda b: s.load(path=b.get('path'), source=b.get('source'), name=b.get('name')),
        '/api/restart': lambda b: s.restart(),
        '/api/pick_init': lambda b: s.pick_init(b.get('constraint', ''), bool(b.get('random'))),
        '/api/simulate': lambda b: s.simulate(b.get('steps', 1), b.get('c', ''), b.get('t', ''), bool(b.get('random'))),
        '/api/choose': lambda b: s.choose(b.get('index'), b.get('constraint')),
        '/api/goto': lambda b: s.goto(b.get('label')),
        '/api/view': lambda b: s.view(b.get('number')),
        '/api/check': lambda b: s.check(b.get('index')),
        '/api/check_formula': lambda b: s.check_formula(b.get('kind'), b.get('formula')),
        '/api/command': lambda b: s.raw(b.get('cmd', '')),
        '/api/grid/start': lambda b: s.grid_start(b),
        '/api/grid/stop': lambda b: s.grid_cancel(),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def _send(self, code, data, ctype):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path in ('/', '/index.html'):
                self._send(200, (STATIC / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            elif u.path == '/api/state':
                self._json(s.poll())
            elif u.path == '/api/log':
                start = max(0, int(q.get('from', 0) or 0) - s.log_base)
                self._json({'items': s.log[start:], 'len': s.log_base + len(s.log)})
            elif u.path == '/api/grid':
                self._json(s.grid_result())
            elif u.path == '/api/models':
                self._json({'root': str(s.root), 'models': s.list_models()})
            elif u.path == '/api/source':
                m = s.model
                src = Path(m['path']).read_text(encoding='utf-8', errors='replace') if m else ''
                self._json({'source': src})
            else:
                self.send_error(404)

        def do_POST(self):
            fn = post_routes.get(urlparse(self.path).path)
            if not fn:
                return self.send_error(404)
            n = int(self.headers.get('Content-Length') or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
            except ValueError:
                body = {}
            try:
                if s.grid_running and fn is not post_routes['/api/grid/stop']:
                    raise RuntimeError('그리드 시뮬레이션이 실행 중입니다. 끝나거나 중지한 뒤에 다시 시도하세요.')
                self._json(fn(body))
            except Exception as e:  # report every failure to the UI instead of dropping the request
                with s.lock:
                    s._log('err', str(e))
                    st = s.state()
                st['error'] = str(e)
                self._json(st, 400)

    return Handler


def serve(session, host, port):
    return ThreadingHTTPServer((host, port), make_handler(session))
