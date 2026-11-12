"""Serve the built UI at "/" with single-page-app fallback (decision D9).

The UI is public (it holds no secrets and must load before the user can sign
in); everything under ``/api`` stays behind the access guard.
"""

from __future__ import annotations

import html
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, Response

_NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>FrameStudio</title>
<style>body{{background:#0e1012;color:#e8eaed;font:15px/1.6 system-ui,sans-serif;
display:grid;place-items:center;min-height:100vh;margin:0}}main{{max-width:36rem;padding:2rem}}
code{{background:#22262b;padding:.1em .4em;border-radius:4px}}</style></head>
<body><main><h1>FrameStudio backend is running</h1>
<p>The UI has not been built at <code>{dist}</code>.</p>
<p>Run <code>bun run build</code> to build it, or <code>bun run dev</code> for the
development server.</p></main></body></html>"""

_NO_CACHE = {"Cache-Control": "no-cache"}
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def mount_ui(app: FastAPI, dist: Path) -> None:
    dist = dist.resolve()
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        if path.startswith(("api/", "auth/")) or path in {"api", "auth"}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        if not index.is_file():
            body = _NOT_BUILT.format(dist=html.escape(str(dist)))
            return HTMLResponse(body, headers=_NO_CACHE)
        if path:
            candidate = (dist / path).resolve()
            if dist in candidate.parents and candidate.is_file():
                hashed = candidate.parent.name == "assets"
                return FileResponse(candidate, headers=_IMMUTABLE if hashed else _NO_CACHE)
            if "." in Path(path).name:
                # A missing file (e.g. an old hashed bundle), not a client route.
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        return FileResponse(index, headers=_NO_CACHE)
