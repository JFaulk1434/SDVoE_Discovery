"""SDVoE device discovery via UDP broadcast (MCU protocol from mcu_api.json)."""

import json
import re
import socket
import struct
import time
import binascii
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

# MCU protocol defaults (from mcu_api.json) — same as mcu_discovery_old.py
DEFAULT_SEND_PORT = 6239
DEFAULT_RESPONSE_PORT = 6240
DEFAULT_BROADCAST_IP = "255.255.255.255"
MCU_DISCOVERY_MAGIC = 0x11223344


def _mac_to_string(mac_bytes: bytes) -> str:
    return ":".join(f"{b:02x}" for b in mac_bytes)


def _load_mcu_config(path: Union[str, Path]) -> Optional[dict[str, Any]]:
    """Load discovery send/response config from mcu_api.json."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        with open(p, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    protocol = data.get("protocol", {})
    discovery = protocol.get("discovery", {})
    send_cfg = discovery.get("send", {})
    resp_cfg = discovery.get("response", {})
    magic_val = None
    for entry in send_cfg.get("fields", []):
        if isinstance(entry, dict) and "Magic_number" in entry:
            magic_val = entry["Magic_number"]
            break
    if isinstance(magic_val, str):
        magic_number = int(magic_val, 16)
    else:
        magic_number = MCU_DISCOVERY_MAGIC
    return {
        "send_port": send_cfg.get("port", DEFAULT_SEND_PORT),
        "broadcast_ip": send_cfg.get("ip", DEFAULT_BROADCAST_IP),
        "response_port": resp_cfg.get("port", DEFAULT_RESPONSE_PORT),
        "magic_number": magic_number,
    }


@dataclass
class DeviceResponse:
    """Response from a discovered device (raw + parsed fields)."""

    ip: str
    port: int
    raw_bytes: bytes
    hex_dump: str = field(init=False)
    parsed: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.hex_dump = binascii.hexlify(self.raw_bytes).decode()

    def to_dict(self) -> dict:
        out: dict = {
            "ip": self.ip,
            "port": self.port,
            "raw_hex": self.hex_dump,
            "raw_length": len(self.raw_bytes),
        }
        if self.parsed:
            out["parsed"] = self.parsed
        return out


def get_response_breakdown(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Return a field-by-field breakdown of a discovery response for display."""
    breakdown: list[dict[str, Any]] = []
    if len(raw_bytes) < 16:
        return breakdown
    # Header: magic, msg_id, protocol_version, command
    magic, msg_id, proto_ver, cmd = struct.unpack(">LLLL", raw_bytes[:16])
    breakdown.append({"offset": "0-3", "name": "Magic_number", "hex": raw_bytes[0:4].hex(), "value": f"0x{magic:08X}"})
    breakdown.append({"offset": "4-7", "name": "Msg_ID", "hex": raw_bytes[4:8].hex(), "value": str(msg_id)})
    breakdown.append({"offset": "8-11", "name": "Protocol_Version", "hex": raw_bytes[8:12].hex(), "value": str(proto_ver)})
    breakdown.append({"offset": "12-15", "name": "Command", "hex": raw_bytes[12:16].hex(), "value": str(cmd)})
    if len(raw_bytes) >= 24:
        breakdown.append({"offset": "16-17", "name": "(reserved?)", "hex": raw_bytes[16:18].hex(), "value": ""})
        breakdown.append({"offset": "18-23", "name": "MCU_MAC", "hex": raw_bytes[18:24].hex(), "value": _mac_to_string(raw_bytes[18:24])})
    if len(raw_bytes) >= 30:
        breakdown.append({"offset": "24-29", "name": "(reserved?)", "hex": raw_bytes[24:30].hex(), "value": ""})
    if len(raw_bytes) >= 36:
        breakdown.append({"offset": "30-35", "name": "Dante_MAC", "hex": raw_bytes[30:36].hex(), "value": _mac_to_string(raw_bytes[30:36])})
    if len(raw_bytes) >= 40:
        breakdown.append({"offset": "36-37", "name": "Board_type", "hex": raw_bytes[36:38].hex(), "value": str(struct.unpack(">H", raw_bytes[36:38])[0])})
        breakdown.append({"offset": "38-39", "name": "USB_Config", "hex": raw_bytes[38:40].hex(), "value": str(struct.unpack(">H", raw_bytes[38:40])[0])})
    if len(raw_bytes) > 40:
        rest = raw_bytes[40:]
        try:
            ascii_str = rest.decode("ascii", errors="replace").strip("\x00").strip()
            breakdown.append({"offset": f"40-{len(raw_bytes)-1}", "name": "MCU_Version (firmware)", "hex": rest.hex(), "value": ascii_str or "(empty)"})
        except Exception:
            breakdown.append({"offset": f"40-{len(raw_bytes)-1}", "name": "MCU_Version (firmware)", "hex": rest.hex(), "value": "(binary)"})
    return breakdown


