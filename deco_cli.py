#!/usr/bin/env python3
"""CLI for querying TP-Link Deco mesh routers."""

import argparse
import json
import os
import subprocess
import sys
import time
from base64 import b64decode
from contextlib import contextmanager
from datetime import datetime, timezone

from tplinkrouterc6u import TPLinkDecoClient
from tplinkrouterc6u.common.package_enum import Connection

DECO_HOST_DEFAULT = "192.168.68.1"

WIFI_BAND_MAP = {
    "host-2g": Connection.HOST_2G,
    "host-5g": Connection.HOST_5G,
    "host-6g": Connection.HOST_6G,
    "guest-2g": Connection.GUEST_2G,
    "guest-5g": Connection.GUEST_5G,
    "guest-6g": Connection.GUEST_6G,
}

# Values from admin/log_export?form=types
LOG_LEVEL_MAP = {
    "all": 8,
    "alert": 1,
    "critical": 2,
    "error": 3,
    "warning": 4,
    "notice": 5,
    "info": 6,
    "debug": 7,
}

LOG_ENDPOINT = "admin/log_export?form=feedback_log"
LOG_PAGE_SIZE = 100


def get_password(args):
    if args.password:
        return args.password
    env_pw = os.environ.get("DECO_PASSWORD")
    if env_pw:
        return env_pw
    print("Error: no password provided. Use --password or the DECO_PASSWORD "
          "environment variable.", file=sys.stderr)
    sys.exit(1)


