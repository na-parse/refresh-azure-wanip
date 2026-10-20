'''Reset orchestration: preflight, new WAN MAC, modem power cycle, recovery.

Sequence (the internet drops at step 2 and returns after step 5):
  1. preflight     gateway API, modem UI and plug all respond (sync, pre-reply)
  2. set_mac       PUT new WAN MAC, wait until the gateway reports it
  3. power_cycle   plug off; the plug turns itself back on after off_seconds
  4. modem_reboot  modem up time lower than before
  5. wan_up        gateway wan1 up with a public IP on the new MAC
If step 2 or 3 fails, the old MAC is restored (all calls are LAN-only).
Optional ntfy.sh notifications go out before step 2 and when the run ends.
'''
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

from .config import Config
from .modem import ModemClient
from .notify import Notifier
from .plug import PlugClient
from .unifi import UnifiClient, random_mac

log = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================
MODEM_BOOTSTRAP_NET = '192.168.100.'  # modem DHCP before it reaches the ISP
NOTIFY_RETRY_SECONDS = 120  # final notice may wait for the internet to return
POLL_SECONDS = 3
PREFLIGHT_TRIES = 4


# =============================================================================
# Helpers
# =============================================================================
def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _wait(check: Callable[[], Any], timeout: float) -> Any:
    '''Poll check() until it returns truthy or timeout; errors count as false.'''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except Exception:
            pass
        time.sleep(POLL_SECONDS)
    return None


def _retry(fn: Callable[[], Any], tries: int = PREFLIGHT_TRIES) -> Any:
    '''Call fn, retrying on any error (modem first login often 401s).'''
    for attempt in range(tries):
        try:
            return fn()
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(POLL_SECONDS)


class ResetError(Exception):
    '''A reset step failed.'''


