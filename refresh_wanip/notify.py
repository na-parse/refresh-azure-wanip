'''ntfy.sh push notifications (optional; disabled when no topic is set).'''
import requests

from .config import NotifyConfig

# =============================================================================
# Constants
# =============================================================================
NTFY_SERVER = 'https://ntfy.sh'
TIMEOUT = 10


# =============================================================================
# Notifier
# =============================================================================
class Notifier:
    '''Publish messages to an ntfy.sh topic.'''

    def __init__(self, cfg: NotifyConfig) -> None:
        self.site = cfg.site
        self.url = f'{NTFY_SERVER}/{cfg.ntfy_topic}' if cfg.ntfy_topic else ''

    @property
    def enabled(self) -> bool:
        '''True when a topic is configured.'''
        return bool(self.url)

    def send(self, title: str, message: str, tags: str = '') -> bool:
        '''Publish one message; raise on HTTP or network errors.'''
        headers = {'Title': title, 'Tags': tags} if tags else {'Title': title}
        r = requests.post(self.url, data=message.encode(), headers=headers,
                          timeout=TIMEOUT)
        r.raise_for_status()
        return True