@contextmanager
def deco_client(args):
    password = get_password(args)
    client = TPLinkDecoClient(args.host, password)
    try:
        client.authorize()
    except Exception as e:
        print(f"Error: Failed to connect to {args.host}: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        yield client
    finally:
        try:
            client.logout()
        except Exception:
            pass


def cmd_status(args):
    with deco_client(args) as client:
        status = client.get_status()
        data = {
            "wan_ip": status.wan_ipv4_addr,
            "lan_ip": status.lan_ipv4_addr,
            "wan_gateway": status.wan_ipv4_gateway,
            "connection_type": status.conn_type,
            "cpu_usage": status.cpu_usage,
            "mem_usage": status.mem_usage,
            "clients_total": status.clients_total,
            "wifi_clients": status.wifi_clients_total,
            "wired_clients": status.wired_total,
            "guest_clients": status.guest_clients_total,
            "iot_clients": status.iot_clients_total,
        }
        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            print(f"WAN IP:       {data['wan_ip']}")
            print(f"LAN IP:       {data['lan_ip']}")
            print(f"Gateway:      {data['wan_gateway']}")
            print(f"Connection:   {data['connection_type']}")
            print(f"CPU usage:    {data['cpu_usage']}%")
            print(f"Memory usage: {data['mem_usage']}%")
            print(f"Devices:      {data['clients_total']} "
                  f"(wifi: {data['wifi_clients']}, "
                  f"wired: {data['wired_clients']}, "
                  f"guest: {data['guest_clients']}, "
                  f"iot: {data['iot_clients']})")


def cmd_devices(args):
    with deco_client(args) as client:
        status = client.get_status()
        devices = []
        for dev in sorted(status.devices, key=lambda d: d.hostname or ""):
            devices.append({
                "hostname": dev.hostname or "(unnamed)",
                "ip": dev.ipaddr or "no IP",
                "mac": dev.macaddr,
                "connection": dev.type.value if dev.type else "unknown",
                "down_speed": dev.down_speed,
                "up_speed": dev.up_speed,
            })
        if args.json:
            print(json.dumps(devices, indent=2, default=str))
        else:
            print(f"{'Hostname':30s}  {'IP':16s}  {'MAC':20s}  {'Connection'}")
            print("-" * 82)
            for d in devices:
                print(f"{d['hostname']:30s}  {d['ip']:16s}  {d['mac']:20s}  {d['connection']}")
            print(f"\nTotal: {len(devices)} devices")


def cmd_firmware(args):
    with deco_client(args) as client:
        client.get_firmware()
        nodes = []
        for item in client.devices:
            nodes.append({
                "model": item.get("device_model", ""),
                "hardware": item.get("hardware_ver", ""),
                "software": item.get("software_ver", ""),
                "role": item.get("role", ""),
                "mac": item.get("mac", ""),
            })
        if args.json:
            print(json.dumps(nodes, indent=2, default=str))
        else:
            for n in nodes:
                role_tag = f" ({n['role']})" if n['role'] else ""
                print(f"{n['model']}{role_tag}")
                print(f"  Hardware: {n['hardware']}")
                print(f"  Software: {n['software']}")
                print(f"  MAC:      {n['mac']}")
                print()


def cmd_clients(args):
    with deco_client(args) as client:
        client.get_firmware()
        node_mac = _normalize_mac(args.node_mac)
        node_info = None
        for item in client.devices:
            if _normalize_mac(item.get("mac", "")) == node_mac:
                node_info = item
                break
        if not node_info:
            if args.json:
                print(json.dumps({"error": "mesh node not found", "mac": args.node_mac}))
            else:
                print(f"No mesh node found with MAC {args.node_mac}", file=sys.stderr)
            sys.exit(1)

        data = client.request("admin/client?form=client_list", json.dumps(
            {"operation": "read", "params": {"device_mac": node_info.get("mac", "")}}))
        devices = []
        for cl in data.get("client_list", []):
            if not cl.get("online"):
                continue
            try:
                name = b64decode(cl["name"]).decode()
            except Exception:
                name = cl.get("name", "(unnamed)")
            devices.append({
                "hostname": name,
                "ip": cl.get("ip", "no IP"),
                "mac": cl.get("mac", ""),
                "connection": cl.get("connection_type", "unknown"),
                "wire_type": cl.get("wire_type", ""),
            })
        devices.sort(key=lambda d: d["hostname"])

        if args.json:
            print(json.dumps({
                "node": {
                    "model": node_info.get("device_model", ""),
                    "role": node_info.get("role", ""),
                    "mac": node_info.get("mac", ""),
                },
                "clients": devices,
            }, indent=2, default=str))
        else:
            model = node_info.get("device_model", "")
            role = node_info.get("role", "")
            print(f"Clients connected to {model} ({role}) [{node_info.get('mac', '')}]:\n")
            print(f"{'Hostname':30s}  {'IP':16s}  {'MAC':20s}  {'Connection'}")
            print("-" * 82)
            for d in devices:
                print(f"{d['hostname']:30s}  {d['ip']:16s}  {d['mac']:20s}  {d['connection']}")
            print(f"\nTotal: {len(devices)} clients")


def cmd_dns(args):
    with deco_client(args) as client:
        ipv4 = client.get_ipv4_status()
        data = {
            "primary_dns": str(ipv4.wan_ipv4_pridns),
            "secondary_dns": str(ipv4.wan_ipv4_snddns),
            "wan_ip": str(ipv4.wan_ipv4_ipaddr),
            "wan_gateway": str(ipv4.wan_ipv4_gateway),
            "wan_netmask": str(ipv4.wan_ipv4_netmask),
            "connection_type": ipv4.wan_ipv4_conntype,
        }
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"Primary DNS:   {data['primary_dns']}")
            print(f"Secondary DNS: {data['secondary_dns']}")
            print(f"WAN IP:        {data['wan_ip']}")
            print(f"Gateway:       {data['wan_gateway']}")
            print(f"Netmask:       {data['wan_netmask']}")
            print(f"Conn type:     {data['connection_type']}")


def _netmask_to_cidr(mask):
    try:
        return sum(bin(int(o)).count("1") for o in mask.split("."))
    except (ValueError, AttributeError):
        return None


def _ip_sort_key(ip):
    try:
        return tuple(int(o) for o in ip.split("."))
    except (ValueError, AttributeError):
        return (999, 999, 999, 999)


