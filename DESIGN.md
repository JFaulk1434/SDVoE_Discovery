# Design Document / PRD: Discovery CLI + Python API

This document describes the design of **SDVoE Discovery** so the same patterns can be reused for similar projects (e.g. **ASpeed_Discovery**). The code and protocols will differ; the structure, CLI shape, and API surface should stay consistent.

---

## 1. Overview and Goals

- **Dual interface:** A CLI for interactive/script use and a Python API for programmatic use (scripts, other packages).
- **Two discovery paths:** One that requires a **control/server API** (list, detail, control) and one that works **without any server** (e.g. UDP broadcast).
- **Optional bundled server:** When a “control server” (or equivalent) is available on disk, the CLI can find it and start it so users don’t have to run it manually. Support both “run from repo” and “global install” (env var + standard directory).
- **Stable, scriptable output:** JSON-serializable structures from the Python API; CLI supports `--format json` and human-readable text (e.g. Rich).
- **Control by identifier:** High-level control (reboot, factory, get-device, netstat, etc.) by a **stable identifier** (e.g. MAC or device ID). CLI uses numeric IDs from `list`; Python API can accept MACs and resolve via the server.

---

## 2. Architecture (High-Level)

```
┌─────────────────────────────────────────────────────────────────┐
│  CLI (argparse, Rich)                                             │
│  Commands: list | detail | broadcast | reboot | factory | ...     │
└───────────────────────────┬─────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────────────┐
│ device_info   │  │ discovery     │  │ control_server_runner  │
│ (list/detail  │  │ (UDP broadcast│  │ (find & start server)  │
│  via API)     │  │  no server)   │  └───────────┬─────────────┘
└───────┬───────┘  └───────────────┘              │
        │                   │                     │
        ▼                   │                     ▼
┌───────────────┐           │            ┌───────────────┐
│ api_client    │           │            │ control       │
│ (REST to      │◄──────────┴────────────│ (reboot,      │
│  server)      │                        │  factory, …   │
└───────────────┘                        └───────┬───────┘
        │                                       │
        └───────────────────────────────────────┘
```

- **CLI** is the single entrypoint; it delegates to modules and never contains protocol logic.
- **api_client** is the only layer that talks HTTP to the control server; all “server” features go through it.
- **discovery** implements the “no server” path (e.g. UDP); it does not depend on the control server.
- **control_server_runner** is optional: find server binary, start it (optionally in a new window), manage port/config. Used only when the product ships or expects such a server.

---

## 3. Module Map and Responsibilities

| Module | Purpose | Depends on |
|--------|---------|------------|
| **cli** | Argument parsing, subcommands, Rich output, “ensure server” flow, dispatch to device_info / discovery / control. | device_info, discovery, api_client, control, control_server_runner |
| **api_client** | Low-level HTTP client for the control/server API (GET/POST, error handling, optional polling for async requests). | — |
| **device_info** | High-level “list” and “detail”: call api_client, normalize response into a stable list/detail shape (IP, MAC, name, type, firmware, etc.). | api_client |
| **discovery** | Standalone discovery (e.g. UDP broadcast): send probe, collect responses, return list of objects (e.g. with `.to_dict()`). | — (optional: config file for ports/magic) |
| **control** | High-level control by **identifier** (e.g. MAC): resolve identifier → device, then call api_client (reboot, factory, get_device, netstat). Returns dicts/lists; netstat can write to file. | api_client, device_info (for resolution) |
| **control_server_runner** | Find server root (cwd, env var, package parent, `~/.appname/`), detect platform, pick binary, find free port, generate config, start process (foreground or new window). | — |

For **ASpeed_Discovery**: keep the same module roles; swap in ASpeed-specific **api_client** (or other transport), **discovery** (ASpeed protocol), and **control** (ASpeed commands). **control_server_runner** may be omitted or replaced with an “ASpeed server” runner if applicable.

---

## 4. CLI Design

### 4.1 Entrypoint and Subcommands

- One top-level command (e.g. `sdvoe-discovery` or `aspeed-discovery`).
- **Subparsers** for each action; no positional “mode” before options.
- Global options on the root parser: `--api-url`, `--timeout`, `--format`, `--version`.
- Per-command options on each subparser; **control commands** share a common group: `--api-url`, `--no-start`, `--start-server`, `--server-warmup`.

### 4.2 Command Groups

**Discovery (read-only)**

- **list** — Short list (IP, MAC, name, type, firmware). Uses server API. Output supports `#` ID column for use with control commands.
- **detail** — Full device info from server API.
- **broadcast** (or equivalent) — Discovery without server (e.g. UDP). Options: timeout, format, optional “show raw response.”

**Control (require server)**

- **reboot** `ID|all`
- **factory** `ID|all`
- **get-device** `ID|all` — Full device object(s).
- **netstat** `ID|all` — With optional `--output FILE`, `--filter …`.

Target is always **ID** (from list) or **all**. Same pattern can be “MAC” or “all” if the ASpeed API is MAC-based.

### 4.3 Server Handling (When Applicable)

- **Default:** If server not running, **prompt** “Start it? [Y/n]” and, if yes, try to find and start it.
- **Flags:** `--no-start` (fail if not running), `--start-server` (start without prompting).
- **After start:** Optional `--server-warmup SECS` (e.g. 5) before running list/detail so discovery can settle.
- **Finding the server:** Try in order: current directory, env var (e.g. `SDVOE_CONTROLSERVER_ROOT`), package/project root, then standard dir (e.g. `~/.sdvoe-discovery`). Document all in README (including macOS/Linux/Windows for the env var).

