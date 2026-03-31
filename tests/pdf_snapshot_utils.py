import hashlib
import json
from pathlib import Path

import pypdfium2 as pdfium

from generators.pdf_generator import PaystubTemplate, generate_paystub_pdf
from models.pay_period import get_pay_periods
from models.payroll_calculator import compute_paystub_data
from sample_data import sample_employee


SNAPSHOT_FIXTURE = Path(__file__).with_name("fixtures").joinpath("pdf_visual_snapshots.json")
DEFAULT_FAILURE_PREVIEW_DIR = Path(__file__).with_name("fixtures").joinpath("_actual_previews")
DEFAULT_REGEN_PREVIEW_DIR = Path(__file__).with_name("fixtures").joinpath("pdf_preview_gallery")


def build_sample_paystub_data() -> dict:
    period = get_pay_periods(2026, sample_employee.frequency)[0]
    return compute_paystub_data(
        sample_employee,
        period.start,
        period.end,
        period.pay_date,
    )


def build_long_content_paystub_data() -> dict:
    data = build_sample_paystub_data()
    data["company_name"] = "Westchester Strategic Logistics and Workforce Operations Consortium"
    data["company_address"] = (
        "8450 INTERNATIONAL COMMERCE PARKWAY SUITE 1800\n"
        "BUILDING NORTH LOBBY EAST ANNEX\n"
        "WHITE PLAINS, NY 10601-4821"
    )
    data["employee_name"] = "Alexandria Catherine Montgomery-Santiago de la Vega"
    data["employee_address"] = (
        "12455 RIVER AND HARBOR VIEW TERRACE APARTMENT 28B\n"
        "LONG ISLAND CITY, NY 11101-8804"
    )
    data["important_notes"] = [
        "DIRECT DEPOSIT ADVICE REISSUED AFTER BANK MERGER REVIEW; VERIFY ROUTING, ACCOUNT OWNERSHIP, AND BENEFIT CARRYOVER DETAILS WITH PAYROLL OPERATIONS.",
        "OVERTIME DIFFERENTIAL REFLECTS MULTI-SITE COVERAGE DURING TRAINING DEPLOYMENT WINDOW AND SHOULD MATCH APPROVED MANAGER ATTESTATION LOGS.",
    ]
    data["footnotes"] = [
        "FOR QUESTIONS ABOUT TAX WITHHOLDING OR BENEFIT ACCRUALS, CONTACT THE SHARED SERVICES TEAM BEFORE THE NEXT BIWEEKLY CLOSE.",
    ]
    data["earnings"][0]["label"] = "Regular Operations Support and Administration"
    data["deductions"][0]["label"] = "401(k) Retirement Savings Plan Contribution"
    data["other_benefits"][0]["label"] = "Group Term Life and Supplemental Coverage"
    return data


def build_snapshot_cases() -> dict[str, dict]:
    return {
        "sample": build_sample_paystub_data(),
        "long_content": build_long_content_paystub_data(),
    }


def render_pdf_snapshot_hash(pdf_path: Path, scale: float = 1.0) -> str:
    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[0]
    bitmap = page.render(scale=scale)
    try:
        digest = hashlib.sha256()
        digest.update(f"{bitmap.width}x{bitmap.height}:{bitmap.stride}".encode("utf-8"))
        digest.update(bytes(bitmap.buffer))
        return digest.hexdigest()
    finally:
        bitmap.close()
        page.close()
        pdf.close()


def save_pdf_preview_pngs(pdf_path: Path, output_dir: Path, stem: str, scale: float = 1.5) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    previews: list[Path] = []
    try:
        for page_index, page in enumerate(pdf):
            bitmap = page.render(scale=scale)
            try:
                preview_path = output_dir / f"{stem}_page_{page_index + 1}.png"
                bitmap.to_pil().save(preview_path, format="PNG")
                previews.append(preview_path)
            finally:
                bitmap.close()
                page.close()
    finally:
        pdf.close()
    return previews


def render_template_snapshots(
    output_dir: str | Path,
    data: dict,
    case_name: str,
    *,
    save_previews: bool = False,
    preview_dir: str | Path | None = None,
    hash_scale: float = 1.0,
    preview_scale: float = 1.5,
) -> tuple[dict[str, str], dict[str, Path]]:
    snapshots: dict[str, str] = {}
    pdf_paths: dict[str, Path] = {}
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    preview_root = Path(preview_dir) if preview_dir is not None else None

    for template in (
        PaystubTemplate.ADP,
        PaystubTemplate.SIMPLE,
        PaystubTemplate.DETACHED_CHECK,
    ):
        pdf_path = Path(generate_paystub_pdf(data, output_dir=str(output_dir_path), template=template))
        snapshot_key = f"{case_name}:{template.value}"
        snapshots[snapshot_key] = render_pdf_snapshot_hash(pdf_path, scale=hash_scale)
        pdf_paths[snapshot_key] = pdf_path
        if save_previews and preview_root is not None:
            save_pdf_preview_pngs(
                pdf_path,
                preview_root / case_name,
                template.value,
                scale=preview_scale,
            )

    return snapshots, pdf_paths


def load_snapshot_fixture() -> dict:
    return json.loads(SNAPSHOT_FIXTURE.read_text())


def write_snapshot_fixture(snapshot_map: dict[str, str]) -> None:
    SNAPSHOT_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FIXTURE.write_text(json.dumps(snapshot_map, indent=2, sort_keys=True) + "\n")
