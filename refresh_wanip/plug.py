'''Shelly Gen2+ smart plug RPC client (digest auth, user is always admin).'''
from typing import Any

import requests
from requests.auth import HTTPDigestAuth

from .config import PlugConfig

# =============================================================================
# Constants
# =============================================================================
TIMEOUT = 5
SHELLY_USER = 'admin'


# =============================================================================
# PlugClient
# =============================================================================
class PlugClient:
    '''Switch status and timed power cycle.'''

    def __init__(self, cfg: PlugConfig) -> None:
        self.base = f'http://{cfg.address}/rpc'
        self.switch_id = cfg.switch_id
        self.auth = HTTPDigestAuth(SHELLY_USER, cfg.password)

    def _rpc(self, method: str, **params: Any) -> dict:
        r = requests.get(f'{self.base}/{method}', auth=self.auth, timeout=TIMEOUT,
                         params={'id': self.switch_id, **params})
        r.raise_for_status()
        return r.json()

    def status(self) -> dict:
        '''Return Switch.GetStatus (output, source, apower, ...).'''
        return self._rpc('Switch.GetStatus')

    def power_cycle(self, off_seconds: int) -> None:
        '''Turn off now; the plug turns itself back on after off_seconds.'''
        self._rpc('Switch.Set', on='false', toggle_after=off_seconds)
