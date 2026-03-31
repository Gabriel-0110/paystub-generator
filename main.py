"""
Paystub Generator CLI.

Batch generation modes:
  1. one stub for one payroll assignment and period
  2. all stubs for one employee assignment for a year
  3. all stubs for every assignment for a full year
  4. import profile data from JSON / CSV / Excel
  5. export profile data to JSON / CSV / Excel

Examples:
    .venv\\Scripts\\python main.py --mode single --assignment sample_payroll --year 2026 --period 1
    .venv\\Scripts\\python main.py --mode employee --assignment sample_payroll --year 2026
    .venv\\Scripts\\python main.py --mode year --year 2026
    .venv\\Scripts\\python main.py --mode year --year 2026 --template simple
    .venv\\Scripts\\python main.py --mode export --format json --output exports\\profiles.json
    .venv\\Scripts\\python main.py --mode import --format excel --input imports\\profiles.xlsx
"""
from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
from generators.batch_generator import (
    generate_all_stubs_for_employee,
    generate_full_year_batch,
    generate_one_stub_for_assignment,
)
from generators.pdf_generator import PaystubTemplate
from models.profile_io import (
    export_profiles_csv,
    export_profiles_excel,
    export_profiles_json,
    import_profiles_csv,
    import_profiles_excel,
    import_profiles_json,
)
from webapp.app import app as web_app


def _arg_value(args: list[str], name: str, default: str | None = None) -> str | None:
    if name in args:
        idx = args.index(name)
        if idx + 1 < len(args):
            return args[idx + 1]

    prefix = f"{name}="
    for arg in args:
        if arg.startswith(prefix):
            return arg.split("=", 1)[1]

    return default


def parse_template_arg(args: list[str]) -> PaystubTemplate | str:
    return _arg_value(args, "--template", PaystubTemplate.DETACHED_CHECK) or PaystubTemplate.DETACHED_CHECK


def parse_mode_arg(args: list[str]) -> str:
    return (_arg_value(args, "--mode", "single") or "single").strip().lower()


def parse_year_arg(args: list[str]) -> int:
    return int(_arg_value(args, "--year", "2026") or "2026")


def parse_period_arg(args: list[str]) -> int:
    return int(_arg_value(args, "--period", "1") or "1")


def parse_assignment_arg(args: list[str]) -> str:
    return _arg_value(args, "--assignment", "sample_payroll") or "sample_payroll"


def parse_output_arg(args: list[str], mode: str, year: int) -> str:
    explicit = _arg_value(args, "--output")
    if explicit:
        return explicit

    if mode == "single":
        return "output/single"
    if mode == "employee":
        return f"output/employee_{year}"
    if mode == "export":
        return "exports/profiles.json"
    return "output/full_year"


def parse_input_arg(args: list[str], default: str | None = None) -> str | None:
    return _arg_value(args, "--input", default)


def parse_format_arg(args: list[str]) -> str:
    return (_arg_value(args, "--format", "json") or "json").strip().lower()


def parse_profiles_root_arg(args: list[str]) -> str | None:
    return _arg_value(args, "--profiles-root")


def parse_host_arg(args: list[str]) -> str:
    return _arg_value(args, "--host", "127.0.0.1") or "127.0.0.1"


def parse_port_arg(args: list[str]) -> int:
    return int(_arg_value(args, "--port", "8010") or "8010")


def print_usage() -> None:
    print("Usage:")
    print("  --mode single   --assignment <id> --year <year> --period <n> [--template <name>] [--output <dir>]")
    print("  --mode employee --assignment <id> --year <year>              [--template <name>] [--output <dir>]")
    print("  --mode year                        --year <year>              [--template <name>] [--output <dir>]")
    print("  --mode export   --format <json|csv|excel>                    [--output <path>] [--profiles-root <dir>]")
    print("  --mode import   --format <json|csv|excel> --input <path>                      [--profiles-root <dir>]")
    print("  --mode web                         [--host <host>] [--port <port>]")


