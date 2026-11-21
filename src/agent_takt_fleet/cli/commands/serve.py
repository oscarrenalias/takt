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

    demo_dir: str | None = None
    driver = None
    if getattr(args, "demo", False):
        import tempfile
        from pathlib import Path

        from ...models import Project
        from ...web.demo import DemoActivityDriver, build_fixture_fleet

        demo_dir = tempfile.mkdtemp(prefix="takt-fleet-demo-")
        fleet = build_fixture_fleet(Path(demo_dir))
        # Demo transcripts live in the fixture's fake config dir, never ~/.claude.
        args.claude_config_dir = str(fleet.claude_config_dir)
        demo_projects = [
            Project(name=p.name, path=p.path, tags=p.tags)
            for p in (*fleet.projects, *fleet.missing)
        ]
        provider = lambda: list(demo_projects)  # noqa: E731
        driver = DemoActivityDriver(fleet)
        print(
            "DEMO MODE: serving generated demo projects with synthetic activity; "
            "the real fleet registry is not used.",
            flush=True,
        )
    else:
        from ...registry import load_registry as provider

    try:
        return _serve(args, host, port, provider, driver, uvicorn, create_app)
    finally:
        if demo_dir is not None:
            import shutil

            shutil.rmtree(demo_dir, ignore_errors=True)


def _serve(args, host, port, provider, driver, uvicorn, create_app) -> int:
    app = create_app(
        provider,
        poll_interval=args.poll_interval,
        claude_config_dir=getattr(args, "claude_config_dir", None),
    )

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
    if driver is not None:
        driver.start()
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    finally:
        if driver is not None:
            driver.stop()
        sock.close()
    return 0
