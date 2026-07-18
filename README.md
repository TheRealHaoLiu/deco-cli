# deco-cli

A CLI for TP-Link **Deco** mesh routers that gives you the monitoring TP-Link
never shipped: system logs, metrics, per-device bandwidth, and new-device
detection — all over the local network, no cloud account required.

Deco routers famously expose almost nothing: the app is cloud-bound, the web UI
is minimal, and there is no official API. This tool drives the router's
undocumented local HTTP API (the same one its own web UI uses), reverse-
engineered from the JavaScript the router serves.

## Tested hardware

- **Deco BE85** (firmware 1.2.1) as master + slave
- **Deco WE10800** (firmware 1.3.1) slaves

It should work on any Deco whose local web UI lives at `/cgi-bin/luci/` (most
current models), since authentication and transport come from
[tplinkrouterc6u](https://github.com/AlexandrErohin/TP-Link-Archer-C6U)'s
`TPLinkDecoClient`. Endpoint availability varies by model — commands fail with
a clear "not supported by this model/firmware" message rather than a stack
trace. Reports of what works on other models are welcome.

## Install

```bash
git clone https://github.com/TheRealHaoLiu/deco-cli && cd deco-cli
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/deco status        # or activate the venv and just `deco`
```

## Authentication

The password is the one you use for the Deco **web UI** (usually the same as
the app password). It comes from the `--password` flag or the `DECO_PASSWORD`
environment variable — the CLI deliberately has no secret-manager integration,
so wire in whatever you use from the outside:

```bash
# 1Password CLI
DECO_PASSWORD=$(op item get TP-link --fields password --reveal) deco status

# pass
DECO_PASSWORD=$(pass show router/deco) deco status

# or a shell function in your rc file so plain `deco` just works:
deco() { DECO_PASSWORD="${DECO_PASSWORD:-$(op item get TP-link --fields password --reveal)}" command deco "$@"; }
```

The router IP comes from `--host` or `$DECO_HOST` (default `192.168.68.1`).

Note: Deco allows one admin session at a time — running the CLI logs out an
open web UI session and vice versa.

## Commands

| Command | What it does |
|---|---|
| `deco status` | Router overview: WAN/LAN IP, CPU, memory, client counts |
| `deco devices` | All connected devices (hostname, IP, MAC, band) |
| `deco clients <node-mac>` | Devices connected to one specific mesh node |
| `deco firmware` | Model / firmware / role / IP per mesh node |
| `deco mesh` | Topology tree with per-node wireless backhaul signal health |
| `deco upgrade-check` | Ask TP-Link's cloud whether firmware updates exist |
| `deco dns` | WAN DNS configuration |
| `deco dhcp` | LAN subnet, DNS handed out, and active IP↔MAC↔host leases |
| `deco wifi` | WiFi band status; `deco wifi-toggle host-5g off` to switch |
| `deco internet` | WAN health (exit code 1 when offline — script it) |
| `deco time` | Router clock, timezone, DST |
| `deco lookup <mac>` | Find a device or mesh node by MAC |
| `deco logs` | System log: `-n 500` tail, `--level error`, `--all`, `-f` follow |
| `deco collect` | Incremental log collector for cron/launchd (see below) |
| `deco metrics` | Snapshot: CPU, memory, WAN, nodes, bandwidth (`--prom`, `--json`) |
| `deco top` | Per-device bandwidth leaderboard; `--watch 5` for live view |
| `deco watch` | Detect never-before-seen devices and WAN IP changes |
| `deco reboot` | Reboot all mesh nodes (asks for confirmation) |

Every command takes `--json` for machine-readable output.

## Monitoring cookbook

**Why a collector?** The Deco keeps its log in a small ring buffer (~8,000
lines — under an hour at full verbosity on a busy network). Anything you don't
pull is gone. `deco collect` pulls the whole buffer, dedupes against the
previous run using a multi-line overlap match, and appends only new lines to
`~/.deco-logs/deco.log`. If a run comes too late it writes a `### gap` marker
instead of silently losing history.

Run it on a schedule from any always-on box on your LAN (a Proxmox LXC, a Pi,
a NAS — it just needs HTTP access to the router). Templates in `deploy/`:

- **Linux (systemd):** `deploy/deco-collect.service` + `deco-collect.timer` —
  10-minute timer, password via a root-owned `/etc/deco-cli.env`. Install
  steps are in the service file's comments.
- **Linux (cron):** `*/10 * * * * DECO_PASSWORD=... /opt/deco-cli/.venv/bin/deco collect`
- **macOS (launchd):** `deploy/com.deco.collect.plist`

**Prometheus / Grafana:**

```bash
deco metrics --prom --output /var/lib/node_exporter/textfile/deco.prom
```

Emits `deco_cpu_usage_ratio`, `deco_mem_usage_ratio`, `deco_wan_online`,
`deco_clients_total`, `deco_mesh_nodes_connected`, aggregate and per-client
bandwidth gauges (`deco_client_down_kbps{mac,name}`). The write is atomic, so
it is safe under the node_exporter textfile collector.

**New-device alerting:**

```bash
deco watch --notify    # macOS notification when an unknown MAC joins
```

First run records a baseline; later runs report only changes. State lives in
`~/.deco-logs/devices.json`.

**Live triage:**

```bash
deco top --watch 5     # who is eating the uplink right now
deco logs -f           # tail -f for your router
```

## How it works

The Deco web UI is a single-page app that talks to
`/cgi-bin/luci/;stok=<token>/admin/<module>?form=<name>` with AES/RSA-encrypted
JSON bodies. The router serves its UI config (`webpages/config/modules.json`)
and per-page model definitions unauthenticated, which is enough to enumerate
every endpoint the firmware supports. This tool combines those endpoints with
`tplinkrouterc6u`'s session/encryption handling.

Log retrieval, for the curious: `admin/log_export?form=feedback_log` with
`operation:"build"` assembles a snapshot server-side, then paged
`operation:"read"` calls (100 lines/page, oldest first) stream it back.

## Known limitations

- **No config backup/restore.** The web UI package contains a backup page, but
  on tested firmware the backing LuCI module (`admin/firmware` `form=config`)
  is not installed — the router literally answers "No root node was
  registered". Backup for Deco exists only inside TP-Link's app/cloud.
- **WiFi TX power** (`admin/wireless?form=power`) returns no band fields on
  the BE85; likely model-dependent.
- Log timestamps have 1-second resolution and the buffer is small; run the
  collector on a schedule if you care about history.
- Parental controls, QoS, and LED schedules are app/cloud-only and not
  reachable through the local API.
- **DHCP static reservations** cannot be read or set. `deco dhcp` shows the LAN
  subnet, DNS, and current leases, but the local API exposes no DHCP pool range
  and no reservation flags — reservations are managed only in the Deco app.

Unofficial project, not affiliated with TP-Link. Poking undocumented APIs is
at your own risk — everything here sticks to read operations plus the same
writes the web UI performs (WiFi toggle, reboot).
