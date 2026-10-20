'''TOML configuration loading and validation.'''
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

# =============================================================================
# Constants
# =============================================================================
SEARCH_PATHS = (
    Path('config.toml'),
    Path('/etc/refresh-azure-wanip/config.toml'),
)


# =============================================================================
# Sections
# =============================================================================
@dataclass(frozen=True)
class ServerConfig:
    '''HTTP trigger API settings.'''
    token: str
    host: str = '0.0.0.0'
    port: int = 8780
    cooldown_seconds: int = 600


@dataclass(frozen=True)
class UnifiConfig:
    '''UniFi Network API (gateway) settings.'''
    address: str
    api_key: str
    wan_network: str
    site: str = 'default'
    verify_tls: bool = False


@dataclass(frozen=True)
class ModemConfig:
    '''Cable modem web UI settings.'''
    username: str
    password: str
    address: str = '192.168.100.1'


@dataclass(frozen=True)
class PlugConfig:
    '''Shelly smart plug settings (digest user is always admin).'''
    address: str
    password: str
    switch_id: int = 0


@dataclass(frozen=True)
class ResetConfig:
    '''Reset sequence tuning.'''
    off_seconds: int = 15
    mac_prefix: str = '02:00:00'
    mac_apply_timeout_seconds: int = 90
    recovery_timeout_seconds: int = 300


@dataclass(frozen=True)
class NotifyConfig:
    '''ntfy.sh notifications; disabled when ntfy_topic is empty.'''
    ntfy_topic: str = ''
    site: str = 'home'


@dataclass(frozen=True)
class Config:
    '''Complete service configuration.'''
    server: ServerConfig
    unifi: UnifiConfig
    modem: ModemConfig
    plug: PlugConfig
    reset: ResetConfig
    notify: NotifyConfig


# =============================================================================
# Loading
# =============================================================================
def find_config(path: str | None) -> Path:
    '''Return the explicit path, or the first existing default location.'''
    if path:
        return Path(path)
    for candidate in SEARCH_PATHS:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        'No config found; pass --config or create one of: '
        + ', '.join(str(p) for p in SEARCH_PATHS)
    )


def _section(cls: type, data: dict[str, Any], name: str) -> Any:
    '''Build a section dataclass, rejecting unknown keys.'''
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f'[{name}] unknown keys: {", ".join(sorted(unknown))}')
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f'[{name}] {exc}') from exc


def validate_mac_prefix(prefix: str) -> str:
    '''Return a normalized 3-octet prefix; must be locally administered unicast.'''
    octets = prefix.lower().split(':')
    if len(octets) != 3 or not all(len(o) == 2 for o in octets):
        raise ValueError(f'mac_prefix must be 3 octets (aa:bb:cc), got {prefix!r}')
    first = int(octets[0], 16)
    if not first & 0x02 or first & 0x01:
        raise ValueError(
            f'mac_prefix {prefix!r} must be locally administered unicast '
            '(first octet second hex digit 2, 6, a or e)'
        )
    return ':'.join(octets)


def load_config(path: Path) -> Config:
    '''Load and validate the TOML config file.'''
    with path.open('rb') as f:
        data = tomllib.load(f)
    config = Config(
        server=_section(ServerConfig, data.get('server', {}), 'server'),
        unifi=_section(UnifiConfig, data.get('unifi', {}), 'unifi'),
        modem=_section(ModemConfig, data.get('modem', {}), 'modem'),
        plug=_section(PlugConfig, data.get('plug', {}), 'plug'),
        reset=_section(ResetConfig, data.get('reset', {}), 'reset'),
        notify=_section(NotifyConfig, data.get('notify', {}), 'notify'),
    )
    validate_mac_prefix(config.reset.mac_prefix)
    if not config.server.token or config.server.token == 'change-me':
        raise ValueError('[server] token must be set')
    return config
