"""Rebuild AdaFace NumPy exports from approved, model-tagged enrollment records."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from workledger.config import Settings
from workledger.models import Site
from workledger.vision.runtime import VisionRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", help="Limit rebuilding to one site UUID")
    args = parser.parse_args()
    os.chdir(Path(__file__).resolve().parents[1])
    settings = Settings()
    engine = create_engine(settings.database_url)
    runtime = VisionRuntime(settings, sessionmaker(engine, expire_on_commit=False))
    try:
        with runtime.factory() as db:
            sites = db.scalars(select(Site)).all()
            if args.site:
                sites = [site for site in sites if site.id == args.site]
                if not sites:
                    raise ValueError("Site UUID not found")
            for site in sites:
                result = runtime.refresh_gallery(site.company_id, site.id)
                print(json.dumps({"site": site.id, **result}))
                if result.get("status") == "pending_rebuild":
                    raise RuntimeError("Gallery rebuild failed")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
