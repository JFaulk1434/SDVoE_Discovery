"""HTTP client for BlueRiver SDVoE Control Server API.

Requires the control server to be running (default port 59402 when started by the CLI).
See PDS-062489 SDVoE Developers API Reference Guide.
"""

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from sdvoe_discovery.control_server_runner import DEFAULT_CONTROLSERVER_PORT

DEFAULT_API_BASE_URL = f"http://127.0.0.1:{DEFAULT_CONTROLSERVER_PORT}"


class ControlServerError(Exception):
    """Raised when the API returns an error or request fails."""

    def __init__(self, message: str, reason: Optional[str] = None, status: Optional[int] = None):
        super().__init__(message)
        self.reason = reason
        self.status = status


class SDVoEAPIClient:
    """Client for the BlueRiver Control Server REST API."""

    def __init__(self, base_url: str = DEFAULT_API_BASE_URL, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        query: Optional[dict] = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        if query:
            qs = "&".join(f"{k}={v}" for k, v in query.items())
            url = f"{url}?{qs}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
                err_json = json.loads(err_body)
                err_obj = err_json.get("error", {})
                raise ControlServerError(
                    err_obj.get("message", err_body),
                    reason=err_obj.get("reason"),
                    status=e.code,
                ) from e
            except (ValueError, KeyError):
                raise ControlServerError(str(e), status=e.code) from e
        except urllib.error.URLError as e:
            raise ControlServerError(f"Connection failed: {e.reason}") from e

    def get_version(self) -> dict:
        """GET /api - server and module version."""
        return self._request("GET", "/api")

    def get_device_list(self) -> dict:
        """GET /api/device - list of discovered devices (device IDs)."""
        return self._request("GET", "/api/device")

    def get(self, target: str, subset: str) -> dict:
        """POST get command: subset in [list, hello, identity, device, settings, ...]."""
        return self._request(
            "POST",
            f"/api/device/{target}",
            body={"op": "get", "subset": subset},
        )

    def get_request(self, request_id: int) -> dict:
        """GET /api/request/{request_id} - poll result of a PROCESSING command."""
        return self._request("GET", f"/api/request/{request_id}")

    def wait_for_request(self, request_id: int, poll_interval: float = 0.5, max_wait: float = 30.0) -> dict:
        """Poll until request completes or timeout. Returns final result."""
        import time
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            r = self.get_request(request_id)
            status = r.get("status")
            if status == "SUCCESS":
                return r
            if status == "ERROR":
                err = r.get("error", {})
                raise ControlServerError(err.get("message", "Request failed"), reason=err.get("reason"))
            time.sleep(poll_interval)
        raise ControlServerError("Request timed out")

    def get_devices_identity(self, target: str = "ALL") -> dict:
        """Get identity (name, type, firmware, etc.) for target. Returns immediately (identity is cached)."""
        return self.get(target, "identity")

    def get_devices_full(self, target: str = "ALL") -> dict:
        """Get full device object(s). May return PROCESSING + request_id; use wait_for_request if needed."""
        return self.get(target, "device")

    def _post_device_op(
        self,
        target: str,
        body: dict,
        request_timeout: float = 60.0,
    ) -> dict:
        """POST to /api/device/{target} with body. If response is PROCESSING, poll until done. Returns final result."""
        resp = self._request("POST", f"/api/device/{target}", body=body)
        status = resp.get("status")
        if status == "PROCESSING":
            req_id = resp.get("request_id")
            if req_id is not None:
                return self.wait_for_request(req_id, max_wait=request_timeout)
        return resp

    def reboot(self, target: str, request_timeout: float = 60.0) -> dict:
        """Reboot device(s). Target: device ID or ALL, ALL_TX, ALL_RX. Returns result with 'reboot' and/or 'error' arrays."""
        return self._post_device_op(target, {"op": "reboot"}, request_timeout=request_timeout)

    def factory(self, target: str, request_timeout: float = 60.0) -> dict:
        """Factory reset device(s). Target: device ID or ALL, ALL_TX, ALL_RX. Returns result with device success/error."""
        return self._post_device_op(target, {"op": "factory"}, request_timeout=request_timeout)

    def netstat_read(
        self,
        target: str,
        filter_name: Optional[str] = None,
        request_timeout: float = 120.0,
    ) -> dict:
        """Read network statistics. Target: device ID or ALL, ALL_TX, ALL_RX. Optional filter: counter, bandwidth, error."""
        body: dict = {"op": "netstat", "option": "read"}
        if filter_name:
            body["filter"] = filter_name
        return self._post_device_op(target, body, request_timeout=request_timeout)