def cmd_dhcp(args):
    with deco_client(args) as client:
        lan = client.request("admin/network?form=lan_ip", json.dumps(
            {"operation": "read", "params": {"device_mac": "default"}}))
        clients = _online_clients(client)

    lan_ip = lan.get("lan_ip", {})
    router_ip = lan_ip.get("ip", "")
    mask = lan_ip.get("mask", "")
    cidr = _netmask_to_cidr(mask)
    dns = [d for d in lan.get("dns_server_ip", []) if d]

    leases = sorted(
        ({"hostname": c["hostname"], "ip": c["ip"], "mac": c["mac"],
          "connection": c["connection"], "wire_type": c["wire_type"]}
         for c in clients),
        key=lambda c: _ip_sort_key(c["ip"]),
    )
    result = {
        "router_ip": router_ip,
        "netmask": mask,
        "cidr": f"{router_ip}/{cidr}" if cidr is not None else None,
        "dns_servers": dns,
        "leases": leases,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Router IP:    {router_ip}"
              + (f"/{cidr}" if cidr is not None else ""))
        print(f"Netmask:      {mask}")
        print(f"DNS handed out: {', '.join(dns) if dns else '(none)'}")
        print()
        print(f"{'IP':16s}  {'MAC':20s}  {'Type':9s}  {'Hostname'}")
        print("-" * 78)
        for lease in leases:
            print(f"{lease['ip']:16s}  {lease['mac']:20s}  "
                  f"{lease['connection']:9s}  {lease['hostname']}")
        print(f"\nTotal: {len(leases)} active leases")
        print("\nNote: the Deco local API does not expose the DHCP pool range or "
              "flag which\nleases are static reservations - those live only in "
              "the Deco app.")


def cmd_wifi(args):
    with deco_client(args) as client:
        status = client.get_status()
        bands = {
            "host_2g": status.wifi_2g_enable,
            "host_5g": status.wifi_5g_enable,
            "host_6g": status.wifi_6g_enable,
            "guest_2g": status.guest_2g_enable,
            "guest_5g": status.guest_5g_enable,
            "guest_6g": status.guest_6g_enable,
        }
        if args.json:
            print(json.dumps(bands, indent=2))
        else:
            for band, enabled in bands.items():
                label = band.replace("_", " ").title()
                state = "enabled" if enabled else ("disabled" if enabled is False else "n/a")
                print(f"  {label:16s}  {state}")


def cmd_wifi_toggle(args):
    band_conn = WIFI_BAND_MAP.get(args.band)
    enable = args.state == "on"
    with deco_client(args) as client:
        client.set_wifi(band_conn, enable)
        state_str = "on" if enable else "off"
        if args.json:
            print(json.dumps({"band": args.band, "state": state_str}))
        else:
            print(f"WiFi {args.band} turned {state_str}")


def _normalize_mac(mac):
    return mac.upper().replace(":", "-").replace(".", "-")


def cmd_lookup(args):
    query = _normalize_mac(args.mac)
    with deco_client(args) as client:
        status = client.get_status()
        for dev in status.devices:
            if _normalize_mac(dev.macaddr) == query:
                result = {
                    "type": "client",
                    "hostname": dev.hostname or "(unnamed)",
                    "ip": dev.ipaddr or "no IP",
                    "mac": dev.macaddr,
                    "connection": dev.type.value if dev.type else "unknown",
                    "down_speed": dev.down_speed,
                    "up_speed": dev.up_speed,
                }
                if args.json:
                    print(json.dumps(result, indent=2, default=str))
                else:
                    print(f"{result['hostname']}  ({result['type']})")
                    print(f"  IP:         {result['ip']}")
                    print(f"  MAC:        {result['mac']}")
                    print(f"  Connection: {result['connection']}")
                return

        client.get_firmware()
        for item in client.devices:
            if _normalize_mac(item.get("mac", "")) == query:
                result = {
                    "type": "mesh_node",
                    "model": item.get("device_model", ""),
                    "role": item.get("role", ""),
                    "hardware": item.get("hardware_ver", ""),
                    "software": item.get("software_ver", ""),
                    "mac": item.get("mac", ""),
                }
                if args.json:
                    print(json.dumps(result, indent=2, default=str))
                else:
                    print(f"{result['model']}  ({result['type']}, {result['role']})")
                    print(f"  Hardware: {result['hardware']}")
                    print(f"  Software: {result['software']}")
                    print(f"  MAC:      {result['mac']}")
                return

        if args.json:
            print(json.dumps({"error": "not found", "mac": args.mac}))
        else:
            print(f"No device found with MAC {args.mac}", file=sys.stderr)
        sys.exit(1)