# =============================================================================
# ResetManager
# =============================================================================
class ResetManager:
    '''Runs one reset at a time and keeps the status of the latest run.'''

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.unifi = UnifiClient(cfg.unifi)
        self.modem = ModemClient(cfg.modem)
        self.plug = PlugClient(cfg.plug)
        self.notifier = Notifier(cfg.notify)
        self._lock = threading.Lock()
        self._running = False
        self._last_start: float | None = None
        self._t0 = 0.0
        self._run: dict[str, Any] = {'state': 'idle'}

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        '''Return the latest run record plus cooldown remaining.'''
        with self._lock:
            return {**self._run, 'cooldown_remaining': self._cooldown_remaining()}

    def request(self) -> tuple[int, dict[str, Any]]:
        '''Start a reset; return (HTTP status, body).'''
        with self._lock:
            if self._running:
                return 409, {'error': 'reset already running', **self._run}
            remaining = self._cooldown_remaining()
            if remaining:
                return 429, {'error': 'cooldown', 'retry_after': remaining}
            self._running = True

        try:
            pre = self.preflight()
        except Exception as exc:
            log.warning('preflight failed: %s', exc)
            with self._lock:
                self._running = False
            return 503, {'error': 'preflight failed', 'detail': str(exc)}

        with self._lock:
            self._last_start = time.monotonic()
            self._t0 = self._last_start
            self._run = {
                'state': 'running', 'step': 'set_mac', 'started': _now(),
                'finished': None, 'old_mac': pre['old_mac'], 'new_mac': None,
                'wan_ip_changed': None, 'error': None, 'events': [],
            }
        threading.Thread(target=self._execute, args=(pre,), daemon=True).start()
        return 202, self.status()

    def preflight(self) -> dict[str, Any]:
        '''Read-only check that gateway, modem and plug respond; raise on failure.'''
        try:
            conf = _retry(self.unifi.wan_conf)
            wan1 = _retry(self.unifi.wan1)
        except Exception as exc:
            raise ResetError(f'gateway API: {exc}') from exc
        try:
            uptime = _retry(self.modem.uptime)
        except Exception as exc:
            raise ResetError(f'modem UI: {exc}') from exc
        try:
            plug = _retry(self.plug.status)
        except Exception as exc:
            raise ResetError(f'plug: {exc}') from exc
        if not plug.get('output'):
            raise ResetError('plug: output is off')
        old_mac = (conf.get('mac_override') if conf.get('mac_override_enabled')
                   else wan1.get('mac')) or ''
        log.info('preflight ok: mac=%s modem_uptime=%ss', old_mac, uptime)
        return {'conf': conf, 'old_mac': old_mac.lower(), 'old_ip': wan1.get('ip'),
                'modem_uptime': uptime}

    # -------------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------------
    def _cooldown_remaining(self) -> int:
        if self._last_start is None:
            return 0
        elapsed = time.monotonic() - self._last_start
        return max(0, int(self.cfg.server.cooldown_seconds - elapsed))

    def _event(self, msg: str, **fields: Any) -> None:
        log.info('%s', msg)
        with self._lock:
            self._run['events'].append(
                {'t': round(time.monotonic() - self._t0, 1), 'msg': msg}
            )
            self._run.update(fields)

    def _wan1_mac_is(self, mac: str) -> bool:
        return (self.unifi.wan1().get('mac') or '').lower() == mac

    def _revert(self, pre: dict[str, Any]) -> None:
        old = pre['old_mac']
        try:
            self.unifi.set_mac(pre['conf'], old)
            applied = _wait(lambda: self._wan1_mac_is(old),
                            self.cfg.reset.mac_apply_timeout_seconds)
            self._event(f'reverted MAC to {old} (applied={bool(applied)})')
        except Exception as exc:
            self._event(f'REVERT FAILED: {exc}')

    def _notify(self, title: str, message: str, tags: str, retry: float) -> None:
        '''Send a notification if enabled; failures are logged, never raised.'''
        if not self.notifier.enabled:
            return
        if retry:
            sent = _wait(lambda: self.notifier.send(title, message, tags), retry)
        else:
            try:
                sent = self.notifier.send(title, message, tags)
            except Exception:
                sent = False
        if not sent:
            log.warning('notification not sent: %s', title)

    def _notify_result(self) -> None:
        with self._lock:
            run = dict(self._run)
        elapsed = round(time.monotonic() - self._t0)
        site = self.notifier.site
        if run['state'] == 'succeeded':
            changed = 'yes' if run['wan_ip_changed'] else 'no'
            self._notify('WAN IP refresh succeeded',
                         f'{site}: new MAC {run["new_mac"]}, WAN IP changed: '
                         f'{changed} ({elapsed}s)', 'white_check_mark',
                         NOTIFY_RETRY_SECONDS)
        else:
            self._notify('WAN IP refresh FAILED',
                         f'{site}: {run["error"]} ({elapsed}s)', 'x',
                         NOTIFY_RETRY_SECONDS)

    def _execute(self, pre: dict[str, Any]) -> None:
        rcfg = self.cfg.reset
        self._notify('WAN IP refresh started',
                     f'Initiated WAN IP refresh for {self.notifier.site}. Internet '
                     f'drops for ~2-3 min (old MAC {pre["old_mac"]})',
                     'arrows_counterclockwise', 0)
        try:
            new = random_mac(rcfg.mac_prefix, pre['old_mac'])
            self.unifi.set_mac(pre['conf'], new)
            self._event(f'set MAC {pre["old_mac"]} -> {new}', new_mac=new)
            if not _wait(lambda: self._wan1_mac_is(new),
                         rcfg.mac_apply_timeout_seconds):
                self._revert(pre)
                raise ResetError('gateway did not apply new MAC')
            self._event('gateway applied new MAC', step='power_cycle')

            try:
                self.plug.power_cycle(rcfg.off_seconds)
            except Exception as exc:
                self._revert(pre)
                raise ResetError(f'plug power cycle: {exc}') from exc
            self._event(f'plug off for {rcfg.off_seconds}s')
            if not _wait(lambda: self.plug.status().get('output'),
                         rcfg.off_seconds + 60):
                raise ResetError('plug did not turn back on')
            self._event('plug back on', step='modem_reboot')

            if not _wait(lambda: self.modem.uptime() < pre['modem_uptime'],
                         rcfg.recovery_timeout_seconds):
                raise ResetError('modem reboot not confirmed')
            self._event('modem rebooted', step='wan_up')

            wan1 = _wait(lambda: self._public_wan1(new),
                         rcfg.recovery_timeout_seconds)
            if not wan1:
                raise ResetError('WAN did not get a public IP')
            self._event('WAN up with public IP', state='succeeded', step='done',
                        wan_ip_changed=wan1.get('ip') != pre['old_ip'])
        except Exception as exc:
            log.error('reset failed: %s', exc)
            self._event(f'FAILED: {exc}', state='failed', error=str(exc))
        finally:
            self._notify_result()
            with self._lock:
                self._run['finished'] = _now()
                self._running = False

    def _public_wan1(self, mac: str) -> dict | None:
        wan1 = self.unifi.wan1()
        ip = wan1.get('ip') or ''
        ok = (wan1.get('up') and ip and not ip.startswith(MODEM_BOOTSTRAP_NET)
              and (wan1.get('mac') or '').lower() == mac)
        return wan1 if ok else None
