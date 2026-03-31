from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.web_snapshot_utils import (
    DEFAULT_PREVIEW_DIR,
    SNAPSHOT_FIXTURE,
    capture_webapp_snapshots,
    wait_for_url,
    write_snapshot_fixture,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Regenerate browser screenshot hashes for the local web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument(
        "--save-previews",
        action="store_true",
        help="Copy the captured screenshots into the committed preview gallery.",
    )
    parser.add_argument(
        "--preview-dir",
        default=str(DEFAULT_PREVIEW_DIR),
        help="Directory to receive saved screenshots when --save-previews is enabled.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = f"http://{args.host}:{args.port}"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "webapp.app:app",
            "--host",
            args.host,
            "--port",
            str(args.port),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_url(base_url)
        with TemporaryDirectory() as temp_dir:
            capture_dir = Path(temp_dir)
            snapshots = capture_webapp_snapshots(base_url, capture_dir)
            write_snapshot_fixture(snapshots)
            print(f"Updated snapshot fixture: {SNAPSHOT_FIXTURE}")
            print(json.dumps(snapshots, indent=2, sort_keys=True))

            if args.save_previews:
                preview_dir = Path(args.preview_dir)
                preview_dir.mkdir(parents=True, exist_ok=True)
                for existing in preview_dir.glob("*.png"):
                    existing.unlink()
                for path in capture_dir.glob("*.png"):
                    shutil.copy2(path, preview_dir / path.name)
                print(f"Preview PNGs written to: {preview_dir}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
