"""Insert many asset rows that share one real file, for scrolling tests.

    uv run python tests/fixtures/seed_library.py DATA_DIR COUNT

The data directory must already hold at least one imported asset; its file
and thumbnail back every seeded row. Rows are spread over 60 days.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from framestudio.db.models import Asset, Generation
from framestudio.db.session import Database
from framestudio.library import search
from framestudio.paths import DataPaths

ASPECTS = [(1024, 576), (576, 1024), (768, 768), (896, 672)]


def main(data_dir: Path, count: int) -> None:
    db = Database(DataPaths(data_dir).database)
    with db.session() as session:
        template = session.scalars(select(Asset).where(Asset.thumb_path.is_not(None))).first()
        if template is None:
            raise SystemExit("import one image first")
        now = datetime.now(UTC)
        ids: list[str] = []
        for index in range(count):
            created = now - timedelta(minutes=index * 9)
            generation = Generation(
                batch_id="seed", mode="image", model_id="mock", provider="mock",
                prompt=f"seeded frame {index}", params={}, status="succeeded",
                progress=1.0, created_at=created,
            )  # fmt: skip
            session.add(generation)
            session.flush()
            width, height = ASPECTS[index % len(ASPECTS)]
            asset = Asset(
                kind="image", sha256=template.sha256, path=template.path, mime=template.mime,
                size_bytes=template.size_bytes, width=width, height=height,
                thumb_path=template.thumb_path, source_generation_id=generation.id,
                created_at=created,
            )  # fmt: skip
            session.add(asset)
            session.flush()
            ids.append(asset.id)
        search.index_assets(session, ids)
    db.dispose()


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]))
