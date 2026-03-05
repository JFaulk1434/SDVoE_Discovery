"""High-level device discovery via BlueRiver Control Server API.

Use these functions from other Python scripts for firmware updates, control, etc.
"""

from typing import Any, Optional

from sdvoe_discovery.api_client import SDVoEAPIClient, ControlServerError


def _device_id_to_mac(device_id: str) -> str:
    """Format device_id (often 12 hex chars) as MAC aa:bb:cc:dd:ee:ff."""
    s = device_id.replace(":", "").lower()
    if len(s) == 12 and all(c in "0123456789abcdef" for c in s):
        return ":".join(s[i : i + 2] for i in range(0, 12, 2))
    return device_id


def _pick_ip(*candidates: Any) -> Optional[str]:
    """First non-empty string that looks like an IP address."""
    for v in candidates:
        if v is None:
            continue
        if isinstance(v, str) and v.strip() and v != "0.0.0.0":
            return v.strip()
        if isinstance(v, dict) and "address" in v:
            a = v.get("address")
            if isinstance(a, str) and a.strip() and a != "0.0.0.0":
                return a.strip()
    return None


def _ip_from_nodes(nodes: list) -> Optional[str]:
    """Try to find an IP address from device nodes (e.g. NETWORK_INTERFACE node has status.ip.address)."""
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for key in ("status", "configuration"):
            obj = node.get(key)
            if not isinstance(obj, dict):
                continue
            ip_obj = obj.get("ip")
            if isinstance(ip_obj, dict) and "address" in ip_obj:
                ip = _pick_ip(ip_obj.get("address"))
                if ip:
                    return ip
            addr = obj.get("address") or obj.get("ip_address")
            ip = _pick_ip(addr)
            if ip:
                return ip
    return None


def _extract_identity(dev: dict) -> dict:
    """Build a flat dict from get identity device object (or device object with identity)."""
    identity = dev.get("identity", {}) if isinstance(dev.get("identity"), dict) else {}
    status = dev.get("status", {}) if isinstance(dev.get("status"), dict) else {}
    device_id = dev.get("device_id", "") or ""
    chipset = identity.get("chipset_type") or dev.get("chipset_type")
    device_type = identity.get("device_type") or dev.get("device_type")
    device_name = identity.get("device_name") or dev.get("device_name")
    out = {
        "device_id": device_id,
        "mac": _device_id_to_mac(device_id) if device_id else None,
        "ip": _pick_ip(
            status.get("ip_address"),
            status.get("ip") if isinstance(status.get("ip"), str) else None,
            status.get("ip", {}).get("address") if isinstance(status.get("ip"), dict) else None,
            identity.get("chip_ip"),
        ),
        "device_name": device_name,
        "alias": identity.get("alias") or dev.get("alias"),
        "type": device_type or chipset,
        "firmware_version": identity.get("firmware_version") or dev.get("firmware_version"),
        "chipset_type": chipset,
    }
    if out["ip"] is None and isinstance(status, dict):
        ip_obj = status.get("ip") or status.get("ip_address")
        if isinstance(ip_obj, dict) and "address" in ip_obj:
            out["ip"] = _pick_ip(ip_obj.get("address"))
        elif isinstance(ip_obj, str):
            out["ip"] = _pick_ip(ip_obj)
    return out


def _extract_device_detail(dev: dict) -> dict:
    """Build a flat dict from get device (full) object, including identity + config/status + nodes."""
    base = _extract_identity(dev)
    identity = dev.get("identity", {}) if isinstance(dev.get("identity"), dict) else {}
    status = dev.get("status", {}) if isinstance(dev.get("status"), dict) else {}
    config = dev.get("configuration", {}) if isinstance(dev.get("configuration"), dict) else {}
    base["firmware_comment"] = identity.get("firmware_comment")
    base["engine"] = identity.get("engine")
    if isinstance(status, dict):
        if base.get("ip") is None:
            base["ip"] = _pick_ip(
                status.get("ip_address"),
                status.get("ip") if isinstance(status.get("ip"), str) else None,
                status.get("ip", {}).get("address") if isinstance(status.get("ip"), dict) else None,
            )
        base["active"] = status.get("active")
        base["update_in_progress"] = status.get("update_in_progress")
    if base.get("ip") is None:
        base["ip"] = _ip_from_nodes(dev.get("nodes", []))
    if isinstance(config, dict):
        base["device_name"] = base.get("device_name") or config.get("device_name")
        if base.get("alias") is None:
            base["alias"] = config.get("alias") or config.get("device_name") or base.get("device_name")
    if base.get("alias") is None:
        base["alias"] = base.get("device_name")
    return base


def get_device_list(
    base_url: str = "http://127.0.0.1:80",
    timeout: float = 10.0,
    request_timeout: float = 60.0,
) -> list[dict]:
    """
    Fast list of all devices with at least IP and MAC (device_id), plus name, type, firmware.

    Uses get device for ALL so IP and type (chipset_type) are available; waits for result if PROCESSING.
    Requires the BlueRiver control server to be running.
    """
    client = SDVoEAPIClient(base_url=base_url, timeout=timeout)
    result_list: list[dict] = []

    try:
        resp = client.get_devices_full("ALL")
        status = resp.get("status")
        if status == "PROCESSING":
            req_id = resp.get("request_id")
            if req_id is not None:
                resp = client.wait_for_request(req_id, max_wait=request_timeout)
                status = resp.get("status")
        if status != "SUCCESS":
            err = resp.get("error", {})
            raise ControlServerError(err.get("message", "get device failed"), reason=err.get("reason"))
        result = resp.get("result")
        if not result:
            return []
        for dev in result.get("devices", []):
            detail = _extract_device_detail(dev)
            result_list.append({k: detail.get(k) for k in (
                "device_id", "mac", "ip", "device_name", "alias", "type", "firmware_version",
                "chipset_type",
            )})
        return result_list
    except ControlServerError:
        raise
    except Exception as e:
        raise ControlServerError(str(e)) from e


def get_device_details(
    base_url: str = "http://127.0.0.1:80",
    timeout: float = 10.0,
    request_timeout: float = 60.0,
) -> list[dict]:
    """
    Detailed list of all devices (IP, MAC, firmware, model, etc.).

    Uses get device for ALL; may poll for PROCESSING. Use for full info.
    """
    client = SDVoEAPIClient(base_url=base_url, timeout=timeout)
    result_list: list[dict] = []

    try:
        resp = client.get_devices_full("ALL")
        status = resp.get("status")
        if status == "PROCESSING":
            req_id = resp.get("request_id")
            if req_id is not None:
                resp = client.wait_for_request(req_id, max_wait=request_timeout)
                status = resp.get("status")
        if status != "SUCCESS":
            err = resp.get("error", {})
            raise ControlServerError(err.get("message", "get device failed"), reason=err.get("reason"))
        result = resp.get("result")
        if not result:
            return []
        for dev in result.get("devices", []):
            result_list.append(_extract_device_detail(dev))
        return result_list
    except ControlServerError:
        raise
    except Exception as e:
        raise ControlServerError(str(e)) from e


def get_devices_fast(
    base_url: str = "http://127.0.0.1:80",
    timeout: float = 10.0,
) -> list[dict]:
    """
    Alias for get_device_list(): fastest way to get IP + MAC (and name, type, firmware).
    """
    return get_device_list(base_url=base_url, timeout=timeout)
