# SDVoE Discovery

CLI and Python API for discovering and controlling SDVoE devices: **BlueRiver Control Server API** (list, detail, reboot, factory, get-device, netstat) and **UDP broadcast** discovery (no server required). All Python API output is JSON-serializable (dicts/lists) for use in other projects.

## Install

Clone the repo and install in editable mode so the Control Server can be included and auto-started by the CLI:

```bash
git clone https://github.com/your-username/SDVoE_Discovery.git
cd SDVoE_Discovery
pip install -e .
```

Place the BlueRiver Control Server (e.g. a `controlserver-*` folder from the BlueRiver SDK) in the project root. The CLI will detect it and can start the server for you when you run `list`, `detail`, or control commands.

**Global install (use Control Server from anywhere):** Install with `pip install .` or `pip install -e .` (no need to be in the repo). Then put the Control Server in one of these places so the CLI can find and start it:

- **`~/.sdvoe-discovery/`** — copy your `controlserver-*` folder here (e.g. `~/.sdvoe-discovery/controlserver-1.2.3/`).
- **Or set `SDVOE_CONTROLSERVER_ROOT`** — point it at the `controlserver-*` folder:

  **macOS / Linux (current terminal):**
  ```bash
  export SDVOE_CONTROLSERVER_ROOT=/path/to/controlserver-1.2.3
  ```

  **macOS / Linux (persistent):** Add the same line to your shell config file (`~/.zshrc`, `~/.bashrc`, or `~/.profile`), then open a new terminal or run `source ~/.zshrc` (or the file you edited).

  **Windows (Command Prompt, current session):**
  ```cmd
  set SDVOE_CONTROLSERVER_ROOT=C:\path\to\controlserver-1.2.3
  ```

  **Windows (PowerShell, current session):**
  ```powershell
  $env:SDVOE_CONTROLSERVER_ROOT = "C:\path\to\controlserver-1.2.3"
  ```

  **Windows (persistent):** System Properties → Advanced → Environment Variables → New (User or System) → Variable name `SDVOE_CONTROLSERVER_ROOT`, value `C:\path\to\controlserver-1.2.3`. Restart the terminal (or reboot) for it to take effect.

The CLI looks (in order) at: current directory, `SDVOE_CONTROLSERVER_ROOT`, the package/project root, then `~/.sdvoe-discovery`.

**Optional:** `pip install sdvoe-discovery` from PyPI is available for discovery-only use or when you run the Control Server elsewhere; use `--api-url` to point at it, or `sdvoe-discovery broadcast` for UDP discovery without the server.

## CLI

### Discovery commands

| Command     | Description |
|------------|-------------|
| `list`     | Fast list via API: IP, MAC, name, type, firmware. Device IDs (1, 2, …) for use with control commands. |
| `detail`   | Full device info via API. |
| `broadcast`| UDP broadcast discovery (MCU protocol); no Control Server required. |

### Control commands (target = device ID from `list`, or `all`)

| Command       | Description |
|---------------|-------------|
| `reboot ID\|all`   | Reboot device(s). |
| `factory ID\|all`  | Factory reset device(s). |
| `get-device ID\|all` | Get full device object(s). |
| `netstat ID\|all`   | Read network statistics. Use `--output FILE` for large output. |

Output: **text** (default, with Rich highlighting) or **JSON** (`--format json`).

### CLI examples

```bash
# Discovery
sdvoe-discovery list
sdvoe-discovery list -f json
sdvoe-discovery detail
sdvoe-discovery broadcast
sdvoe-discovery broadcast -t 5 -f json --show-response

# Control (use device ID from list, or "all")
sdvoe-discovery reboot 1
sdvoe-discovery reboot all
sdvoe-discovery factory 2
sdvoe-discovery get-device all --format json
sdvoe-discovery netstat all -o netstat.json

# Control Server URL / options
sdvoe-discovery list --api-url http://192.168.1.10:80
sdvoe-discovery list --no-start
sdvoe-discovery list --start-server --server-warmup 5
```

### Control Server (list / detail / control)

- If the server is not running, the CLI can start it:
  - **Prompt**: “Control server not running. Start it? [Y/n]” — starts in a **minimized new window** so it stays up after the CLI exits.
  - `--start-server` — Start automatically (no prompt).
  - `--no-start` — Do not start; fail if server not reachable.
- `--api-url` (default `http://127.0.0.1:80`), `--timeout`, `--request-timeout`, `--server-warmup` (seconds after start).