def run_single(args: list[str]) -> int:
    year = parse_year_arg(args)
    period = parse_period_arg(args)
    assignment = parse_assignment_arg(args)
    template = parse_template_arg(args)
    output_dir = parse_output_arg(args, "single", year)

    path = generate_one_stub_for_assignment(
        assignment_profile_id=assignment,
        year=year,
        period_number=period,
        template=template,
        output_dir=output_dir,
    )

    print("-- One stub ----------------------------------------------")
    print(f"   Assignment : {assignment}")
    print(f"   Year       : {year}")
    print(f"   Period     : {period}")
    print(f"   Output     : {path}")
    return 0


def run_employee(args: list[str]) -> int:
    year = parse_year_arg(args)
    assignment = parse_assignment_arg(args)
    template = parse_template_arg(args)
    output_dir = parse_output_arg(args, "employee", year)

    paths = generate_all_stubs_for_employee(
        assignment_profile_id=assignment,
        year=year,
        template=template,
        output_dir=Path(output_dir) / assignment,
    )

    print("-- Employee batch ----------------------------------------")
    print(f"   Assignment : {assignment}")
    print(f"   Year       : {year}")
    print(f"   Generated  : {len(paths)}")
    print(f"   Output dir : {Path(output_dir) / assignment}")
    return 0


def run_year(args: list[str]) -> int:
    year = parse_year_arg(args)
    template = parse_template_arg(args)
    output_dir = parse_output_arg(args, "year", year)

    batches = generate_full_year_batch(
        year=year,
        template=template,
        output_dir=output_dir,
    )

    total = sum(len(paths) for paths in batches.values())
    print("-- Full-year batch ---------------------------------------")
    print(f"   Year        : {year}")
    print(f"   Assignments : {len(batches)}")
    print(f"   PDFs        : {total}")
    print(f"   Output dir  : {output_dir}")
    for assignment_id, paths in batches.items():
        print(f"   {assignment_id:<20} {len(paths)}")
    return 0


def run_export(args: list[str]) -> int:
    file_format = parse_format_arg(args)
    profiles_root = parse_profiles_root_arg(args)
    explicit_output = _arg_value(args, "--output")
    if explicit_output:
        output_path = explicit_output
    elif file_format == "csv":
        output_path = "exports/csv_profiles"
    elif file_format == "excel":
        output_path = "exports/profiles.xlsx"
    else:
        output_path = "exports/profiles.json"

    if file_format == "json":
        result = export_profiles_json(output_path, root=profiles_root or "profiles")
    elif file_format == "csv":
        result = export_profiles_csv(output_path, root=profiles_root or "profiles")
    elif file_format == "excel":
        result = export_profiles_excel(output_path, root=profiles_root or "profiles")
    else:
        print(f"Unknown export format: {file_format}")
        return 1

    print("-- Export profiles ---------------------------------------")
    print(f"   Format : {file_format}")
    print(f"   Output : {result}")
    return 0


def run_import(args: list[str]) -> int:
    file_format = parse_format_arg(args)
    input_path = parse_input_arg(args)
    profiles_root = parse_profiles_root_arg(args)
    if not input_path:
        print("Import requires --input")
        return 1

    if file_format == "json":
        import_profiles_json(input_path, root=profiles_root or "profiles")
    elif file_format == "csv":
        import_profiles_csv(input_path, root=profiles_root or "profiles")
    elif file_format == "excel":
        import_profiles_excel(input_path, root=profiles_root or "profiles")
    else:
        print(f"Unknown import format: {file_format}")
        return 1

    print("-- Import profiles ---------------------------------------")
    print(f"   Format : {file_format}")
    print(f"   Input  : {input_path}")
    return 0


def run_web(args: list[str]) -> int:
    host = parse_host_arg(args)
    port = parse_port_arg(args)
    print("-- Local web app -----------------------------------------")
    print(f"   URL  : http://{host}:{port}")
    uvicorn.run(web_app, host=host, port=port)
    return 0


def main(args: list[str]) -> int:
    if "--help" in args or "-h" in args:
        print_usage()
        return 0

    mode = parse_mode_arg(args)
    if mode == "single":
        return run_single(args)
    if mode == "employee":
        return run_employee(args)
    if mode == "year":
        return run_year(args)
    if mode == "export":
        return run_export(args)
    if mode == "import":
        return run_import(args)
    if mode == "web":
        return run_web(args)

    print(f"Unknown mode: {mode}")
    print_usage()
    return 1


def cli() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(cli())
