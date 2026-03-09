"""CLI for SDVoE device discovery (API and UDP broadcast)."""

import argparse
import json
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from rich.console import Console

from sdvoe_discovery import __version__
from sdvoe_discovery.discovery import (
    DEFAULT_RESPONSE_PORT,
    DEFAULT_SEND_PORT,
    discover_devices,
    get_response_breakdown,
)
from sdvoe_discovery.device_info import get_device_list, get_device_details
from sdvoe_discovery.api_client import SDVoEAPIClient, ControlServerError
from sdvoe_discovery.control_server_runner import (
    DEFAULT_CONTROLSERVER_PORT,
    find_controlserver_root,
    get_available_platform_folders,
    get_binary_path,
    is_controlserver_running,
    run_controlserver_auto,
    save_cached_controlserver_root,
    detect_platform,
)

console = Console()
err_console = Console(file=sys.stderr)


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _ensure_controlserver(
    api_url: str,
    no_start: bool,
    start_server: bool,
    project_root: Optional[Path] = None,
) -> Tuple[Optional[str], Optional[Path], bool]:
    """
    If control server is reachable at api_url, return (api_url, None, False).
    Otherwise: if no_start, return (None, None, False). If start_server or prompt Y,
    try to start it and return (new_base_url, temp_config_path, True) or (None, None, False).
    Third value is True when we just started the server (caller may want to wait for discovery).
    """
    if is_controlserver_running(api_url):
        return (api_url, None, False)

    # Pass None to try all locations (cwd, SDVOE_CONTROLSERVER_ROOT, package parent, ~/.sdvoe-discovery)
    root = find_controlserver_root(project_root)
    if not root and not no_start:
        # Give a chance to type the path (e.g. after new terminal where env var was lost)
        err_console.print("[yellow]Control server not running[/] and no controlserver-* folder found.")
        err_console.print("Tip: [bold]export SDVOE_CONTROLSERVER_ROOT[/] only lasts for this terminal; add it to [bold]~/.zshrc[/] or [bold]~/.bashrc[/] to persist.")
        try:
            path_input = input("Path to controlserver-* folder (or Enter to skip): ").strip()
            if path_input:
                user_path = Path(path_input).expanduser().resolve()
                root = find_controlserver_root(user_path)
                if root:
                    save_cached_controlserver_root(root)
        except EOFError:
            path_input = ""
        if not root:
            err_console.print("The Control Server is a separate process (e.g. from the BlueRiver SDK).")
            err_console.print("  • If it is already running elsewhere, use [bold]--api-url URL --no-start[/] (e.g. [bold]--api-url http://192.168.1.10:80[/]).")
            err_console.print("  • For discovery without the server, use [bold]sdvoe-discovery broadcast[/].")
            err_console.print("  • To have the CLI start the server, run from a directory that contains a [bold]controlserver-*[/] folder, or put one in [bold]~/.sdvoe-discovery/[/], or set [bold]SDVOE_CONTROLSERVER_ROOT[/] to its path.")
            return (None, None, False)
    elif not root:
        return (None, None, False)
    else:
        save_cached_controlserver_root(root)

    platforms = get_available_platform_folders(root)
    if not platforms:
        if no_start:
            return (None, None, False)
        err_console.print("[red]No control server binary found[/] in controlserver-* platform folders.")
        return (None, None, False)

    detected = detect_platform()
    platform_choice = detected if detected and detected in platforms else None
    if not platform_choice and len(platforms) == 1:
        platform_choice = platforms[0]
    if not platform_choice and len(platforms) > 1:
        if no_start:
            return (None, None, False)
        err_console.print("[yellow]Control server not running.[/] Select platform to start:")
        for i, p in enumerate(platforms, 1):
            err_console.print(f"  {i}) {p}")
        try:
            raw = input("Choice [1]: ").strip() or "1"
            idx = int(raw)
            if 1 <= idx <= len(platforms):
                platform_choice = platforms[idx - 1]
        except (ValueError, EOFError):
            platform_choice = platforms[0]
        if not platform_choice:
            platform_choice = platforms[0]

    if no_start:
        return (None, None, False)

    if not start_server:
        try:
            answer = input("Control server not running. Start it? [Y/n]: ").strip().lower()
            if answer and answer != "y" and answer != "yes":
                return (None, None, False)
        except EOFError:
            return (None, None, False)

    base_url, proc, temp_config = run_controlserver_auto(
        project_root=root,
        platform_folder=platform_choice,
    )
    if not base_url:
        err_console.print("[red]Failed to start control server.[/]")
        return (None, temp_config, False)
    for _ in range(30):
        if is_controlserver_running(base_url):
            if proc and getattr(proc, "poll", None) is not None and proc.poll() is not None:
                pass
            return (base_url, temp_config, True)
        time.sleep(0.5)
    err_console.print("[red]Control server started but did not become ready in time.[/]")
    return (None, temp_config, False)


