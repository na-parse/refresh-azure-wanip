# refresh-azure-wanip

Get a new public WAN IP for the Azure site on request. The service sets a new
WAN MAC on the UniFi gateway, then power-cycles the cable modem with a Shelly
smart plug. When the modem comes back, Comcast sees a new MAC and hands out a
new IP.

It's a tiny HTTP service, meant to be called a few times a year:

```sh
# curl (preferred, and the one to use in scripts)
curl -sS --fail-with-body --data 'token=XXXX' http://<host>:8780/reset
curl -sS http://<host>:8780/status

# wget (shorter to type by hand)
wget -qO- --content-on-error --post-data='token=XXXX' http://<host>:8780/reset
wget -qO- http://<host>:8780/status
```

Both print the JSON reply, including error bodies, and exit non-zero on
`4xx` / `5xx` (curl 22, wget 8):

- **curl:** `--data` sends a form POST. `--fail-with-body` needs curl 7.76+.
  Add `-w ' [%{http_code}]\n'` to also print the status code.
- **wget:** without `--content-on-error`, wget prints nothing on errors. That
  flag needs GNU wget 1.14+ (BusyBox wget doesn't have it).

## How a reset works

| # | Step | Notes |
|---|---|---|
| 1 | Preflight | Gateway API, modem web UI and plug must respond. Runs **before** the reply. If anything fails → `503` and no changes are made |
| 2 | `set_mac` | New MAC = `mac_prefix` + 3 random octets (never the current one). Waits until the gateway's `wan1` reports it. **The internet drops here** |
| 3 | `power_cycle` | Plug off; the plug's own timer turns it back on after `off_seconds` |
| 4 | `modem_reboot` | Confirms the modem's up time reset |
| 5 | `wan_up` | Gateway WAN is up with a public IP (ignores the modem's temporary `192.168.100.x` address) |

If step 2 or 3 fails, the old MAC is put back. Every call goes to a LAN
address (gateway, modem `192.168.100.1`, plug), so losing the internet
partway through doesn't affect the service.

Measured on 2026-10-19: MAC applied at +13s, modem rebooted at +98s, new
public IP at +139s.

## API

| Request | Responses |
|---|---|
| `POST /reset` with form data `token=<token>` | `202` started · `401` bad token · `409` already running · `429` cooldown (`Retry-After`) · `503` preflight failed |
| `GET /status` | `200` with the latest run as JSON |

Status body:

```json
{
  "state": "succeeded",
  "step": "done",
  "started": "2026-10-19T19:10:02-07:00",
  "finished": "2026-10-19T19:12:21-07:00",
  "old_mac": "f2:96:1a:9a:4a:33",
  "new_mac": "f2:96:1a:b7:cb:38",
  "wan_ip_changed": true,
  "error": null,
  "events": [{"t": 0.8, "msg": "set MAC f2:96:1a:9a:4a:33 -> f2:96:1a:b7:cb:38"}],
  "cooldown_remaining": 461
}
```

`state` is one of `idle`, `running`, `succeeded` or `failed`.

## Configuration

Copy `config.example.toml` to `config.toml` and run `chmod 600 config.toml`.
The service looks for its config in this order: `--config PATH`, then
`./config.toml`, then `/etc/refresh-azure-wanip/config.toml`.

| Section | Keys |
|---|---|
| `[server]` | `host`, `port` (8780), `token`, `cooldown_seconds` (600) |
| `[unifi]` | `address` (gateway IP), `api_key`, `site`, `wan_network`, `verify_tls` |
| `[modem]` | `address` (192.168.100.1), `username`, `password` |
| `[plug]` | `address`, `password` (user is always `admin`), `switch_id` |
| `[reset]` | `off_seconds` (15), `mac_prefix`, `mac_apply_timeout_seconds`, `recovery_timeout_seconds` |

`mac_prefix` must be a locally administered unicast prefix: the first octet's
second hex digit must be `2`, `6`, `a` or `e`. The IEEE never assigns these
addresses to hardware, so they can't clash with a real device.

## Running

Requires Python 3.11+ and `requests`.

```sh
uv run refresh-azure-wanip --check          # read-only preflight, then exit
uv run refresh-azure-wanip --reset          # run one reset here, then exit
uv run refresh-azure-wanip                  # serve on [server] host:port
uv run refresh-azure-wanip --config /path/to/config.toml
```

`--reset` runs the same steps as `POST /reset` without starting the server.
Progress is logged as it happens, the final status JSON is printed at the end,
and the exit code is `0` on success or `1` on failure (including preflight).
Notes:

- There is no cooldown between CLI runs, and no check for a reset already
  running in the service, so don't run both at once.
- Don't press Ctrl-C mid-reset: the old MAC won't be restored. The plug still
  turns back on by itself and the new MAC works, but the run isn't confirmed.

### systemd (service host)

```sh
sudo useradd --system --no-create-home refresh-wanip
sudo git clone <repo> /opt/refresh-azure-wanip
cd /opt/refresh-azure-wanip && sudo uv venv && sudo uv pip install .
sudo install -d -m 750 -o root -g refresh-wanip /etc/refresh-azure-wanip
sudo install -m 640 -o root -g refresh-wanip config.toml /etc/refresh-azure-wanip/
sudo cp deploy/refresh-azure-wanip.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now refresh-azure-wanip
journalctl -u refresh-azure-wanip -f
```

The host needs to reach: the gateway (TCP 443), the modem at `192.168.100.1`
(TCP 443, routed through the gateway) and the plug (TCP 80).

## Hardware notes

- **Modem:** ARRIS SB8200 (Comcast firmware). It can't be rebooted through its
  web UI or SNMP, which is why a smart plug is used. The web UI is only used to
  read the up time. It allows one session at a time and often rejects the first
  login with a 401, so logins are retried.
- **Plug:** Shelly Gen2+ (`toggle_after` timer; set `initial_state=on` and
  `auto_on` so the modem always comes back on).
- **Gateway:** UniFi classic Network API (`rest/networkconf`, `stat/device`)
  using an `X-API-KEY` with write access.