def _parse_discovery_response(
    data: bytes, addr: tuple[str, int], magic_number: int
) -> Optional[dict[str, Any]]:
    """Parse MCU discovery response (same layout as mcu_discovery_old.py)."""
    if len(data) < 16:
        return None
    magic, msg_id, protocol_version, command = struct.unpack(">LLLL", data[:16])
    if magic != magic_number:
        return None

    # MCU MAC at offset 18, AVP = MCU - 1
    mcu_mac_str = "00:00:00:00:00:00"
    avp_mac_str = "00:00:00:00:00:00"
    if len(data) >= 24:
        mcu_mac_str = _mac_to_string(data[18:24])
        try:
            mcu_mac_int = int(mcu_mac_str.replace(":", ""), 16)
            avp_mac_int = (mcu_mac_int - 1) & 0xFFFFFFFFFFFF
            avp_mac_str = ":".join(
                f"{(avp_mac_int >> (40 - i * 8)) & 0xFF:02x}"
                for i in range(6)
            )
        except ValueError:
            avp_mac_str = mcu_mac_str

    # Dante MAC at offset 30
    dante_mac_str = "00:00:00:00:00:00"
    if len(data) >= 36:
        dante_mac_str = _mac_to_string(data[30:36])

    # Board type, USB config at 36
    board_type: Optional[int] = None
    usb_config: Optional[int] = None
    offset = 40
    if len(data) >= 40:
        board_type, usb_config = struct.unpack(">HH", data[36:40])

    # Firmware/version string after offset 40
    mcu_version: Optional[str] = None
    if len(data) > offset:
        firmware_bytes = data[offset:]
        firmware_str = firmware_bytes.decode("ascii", errors="ignore").strip("\x00")
        version_match = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", firmware_str)
        build_match = re.search(r"build\s+(\d+)", firmware_str, re.I)
        if version_match:
            mcu_version = version_match.group(1)
            if build_match:
                mcu_version += f" build {build_match.group(1)}"
        elif build_match:
            mcu_version = f"build {build_match.group(1)}"
        else:
            mcu_version = firmware_str if firmware_str else "Unknown"

    result: dict[str, Any] = {
        "ip": addr[0],
        "mcu_mac": mcu_mac_str,
        "avp_mac": avp_mac_str,
        "dante_mac": dante_mac_str,
    }
    if board_type is not None:
        result["board_type"] = board_type
    if usb_config is not None:
        result["usb_config"] = usb_config
    if mcu_version is not None:
        result["mcu_version"] = mcu_version
    return result


class SDVoEDiscovery:
    """
    Discover SDVoE/MCU devices using UDP broadcast.
    Uses MCU protocol (mcu_api.json) by default: send 6239, listen 6240, magic 0x11223344.
    """

    def __init__(
        self,
        send_port: int = DEFAULT_SEND_PORT,
        response_port: int = DEFAULT_RESPONSE_PORT,
        broadcast_ip: str = DEFAULT_BROADCAST_IP,
        magic_number: int = MCU_DISCOVERY_MAGIC,
        timeout: float = 3.0,
        config_path: Optional[Union[str, Path]] = None,
    ):
        if config_path:
            cfg = _load_mcu_config(config_path)
            if cfg:
                send_port = cfg["send_port"]
                response_port = cfg["response_port"]
                broadcast_ip = cfg["broadcast_ip"]
                magic_number = cfg["magic_number"]
        self.send_port = send_port
        self.response_port = response_port
        self.broadcast_ip = broadcast_ip
        self.magic_number = magic_number
        self.timeout = timeout

    def build_discovery_packet(self) -> bytes:
        """Build MCU discovery request: magic, msg_id, protocol_version=0, command=0."""
        msg_id = int(time.time()) & 0xFFFFFFFF
        return struct.pack(
            ">LLLL",
            self.magic_number,
            msg_id,
            0,
            0,
        )

    def discover(
        self,
        interface_ip: Optional[str] = None,
    ) -> list[DeviceResponse]:
        """Send discovery broadcast and return device responses (raw + parsed)."""
        responses: list[DeviceResponse] = []

        try:
            send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            if interface_ip:
                try:
                    send_sock.bind((interface_ip, 0))
                except OSError:
                    send_sock.bind(("0.0.0.0", 0))
            else:
                send_sock.bind(("0.0.0.0", 0))

            recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            recv_sock.bind(("0.0.0.0", self.response_port))
            recv_sock.settimeout(0.5)

            packet = self.build_discovery_packet()
            send_sock.sendto(packet, (self.broadcast_ip, self.send_port))
            send_sock.close()

            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    data, (ip, port) = recv_sock.recvfrom(4096)
                    parsed = _parse_discovery_response(
                        data, (ip, port), self.magic_number
                    )
                    responses.append(
                        DeviceResponse(
                            ip=ip,
                            port=port,
                            raw_bytes=data,
                            parsed=parsed or {},
                        )
                    )
                except socket.timeout:
                    continue
                except OSError:
                    break

            recv_sock.close()
        except OSError as e:
            raise RuntimeError(f"Discovery socket error: {e}") from e

        return responses


def discover_devices(
    interface_ip: Optional[str] = None,
    send_port: Optional[int] = None,
    response_port: Optional[int] = None,
    timeout: float = 3.0,
    config_path: Optional[Union[str, Path]] = None,
) -> list[DeviceResponse]:
    """Run discovery and return device responses (uses mcu_api.json if config_path given)."""
    resolved_send = send_port
    resolved_response = response_port
    if config_path:
        cfg = _load_mcu_config(config_path)
        if cfg:
            if resolved_send is None:
                resolved_send = cfg["send_port"]
            if resolved_response is None:
                resolved_response = cfg["response_port"]
    if resolved_send is None:
        resolved_send = DEFAULT_SEND_PORT
    if resolved_response is None:
        resolved_response = DEFAULT_RESPONSE_PORT
    discovery = SDVoEDiscovery(
        send_port=resolved_send,
        response_port=resolved_response,
        timeout=timeout,
        config_path=config_path,
    )
    return discovery.discover(interface_ip=interface_ip)