# The router builds a log snapshot server-side, then serves it in pages of
# 100 lines, oldest first (totalNum = page count, fixed until the next build).
def _log_build(client, level):
    client.request(LOG_ENDPOINT, json.dumps(
        {"operation": "build", "params": {"level": LOG_LEVEL_MAP[level]}}))


def _log_read_page(client, index):
    data = client.request(LOG_ENDPOINT, json.dumps(
        {"operation": "read", "params": {"index": index, "limit": LOG_PAGE_SIZE}}))
    return data.get("totalNum", 0), [e["content"] for e in data.get("logList", [])]


def _log_fetch_all(client):
    total, lines = _log_read_page(client, 0)
    for idx in range(1, total):
        lines.extend(_log_read_page(client, idx)[1])
    return lines


def _log_fetch_tail(client, count):
    total, first_page = _log_read_page(client, 0)
    if total <= 1:
        return first_page[-count:]
    lines = []
    idx = total - 1
    while idx > 0 and len(lines) < count:
        lines = _log_read_page(client, idx)[1] + lines
        idx -= 1
    if len(lines) < count:
        lines = first_page + lines
    return lines[-count:]


def _split_new_lines(prev_tail, lines):
    """Locate prev_tail (the previous snapshot's final lines) inside lines and
    return everything after it. Single lines repeat legitimately, so match a
    multi-line window; retry with shorter suffixes in case the oldest needle
    lines rotated out of the ring buffer. None means no overlap at all."""
    if not prev_tail:
        return lines
    for k in (len(prev_tail), 100, 50, 20):
        needle = prev_tail[-k:]
        n = len(needle)
        if n > len(lines):
            continue
        for i in range(len(lines) - n + 1):
            if lines[i:i + n] == needle:
                return lines[i + n:]
    return None


FOLLOW_NEEDLE = 50


def _follow_poll(client, level, needle):
    _log_build(client, level)
    total, page0 = _log_read_page(client, 0)
    if total <= 1:
        return _split_new_lines(needle, page0)
    acc = []
    for idx in range(total - 1, 0, -1):
        acc = _log_read_page(client, idx)[1] + acc
        new = _split_new_lines(needle, acc)
        if new is not None:
            return new
    return _split_new_lines(needle, page0 + acc)


def _logs_follow(args):
    with deco_client(args) as client:
        _log_build(client, args.level)
        lines = _log_fetch_tail(client, max(args.lines, FOLLOW_NEEDLE))
        for line in lines[-args.lines:]:
            print(line, flush=True)
        needle = lines[-FOLLOW_NEEDLE:]
        try:
            while True:
                time.sleep(args.interval)
                try:
                    new = _follow_poll(client, args.level, needle)
                except Exception:
                    client.authorize()
                    new = _follow_poll(client, args.level, needle)
                if new is None:
                    print("deco: buffer rolled over between polls; some lines missed",
                          file=sys.stderr)
                    new = _log_fetch_all(client)
                for line in new:
                    print(line, flush=True)
                if new:
                    needle = (needle + new)[-FOLLOW_NEEDLE:]
        except KeyboardInterrupt:
            pass


def cmd_logs(args):
    if args.follow:
        _logs_follow(args)
        return
    with deco_client(args) as client:
        _log_build(client, args.level)
        lines = _log_fetch_all(client) if args.all else _log_fetch_tail(client, args.lines)

    if args.json:
        print(json.dumps({"level": args.level, "count": len(lines), "lines": lines},
                         indent=2))
    else:
        for line in lines:
            print(line)


LOG_STATE_TAIL = 200


def cmd_collect(args):
    log_dir = os.path.expanduser(args.dir)
    os.makedirs(log_dir, exist_ok=True)
    state_path = os.path.join(log_dir, "state.json")
    output_path = os.path.join(log_dir, "deco.log")

    prev_tail = []
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                prev_tail = json.load(f).get("tail", [])
        except (OSError, json.JSONDecodeError):
            prev_tail = []

    with deco_client(args) as client:
        _log_build(client, args.level)
        lines = _log_fetch_all(client)

    new = _split_new_lines(prev_tail, lines)
    gap = new is None
    if gap:
        new = lines

    with open(output_path, "a") as f:
        if gap and prev_tail:
            f.write("### deco collect: gap - router buffer rolled over since last run\n")
        for line in new:
            f.write(line + "\n")

    with open(state_path, "w") as f:
        json.dump({"tail": lines[-LOG_STATE_TAIL:],
                   "updated": datetime.now(timezone.utc).isoformat()}, f)

    if args.json:
        print(json.dumps({"appended": len(new), "gap": gap, "output": output_path}))
    else:
        gap_note = " (gap: buffer rolled over since last run)" if gap and prev_tail else ""
        print(f"Appended {len(new)} new lines to {output_path}{gap_note}")


