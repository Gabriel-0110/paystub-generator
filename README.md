# Paystub Generator

Paystub Generator is a local-first Python project for creating payroll paystub PDFs from reusable profile data. It includes a command-line workflow for single and batch generation, profile import and export utilities, and a FastAPI web app for editing paystub inputs, previewing results, and generating PDFs in the browser.

Developer: Gabriel Chiappa  
LinkedIn: `/gabriel-chiappa`  
Email: `gabriel.chiappa@outlook.com`

## Features

- Generate a single paystub PDF for a selected payroll assignment and pay period.
- Generate full employee or full-year batches of paystub PDFs.
- Support multiple PDF layouts, including ADP-style, simple, and detached-check templates.
- Store reusable company, employee, tax-default, deduction-default, and assignment profiles as JSON.
- Import and export profile bundles in JSON, CSV ZIP, and Excel formats.
- Run a local FastAPI web app for draft editing, previewing totals, and PDF generation.
- Optionally connect the web app to Supabase-backed profile storage.
- Maintain PDF and web visual snapshot fixtures for regression testing.

## Tech Stack

- Python 3.12+
- FastAPI
- Uvicorn
- ReportLab
- Pydantic
- OpenPyXL
- HTTPX
- Playwright
- pypdfium2
- `uv` lockfile workflow via `uv.lock`

## Installation

### Option 1: `uv` workflow

```powershell
uv sync
```

### Option 2: editable install with `pip`

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

For development tooling:

```powershell
pip install -e .[dev]
```

## Environment and setup

This project works without environment variables by default and uses the local `profiles/` directory for saved payroll data.

Optional Supabase-backed profile storage is enabled when both of these variables are set:

```powershell
$env:SUPABASE_URL="http://localhost:8000"
$env:SUPABASE_PUBLISHABLE_KEY="your_publishable_key_here"
```

Copy `.env.example` if you want a starting point for local environment configuration.

## How to run locally

### CLI

Single paystub:

```powershell
uv run python main.py --mode single --assignment sample_payroll --year 2026 --period 1
```

Full employee batch:

```powershell
uv run python main.py --mode employee --assignment sample_payroll --year 2026
```

Full-year batch for all saved assignments:

```powershell
uv run python main.py --mode year --year 2026
```

If installed editable, you can also use the console command:

```powershell
paystub-generator --mode single --assignment sample_payroll --year 2026 --period 1
```

### Local web app

```powershell
uv run python main.py --mode web
```

The app defaults to `http://127.0.0.1:8010`.

You can also run it directly with Uvicorn:

```powershell
uv run uvicorn webapp.app:app --reload --port 8010
```

## How to generate the PDF

The simplest path is the CLI single mode:

```powershell
uv run python main.py --mode single --assignment sample_payroll --year 2026 --period 1 --template detached_check
```

Generated PDFs are written to `output/` by default. The directory is intentionally gitignored because it is runtime output, not source material.

You can also generate from the web app:

1. Start the app with `uv run python main.py --mode web`.
2. Open `http://127.0.0.1:8010`.
3. Load the sample draft or a saved assignment.
4. Refresh preview.
5. Click `Generate PDF` or generate a batch ZIP if multiple stubs are selected.

## Example usage

Export profiles to JSON:

```powershell
uv run python main.py --mode export --format json --output exports\profiles.json
```

Import profiles from Excel:

```powershell
uv run python main.py --mode import --format excel --input imports\profiles.xlsx
```

Generate a simple-template PDF for the first sample pay period:

```powershell
uv run python main.py --mode single --assignment sample_payroll --year 2026 --period 1 --template simple
```

## Project structure

```text
.
|-- generators/              PDF rendering and batch generation logic
|-- models/                  payroll models, validation, and profile IO
|-- profiles/                public-safe sample profile data
|-- scripts/                 snapshot regeneration helpers
|-- tests/                   unit and visual regression tests
|-- webapp/                  FastAPI app, templates, and static assets
|-- review/README.md         tracked note for local-only quarantined artifacts
|-- .env.example             optional environment variable template
|-- .gitignore
|-- CONTRIBUTING.md
|-- LICENSE
|-- main.py                  CLI entry module
|-- pyproject.toml
|-- sample_data.py           sample assignment loader used by demos and tests
|-- uv.lock
```

## Development notes

- `pyproject.toml` and `uv.lock` are the canonical dependency sources. A separate `requirements.txt` is intentionally omitted.
- The sample data in `profiles/` is sanitized for public release and is safe demo content, not real payroll data.
- Existing local review artifacts are quarantined under `review/local-artifacts/`, which is gitignored.
- Snapshot utilities live in `scripts/` and `tests/fixtures/`:

Regenerate PDF snapshots:

```powershell
uv run python scripts\regenerate_pdf_snapshots.py --save-previews
```

Install Chromium for web snapshot tests:

```powershell
uv run playwright install chromium
```

Regenerate web snapshots:

```powershell
uv run python scripts\regenerate_web_snapshots.py --save-previews
```

Run the automated test suite:

```powershell
uv run python -m unittest
```

## Limitations and future improvements

- This project is a document-generation tool and does not provide payroll compliance advice.
- Template data fields are intentionally narrower than a full payroll platform.
- The web app is optimized for local use and does not include authentication or multi-user controls.
- Supabase integration assumes an existing table and environment configuration.
- Future work could include richer template customization, stronger packaging polish, and safer export redaction options.
