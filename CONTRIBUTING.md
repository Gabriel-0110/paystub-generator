# Contributing

## Development setup

1. Install Python 3.12 or newer.
2. Install dependencies with `uv sync` or `pip install -e .`.
3. Install Playwright browsers if you plan to run web snapshot tests:
   `uv run playwright install chromium`

## Workflow

1. Create a feature branch.
2. Keep sample data public-safe and avoid committing real payroll data.
3. Run `uv run python -m unittest` before opening a pull request.
4. Regenerate snapshot fixtures only when visual output intentionally changes.

## Pull requests

- Describe behavior changes and any data-model or output-format impact.
- Include screenshots or regenerated fixture notes for UI or PDF template changes.
- Do not commit `.env`, generated PDFs, temporary exports, or local review artifacts.

## Maintainer

- Developer: Gabriel Chiappa
- LinkedIn: `/gabriel-chiappa`
- Email: `gabriel.chiappa@outlook.com`
