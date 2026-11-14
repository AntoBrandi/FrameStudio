"""``framestudio`` command line: serve, print the token, export the OpenAPI schema."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

from framestudio import __version__


def _apply_env(args: argparse.Namespace) -> None:
    """Pass CLI flags to Config through the environment, so uvicorn's reload
    worker (a separate process) sees the same configuration."""
    if getattr(args, "data_dir", None):
        os.environ["FRAMESTUDIO_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    if getattr(args, "port", None) is not None:
        os.environ["FRAMESTUDIO_PORT"] = str(args.port)
    if getattr(args, "host", None):
        os.environ["FRAMESTUDIO_HOST"] = args.host
    if getattr(args, "dev", False):
        os.environ["FRAMESTUDIO_DEV"] = "1"


def _watch_stdin(server: object) -> None:
    """Stop when the parent closes our stdin (the desktop shell exited)."""

    def watch() -> None:
        try:
            while sys.stdin.buffer.read(1024):
                pass
        except (OSError, ValueError):
            pass
        server.should_exit = True  # type: ignore[attr-defined]

    threading.Thread(target=watch, name="stdin-watch", daemon=True).start()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from framestudio.app import resolve_bind_host
    from framestudio.config import Config

    _apply_env(args)
    config = Config()
    host = resolve_bind_host(config)
    os.environ["FRAMESTUDIO_HOST"] = host
    url_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host  # noqa: S104
    print(
        f"FrameStudio {__version__} backend on http://{url_host}:{config.port}/ "
        f"(data: {config.data_dir})",
        flush=True,
    )
    print(
        "Sign in from a browser with the link from: framestudio token --url",
        flush=True,
    )
    uv_config = uvicorn.Config(
        "framestudio.app:create_app",
        factory=True,
        host=host,
        port=config.port,
        log_level=args.log_level,
        server_header=False,
        proxy_headers=False,
        timeout_graceful_shutdown=3,
    )
    if args.reload:
        uvicorn.run(
            "framestudio.app:create_app",
            factory=True,
            host=host,
            port=config.port,
            reload=True,
            reload_dirs=[str(Path(__file__).resolve().parent)],
            log_level=args.log_level,
            server_header=False,
            proxy_headers=False,
            timeout_graceful_shutdown=3,
        )
        return 0
    server = uvicorn.Server(uv_config)
    if args.exit_on_stdin_eof:
        _watch_stdin(server)
    server.run()
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    from framestudio.config import Config
    from framestudio.paths import DataPaths
    from framestudio.security import load_or_create_token, write_new_token

    _apply_env(args)
    config = Config()
    paths = DataPaths(config.data_dir).ensure()
    token = write_new_token(paths.token) if args.rotate else load_or_create_token(paths.token)
    if args.rotate:
        print("New token written. Restart the backend to use it.", file=sys.stderr)
    if args.url:
        print(f"http://127.0.0.1:{config.port}/auth/login?token={token}")
    else:
        print(token)
    return 0


def build_openapi() -> dict[str, object]:
    from framestudio.app import create_app
    from framestudio.config import Config

    with tempfile.TemporaryDirectory(prefix="framestudio-openapi-") as tmp:
        app = create_app(Config(data_dir=Path(tmp), token="x" * 43, ui_dist=Path(tmp) / "ui"))
        schema = app.openapi()
        app.state.services.db.dispose()
        return schema


def cmd_openapi(args: argparse.Namespace) -> int:
    text = json.dumps(build_openapi(), indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    from framestudio.catalog import CatalogError, load_catalog

    try:
        catalog = load_catalog(Path(args.dir)) if args.dir else load_catalog()
    except CatalogError as exc:
        print(exc, file=sys.stderr)
        return 1
    for model in catalog.all():
        print(f"{model.id:24} {model.runtime:5} {', '.join(model.modes):12} {model.license.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("alembic").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="framestudio", description="FrameStudio backend")
    parser.add_argument("--version", action="version", version=f"framestudio {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the backend and serve the UI")
    serve.add_argument("--host", help="bind address (default 127.0.0.1, or all with remote access)")
    serve.add_argument("--port", type=int, help="port (default 8790)")
    serve.add_argument("--data-dir", help="data directory (default: per-user data dir)")
    serve.add_argument("--reload", action="store_true", help="restart on code changes")
    serve.add_argument("--dev", action="store_true", help="development mode")
    serve.add_argument(
        "--exit-on-stdin-eof",
        action="store_true",
        help="stop when stdin closes (used by the desktop shell)",
    )
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=cmd_serve)

    token = sub.add_parser("token", help="print the access token")
    token.add_argument("--url", action="store_true", help="print a browser sign-in link")
    token.add_argument("--rotate", action="store_true", help="replace the token")
    token.add_argument("--data-dir")
    token.add_argument("--port", type=int)
    token.set_defaults(func=cmd_token)

    openapi = sub.add_parser("openapi", help="write the OpenAPI schema")
    openapi.add_argument("--out", help="output file (default stdout)")
    openapi.set_defaults(func=cmd_openapi)

    check = sub.add_parser("catalog", help="check the model catalog and list it")
    check.add_argument("--dir", help="catalog directory (default: the built-in one)")
    check.set_defaults(func=cmd_catalog)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
