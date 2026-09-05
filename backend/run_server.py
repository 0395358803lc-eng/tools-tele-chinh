"""Owned Uvicorn runner for Multi TG Manager.

Keeping the Server object in-process lets the authenticated shutdown endpoint
request a graceful stop by setting Server.should_exit instead of emitting a
Windows CTRL_BREAK event to the surrounding console/process group.
"""
from __future__ import annotations

import logging

import uvicorn

from app.main import app

log = logging.getLogger("run_server")


def main() -> int:
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)

    def request_server_shutdown():
        server.should_exit = True

    app.state.request_server_shutdown = request_server_shutdown
    try:
        server.run()
    finally:
        app.state.request_server_shutdown = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
