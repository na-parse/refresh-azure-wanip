'''Command line entry: refresh-azure-wanip [--config PATH] [--check | --reset].'''
import argparse
import json
import logging
import sys
import time

from .config import find_config, load_config
from .reset import POLL_SECONDS, ResetManager
from .server import serve


def main() -> None:
    '''Load config, then run the preflight (--check), a reset (--reset) or serve.'''
    parser = argparse.ArgumentParser(prog='refresh-azure-wanip')
    parser.add_argument('--config', help='path to config.toml')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true',
                      help='run the read-only preflight and exit')
    mode.add_argument('--reset', action='store_true',
                      help='run one reset in the foreground and exit')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    path = find_config(args.config)
    cfg = load_config(path)
    logging.getLogger(__name__).info('config %s', path)
    manager = ResetManager(cfg)

    if args.check:
        pre = manager.preflight()
        print(json.dumps({k: v for k, v in pre.items() if k != 'conf'}, indent=2))
        return
    if args.reset:
        code, body = manager.request()
        if code != 202:
            print(json.dumps(body, indent=2))
            sys.exit(1)
        while manager.status()['finished'] is None:
            time.sleep(POLL_SECONDS)
        final = manager.status()
        print(json.dumps(final, indent=2))
        sys.exit(0 if final['state'] == 'succeeded' else 1)
    serve(manager, cfg.server.host, cfg.server.port, cfg.server.token)


if __name__ == '__main__':
    main()