def _online_clients(client):
    data = client.request("admin/client?form=client_list", json.dumps(
        {"operation": "read", "params": {"device_mac": "default"}}))
    clients = []
    for cl in data.get("client_list", []):
        if not cl.get("online"):
            continue
        try:
            name = b64decode(cl["name"]).decode()
        except Exception:
            name = cl.get("name", "(unnamed)")
        clients.append({
            "hostname": name or "(unnamed)",
            "ip": cl.get("ip", ""),
            "mac": cl.get("mac", ""),
            "connection": cl.get("connection_type", "unknown"),
            "wire_type": cl.get("wire_type", ""),
            "down_kbps": cl.get("down_speed") or 0,
            "up_kbps": cl.get("up_speed") or 0,
        })
    return clients


def cmd_internet(args):
    with deco_client(args) as client:
        data = client.request("admin/network?form=internet",
                              json.dumps({"operation": "read"}))
    ipv4 = data.get("ipv4", {})
    ipv6 = data.get("ipv6", {})
    result = {
        "link_status": data.get("link_status"),
        "ipv4_status": ipv4.get("inet_status"),
        "ipv4_dial_status": ipv4.get("dial_status"),
        "ipv4_type": ipv4.get("connect_type"),
        "ipv6_status": ipv6.get("inet_status"),
    }
    online = result["ipv4_status"] == "online"
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Link:  {result['link_status']}")
        print(f"IPv4:  {result['ipv4_status']} "
              f"({result['ipv4_type']}, {result['ipv4_dial_status']})")
        print(f"IPv6:  {result['ipv6_status']}")
    sys.exit(0 if online else 1)


def cmd_time(args):
    with deco_client(args) as client:
        data = client.request("admin/device?form=timesetting",
                              json.dumps({"operation": "read"}))
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        offset = int(data.get("timezone", 0))
        print(f"Date:      {data.get('date')}")
        print(f"Time:      {data.get('time')}")
        print(f"Timezone:  {data.get('continent')}/{data.get('tz_region')} "
              f"(UTC{offset // 60:+d}:{abs(offset) % 60:02d})")
        print(f"DST:       {data.get('dst_status')}")


def cmd_upgrade_check(args):
    with deco_client(args) as client:
        client.request("admin/cloud?form=firmware_status",
                       json.dumps({"operation": "check_upgrade"}))
        for _ in range(10):
            time.sleep(3)
            status = client.request("admin/cloud?form=firmware_status",
                                    json.dumps({"operation": "read"}))
            if status.get("status") == "idle":
                break
        data = client.request("admin/device?form=device_list",
                              json.dumps({"operation": "read"}))
    nodes = []
    for item in data.get("device_list", []):
        nodes.append({
            "model": item.get("device_model", ""),
            "role": item.get("role", ""),
            "mac": item.get("mac", ""),
            "current": item.get("software_ver", ""),
            "new_version": item.get("new_version"),
            "needs_upgrade": bool(item.get("need_to_upgrade")),
        })
    if args.json:
        print(json.dumps(nodes, indent=2))
    else:
        for n in nodes:
            if n["needs_upgrade"]:
                verdict = f"UPDATE AVAILABLE -> {n['new_version']}"
            else:
                verdict = "up to date"
            print(f"{n['model']} ({n['role']}): {n['current']} - {verdict}")


