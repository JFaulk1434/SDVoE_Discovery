"""
Control API: reboot, factory, get_device, netstat by device MAC address.

All functions take a list of MAC addresses (one or many), resolve them to device IDs
via the Control Server API, and return JSON-serializable dicts or write to file (netstat).
Requires the BlueRiver Control Server to be running.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from sdvoe_discovery.api_client import SDVoEAPIClient, ControlServerError, DEFAULT_API_BASE_URL
from sdvoe_discovery.device_info import get_device_list, get_device_details


def _normalize_mac(mac: str) -> str:
    """Normalize MAC to 12 lowercase hex chars (no colons) for comparison."""
    s = mac.replace(":", "").replace("-", "").strip().lower()
    return s if len(s) == 12 and all(c in "0123456789abcdef" for c in s) else ""


def _resolve_macs_to_devices(
    macs: list[str],
    base_url: str = DEFAULT_API_BASE_URL,
    timeout: float = 10.0,
    request_timeout: float = 60.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Resolve a list of MAC addresses to device list entries (with device_id, mac, etc.).
    Returns (list of matching devices from get_device_list, list of MACs not found).
    """
    if not macs:
        return [], []
    devices = get_device_list(base_url=base_url, timeout=timeout, request_timeout=request_timeout)
    normalized_wanted = {_normalize_mac(m) for m in macs if _normalize_mac(m)}
    if not normalized_wanted:
        return [], list(macs)
    found: list[dict[str, Any]] = []
    for d in devices:
        mac = d.get("mac")
        if not mac:
            continue
        norm = _normalize_mac(mac)
        if norm in normalized_wanted:
            found.append(d)
    found_norm = {_normalize_mac(d.get("mac") or "") for d in found}
    not_found = [m for m in macs if _normalize_mac(m) and _normalize_mac(m) not in found_norm]
    return found, not_found


def reboot(
    macs: list[str],
    base_url: str = DEFAULT_API_BASE_URL,
    timeout: float = 10.0,
    request_timeout: float = 60.0,
) -> dict[str, Any]:
    """
    Reboot one or more devices by MAC address.

    Args:
        macs: List of device MAC addresses (e.g. ["aa:bb:cc:dd:ee:ff"] or ["34:1b:22:f0:04:c3", "34:1b:22:f0:04:c4"]).
        base_url: Control Server API base URL.
        timeout: HTTP timeout in seconds.
        request_timeout: Max wait for each reboot request when the API returns PROCESSING.

    Returns:
        Dict with keys: success (bool), rebooted (list of device_id that succeeded),
        errors (list of dicts with device_id, message), not_found_macs (list of MACs not found on the server).
    """
    devices, not_found_macs = _resolve_macs_to_devices(macs, base_url, timeout, request_timeout)
    client = SDVoEAPIClient(base_url=base_url, timeout=timeout)
    rebooted: list[str] = []
    errors: list[dict[str, Any]] = []
    for d in devices:
        device_id = d.get("device_id")
        if not device_id:
            continue
        try:
            resp = client.reboot(device_id, request_timeout=request_timeout)
            status = resp.get("status")
            result = resp.get("result") or {}
            if status == "SUCCESS":
                for r in result.get("reboot", []):
                    did = r.get("device_id") if isinstance(r, dict) else str(r)
                    if did:
                        rebooted.append(did)
                for e in result.get("error", []):
                    errors.append({
                        "device_id": e.get("device_id", device_id),
                        "message": e.get("message") or e.get("reason", "unknown"),
                    })
            else:
                err = resp.get("error", {})
                errors.append({
                    "device_id": device_id,
                    "message": err.get("message") or err.get("reason", "request failed"),
                })
        except ControlServerError as ex:
            errors.append({"device_id": device_id, "message": str(ex)})
    return {
        "success": len(errors) == 0 and len(not_found_macs) == 0,
        "rebooted": rebooted,
        "errors": errors,
        "not_found_macs": not_found_macs,
    }


