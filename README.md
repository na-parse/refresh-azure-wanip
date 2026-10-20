# refresh-azure-wanip

Get a new public WAN IP on request at a site with a UniFi gateway and a cable
modem. The service sets a new WAN MAC on the gateway, then power-cycles the
modem with a Shelly smart plug. When the modem comes back, the ISP sees a new
MAC and hands out a new IP.

"Azure" in the name is the author's site name; this has nothing to do with
Microsoft Azure.

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

If `[notify]` is set, an ntfy.sh notification goes out just before step 2
("WAN IP refresh started") and another when the run ends ("WAN IP refresh
succeeded" / "FAILED"). Titles are kept short because iOS cuts them off at
around 40 characters; the site name and details go in the message body. The
final one retries for up to 2 minutes in case the internet isn't back yet. A failed preflight (`503`) sends nothing, since no
reset starts. Notification failures are logged and never stop a reset.

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
  "old_mac": "02:00:00:9a:4a:33",
  "new_mac": "02:00:00:b7:cb:38",
  "wan_ip_changed": true,
  "error": null,
  "events": [{"t": 0.8, "msg": "set MAC 02:00:00:9a:4a:33 -> 02:00:00:b7:cb:38"}],
  "cooldown_remaining": 461
}
```

`state` is one of `idle`, `running`, `succeeded` or `failed`.

## Configuration

Copy `config.example.toml` to `config.toml` and run `chmod 600 config.toml`.
The service looks for its config in this order: `--config PATH`, then
`./config.toml`, then `/etc/refresh-azure-wanip/config.toml`. The systemd
service passes `--config /opt/refresh-azure-wanip/config.toml` (see
[Installing as a systemd service](#installing-as-a-systemd-service)).

| Section | Keys |
|---|---|
| `[server]` | `host`, `port` (8780), `token`, `cooldown_seconds` (600) |
| `[unifi]` | `address` (gateway hostname or IP), `api_key`, `site` (`default`), `wan_network` (required), `verify_tls` |
| `[modem]` | `address` (192.168.100.1), `username`, `password` |
| `[plug]` | `address`, `password` (user is always `admin`), `switch_id` |
| `[reset]` | `off_seconds` (15), `mac_prefix` (`02:00:00`), `mac_apply_timeout_seconds`, `recovery_timeout_seconds` |
| `[notify]` | Optional. `ntfy_topic` (empty = off), `site` (`home`, the name used in messages) |

`mac_prefix` must be a locally administered unicast prefix: the first octet's
second hex digit must be `2`, `6`, `a` or `e`. The IEEE never assigns these
addresses to hardware, so they can't clash with a real device. Pick your own
prefix rather than keeping the default.

For the gateway, use its hostname with `verify_tls = true` when it has a valid
certificate (e.g. Let's Encrypt), so the API key is only sent to the real
gateway. The name **must resolve through DNS on the LAN**: the internet drops
partway through a reset and the service keeps calling the gateway the whole
time. If no local DNS server holds the record, add it to the service host's
`/etc/hosts`. With an IP address or a self-signed certificate, set
`verify_tls = false`. An expired certificate fails the preflight (`503`), so
no reset starts.

`ntfy_topic` is a topic on the public https://ntfy.sh server. Anyone who
knows the name can read and post to it, so treat it like a password and keep
it out of the repo.

### Finding the UniFi values

All of these come from the UniFi Network web UI (written against Network 10.6
on a UDM Pro).

| Key | Where to find it |
|---|---|
| `address` | The host in the URL you use for the UniFi UI: `https://gw.example.com/network/default/settings/internet` → `gw.example.com` |
| `site` | The part of that URL after `/network/`: `.../network/default/settings/...` → `default` |
| `wan_network` | **Settings → Internet**, the **Name** column for the WAN to rotate (also the title of its settings panel when you click it). It must match exactly, including case |
| `api_key` | **Control Plane → Integrations → Create API Key** (UniFi Network 9.0+). Create it while signed in as an admin who can change network settings |

On the same **Settings → Internet** page:

- **The WAN must be on `WAN1`** (the **Interface** column). The service reads
  the gateway's WAN1 status, so a WAN on WAN2 isn't supported.