def _prom_escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _metrics_prom(m):
    out = []

    def gauge(name, help_text, samples):
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} gauge")
        for labels, value in samples:
            if labels:
                label_str = ",".join(f'{k}="{_prom_escape(v)}"'
                                     for k, v in labels.items())
                out.append(f"{name}{{{label_str}}} {value}")
            else:
                out.append(f"{name} {value}")

    gauge("deco_cpu_usage_ratio", "Router CPU usage (0-1)", [(None, m["cpu_usage"])])
    gauge("deco_mem_usage_ratio", "Router memory usage (0-1)", [(None, m["mem_usage"])])
    gauge("deco_wan_online", "WAN IPv4 online (1/0)", [(None, int(m["wan_online"]))])
    gauge("deco_clients_total", "Online client count", [(None, m["clients_total"])])
    gauge("deco_mesh_nodes_total", "Mesh node count", [(None, m["nodes_total"])])
    gauge("deco_mesh_nodes_connected", "Connected mesh node count",
          [(None, m["nodes_connected"])])
    gauge("deco_clients_down_kbps_total", "Sum of client download speeds (KB/s)",
          [(None, m["down_kbps_total"])])
    gauge("deco_clients_up_kbps_total", "Sum of client upload speeds (KB/s)",
          [(None, m["up_kbps_total"])])
    gauge("deco_client_down_kbps", "Per-client download speed (KB/s)",
          [({"mac": c["mac"], "name": c["hostname"]}, c["down_kbps"])
           for c in m["clients"]])
    gauge("deco_client_up_kbps", "Per-client upload speed (KB/s)",
          [({"mac": c["mac"], "name": c["hostname"]}, c["up_kbps"])
           for c in m["clients"]])
    return "\n".join(out) + "\n"


def cmd_metrics(args):
    with deco_client(args) as client:
        perf = client.request("admin/network?form=performance",
                              json.dumps({"operation": "read"}))
        inet = client.request("admin/network?form=internet",
                              json.dumps({"operation": "read"}))
        nodes = client.request("admin/device?form=device_list",
                               json.dumps({"operation": "read"})).get("device_list", [])
        clients = _online_clients(client)

    m = {
        "cpu_usage": perf.get("cpu_usage", 0),
        "mem_usage": perf.get("mem_usage", 0),
        "wan_online": inet.get("ipv4", {}).get("inet_status") == "online",
        "clients_total": len(clients),
        "nodes_total": len(nodes),
        "nodes_connected": sum(1 for n in nodes
                               if n.get("group_status") == "connected"),
        "down_kbps_total": sum(c["down_kbps"] for c in clients),
        "up_kbps_total": sum(c["up_kbps"] for c in clients),
        "clients": clients,
    }

    if args.prom:
        output = _metrics_prom(m)
    elif args.json:
        output = json.dumps(m, indent=2)
    else:
        output = (
            f"CPU:     {m['cpu_usage'] * 100:.0f}%\n"
            f"Memory:  {m['mem_usage'] * 100:.0f}%\n"
            f"WAN:     {'online' if m['wan_online'] else 'OFFLINE'}\n"
            f"Nodes:   {m['nodes_connected']}/{m['nodes_total']} connected\n"
            f"Clients: {m['clients_total']} "
            f"(down {m['down_kbps_total']} KB/s, up {m['up_kbps_total']} KB/s)"
        )

    if args.output:
        tmp_path = args.output + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(output if output.endswith("\n") else output + "\n")
        os.replace(tmp_path, args.output)
    else:
        print(output)


def _print_top(clients, count):
    print(f"{'#':>2}  {'Device':30s}  {'IP':16s}  {'Conn':8s}  "
          f"{'Down KB/s':>9s}  {'Up KB/s':>8s}")
    print("-" * 84)
    for i, c in enumerate(clients[:count], 1):
        print(f"{i:>2}  {c['hostname'][:30]:30s}  {c['ip']:16s}  "
              f"{c['connection']:8s}  {c['down_kbps']:>9d}  {c['up_kbps']:>8d}")


def cmd_top(args):
    with deco_client(args) as client:
        try:
            while True:
                clients = _online_clients(client)
                clients.sort(key=lambda c: -(c["down_kbps"] + c["up_kbps"]))
                if args.json:
                    print(json.dumps(clients[:args.count], indent=2))
                else:
                    if args.watch:
                        print("\033[2J\033[H", end="")
                        print(f"deco top - {datetime.now():%H:%M:%S} - "
                              f"{len(clients)} clients online\n")
                    _print_top(clients, args.count)
                sys.stdout.flush()
                if not args.watch:
                    break
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass


