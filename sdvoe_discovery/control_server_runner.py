"""
Discover and run the SDVoE Control Server (controlserver-*).

- Finds any folder named controlserver-* in the project (version-agnostic).
- Detects OS/arch and selects the matching platform subfolder, or lets the user choose.
- Uses a non-privileged HTTP port (finds an available one).
- Generates a temporary config with http_port overridden so 80/443 are not required.
"""

import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Platform subfolder names (controlserver ships these). Map (sys.platform, machine) -> folder name.
_PLATFORM_MAP = {
    ("darwin", "arm64"): "macosx-aarch64",
    ("darwin", "aarch64"): "macosx-aarch64",
    ("darwin", "x86_64"): "macosx-x86_64",
    ("darwin", "amd64"): "macosx-x86_64",
    ("linux", "x86_64"): "linux-x86_64",
    ("linux", "amd64"): "linux-x86_64",
    ("linux", "aarch64"): "linux-aarch64",
    ("linux", "arm64"): "linux-aarch64",
    ("win32", "amd64"): "windows-x86_64-w64-mingw32",
    ("win32", "x86_64"): "windows-x86_64-w64-mingw32",
}

# Binary name per platform
_BINARY_NAME = "controlserver.exe" if sys.platform == "win32" else "controlserver"


def find_controlserver_root(search_start: Optional[Path] = None) -> Optional[Path]:
    """
    Return the first directory whose name starts with "controlserver-" under search_start.
    If search_start is None, try multiple locations in order: cwd, SDVOE_CONTROLSERVER_ROOT (if set),
    package parent (project root when running from source), then ~/.sdvoe-discovery (for global installs).
    search_start may be a directory that either is named controlserver-* or contains a child named controlserver-*.
    """
    def _find_in(root: Path) -> Optional[Path]:
        root = Path(root)
        if not root.is_dir():
            return None
        if root.name.startswith("controlserver-"):
            return root
        for item in sorted(root.iterdir()):
            if item.is_dir() and item.name.startswith("controlserver-"):
                return item
        return None

    if search_start is not None:
        return _find_in(search_start)

    candidates: list[Path] = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent,
    ]
    env_root = os.environ.get("SDVOE_CONTROLSERVER_ROOT")
    if env_root:
        candidates.insert(1, Path(env_root))
    sdvoe_home = Path.home() / ".sdvoe-discovery"
    if sdvoe_home not in candidates:
        candidates.append(sdvoe_home)

    for root in candidates:
        found = _find_in(root)
        if found is not None:
            return found
    return None


def get_available_platform_folders(root: Path) -> list[str]:
    """Return subfolder names that contain a controlserver binary."""
    folders = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        binary = item / _BINARY_NAME
        if binary.is_file() and os.access(binary, os.X_OK):
            folders.append(item.name)
    return sorted(folders)


def detect_platform() -> Optional[str]:
    """
    Return the platform subfolder name for the current OS/arch, or None if not in our map.
    """
    import platform
    machine = (platform.machine() or "").lower()
    key = (sys.platform, machine)
    return _PLATFORM_MAP.get(key)


def get_binary_path(
    root: Path,
    platform_folder: Optional[str] = None,
) -> Optional[Path]:
    """
    Return path to the controlserver binary.
    If platform_folder is None, use detect_platform() and fall back to first available.
    """
    root = Path(root)
    if platform_folder:
        binary = root / platform_folder / _BINARY_NAME
        if binary.is_file():
            return binary
        return None
    # Auto-detect
    detected = detect_platform()
    if detected:
        binary = root / detected / _BINARY_NAME
        if binary.is_file():
            return binary
    # First available
    for name in get_available_platform_folders(root):
        binary = root / name / _BINARY_NAME
        if binary.is_file():
            return binary
    return None


