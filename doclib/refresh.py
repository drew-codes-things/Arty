import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doclib.build import build  # noqa: E402
from doclib.fetch import fetch_all  # noqa: E402
from doclib.sources import by_key  # noqa: E402

DEFAULT_ROOT = Path("/srv/ai/docs-library")
DATABASE_NAME = "docs.db"
RAW_FOLDER = "raw"
DISK_RESERVE_BYTES = 8 * 1024 ** 3


def free_bytes(folder: Path) -> int:
    return shutil.disk_usage(folder).free


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download the documentation sources and rebuild the search index.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--only", nargs="*", help="source keys to fetch (the index is always rebuilt from every download)")
    parser.add_argument("--no-fetch", action="store_true", help="rebuild the index from what is already downloaded")
    parser.add_argument("--reserve-gb", type=float, default=DISK_RESERVE_BYTES / 1024 ** 3,
                        help="stop before downloading when the disk has less free space than this")
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    root = options.root
    raw = root / RAW_FOLDER
    raw.mkdir(parents=True, exist_ok=True)
    outcome = {}
    if not options.no_fetch:
        reserve = int(options.reserve_gb * 1024 ** 3)
        if free_bytes(root) < reserve:
            logging.error("only %.1f GB free under %s, less than the %.1f GB reserve; nothing downloaded",
                          free_bytes(root) / 1024 ** 3, root, options.reserve_gb)
            return 2
        outcome = fetch_all(by_key(options.only), raw)
    database = root / DATABASE_NAME
    if database.exists() and outcome and all(result == "unchanged" for result in outcome.values()):
        print(json.dumps({"fetched": outcome, "sections": "unchanged; index kept"}, indent=1))
        return 0
    counts = build(by_key(None), raw, database)
    print(json.dumps({"fetched": outcome, "sections": counts}, indent=1))
    failed = [key for key, result in outcome.items() if result.startswith("failed")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
