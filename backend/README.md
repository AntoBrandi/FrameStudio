# FrameStudio backend

FastAPI app that owns data, jobs, engines and keys, and serves the UI over HTTP.
Run it from the repository root with `bun run dev`, or directly:

```sh
uv run framestudio serve            # http://127.0.0.1:8790/
uv run framestudio token --url      # browser sign-in link
uv run pytest                       # tests
```

See the repository README for the architecture and the plan step it implements.
