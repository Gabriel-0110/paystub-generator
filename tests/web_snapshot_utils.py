import hashlib
import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


SNAPSHOT_FIXTURE = Path(__file__).with_name("fixtures").joinpath("web_visual_snapshots.json")
DEFAULT_PREVIEW_DIR = Path(__file__).with_name("fixtures").joinpath("web_preview_gallery")
DEFAULT_FAILURE_PREVIEW_DIR = Path(__file__).with_name("fixtures").joinpath("_actual_web_previews")


def wait_for_url(url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0):
                return
        except Exception as exc:  # pragma: no cover - utility polling path
            last_error = exc
            time.sleep(0.4)
    raise RuntimeError(f"Timed out waiting for {url}") from last_error


def render_image_hash(path: str | Path) -> str:
    file_path = Path(path)
    digest = hashlib.sha256()
    digest.update(file_path.read_bytes())
    return digest.hexdigest()


def capture_webapp_snapshots(base_url: str, output_dir: str | Path) -> dict[str, str]:
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1600}, color_scheme="light")
        page = context.new_page()
        try:
            page.emulate_media(reduced_motion="reduce")
            def capture(name: str) -> None:
                page.evaluate(
                    """
                    () => {
                      const active = document.activeElement;
                      if (active && typeof active.blur === "function") active.blur();
                    }
                    """
                )
                page.wait_for_timeout(80)
                page.screenshot(path=str(output_dir_path / f"{name}.png"), full_page=True)

            page.goto(base_url, wait_until="networkidle")
            page.locator("#company_name").wait_for()
            page.locator("#preview-badge").wait_for()
            page.add_style_tag(
                content="""
                *, *::before, *::after {
                  transition: none !important;
                  animation: none !important;
                  caret-color: transparent !important;
                }
                """
            )

            capture("default-workspace")

            page.get_by_role("button", name="Refresh preview").click()
            page.locator("#preview-badge.is-fresh").wait_for()
            capture("preview-current")

            page.locator("#company_name").fill("")
            page.get_by_role("button", name="Refresh preview").click()
            page.get_by_text("Company name is required.").wait_for()
            capture("validation-required")

            page.locator("#load-assignment-button").click()
            page.locator("#preview-badge.is-fresh").wait_for()
            page.get_by_text("Loaded", exact=False).wait_for()
            capture("assignment-loaded")

            page.get_by_role("button", name="Generate PDF").click()
            page.get_by_text("PDF generated successfully.", exact=False).wait_for()
            capture("pdf-generated")
        finally:
            context.close()
            browser.close()

    return {
        path.stem: render_image_hash(path)
        for path in sorted(output_dir_path.glob("*.png"))
    }


def load_snapshot_fixture() -> dict[str, str]:
    if not SNAPSHOT_FIXTURE.exists():
        return {}
    return json.loads(SNAPSHOT_FIXTURE.read_text(encoding="utf-8"))


def write_snapshot_fixture(snapshot_map: dict[str, str]) -> None:
    SNAPSHOT_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FIXTURE.write_text(json.dumps(snapshot_map, indent=2, sort_keys=True) + "\n", encoding="utf-8")