def cmd_watch(args):
    state_dir = os.path.expanduser(args.dir)
    os.makedirs(state_dir, exist_ok=True)
    state_path = os.path.join(state_dir, "devices.json")
    state = {"wan_ip": None, "devices": {}}
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

    with deco_client(args) as client:
        wan = client.request("admin/network?form=wan_ipv4",
                             json.dumps({"operation": "read"}))
        clients = _online_clients(client)
    wan_info = wan.get("wan", {}).get("ip_info") or wan.get("wan", {}).get("mobile_cpe") or {}
    wan_ip = wan_info.get("ip")

    now = datetime.now(timezone.utc).isoformat()
    first_run = not state["devices"]
    new_devices = []
    for c in clients:
        known = state["devices"].get(c["mac"])
        if known is None:
            if not first_run:
                new_devices.append(c)
            state["devices"][c["mac"]] = {"name": c["hostname"], "ip": c["ip"],
                                          "first_seen": now, "last_seen": now}
        else:
            known.update({"name": c["hostname"], "ip": c["ip"], "last_seen": now})

    wan_changed = state["wan_ip"] is not None and wan_ip != state["wan_ip"]
    old_wan = state["wan_ip"]
    state["wan_ip"] = wan_ip
    with open(state_path, "w") as f:
        json.dump(state, f, indent=1)

    if args.notify and (new_devices or wan_changed):
        parts = []
        if new_devices:
            parts.append(f"{len(new_devices)} new device(s): "
                         + ", ".join(d["hostname"] for d in new_devices[:3]))
        if wan_changed:
            parts.append(f"WAN IP changed to {wan_ip}")
        msg = "; ".join(parts).replace('"', "'")
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e",
                            f'display notification "{msg}" with title "Deco"'],
                           check=False)
        else:
            print("--notify requires macOS; skipping notification.", file=sys.stderr)

    if args.json:
        print(json.dumps({"first_run": first_run, "new_devices": new_devices,
                          "wan_ip": wan_ip, "wan_changed": wan_changed,
                          "previous_wan_ip": old_wan,
                          "known_devices": len(state["devices"])}, indent=2))
    else:
        if first_run:
            print(f"Baseline recorded: {len(state['devices'])} devices, WAN {wan_ip}")
        else:
            if new_devices:
                print(f"{len(new_devices)} NEW device(s):")
                for d in new_devices:
                    print(f"  {d['hostname']:30s}  {d['ip']:16s}  {d['mac']:20s}  "
                          f"{d['connection']}")
            if wan_changed:
                print(f"WAN IP changed: {old_wan} -> {wan_ip}")
            if not new_devices and not wan_changed:
                print(f"No changes ({len(state['devices'])} known devices, WAN {wan_ip})")


