"""takt-fleet serve command handler (read-only web UI)."""
from __future__ import annotations

import argparse
import socket
import sys
import threading

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
FLEET_UI_HINT = (
    "takt-fleet serve requires the 'fleet-ui' extra "
    "(uv sync --extra fleet-ui  or  pip install 'agent-takt[fleet-ui]')"
)


def _format_url(host: str, port: int) -> str:
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}"


def command_serve(args: argparse.Namespace) -> int:
    host: str = args.host
    port: int = args.port
    allow_remote: bool = getattr(args, "allow_remote", False)

    if host not in LOCAL_HOSTS and not allow_remote:
        print(
            f"error: refusing to bind to non-local host {host!r} without --allow-remote. "
            "The server has no authentication and exposes traces and transcripts.",
            file=sys.stderr,
        )
        return 2

    try:
        import starlette  # noqa: F401
        import uvicorn

        from ...web.app import create_app
    except ImportError as e:
        print(f"error: {FLEET_UI_HINT} ({e})", file=sys.stderr)
        return 1

    if host not in LOCAL_HOSTS:
        print(
            "warning: --allow-remote is set; traces and transcripts may be reachable "
            "by other machines without authentication.",
            file=sys.stderr,
        )

    from ...registry import load_registry

    app = create_app(load_registry, poll_interval=args.poll_interval)

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as e:
        sock.close()
        print(f"error: cannot bind {host}:{port}: {e}", file=sys.stderr)
        return 1

    actual_port = sock.getsockname()[1]
    url = _format_url(host, actual_port)
    print(f"takt-fleet serving at {url} (Ctrl-C to stop)", flush=True)

    if getattr(args, "open", False):
        import webbrowser

        threading.Timer(0.5, webbrowser.open, args=(url,)).start()

    config = uvicorn.Config(app, log_level="warning")
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    return 0
