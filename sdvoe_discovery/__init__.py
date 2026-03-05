"""SDVoE Discovery - CLI and Python API for SDVoE device discovery."""

__version__ = "0.1.0"

from sdvoe_discovery.discovery import (
    SDVoEDiscovery,
    discover_devices,
    get_response_breakdown,
)
from sdvoe_discovery.api_client import SDVoEAPIClient, ControlServerError
from sdvoe_discovery.device_info import get_device_list, get_device_details, get_devices_fast
from sdvoe_discovery.control import reboot, factory, get_device, netstat

__all__ = [
    "__version__",
    "SDVoEDiscovery",
    "discover_devices",
    "get_response_breakdown",
    "SDVoEAPIClient",
    "ControlServerError",
    "get_device_list",
    "get_device_details",
    "get_devices_fast",
    "reboot",
    "factory",
    "get_device",
    "netstat",
]
