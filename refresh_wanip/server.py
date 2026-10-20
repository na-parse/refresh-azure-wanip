'''Minimal HTTP trigger API: POST /reset, GET /status.'''
import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

from .reset import ResetManager

log = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================
MAX_BODY = 4096
ROUTES = {'/reset': 'POST', '/status': 'GET'}


# =============================================================================
# Handler
# =============================================================================
class Handler(BaseHTTPRequestHandler):
    '''Routes requests to the shared ResetManager.'''

    manager: ResetManager
    token: str

    def _send(self, code: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, indent=2).encode() + b'\n'
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        if code == 429:
            self.send_header('Retry-After', str(body.get('retry_after', 0)))
        self.end_headers()
        self.wfile.write(data)

    def _route(self, method: str) -> bool:
        path = self.path.split('?', 1)[0]
        if path not in ROUTES:
            self._send(404, {'error': 'not found'})
            return False
        if ROUTES[path] != method:
            self._send(405, {'error': f'use {ROUTES[path]}'})
            return False
        return True

    def do_GET(self) -> None:
        '''GET /status'''
        if self._route('GET'):
            self._send(200, self.manager.status())

    def do_POST(self) -> None:
        '''POST /reset with form data token=<token>'''
        if not self._route('POST'):
            return
        length = min(int(self.headers.get('Content-Length') or 0), MAX_BODY)
        form = parse_qs(self.rfile.read(length).decode(errors='replace'))
        supplied = (form.get('token') or [''])[0]
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            log.warning('reset rejected: bad token from %s', self.client_address[0])
            self._send(401, {'error': 'unauthorized'})
            return
        log.info('reset requested by %s', self.client_address[0])
        self._send(*self.manager.request())

    def log_message(self, format: str, *args: Any) -> None:
        log.info('%s %s', self.client_address[0], format % args)


# =============================================================================
# Entry
# =============================================================================
def serve(manager: ResetManager, host: str, port: int, token: str) -> None:
    '''Serve forever on host:port.'''
    Handler.manager = manager
    Handler.token = token
    with ThreadingHTTPServer((host, port), Handler) as httpd:
        log.info('listening on %s:%s', host, port)
        httpd.serve_forever()