def _cmd_list(parsed: argparse.Namespace) -> int:
    """Fast list via Control Server API (IP, MAC, name, type, firmware)."""
    api_url = parsed.api_url
    if not getattr(parsed, "no_start", False):
        resolved_url, _, just_started = _ensure_controlserver(
            api_url,
            no_start=getattr(parsed, "no_start", False),
            start_server=getattr(parsed, "start_server", False),
        )
        if resolved_url:
            api_url = resolved_url
            if just_started:
                warmup = getattr(parsed, "server_warmup", 5) or 0
                if warmup > 0:
                    with err_console.status("Starting server...", spinner="dots"):
                        time.sleep(warmup)
    if not is_controlserver_running(api_url):
        err_console.print("[red]Error:[/] Control server not running. Use [bold]--api-url URL[/] if it runs elsewhere, [bold]--start-server[/] to start from a controlserver-* folder, or [bold]--no-start[/] to skip.")
        return 1
    try:
        devices = get_device_list(
            base_url=api_url,
            timeout=parsed.timeout,
            request_timeout=getattr(parsed, "request_timeout", 60.0),
        )
    except ControlServerError as e:
        err_console.print(f"[red]Error:[/] {e}")
        return 1
    if parsed.format == "json":
        out = []
        for i, d in enumerate(devices, 1):
            row = {"id": i, **{k: v for k, v in d.items() if v is not None}}
            out.append(row)
        print(json.dumps({"count": len(devices), "devices": out}, indent=2))
        return 0
    console.print(f"[bold]SDVoE devices (API)[/] — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(f"[dim]Found {len(devices)} device(s). Use id with control commands: sdvoe-discovery reboot <id|all>[/]\n")
    for i, d in enumerate(devices, 1):
        console.print(f"  [bold cyan]Device {i}:[/]")
        console.print(f"    [dim]#:[/]      [yellow]{i}[/]")
        console.print(f"    [dim]IP:[/]     [green]{d.get('ip') or '—'}[/]")
        console.print(f"    [dim]MAC:[/]    [yellow]{d.get('mac') or '—'}[/]")
        console.print(f"    [dim]Name:[/]   {d.get('device_name') or '—'}")
        console.print(f"    [dim]Alias:[/]  {d.get('alias') or '—'}")
        console.print(f"    [dim]Type:[/]   [magenta]{d.get('type') or '—'}[/]")
        console.print(f"    [dim]Firmware:[/] {d.get('firmware_version') or '—'}")
        console.print()
    if not devices:
        console.print("[dim]No devices. Is the Control Server running and devices on the network?[/]")
    return 0


def _cmd_detail(parsed: argparse.Namespace) -> int:
    """Detailed list via Control Server API (full device info)."""
    api_url = parsed.api_url
    if not getattr(parsed, "no_start", False):
        resolved_url, _, just_started = _ensure_controlserver(
            api_url,
            no_start=getattr(parsed, "no_start", False),
            start_server=getattr(parsed, "start_server", False),
        )
        if resolved_url:
            api_url = resolved_url
            if just_started:
                warmup = getattr(parsed, "server_warmup", 5) or 0
                if warmup > 0:
                    with err_console.status("Starting server...", spinner="dots"):
                        time.sleep(warmup)
    if not is_controlserver_running(api_url):
        err_console.print("[red]Error:[/] Control server not running. Use [bold]--api-url URL[/] if it runs elsewhere, [bold]--start-server[/] to start from a controlserver-* folder, or [bold]--no-start[/] to skip.")
        return 1
    try:
        devices = get_device_details(
            base_url=api_url,
            timeout=parsed.timeout,
            request_timeout=parsed.request_timeout,
        )
    except ControlServerError as e:
        err_console.print(f"[red]Error:[/] {e}")
        return 1
    if parsed.format == "json":
        out = []
        for i, d in enumerate(devices, 1):
            row = {"id": i, **{k: v for k, v in d.items() if v is not None}}
            out.append(row)
        print(json.dumps({"count": len(devices), "devices": out}, indent=2))
        return 0
    console.print(f"[bold]SDVoE devices (detailed)[/] — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(f"[dim]Found {len(devices)} device(s). Use id with control commands: sdvoe-discovery reboot <id|all>[/]\n")
    detail_cols = ("ip", "mac", "device_id", "device_name", "alias", "type", "firmware_version", "firmware_comment", "chipset_type", "engine", "active", "update_in_progress")
    for i, d in enumerate(devices, 1):
        console.print(f"  [bold cyan]Device {i}:[/]")
        for k in detail_cols:
            v = d.get(k) or "—"
            key = k.replace("_", " ").title()
            if k == "ip":
                console.print(f"    [dim]{key}:[/] [green]{v}[/]")
            elif k == "mac":
                console.print(f"    [dim]{key}:[/] [yellow]{v}[/]")
            else:
                console.print(f"    [dim]{key}:[/] {v}")
        console.print()
    if not devices:
        console.print("[dim]No devices. Is the Control Server running and devices on the network?[/]")
    return 0


def _cmd_broadcast(parsed: argparse.Namespace) -> int:
    """UDP broadcast discovery (MCU protocol, no Control Server required)."""
    config_path: Optional[str] = None
    if parsed.config:
        p = Path(parsed.config)
        if p.is_file():
            config_path = str(p)
        elif parsed.verbose:
            err_console.print(f"[dim]Config not found: {parsed.config}, using built-in MCU defaults[/]")
    interface_ip = parsed.interface or get_local_ip()
    if parsed.verbose:
        err_console.print(f"[dim]Using interface: {interface_ip}[/]")
    try:
        devices = discover_devices(
            interface_ip=interface_ip,
            send_port=parsed.send_port,
            response_port=parsed.response_port,
            timeout=parsed.timeout,
            config_path=config_path,
        )
    except RuntimeError as e:
        err_console.print(f"[red]Error:[/] {e}")
        return 1
    if parsed.format == "json":
        print(json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "count": len(devices),
            "devices": [d.to_dict() for d in devices],
        }, indent=2))
        return 0
    console.print(f"[bold]SDVoE Discovery (broadcast)[/] — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(f"[dim]Found {len(devices)} device(s)[/]" if devices else "[dim]No devices responded. Check same subnet, firewall (UDP 6239/6240).[/]")
    console.print()
    def _s(v): return "—" if v is None else str(v)
    for i, dev in enumerate(devices, 1):
        p = dev.parsed or {}
        console.print(f"  [bold cyan]Device {i}:[/]")
        console.print(f"    [dim]#:[/]        [yellow]{i}[/]")
        console.print(f"    [dim]IP:[/]       [green]{_s(p.get('ip', dev.ip))}[/]")
        console.print(f"    [dim]MCU MAC:[/]  [yellow]{_s(p.get('mcu_mac'))}[/]")
        console.print(f"    [dim]MCU Ver:[/]  {_s(p.get('mcu_version'))}")
        console.print(f"    [dim]AVP MAC:[/]  {_s(p.get('avp_mac'))}")
        console.print(f"    [dim]Dante MAC:[/] {_s(p.get('dante_mac'))}")
        console.print(f"    [dim]Board:[/]    {_s(p.get('board_type'))}")
        console.print(f"    [dim]USB:[/]      {_s(p.get('usb_config'))}")
        console.print()
    if parsed.show_response or (parsed.raw and devices):
        for i, dev in enumerate(devices, 1):
            if parsed.show_response:
                console.print(f"\n[bold]Device {i} response breakdown:[/]")
                for row in get_response_breakdown(dev.raw_bytes):
                    val = f"  → {row['value']}" if row.get("value") else ""
                    console.print(f"  [dim]{row['offset']}[/] [cyan]{row['name']}:[/] {row['hex']}{val}")
                console.print(f"  [dim]Raw hex:[/] {dev.hex_dump}")
            elif parsed.raw:
                console.print(f"\n[bold]Device {i} raw:[/] {dev.hex_dump}")
    return 0


def _get_api_url_and_devices(parsed: argparse.Namespace) -> Tuple[Optional[str], list]:
    """Ensure control server is up, optionally start it, return (api_url, devices). devices have device_id for resolving target."""
    api_url = getattr(parsed, "api_url", f"http://127.0.0.1:{DEFAULT_CONTROLSERVER_PORT}")
    if not getattr(parsed, "no_start", False):
        resolved_url, _, just_started = _ensure_controlserver(
            api_url,
            no_start=getattr(parsed, "no_start", False),
            start_server=getattr(parsed, "start_server", False),
        )
        if resolved_url:
            api_url = resolved_url
            if just_started:
                warmup = getattr(parsed, "server_warmup", 5) or 0
                if warmup > 0:
                    with err_console.status("Starting server...", spinner="dots"):
                        time.sleep(warmup)
    if not is_controlserver_running(api_url):
        return (None, [])
    try:
        devices = get_device_list(
            base_url=api_url,
            timeout=getattr(parsed, "timeout", 10.0),
            request_timeout=getattr(parsed, "request_timeout", 60.0),
        )
    except ControlServerError:
        return (api_url, [])
    return (api_url, devices)


def _resolve_target(devices: list, target: str) -> Optional[str]:
    """Resolve target string to API target: 'ALL' or device_id. target is 'all' or 1-based index like '1', '2'."""
    s = (target or "").strip().lower()
    if s == "all":
        return "ALL"
    if s.isdigit():
        idx = int(s)
        if 1 <= idx <= len(devices):
            return devices[idx - 1].get("device_id")
    return target.strip() or None


def _cmd_reboot(parsed: argparse.Namespace) -> int:
    """Reboot device(s). Target: device id (from list) or 'all'."""
    api_url, devices = _get_api_url_and_devices(parsed)
    if not api_url:
        err_console.print("[red]Error:[/] Control server not running. Use [bold]--api-url URL[/] if it runs elsewhere, [bold]--start-server[/], or [bold]--no-start[/] to skip.")
        return 1
    target = _resolve_target(devices, parsed.target)
    if not target:
        err_console.print("[red]Error:[/] Invalid target. Use a device id (1, 2, ...) or 'all'. Run [bold]sdvoe-discovery list[/] for ids.")
        return 1
    try:
        client = SDVoEAPIClient(base_url=api_url, timeout=getattr(parsed, "timeout", 10.0))
        resp = client.reboot(target, request_timeout=getattr(parsed, "request_timeout", 60.0))
    except ControlServerError as e:
        err_console.print(f"[red]Error:[/] {e}")
        return 1
    status = resp.get("status")
    if status != "SUCCESS":
        err_console.print(f"[red]Error:[/] {resp.get('error', {})}")
        return 1
    result = resp.get("result") or {}
    rebooted = result.get("reboot", [])
    errors = result.get("error", [])
    for r in rebooted:
        did = r.get("device_id", "?")
        console.print(f"[green]Reboot sent:[/] {did}")
    for e in errors:
        err_console.print(f"[red]Failed {e.get('device_id', '?')}:[/] {e.get('message', e.get('reason', ''))}")
    return 0 if not errors else 1


def _cmd_factory(parsed: argparse.Namespace) -> int:
    """Factory reset device(s). Target: device id or 'all'."""
    api_url, devices = _get_api_url_and_devices(parsed)
    if not api_url:
        err_console.print("[red]Error:[/] Control server not running. Use [bold]--api-url URL[/] if it runs elsewhere, [bold]--start-server[/], or [bold]--no-start[/] to skip.")
        return 1
    target = _resolve_target(devices, parsed.target)
    if not target:
        err_console.print("[red]Error:[/] Invalid target. Use a device id (1, 2, ...) or 'all'. Run [bold]sdvoe-discovery list[/] for ids.")
        return 1
    try:
        client = SDVoEAPIClient(base_url=api_url, timeout=getattr(parsed, "timeout", 10.0))
        resp = client.factory(target, request_timeout=getattr(parsed, "request_timeout", 60.0))
    except ControlServerError as e:
        err_console.print(f"[red]Error:[/] {e}")
        return 1
    status = resp.get("status")
    if status != "SUCCESS":
        err_console.print(f"[red]Error:[/] {resp.get('error', {})}")
        return 1
    result = resp.get("result") or {}
    # API may return device_success (4.2) or similar; treat any list of success as ok
    success = result.get("device_success") or result.get("factory") or result.get("reboot") or []
    errors = result.get("error", [])
    for s in success:
        did = s.get("device_id", "?") if isinstance(s, dict) else str(s)
        console.print(f"[green]Factory reset sent:[/] {did}")
    for e in errors:
        err_console.print(f"[red]Failed {e.get('device_id', '?')}:[/] {e.get('message', e.get('reason', ''))}")
    return 0 if not errors else 1


def _cmd_get_device(parsed: argparse.Namespace) -> int:
    """Get full device object(s). Target: device id or 'all'."""
    api_url, devices = _get_api_url_and_devices(parsed)
    if not api_url:
        err_console.print("[red]Error:[/] Control server not running. Use [bold]--api-url URL[/] if it runs elsewhere, [bold]--start-server[/], or [bold]--no-start[/] to skip.")
        return 1
    target = _resolve_target(devices, parsed.target)
    if not target:
        err_console.print("[red]Error:[/] Invalid target. Use a device id (1, 2, ...) or 'all'. Run [bold]sdvoe-discovery list[/] for ids.")
        return 1
    try:
        client = SDVoEAPIClient(base_url=api_url, timeout=getattr(parsed, "timeout", 10.0))
        resp = client.get_devices_full(target)
        status = resp.get("status")
        if status == "PROCESSING":
            req_id = resp.get("request_id")
            if req_id is not None:
                resp = client.wait_for_request(req_id, max_wait=getattr(parsed, "request_timeout", 60.0))
        if resp.get("status") != "SUCCESS":
            err_console.print(f"[red]Error:[/] {resp.get('error', {})}")
            return 1
    except ControlServerError as e:
        err_console.print(f"[red]Error:[/] {e}")
        return 1
    if parsed.format == "json":
        print(json.dumps(resp.get("result", resp), indent=2))
        return 0
    result = resp.get("result") or {}
    devs = result.get("devices", [])
    errs = result.get("error", [])
    for d in devs:
        console.print(d)
    for e in errs:
        err_console.print(f"[red]{e}[/]")
    return 0


def _cmd_netstat(parsed: argparse.Namespace) -> int:
    """Read network statistics. Target: device id or 'all'. Use --output to write to file (recommended for 'all')."""
    api_url, devices = _get_api_url_and_devices(parsed)
    if not api_url:
        err_console.print("[red]Error:[/] Control server not running. Use [bold]--api-url URL[/] if it runs elsewhere, [bold]--start-server[/], or [bold]--no-start[/] to skip.")
        return 1
    target = _resolve_target(devices, parsed.target)
    if not target:
        err_console.print("[red]Error:[/] Invalid target. Use a device id (1, 2, ...) or 'all'. Run [bold]sdvoe-discovery list[/] for ids.")
        return 1
    try:
        client = SDVoEAPIClient(base_url=api_url, timeout=getattr(parsed, "timeout", 10.0))
        resp = client.netstat_read(
            target,
            filter_name=getattr(parsed, "filter", None),
            request_timeout=getattr(parsed, "request_timeout", 120.0),
        )
    except ControlServerError as e:
        err_console.print(f"[red]Error:[/] {e}")
        return 1
    if resp.get("status") != "SUCCESS":
        err_console.print(f"[red]Error:[/] {resp.get('error', {})}")
        return 1
    out = json.dumps(resp.get("result", resp), indent=2)
    output_path = getattr(parsed, "output", None)
    if output_path:
        Path(output_path).write_text(out, encoding="utf-8")
        console.print(f"[green]Wrote netstat to[/] [bold]{output_path}[/]")
    else:
        print(out)
    return 0


_MAIN_HELP_EPILOG = """
Commands and options (see also: sdvoe-discovery <command> --help):

  list                  Fast list (IP, MAC, name, type, firmware) from Control Server API
    --api-url URL         API base URL (default: http://127.0.0.1:59402)
    --timeout, -t SECS    HTTP timeout in seconds (default: 10.0)
    --request-timeout SECS  Max wait for get device in seconds (default: 60.0)
    --no-start            Do not prompt to start control server; fail if not running
    --start-server        Start control server automatically if not running
    --server-warmup SECS  After starting server, wait SECS for discovery (default: 5; use 0 to skip)
    --format, -f          Output format: text or json (default: text)

  detail                Detailed list (full device info) from Control Server API
    (same options as list)

  broadcast             UDP broadcast discovery (MCU protocol, no API server)
    --interface, -i IP    Interface IP to bind (default: auto-detect)
    --timeout, -t SECS    Discovery timeout in seconds (default: 3.0)
    --config, -c PATH     Path to mcu_api.json (default: mcu_api.json)
    --send-port PORT      UDP send port (default: from config or 6239)
    --response-port PORT  UDP response port (default: from config or 6240)
    --raw                 Show raw hex dump for each response
    --show-response       Show field-by-field breakdown and full raw hex
    --verbose, -v         Verbose output to stderr
    --format, -f          Output format: text or json (default: text)

  Control commands (target = device id from list, or 'all'):
  reboot ID|all           Reboot device(s)
  factory ID|all          Factory reset device(s)
  get-device ID|all       Get full device object(s)
  netstat ID|all          Read network statistics (use --output FILE for large output)
"""


def _add_control_common(parser: argparse.ArgumentParser, request_timeout_default: float = 60.0) -> None:
    """Add common options for control commands (api-url, timeout, request-timeout, no-start, start-server, server-warmup)."""
    parser.add_argument("--api-url", default=f"http://127.0.0.1:{DEFAULT_CONTROLSERVER_PORT}", help="Control Server API base URL")
    parser.add_argument("--timeout", "-t", type=float, default=10.0, metavar="SECS", help="HTTP timeout in seconds")
    parser.add_argument("--request-timeout", type=float, default=request_timeout_default, metavar="SECS", help="Max wait for request in seconds")
    parser.add_argument("--no-start", action="store_true", help="Do not prompt to start control server")
    parser.add_argument("--start-server", action="store_true", help="Start control server automatically if not running")
    parser.add_argument("--server-warmup", type=float, default=5.0, metavar="SECS", help="Wait SECS after starting server (default: 5)")


def main(args: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover SDVoE devices: via Control Server API (list/detail) or UDP broadcast.",
        prog="sdvoe-discovery",
        epilog=_MAIN_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--format", "-f",
        choices=("text", "json"),
        default="text",
        help="Output format: text (human-readable) or json (default: text)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", help="Command")

    # list: fast API list (IP, MAC, name, type, firmware)
    plist = sub.add_parser(
        "list",
        help="Fast list of devices (Control Server API: IP, MAC, name, type, firmware)",
        description="Get a fast list of SDVoE devices from the Control Server API (IP, MAC, name, alias, type, firmware).",
    )
    plist.add_argument(
        "--format", "-f",
        choices=("text", "json"),
        default="text",
        help="Output format: text or json (default: text)",
    )
    plist.add_argument(
        "--api-url",
        default=f"http://127.0.0.1:{DEFAULT_CONTROLSERVER_PORT}",
        help="Control Server API base URL (default: http://127.0.0.1:%s)" % DEFAULT_CONTROLSERVER_PORT,
    )
    plist.add_argument(
        "--timeout", "-t",
        type=float,
        default=10.0,
        metavar="SECS",
        help="HTTP timeout in seconds (default: 10.0)",
    )
    plist.add_argument(
        "--request-timeout",
        type=float,
        default=60.0,
        metavar="SECS",
        help="Max wait for get device when server returns PROCESSING, in seconds (default: 60.0)",
    )
    plist.add_argument(
        "--no-start",
        action="store_true",
        help="Do not prompt to start control server; fail if not running at --api-url",
    )
    plist.add_argument(
        "--start-server",
        action="store_true",
        help="Start control server automatically on an available port if not running",
    )
    plist.add_argument(
        "--server-warmup",
        type=float,
        default=5.0,
        metavar="SECS",
        help="After starting the server, wait this many seconds for device discovery before listing (default: 5.0; use 0 to skip)",
    )
    plist.set_defaults(_run=_cmd_list)

    # detail: full API device info
    pdetail = sub.add_parser(
        "detail",
        help="Detailed list (Control Server API: full device info)",
        description="Get detailed info for all SDVoE devices (full device object: IP, MAC, identity, firmware, status, etc.).",
    )
    pdetail.add_argument(
        "--format", "-f",
        choices=("text", "json"),
        default="text",
        help="Output format: text or json (default: text)",
    )
    pdetail.add_argument(
        "--api-url",
        default=f"http://127.0.0.1:{DEFAULT_CONTROLSERVER_PORT}",
        help="Control Server API base URL (default: http://127.0.0.1:%s)" % DEFAULT_CONTROLSERVER_PORT,
    )
    pdetail.add_argument(
        "--timeout", "-t",
        type=float,
        default=10.0,
        metavar="SECS",
        help="HTTP timeout in seconds (default: 10.0)",
    )
    pdetail.add_argument(
        "--request-timeout",
        type=float,
        default=60.0,
        metavar="SECS",
        help="Max wait for get device when server returns PROCESSING, in seconds (default: 60.0)",
    )
    pdetail.add_argument(
        "--no-start",
        action="store_true",
        help="Do not prompt to start control server; fail if not running at --api-url",
    )
    pdetail.add_argument(
        "--start-server",
        action="store_true",
        help="Start control server automatically on an available port if not running",
    )
    pdetail.add_argument(
        "--server-warmup",
        type=float,
        default=5.0,
        metavar="SECS",
        help="After starting the server, wait this many seconds for device discovery before listing (default: 5.0; use 0 to skip)",
    )
    pdetail.set_defaults(_run=_cmd_detail)

    # broadcast: UDP discovery (no Control Server)
    pb = sub.add_parser(
        "broadcast",
        help="UDP broadcast discovery (MCU protocol, no API server)",
        description="Discover devices via UDP broadcast using the MCU protocol (mcu_api.json). No Control Server required.",
    )
    pb.add_argument(
        "--format", "-f",
        choices=("text", "json"),
        default="text",
        help="Output format: text or json (default: text)",
    )
    pb.add_argument(
        "--interface", "-i",
        metavar="IP",
        help="Network interface IP to bind for sending discovery (default: auto-detect)",
    )
    pb.add_argument(
        "--timeout", "-t",
        type=float,
        default=3.0,
        metavar="SECS",
        help="How long to wait for device responses in seconds (default: 3.0)",
    )
    pb.add_argument(
        "--config", "-c",
        default="mcu_api.json",
        metavar="PATH",
        help="Path to mcu_api.json for ports and magic number (default: mcu_api.json in cwd)",
    )
    pb.add_argument(
        "--send-port",
        type=int,
        default=None,
        metavar="PORT",
        help="UDP port for discovery request (default: from config or 6239)",
    )
    pb.add_argument(
        "--response-port",
        type=int,
        default=None,
        metavar="PORT",
        help="UDP port to listen for device responses (default: from config or 6240)",
    )
    pb.add_argument(
        "--raw",
        action="store_true",
        help="Show raw hex dump for each device response",
    )
    pb.add_argument(
        "--show-response",
        action="store_true",
        help="Show field-by-field response breakdown and full raw hex",
    )
    pb.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print interface and discovery info to stderr",
    )
    pb.set_defaults(_run=_cmd_broadcast)

    # Control commands: reboot, factory, get-device, netstat (target = id | all)
    preboot = sub.add_parser("reboot", help="Reboot device(s) (Control Server API)")
    preboot.add_argument("target", metavar="ID|all", help="Device id from list (1, 2, ...) or 'all'")
    _add_control_common(preboot)
    preboot.set_defaults(_run=_cmd_reboot)

    pfactory = sub.add_parser("factory", help="Factory reset device(s) (Control Server API)")
    pfactory.add_argument("target", metavar="ID|all", help="Device id from list or 'all'")
    _add_control_common(pfactory)
    pfactory.set_defaults(_run=_cmd_factory)

    pgetdev = sub.add_parser("get-device", help="Get full device object(s) (Control Server API)")
    pgetdev.add_argument("target", metavar="ID|all", help="Device id from list or 'all'")
    pgetdev.add_argument("--format", "-f", choices=("text", "json"), default="json", help="Output format (default: json)")
    _add_control_common(pgetdev)
    pgetdev.set_defaults(_run=_cmd_get_device)

    pnetstat = sub.add_parser("netstat", help="Read network statistics (Control Server API)")
    pnetstat.add_argument("target", metavar="ID|all", help="Device id from list or 'all'")
    pnetstat.add_argument("--output", "-o", metavar="FILE", help="Write output to FILE (recommended for netstat all)")
    pnetstat.add_argument("--filter", choices=("counter", "bandwidth", "error"), help="Return only counter, bandwidth, or error stats")
    _add_control_common(pnetstat, request_timeout_default=120.0)
    pnetstat.set_defaults(_run=_cmd_netstat)

    parsed = parser.parse_args(args)

    if not parsed.command:
        parser.print_help()
        return 0

    return parsed._run(parsed)


if __name__ == "__main__":
    sys.exit(main())
