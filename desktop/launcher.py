"""Native Windows desktop launcher for Multi TG Manager."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "Multi TG Manager"
APP_DIR = "MultiTGManager"
HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def _bundle_backend_root() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS"))
        return (base / "backend").resolve()
    return (Path(__file__).resolve().parents[1] / "backend").resolve()


def _runtime_root() -> Path:
    override = os.environ.get("MTM_RUNTIME_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return (Path(local) / APP_DIR).resolve()
    return (Path.home() / "AppData" / "Local" / APP_DIR).resolve()


def _configure_paths() -> tuple[Path, Path, Path]:
    backend_root = _bundle_backend_root()
    runtime_root = _runtime_root()
    runtime_backend = runtime_root / "backend"
    runtime_backend.mkdir(parents=True, exist_ok=True)
    os.environ["MTM_BACKEND_ROOT"] = str(backend_root)
    os.environ["MTM_RUNTIME_ROOT"] = str(runtime_root)
    return backend_root, runtime_root, runtime_backend / ".env"


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _config_valid(values: dict[str, str]) -> bool:
    try:
        api_id = int(values.get("TG_API_ID", "0"))
    except ValueError:
        return False
    password = values.get("APP_PASSWORD", "")
    secret = values.get("SESSION_SECRET", "")
    return (
        api_id > 0
        and len(values.get("TG_API_HASH", "")) >= 16
        and 12 <= len(password) <= 256
        and len(secret) >= 48
    )


def _write_env(path: Path, api_id: str, api_hash: str, password: str, session_secret: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"TG_API_ID={api_id.strip()}\n"
        f"TG_API_HASH={api_hash.strip()}\n"
        f"APP_PASSWORD={password}\n"
        f"SESSION_SECRET={session_secret}\n"
    )
    path.write_text(content, encoding="utf-8")


def _first_run_setup(env_path: Path) -> bool:
    current = _read_env(env_path)
    if _config_valid(current):
        return True

    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title(f"{APP_NAME} - First Run Setup")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = tk.Frame(root, padx=20, pady=18)
    frame.grid(row=0, column=0)

    tk.Label(
        frame,
        text="First Run Setup",
        font=("Segoe UI", 14, "bold"),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

    tk.Label(
        frame,
        text="Enter your Telegram API credentials and a local dashboard password.",
        justify="left",
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))

    labels = [
        ("Telegram API ID", "TG_API_ID", False),
        ("Telegram API Hash", "TG_API_HASH", False),
        ("Dashboard Password", "APP_PASSWORD", True),
    ]
    entries: dict[str, tk.Entry] = {}
    for idx, (label, key, secret) in enumerate(labels, start=2):
        tk.Label(frame, text=label).grid(row=idx, column=0, sticky="w", pady=5)
        entry = tk.Entry(frame, width=42, show="*" if secret else "")
        entry.insert(0, current.get(key, ""))
        entry.grid(row=idx, column=1, padx=(12, 0), pady=5)
        entries[key] = entry

    tk.Label(
        frame,
        text="Credentials are stored only on this Windows user profile.",
        fg="#555555",
    ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 12))

    result = {"ok": False}

    def save():
        api_id = entries["TG_API_ID"].get().strip()
        api_hash = entries["TG_API_HASH"].get().strip()
        password = entries["APP_PASSWORD"].get()
        try:
            if int(api_id) <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "Telegram API ID must be a positive integer.")
            return
        if len(api_hash) < 16:
            messagebox.showerror(APP_NAME, "Telegram API Hash is missing or too short.")
            return
        if not 12 <= len(password) <= 256:
            messagebox.showerror(APP_NAME, "Dashboard password must contain 12-256 characters.")
            return
        session_secret = current.get("SESSION_SECRET", "")
        if len(session_secret) < 48:
            session_secret = secrets.token_urlsafe(64)
        _write_env(env_path, api_id, api_hash, password, session_secret)
        result["ok"] = True
        root.destroy()

    def cancel():
        root.destroy()

    buttons = tk.Frame(frame)
    buttons.grid(row=6, column=0, columnspan=2, sticky="e")
    tk.Button(buttons, text="Cancel", width=10, command=cancel).pack(side="left", padx=(0, 8))
    tk.Button(buttons, text="Save & Launch", width=14, command=save).pack(side="left")

    root.protocol("WM_DELETE_WINDOW", cancel)
    root.mainloop()
    return bool(result["ok"])


def _port_in_use() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _wait_ready(timeout: float = 35.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{URL}/api/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("backend") == "ok":
                    return payload
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"Desktop backend did not become ready ({type(last_error).__name__ if last_error else 'timeout'})")


def _start_server():
    import uvicorn
    from app.main import app

    config = uvicorn.Config(
        app=app,
        host=HOST,
        port=PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)

    def request_server_shutdown():
        server.should_exit = True

    app.state.request_server_shutdown = request_server_shutdown
    thread = threading.Thread(
        target=server.run,
        name="mtm-backend",
        daemon=True,
    )
    thread.start()
    return server, thread, app


def _stop_server(server, thread, app):
    server.should_exit = True
    thread.join(timeout=20)
    app.state.request_server_shutdown = None


def _smoke_test(runtime_root: Path, env_path: Path) -> int:
    _write_env(
        env_path,
        "12345",
        "0123456789abcdef0123456789abcdef",
        "desktop-smoke-password-123",
        secrets.token_urlsafe(64),
    )
    server = thread = app = None
    code = 0
    try:
        server, thread, app = _start_server()
        health = _wait_ready()
        if health.get("database") != "ok" or health.get("secret_store") != "ok":
            code = 2
        else:
            with urllib.request.urlopen(f"{URL}/", timeout=5) as response:
                html = response.read().decode("utf-8", errors="replace")
                if response.status != 200 or "<html" not in html.lower():
                    code = 3
    except Exception:
        code = 6
    finally:
        if server is not None:
            _stop_server(server, thread, app)

    if code == 0:
        shutil.rmtree(runtime_root, ignore_errors=True)
    return code


def _show_error(message: str, runtime_root: Path):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            APP_NAME,
            f"{message}\n\nLogs: {runtime_root / 'data' / 'logs' / 'app.log'}",
        )
        root.destroy()
    except Exception:
        pass


def main() -> int:
    _backend_root, runtime_root, env_path = _configure_paths()
    smoke = "--smoke-test" in sys.argv
    if smoke:
        code = _smoke_test(runtime_root, env_path)
        # PyInstaller/GUI support libraries can leave non-daemon helper threads
        # behind even though the backend already shut down. CI smoke must have
        # deterministic process lifetime.
        if getattr(sys, "frozen", False):
            os._exit(code)
        return code

    if _port_in_use():
        _show_error(
            "Port 8000 is already in use. Close the other Multi TG Manager instance and try again.",
            runtime_root,
        )
        return 4

    if not _first_run_setup(env_path):
        return 0

    server = thread = app = None
    try:
        server, thread, app = _start_server()
        _wait_ready()

        import webview

        webview.create_window(
            APP_NAME,
            URL,
            width=1400,
            height=900,
            min_size=(1024, 700),
        )
        webview.start(debug=False)
        return 0
    except Exception as exc:
        _show_error(
            f"Multi TG Manager could not start ({type(exc).__name__}).",
            runtime_root,
        )
        return 5
    finally:
        if server is not None:
            _stop_server(server, thread, app)


if __name__ == "__main__":
    raise SystemExit(main())
