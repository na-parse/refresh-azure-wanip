'''ARRIS SB8200 (Comcast firmware) web UI client - read-only up time check.

The modem speaks legacy TLS, allows one session at a time and sets an HttpOnly
sessionId cookie that cookie jars won't send to a bare IP, so the cookie
header is set by hand. The first login after idle sometimes returns 401;
callers should retry.
'''
import base64
import re
import ssl
import urllib.request

from .config import ModemConfig

# =============================================================================
# Constants
# =============================================================================
TIMEOUT = 10
UPTIME_RE = re.compile(r'(\d+) days (\d+)h:(\d+)m:(\d+)s')


def _tls_context() -> ssl.SSLContext:
    ctx = ssl._create_unverified_context()
    ctx.set_ciphers('DEFAULT:@SECLEVEL=0')
    ctx.minimum_version = ssl.TLSVersion.TLSv1
    return ctx


# =============================================================================
# ModemClient
# =============================================================================
class ModemClient:
    '''Log in, read up time, log out.'''

    def __init__(self, cfg: ModemConfig) -> None:
        self.base = f'https://{cfg.address}/'
        self._auth = base64.b64encode(
            f'{cfg.username}:{cfg.password}'.encode()
        ).decode()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_tls_context())
        )

    def _open(self, path: str, headers: dict[str, str]) -> tuple[str, str]:
        req = urllib.request.Request(f'{self.base}{path}', headers=headers)
        with self._opener.open(req, timeout=TIMEOUT) as r:
            return r.read().decode(errors='replace'), r.headers.get('Set-Cookie', '')

    def _login(self) -> tuple[str, str]:
        body, cookie = self._open(
            f'cmconnectionstatus.html?login_{self._auth}',
            {'Authorization': f'Basic {self._auth}'},
        )
        token = body.strip()
        match = re.search(r'sessionId=([^;]*)', cookie)
        if len(token) < 10 or not match:
            raise RuntimeError('modem login failed')
        return token, match.group(1)

    def _logout(self, sid: str) -> None:
        try:
            self._open('logout.html', {'Cookie': f'sessionId={sid}'})
        except OSError:
            pass

    def uptime(self) -> int:
        '''Return modem up time in seconds.'''
        token, sid = self._login()
        try:
            body, _ = self._open(f'cmswinfo.html?ct_{token}',
                                 {'Cookie': f'sessionId={sid}'})
        finally:
            self._logout(sid)
        match = UPTIME_RE.search(body)
        if not match:
            raise RuntimeError('modem up time not found')
        d, h, m, s = map(int, match.groups())
        return ((d * 24 + h) * 60 + m) * 60 + s
