from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.pdf_snapshot_utils import (
    DEFAULT_REGEN_PREVIEW_DIR,
    SNAPSHOT_FIXTURE,
    build_snapshot_cases,
    render_template_snapshots,
    write_snapshot_fixture,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Regenerate PDF visual snapshot hashes.")
    parser.add_argument(
        "--save-previews",
        action="store_true",
        help="Also save preview PNGs for each rendered snapshot case.",
    )
    parser.add_argument(
        "--preview-dir",
        default=str(DEFAULT_REGEN_PREVIEW_DIR),
        help="Directory where preview PNGs should be written when --save-previews is enabled.",
    )
    parser.add_argument(
        "--hash-scale",
        type=float,
        default=1.0,
        help="Rasterization scale used for snapshot hashes.",
    )
    parser.add_argument(
        "--preview-scale",
        type=float,
        default=1.5,
        help="Rasterization scale used for saved PNG previews.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    all_snapshots: dict[str, str] = {}

    with TemporaryDirectory() as temp_dir:
        for case_name, data in build_snapshot_cases().items():
            snapshots, _ = render_template_snapshots(
                temp_dir,
                data,
                case_name,
                save_previews=args.save_previews,
                preview_dir=args.preview_dir,
                hash_scale=args.hash_scale,
                preview_scale=args.preview_scale,
            )
            all_snapshots.update(snapshots)

    write_snapshot_fixture(all_snapshots)
    print(f"Updated snapshot fixture: {SNAPSHOT_FIXTURE}")
    if args.save_previews:
        print(f"Preview PNGs written to: {Path(args.preview_dir)}")
    print(json.dumps(all_snapshots, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