### 4.4 Output

- **Human:** Rich (syntax highlighting, spinners for “Starting server…”, clear error messages). Optional table for list.
- **Machine:** `--format json` (or `-f json`) for list/detail/control output; one JSON object or array per command result.
- **Large output:** For commands that can return a lot (e.g. netstat all), support `--output FILE` to write JSON to file.

---

## 5. Python API Design

### 5.1 Public Surface (re-exported from package `__init__.py`)

- **Version:** `__version__`
- **Discovery (no server):** e.g. `discover_devices(timeout=…)` → list of response objects with `.to_dict()`.
- **Discovery (server):** `get_device_list(base_url=…)`, `get_device_details(base_url=…)` → list of dicts (stable keys: ip, mac, name, type, firmware_version, device_id, etc.).
- **Control by identifier:** `reboot(macs, base_url=…)`, `factory(macs, …)`, `get_device(macs, …)`, `netstat(macs, output_file=…)` → dict or list of dicts; netstat can write to file and return path.
- **Low-level client:** e.g. `SDVoEAPIClient(base_url=…)` for custom API calls; plus a dedicated exception (e.g. `ControlServerError`).

All return types are JSON-serializable (dict/list, no custom objects in the public return values except where explicitly documented).

### 5.2 Control Functions: Signature and Return Shape

- **Input:** List of identifiers (e.g. MAC strings). Optional: `base_url`, `timeout`, `request_timeout`.
- **Resolution:** Resolve identifiers to “device” records via the server (e.g. get device list, match MAC). Report “not found” identifiers in the result.
- **Return:** One dict per function, e.g.:
  - `success: bool`
  - `rebooted` / `reset` / etc.: list of device IDs that succeeded
  - `errors`: list of `{device_id, message}` (or similar)
  - `not_found_macs` (or equivalent): list of identifiers that were not in the server list

Netstat: same resolution; return dict with `statistics` and `error`, or write to `output_file` and return the path string.

### 5.3 Docstrings

- Every public function has a short summary, **Args** (with types and defaults), and **Returns** (structure of dict/list). This supports both users and reuse in another project (e.g. ASpeed_Discovery).

---

## 6. Optional Control Server (Runner)

- **Search order when no explicit path is given:**  
  1) Current working directory  
  2) Env var (e.g. `SDVOE_CONTROLSERVER_ROOT`)  
  3) Package/project parent (for `pip install -e .`)  
  4) Standard directory (e.g. `~/.sdvoe-discovery`)

- **Env var:** Points to the server root folder (the one that contains platform subdirs or the binary). Document for macOS, Linux, and Windows (current session + persistent).

- **Platform detection:** Map `(sys.platform, machine)` to a subfolder name (e.g. macosx-aarch64, linux-x86_64). If multiple platforms exist, optionally prompt user to choose.

- **Start modes:**  
  - Foreground (CLI waits; server dies when CLI exits).  
  - New window (minimized) so server keeps running after CLI exits (macOS: AppleScript + Terminal; Windows: `start /min cmd /k ...`; Linux: gnome-terminal / xterm or fallback to background).

- **Port:** Prefer non-privileged port (e.g. 8080+); find free port, generate temp config with that port, pass to server. CLI then uses `http://127.0.0.1:{port}` as `--api-url` for that run.

---

## 7. Error Handling and Messaging

- **API/client errors:** Raise a single exception type (e.g. `ControlServerError`) with message, optional reason/code. CLI catches and prints a short, actionable message.
- **“Server not running”:** Print what to do: use `--api-url`, use broadcast, or put server in standard location / set env var.
- **“No server folder found”:** List the search locations and refer to README for env var and standard directory.

---

## 8. Reuse Checklist for ASpeed_Discovery

Use this as a mapping from SDVoE Discovery to a new “ASpeed_Discovery” app (same design, different protocol/code).

| SDVoE Discovery | ASpeed_Discovery (suggested) |
|-----------------|-----------------------------|
| `sdvoe-discovery` | `aspeed-discovery` (or chosen name) |
| BlueRiver Control Server API | ASpeed API / gateway (REST or other; implement in `api_client`) |
| controlserver-* folder, `SDVOE_CONTROLSERVER_ROOT`, `~/.sdvoe-discovery` | ASpeed server/gateway path, `ASPEED_SERVER_ROOT` (or similar), `~/.aspeed-discovery` |
| UDP broadcast (ports 6239/6240, magic) | ASpeed discovery protocol (implement in `discovery`) |
| Device ID from list (1, 2, …) + MAC in API | Same idea: list shows IDs; control by ID or MAC (or ASpeed identifier) |
| get_device_list, get_device_details | Same names; implementation calls ASpeed API and normalizes to same dict shape (ip, mac, name, type, firmware, etc.) |
| reboot, factory, get_device, netstat | Same high-level operations; implementation uses ASpeed API |
| ControlServerError | e.g. ASpeedAPIError or keep generic name |
| Rich + JSON output | Same |
| Install: clone + `pip install -e .`; global: env var or `~/.appname` | Same pattern; document ASpeed env var and paths |

Keep **DESIGN.md** (or this PRD) in both repos so future you (or the AI) can keep ASpeed_Discovery aligned with this design while swapping in ASpeed-specific code in `api_client`, `discovery`, `control`, and optionally the server runner.