def find_available_port(start: int = 8080, end: int = 65535) -> Optional[int]:
    """Return the first TCP port in [start, end] that is not in use, or None."""
    for port in range(start, min(end, start + 1000)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return None


def create_config_with_http_port(
    base_config_path: Path,
    http_port: int,
    temp_dir: Optional[Path] = None,
) -> Path:
    """
    Create a temporary config file equal to base_config_path but with http_port = http_port.
    Returns path to the temp file.
    """
    base_config_path = Path(base_config_path)
    text = base_config_path.read_text(encoding="utf-8", errors="replace")
    # Replace http_port line (allow optional spaces around =)
    text = re.sub(
        r"^(\s*http_port\s*=\s*)\d+(\s*)$",
        rf"\g<1>{http_port}\g<2>",
        text,
        flags=re.MULTILINE,
    )
    if temp_dir is None:
        temp_dir = Path(tempfile.gettempdir())
    temp_dir = Path(temp_dir)
    fd, path = tempfile.mkstemp(suffix=".conf", prefix="sdvoe_controlserver_", dir=str(temp_dir))
    os.close(fd)
    Path(path).write_text(text, encoding="utf-8")
    return Path(path)


def is_controlserver_running(base_url: str, timeout: float = 1.0) -> bool:
    """Return True if a GET to base_url/api succeeds (or connection is accepted)."""
    import urllib.request
    url = f"{base_url.rstrip('/')}/api"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as _:
            return True
    except Exception:
        return False


def start_controlserver(
    binary_path: Path,
    config_path: Path,
    cwd: Optional[Path] = None,
) -> subprocess.Popen:
    """
    Start the control server. binary_path is the controlserver executable;
    config_path is the path to the config file (e.g. temp config with http_port set).
    cwd should be the platform directory (so relative paths in config work).
    Returns the Popen instance. Caller should wait briefly then use is_controlserver_running.
    """
    cwd = Path(cwd) if cwd else binary_path.parent
    proc = subprocess.Popen(
        [str(binary_path), "--file", str(config_path)],
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    return proc


def start_controlserver_in_new_window(
    binary_path: Path,
    config_path: Path,
    cwd: Optional[Path] = None,
) -> bool:
    """
    Start the control server in a new, minimized OS window so it persists after the CLI exits.
    Returns True if the launch command succeeded (server may still be starting).
    """
    cwd = Path(cwd) if cwd else binary_path.parent
    binary_path = binary_path.resolve()
    config_path = config_path.resolve()
    cwd = cwd.resolve()

    if sys.platform == "darwin":
        # macOS: AppleScript to open Terminal, run server, then minimize window
        def _esc_shell(s: str) -> str:
            """Escape for shell single-quoted string: ' -> '\'' """
            return str(s).replace("'", "'\"'\"'")
        cmd = f"cd '{_esc_shell(cwd)}' && '{_esc_shell(binary_path)}' --file '{_esc_shell(config_path)}'"
        # Escape for AppleScript double-quoted string: \ -> \\, " -> \"
        cmd_osa = cmd.replace("\\", "\\\\").replace('"', '\\"')
        try:
            subprocess.run(
                ["osascript", "-e", f'tell application "Terminal" to do script "{cmd_osa}"'],
                check=True,
                timeout=10,
            )
            subprocess.run(
                ["osascript", "-e", "tell application \"Terminal\" to activate", "-e", "delay 0.5", "-e", "tell application \"Terminal\" to set miniaturized of front window to true"],
                check=False,
                timeout=5,
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False

    if sys.platform == "win32":
        # Windows: start /min cmd /k "cd /d cwd && binary --file config"
        cmd = f'cd /d "{cwd}" && "{binary_path}" --file "{config_path}"'
        try:
            subprocess.run(
                ["cmd", "/c", "start", '"SDVoE Control Server"', "/min", "cmd", "/k", cmd],
                check=True,
                timeout=10,
                cwd=str(cwd),
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # Linux: try gnome-terminal (minimize) or xterm -iconic, else fall back to background process
    if sys.platform == "linux":
        cmd = f"cd '{cwd}' && exec '{binary_path}' --file '{config_path}'"
        for launcher in (
            ["gnome-terminal", "--window", "--minimize", "--", "bash", "-c", cmd],
            ["gnome-terminal", "--", "bash", "-c", cmd],
            ["xterm", "-iconic", "-e", f"cd {cwd} && {binary_path} --file {config_path}"],
            ["xterm", "-e", f"cd {cwd} && {binary_path} --file {config_path}; exec bash"],
        ):
            try:
                subprocess.Popen(
                    launcher,
                    cwd=str(cwd),
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except FileNotFoundError:
                continue
    return False


def get_http_port_from_config(config_path: Path) -> Optional[int]:
    """Read http_port from an existing config file."""
    text = Path(config_path).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^\s*http_port\s*=\s*(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def run_controlserver_auto(
    project_root: Optional[Path] = None,
    platform_folder: Optional[str] = None,
    preferred_port_start: int = 8080,
    in_new_window: bool = True,
) -> tuple[Optional[str], Optional[subprocess.Popen], Optional[Path]]:
    """
    If control server is already running at 127.0.0.1:preferred_port_start or common ports, return (base_url, None, None).
    Otherwise find controlserver-*, pick platform, find a free port, create temp config, start process.
    If in_new_window is True (default), start the server in a new minimized OS window so it persists after the CLI exits.
    Returns (base_url, process_or_None, temp_config_path). Process is None when in_new_window is True.
    """
    # Pass None to try all locations (cwd, SDVOE_CONTROLSERVER_ROOT, package parent, ~/.sdvoe-discovery)
    root = find_controlserver_root(project_root)
    if not root:
        return (None, None, None)

    # Check if already running on a few common ports
    for port in (80, 443, preferred_port_start, 9080, 9081):
        url = f"http://127.0.0.1:{port}"
        if is_controlserver_running(url):
            return (url, None, None)

    binary = get_binary_path(root, platform_folder)
    if not binary:
        return (None, None, None)

    port = find_available_port(start=preferred_port_start)
    if not port:
        return (None, None, None)

    platform_dir = binary.parent
    base_config = platform_dir / "controlserver.conf"
    if not base_config.is_file():
        return (None, None, None)

    temp_config = create_config_with_http_port(base_config, port)
    try:
        if in_new_window:
            launched = start_controlserver_in_new_window(binary, temp_config, cwd=platform_dir)
            if launched:
                return (f"http://127.0.0.1:{port}", None, temp_config)
            # Fall back to background process if new-window launch failed
        proc = start_controlserver(binary, temp_config, cwd=platform_dir)
        if proc.poll() is not None:
            return (None, None, temp_config)
        return (f"http://127.0.0.1:{port}", proc, temp_config)
    except Exception:
        try:
            temp_config.unlink(missing_ok=True)
        except Exception:
            pass
        return (None, None, None)