---

## Python API

When **importing** the module, all control APIs take **lists of device MAC addresses** and return **dicts or lists** (no Rich/print). Discovery APIs return lists of dicts.

### Discovery (dict/list output)

```python
from sdvoe_discovery import (
    get_device_list,    # list[dict] — IP, MAC, name, type, firmware
    get_device_details, # list[dict] — full device info
    get_devices_fast,   # alias for get_device_list
    discover_devices,   # UDP broadcast — list of DeviceResponse (use .to_dict() for dict)
    SDVoEAPIClient,
    ControlServerError,
)

# Control Server must be running
devices: list[dict] = get_device_list(base_url="http://127.0.0.1:80")
for d in devices:
    print(d["ip"], d["mac"], d.get("firmware_version"))

devices = get_device_details(base_url="http://127.0.0.1:80")

# UDP broadcast (no Control Server)
responses = discover_devices(timeout=3.0)
for dev in responses:
    print(dev.ip, dev.parsed.get("mcu_mac"))
```

### Control by MAC (reboot, factory, get_device, netstat)

All four functions take a **list of MAC addresses** (one or many). They resolve MACs to devices via the API and return **dict** or **list[dict]**.

```python
from sdvoe_discovery import reboot, factory, get_device, netstat, ControlServerError

# Single or multiple MACs
macs = ["34:1b:22:f0:04:c3"]
macs = ["34:1b:22:f0:04:c3", "34:1b:22:f0:04:c4"]
```

#### `reboot(macs, ...) -> dict`

Reboot devices by MAC. Returns a success summary and per-device results.

- **Args:** `macs: list[str]`, `base_url: str = "http://127.0.0.1:80"`, `timeout: float = 10.0`, `request_timeout: float = 60.0`
- **Returns:** `dict` with keys: `success` (bool), `rebooted` (list of device_id), `errors` (list of `{device_id, message}`), `not_found_macs` (list of MACs not found)

```python
result = reboot(macs, base_url="http://127.0.0.1:80")
if result["success"]:
    print("Rebooted:", result["rebooted"])
else:
    print("Errors:", result["errors"], "Not found:", result["not_found_macs"])
```

#### `factory(macs, ...) -> dict`

Factory reset devices by MAC.

- **Args:** `macs: list[str]`, `base_url`, `timeout`, `request_timeout`
- **Returns:** `dict` with keys: `success`, `reset` (list of device_id), `errors`, `not_found_macs`

```python
result = factory(macs)
```

#### `get_device(macs, ...) -> list[dict]`

Get full device object(s) for the given MACs.

- **Args:** `macs: list[str]`, `base_url`, `timeout`, `request_timeout`
- **Returns:** `list[dict]` — same shape as `get_device_details` (ip, mac, device_id, device_name, alias, type, firmware_version, etc.). Only devices whose MAC is in `macs` are returned.

```python
devices = get_device(macs)
for d in devices:
    print(d["device_id"], d["ip"], d["firmware_version"])
```

#### `netstat(macs, output_file=None, ...) -> dict | str`

Read network statistics for the given MACs. Optionally write JSON to a file.

- **Args:** `macs: list[str]`, `output_file: str | Path | None = None`, `base_url`, `timeout`, `request_timeout: float = 120.0`, `filter_name: str | None = None` (e.g. `"counter"`, `"bandwidth"`, `"error"`)
- **Returns:** If `output_file` is `None`: `dict` with keys `statistics`, `error`. If `output_file` is set: **str** (path to the written file).

```python
# Return dict
data = netstat(macs)
print(data["statistics"], data["error"])

# Write to file (e.g. for netstat all — large payload)
path = netstat(macs, output_file="/tmp/netstat.json")
print("Wrote", path)
```

### Low-level client

For custom Control Server API calls:

```python
from sdvoe_discovery import SDVoEAPIClient, ControlServerError

client = SDVoEAPIClient(base_url="http://127.0.0.1:80")
r = client.get("ALL", "identity")
r = client.reboot("001ec0f03668")  # by device_id
r = client.netstat_read("ALL", filter_name="bandwidth")
```

---

## Requirements

- **Python 3.9+**
- **rich** — installed automatically with `pip install -e .`
- **Control Server** — separate process from the BlueRiver SDK (e.g. `controlserver-*` folder). Required for API commands (list, detail, reboot, factory, get-device, netstat). Not required for `broadcast`.
