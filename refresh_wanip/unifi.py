'''UniFi Network classic API: WAN MAC override and gateway WAN state.'''
import secrets
from typing import Any

import requests
import urllib3

from .config import UnifiConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================================================================
# Constants
# =============================================================================
GATEWAY_TYPES = ('udm', 'ugw', 'uxg')
TIMEOUT = 15


# =============================================================================
# MAC helpers
# =============================================================================
def random_mac(prefix: str, exclude: str) -> str:
    '''Return prefix + 3 random octets, never equal to exclude.'''
    while True:
        tail = ':'.join(f'{b:02x}' for b in secrets.token_bytes(3))
        mac = f'{prefix.lower()}:{tail}'
        if mac != exclude.lower():
            return mac


# =============================================================================
# UnifiClient
# =============================================================================
class UnifiClient:
    '''Minimal classic Network API client authenticated with X-API-KEY.'''

    def __init__(self, cfg: UnifiConfig) -> None:
        self.cfg = cfg
        self.base = f'https://{cfg.address}/proxy/network/api/s/{cfg.site}'
        self.session = requests.Session()
        self.session.headers.update({
            'X-API-KEY': cfg.api_key,
            'Accept': 'application/json',
        })
        self.session.verify = cfg.verify_tls

    def _data(self, method: str, path: str, **kwargs: Any) -> list[dict]:
        r = self.session.request(method, f'{self.base}{path}', timeout=TIMEOUT,
                                 **kwargs)
        r.raise_for_status()
        body = r.json()
        if body.get('meta', {}).get('rc') != 'ok':
            raise RuntimeError(f'{method} {path}: {body.get("meta")}')
        return body.get('data', [])

    def wan_conf(self) -> dict:
        '''Return the full networkconf object for the configured WAN.'''
        for net in self._data('GET', '/rest/networkconf'):
            if net.get('purpose') == 'wan' and net.get('name') == self.cfg.wan_network:
                return net
        raise LookupError(f'WAN network {self.cfg.wan_network!r} not found')

    def wan1(self) -> dict:
        '''Return the gateway's live wan1 state (ifname, mac, ip, up).'''
        for dev in self._data('GET', '/stat/device'):
            if dev.get('type') in GATEWAY_TYPES:
                return dev.get('wan1') or {}
        raise LookupError('No gateway found in stat/device')

    def set_mac(self, conf: dict, mac: str) -> None:
        '''Enable the MAC override on the WAN networkconf (full-object PUT).'''
        body = {**conf, 'mac_override_enabled': True, 'mac_override': mac}
        self._data('PUT', f'/rest/networkconf/{conf["_id"]}', json=body)