def factory(
    macs: list[str],
    base_url: str = DEFAULT_API_BASE_URL,
    timeout: float = 10.0,
    request_timeout: float = 60.0,
) -> dict[str, Any]:
    """
    Factory reset one or more devices by MAC address.

    Args:
        macs: List of device MAC addresses.
        base_url: Control Server API base URL.
        timeout: HTTP timeout in seconds.
        request_timeout: Max wait for each factory request when the API returns PROCESSING.

    Returns:
        Dict with keys: success (bool), reset (list of device_id that succeeded),
        errors (list of dicts with device_id, message), not_found_macs (list of MACs not found).
    """
    devices, not_found_macs = _resolve_macs_to_devices(macs, base_url, timeout, request_timeout)
    client = SDVoEAPIClient(base_url=base_url, timeout=timeout)
    reset: list[str] = []
    errors: list[dict[str, Any]] = []
    for d in devices:
        device_id = d.get("device_id")
        if not device_id:
            continue
        try:
            resp = client.factory(device_id, request_timeout=request_timeout)
            status = resp.get("status")
            result = resp.get("result") or {}
            if status == "SUCCESS":
                success_list = result.get("device_success") or result.get("factory") or result.get("reboot") or []
                for s in success_list:
                    did = s.get("device_id") if isinstance(s, dict) else str(s)
                    if did:
                        reset.append(did)
                for e in result.get("error", []):
                    errors.append({
                        "device_id": e.get("device_id", device_id),
                        "message": e.get("message") or e.get("reason", "unknown"),
                    })
            else:
                err = resp.get("error", {})
                errors.append({
                    "device_id": device_id,
                    "message": err.get("message") or err.get("reason", "request failed"),
                })
        except ControlServerError as ex:
            errors.append({"device_id": device_id, "message": str(ex)})
    return {
        "success": len(errors) == 0 and len(not_found_macs) == 0,
        "reset": reset,
        "errors": errors,
        "not_found_macs": not_found_macs,
    }


def get_device(
    macs: list[str],
    base_url: str = DEFAULT_API_BASE_URL,
    timeout: float = 10.0,
    request_timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """
    Get full device object(s) for one or more devices by MAC address.

    Args:
        macs: List of device MAC addresses.
        base_url: Control Server API base URL.
        timeout: HTTP timeout in seconds.
        request_timeout: Max wait when the API returns PROCESSING.

    Returns:
        List of device dicts (same shape as get_device_details: ip, mac, device_id, device_name,
        alias, type, firmware_version, etc.). Only devices whose MAC is in macs are returned.
        Empty list if none found or if macs is empty.
    """
    if not macs:
        return []
    all_devices = get_device_details(
        base_url=base_url, timeout=timeout, request_timeout=request_timeout
    )
    normalized_wanted = {_normalize_mac(m) for m in macs if _normalize_mac(m)}
    if not normalized_wanted:
        return []
    return [
        d for d in all_devices
        if _normalize_mac(d.get("mac") or "") in normalized_wanted
    ]


def netstat(
    macs: list[str],
    output_file: Optional[str | Path] = None,
    base_url: str = DEFAULT_API_BASE_URL,
    timeout: float = 10.0,
    request_timeout: float = 120.0,
    filter_name: Optional[str] = None,
) -> dict[str, Any] | str:
    """
    Read network statistics for one or more devices by MAC address.

    Args:
        macs: List of device MAC addresses.
        output_file: If set, write the full netstat JSON to this path and return the path (str).
            If None, return the result as a dict.
        base_url: Control Server API base URL.
        timeout: HTTP timeout in seconds.
        request_timeout: Max wait for netstat (can be long for many devices).
        filter_name: Optional filter: "counter", "bandwidth", or "error".

    Returns:
        If output_file is None: dict with keys statistics (list), error (list), filtered to the
        requested MACs. If output_file is set: the path to the written file as a string.
    """
    devices, not_found_macs = _resolve_macs_to_devices(macs, base_url, timeout, request_timeout)
    if not devices:
        result: dict[str, Any] = {
            "statistics": [],
            "error": [{"device_id": "", "reason": "NOT_FOUND", "message": f"MACs not found: {not_found_macs}"}]
            if not_found_macs else [],
        }
        if output_file:
            path = Path(output_file)
            path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return str(path.resolve())
        return result
    client = SDVoEAPIClient(base_url=base_url, timeout=timeout)
    device_ids = [d["device_id"] for d in devices if d.get("device_id")]
    normalized_found = {_normalize_mac(d.get("mac") or "") for d in devices}
    # Request ALL and filter by MAC (netstat for many devices in one call)
    resp = client.netstat_read(
        "ALL",
        filter_name=filter_name,
        request_timeout=request_timeout,
    )
    if resp.get("status") != "SUCCESS":
        err = resp.get("error", {})
        out = {
            "statistics": [],
            "error": [{"message": err.get("message") or err.get("reason", "netstat failed")}],
        }
        if output_file:
            Path(output_file).write_text(json.dumps(out, indent=2), encoding="utf-8")
            return str(Path(output_file).resolve())
        return out
    result_obj = resp.get("result") or {}
    stats_list = result_obj.get("statistics", [])
    error_list = result_obj.get("error", [])
    # Filter statistics to requested device_ids (we have device_ids from our resolved devices)
    device_id_set = set(device_ids)
    stats_filtered = [s for s in stats_list if s.get("device_id") in device_id_set]
    errors_filtered = [e for e in error_list if e.get("device_id") in device_id_set]
    if not_found_macs:
        errors_filtered.append({
            "device_id": "",
            "reason": "NOT_FOUND",
            "message": f"MACs not found: {not_found_macs}",
        })
    out = {"statistics": stats_filtered, "error": errors_filtered}
    if output_file:
        path = Path(output_file)
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        return str(path.resolve())
    return out
