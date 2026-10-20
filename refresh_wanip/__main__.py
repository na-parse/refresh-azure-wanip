'''Command line entry: refresh-azure-wanip [--config PATH] [--check].'''
import argparse
import json
import logging
import sys

from .config import find_config, load_config
from .reset import ResetManager
from .server import serve


def main() -> None:
    '''Load config, then run the preflight (--check) or serve the API.'''
    parser = argparse.ArgumentParser(prog='refresh-azure-wanip')
    parser.add_argument('--config', help='path to config.toml')
    parser.add_argument('--check', action='store_true',
                        help='run the read-only preflight and exit')
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
    serve(manager, cfg.server.host, cfg.server.port, cfg.server.token)


if __name__ == '__main__':
    main()