- **MAC Address Clone** (in the WAN's panel under **Advanced → Manual**) is
  the setting the service changes. You don't need to turn it on yourself: the
  first reset turns it on and fills it in, and after each reset it shows the
  new MAC. To go back to the gateway's own MAC, untick it and power-cycle the
  modem (the public IP will change again).

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

## Installing as a systemd service

These steps install the service on a Linux host (written against Debian 13) so
it starts at boot and restarts if it crashes. Everything lives in one folder:

| Path | What it is |
|---|---|
| `/opt/refresh-azure-wanip/` | The code, a git checkout |
| `/opt/refresh-azure-wanip/.venv/` | Python environment with the service and `requests` |
| `/opt/refresh-azure-wanip/config.toml` | Your settings and passwords. Gitignored, so `git pull` never touches it |
| `/etc/systemd/system/refresh-azure-wanip.service` | Tells systemd how to run it |

The service runs as its own user, `refresh-wanip`, which can read the code and
config but can't change anything.

### Before you start

- **Run every step as root.** Use `ssh root@<host>` or `sudo -i`. Don't put
  `sudo` in front of each command: `uv` is usually installed in your home
  folder (`~/.local/bin`), and `sudo` uses a fixed PATH that doesn't include
  it, so `sudo uv ...` fails with `command not found`.
- **Install `uv` for root** if `uv --version` doesn't work in the root shell.
  See [uv's install docs](https://docs.astral.sh/uv/getting-started/installation/).
- **Network access.** The host must reach the gateway (TCP 443), the modem at
  `192.168.100.1` (TCP 443, routed through the gateway) and the plug (TCP 80).

### 1. Create the service user

```sh
useradd --system --no-create-home --shell /usr/sbin/nologin refresh-wanip
```

A system account with no home folder and no login shell, so it can only be
used to run the service.

### 2. Get the code and install it

```sh
git clone https://github.com/na-parse/refresh-azure-wanip.git /opt/refresh-azure-wanip
cd /opt/refresh-azure-wanip
uv venv --no-managed-python
uv pip install .
```

`--no-managed-python` makes the environment use the system Python (e.g.
`/usr/bin/python3`). Without it, uv may download its own Python into
`/root/.local/share/uv/`, which the service user can't read, and the service
fails to start. Python 3.11+ is required (Debian 13 has 3.13).

### 3. Create the config

```sh
cp config.example.toml config.toml
nano config.toml
chown root:refresh-wanip config.toml
chmod 640 config.toml
```

Fill in every section (see [Configuration](#configuration) and
[Finding the UniFi values](#finding-the-unifi-values)). The owner and mode
let root edit the file and let the service user read it; nobody else can see
the passwords and API key in it.

### 4. Test the config as the service user

```sh
runuser -u refresh-wanip -- .venv/bin/refresh-azure-wanip --check \
    --config /opt/refresh-azure-wanip/config.toml
```

This runs the read-only preflight exactly as the service will. It confirms
that the service user can read the config and reach the gateway, modem and
plug. Fix anything it reports before going on.

### 5. Install and start the service

```sh
cp deploy/refresh-azure-wanip.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now refresh-azure-wanip
```

`daemon-reload` makes systemd read the new unit file. `enable --now` starts
the service and sets it to start at boot.

### 6. Check it's running

```sh
systemctl status refresh-azure-wanip
curl -sS http://localhost:8780/status
```

`status` should show `active (running)`, and `curl` should print the status
JSON with `"state": "idle"`.

### Day-to-day

| Task | Command (as root) |
|---|---|
| Watch the log | `journalctl -u refresh-azure-wanip -f` |
| Restart after editing `config.toml` | `systemctl restart refresh-azure-wanip` |
| Stop / start | `systemctl stop refresh-azure-wanip` / `systemctl start refresh-azure-wanip` |

To update to the latest code:

```sh
cd /opt/refresh-azure-wanip
git pull
uv pip install --reinstall-package refresh-azure-wanip .
systemctl restart refresh-azure-wanip
```

`--reinstall-package` is needed because the version number usually doesn't
change between commits, and uv would otherwise treat the old copy as
current. If `git pull` changed `deploy/refresh-azure-wanip.service`, copy it
again and run `systemctl daemon-reload` before restarting.

To remove it completely:

```sh
systemctl disable --now refresh-azure-wanip
rm /etc/systemd/system/refresh-azure-wanip.service
systemctl daemon-reload
rm -r /opt/refresh-azure-wanip
userdel refresh-wanip
```

## Hardware notes

- **Modem:** ARRIS SB8200 (Comcast firmware). It can't be rebooted through its
  web UI or SNMP, which is why a smart plug is used. The web UI is only used to
  read the up time. It allows one session at a time and often rejects the first
  login with a 401, so logins are retried.
- **Plug:** Shelly Gen2+ (`toggle_after` timer; set `initial_state=on` and
  `auto_on` so the modem always comes back on).
- **Gateway:** UniFi classic Network API (`rest/networkconf`, `stat/device`)
  using an `X-API-KEY` with write access.