def cmd_reboot(args):
    if not args.yes and not args.json:
        confirm = input("Reboot all mesh nodes? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return
    with deco_client(args) as client:
        client.reboot()
        if args.json:
            print(json.dumps({"status": "rebooting"}))
        else:
            print("Reboot initiated for all mesh nodes.")


COMMANDS = {
    "status": cmd_status,
    "devices": cmd_devices,
    "firmware": cmd_firmware,
    "dns": cmd_dns,
    "dhcp": cmd_dhcp,
    "wifi": cmd_wifi,
    "wifi-toggle": cmd_wifi_toggle,
    "lookup": cmd_lookup,
    "clients": cmd_clients,
    "logs": cmd_logs,
    "collect": cmd_collect,
    "internet": cmd_internet,
    "time": cmd_time,
    "upgrade-check": cmd_upgrade_check,
    "metrics": cmd_metrics,
    "top": cmd_top,
    "watch": cmd_watch,
    "reboot": cmd_reboot,
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="deco",
        description="Query and manage TP-Link Deco mesh routers",
    )
    parser.add_argument("--host", default=os.environ.get("DECO_HOST", DECO_HOST_DEFAULT),
                        help="Router IP (default: $DECO_HOST or 192.168.68.1)")
    parser.add_argument("--password", default=None,
                        help="Router password (default: $DECO_PASSWORD)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    subs = parser.add_subparsers(dest="command", required=True)
    json_kw = dict(action="store_true", help=argparse.SUPPRESS)

    for name, help_text in [
        ("status", "Router status overview"),
        ("devices", "List connected devices"),
        ("firmware", "Firmware/model info per mesh node"),
        ("dns", "DNS configuration"),
        ("dhcp", "LAN/DHCP config and active leases (read-only)"),
        ("wifi", "WiFi band status"),
        ("internet", "WAN connectivity status (exit 1 if offline)"),
        ("time", "Router date/time and timezone"),
        ("upgrade-check", "Check for firmware updates"),
    ]:
        sp = subs.add_parser(name, help=help_text)
        sp.add_argument("--json", **json_kw)

    mt = subs.add_parser("metrics", help="Metrics snapshot (text, JSON, or Prometheus)")
    mt.add_argument("--json", **json_kw)
    mt.add_argument("--prom", action="store_true",
                    help="Output Prometheus textfile format")
    mt.add_argument("--output", default=None,
                    help="Write atomically to a file instead of stdout "
                         "(for node_exporter textfile collector)")

    tp = subs.add_parser("top", help="Per-device bandwidth, sorted by usage")
    tp.add_argument("--json", **json_kw)
    tp.add_argument("-n", "--count", type=int, default=15,
                    help="Number of devices to show (default: 15)")
    tp.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                    help="Refresh every N seconds until interrupted")

    wa = subs.add_parser("watch", help="Detect new devices and WAN IP changes")
    wa.add_argument("--json", **json_kw)
    wa.add_argument("--dir", default="~/.deco-logs",
                    help="State directory (default: ~/.deco-logs)")
    wa.add_argument("--notify", action="store_true",
                    help="Send a macOS notification on changes")

    lu = subs.add_parser("lookup", help="Look up a device by MAC address")
    lu.add_argument("--json", **json_kw)
    lu.add_argument("mac", help="MAC address (any format: AA:BB:CC:DD:EE:FF, AA-BB-CC-DD-EE-FF)")

    cl = subs.add_parser("clients", help="List clients connected to a specific mesh node")
    cl.add_argument("--json", **json_kw)
    cl.add_argument("node_mac", help="MAC address of the mesh node (use 'deco firmware' to list nodes)")

    wt = subs.add_parser("wifi-toggle", help="Toggle a WiFi band on/off")
    wt.add_argument("--json", **json_kw)
    wt.add_argument("band", choices=list(WIFI_BAND_MAP.keys()),
                     help="WiFi band to toggle")
    wt.add_argument("state", choices=["on", "off"],
                     help="Turn band on or off")

    lg = subs.add_parser("logs", help="Pull system logs from the router")
    lg.add_argument("--json", **json_kw)
    lg.add_argument("--level", choices=list(LOG_LEVEL_MAP), default="all",
                    help="Log level filter (default: all)")
    lg.add_argument("-n", "--lines", type=int, default=100,
                    help="Number of most recent lines to show (default: 100)")
    lg.add_argument("--all", action="store_true",
                    help="Fetch the entire log instead of the most recent lines")
    lg.add_argument("-f", "--follow", action="store_true",
                    help="Keep polling and print new lines as they appear (like tail -f)")
    lg.add_argument("--interval", type=int, default=10,
                    help="Polling interval in seconds for --follow (default: 10)")

    co = subs.add_parser("collect",
                         help="Append new log lines to a local file (cron/launchd friendly)")
    co.add_argument("--json", **json_kw)
    co.add_argument("--level", choices=list(LOG_LEVEL_MAP), default="all",
                    help="Log level filter (default: all)")
    co.add_argument("--dir", default="~/.deco-logs",
                    help="Directory for collected log and state (default: ~/.deco-logs)")

    rb = subs.add_parser("reboot", help="Reboot all mesh nodes")
    rb.add_argument("--json", **json_kw)
    rb.add_argument("-y", "--yes", action="store_true",
                     help="Skip confirmation prompt")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    handler = COMMANDS.get(args.command)
    if not handler:
        parser.print_help()
        sys.exit(1)
    try:
        handler(args)
    except SystemExit:
        raise
    except Exception as e:
        msg = str(e)
        if "no such callback" in msg or "No root node" in msg:
            print(f"Error: your Deco model/firmware does not support this "
                  f"operation.\n({msg[:200]})", file=sys.stderr)
        else:
            print(f"Error: {msg[:300]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
