#!/usr/bin/env python3
"""Quick connectivity test for a TP-Link Deco router.

Uses the same host/password resolution as the CLI:
$DECO_HOST (default 192.168.68.1) and $DECO_PASSWORD.
"""

import sys
from argparse import Namespace

from deco_cli import DECO_HOST_DEFAULT, deco_client
import os

args = Namespace(
    host=os.environ.get("DECO_HOST", DECO_HOST_DEFAULT),
    password=None,
)

with deco_client(args) as client:
    print("Connected to Deco successfully!\n")
    status = client.get_status()
    print(f"WAN IP:       {status.wan_ipv4_addr}")
    print(f"LAN IP:       {status.lan_ipv4_addr}")
    print(f"CPU usage:    {status.cpu_usage}%")
    print(f"Memory usage: {status.mem_usage}%")
    print(f"Devices:      {status.clients_total} "
          f"(wifi: {status.wifi_clients_total}, wired: {status.wired_total})")
    print()
    for dev in sorted(status.devices, key=lambda d: d.hostname or ""):
        name = dev.hostname or "(unnamed)"
        ip = dev.ipaddr or "no IP"
        conn = dev.type.value if dev.type else "?"
        print(f"  {name:30s}  {ip:16s}  {dev.macaddr:20s}  {conn}")
