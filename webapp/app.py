from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from generators.pdf_generator import PaystubTemplate
from models.paystub import Paystub
from webapp import service


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


class PreviewRequest(BaseModel):
    paystub: Paystub
    generation_plan: dict | None = None


class GenerateRequest(BaseModel):
    paystub: Paystub
    template: PaystubTemplate = PaystubTemplate.DETACHED_CHECK
    generation_plan: dict | None = None


class AssignmentLoadRequest(BaseModel):
    assignment_id: str
    year: int
    period_number: int


class ProfileExportRequest(BaseModel):
    file_format: str


class ProfileSaveRequest(BaseModel):
    record: dict


app = FastAPI(title="Paystub Generator Web", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_title": "Paystub Studio",
        },
    )


@app.get("/api/bootstrap")
async def bootstrap() -> dict:
    return service.build_bootstrap_payload()


@app.get("/api/assignments/{assignment_id}/periods")
async def assignment_periods(assignment_id: str, year: int) -> dict:
    return service.list_assignment_periods(assignment_id, year)


@app.post("/api/profiles/load-assignment")
async def load_assignment(request: AssignmentLoadRequest) -> dict:
    return service.load_assignment_paystub(
        assignment_id=request.assignment_id,
        year=request.year,
        period_number=request.period_number,
    )


@app.post("/api/profiles/export")
async def export_profiles(request: ProfileExportRequest) -> dict:
    result = service.export_profiles_bundle(request.file_format)
    return {
        **result,
        "download_url": f"/api/profile-exports/{result['filename']}",
    }


@app.post("/api/profiles/import")
async def import_profiles(
    file_format: str | None = Form(None),
    upload: UploadFile = File(...),
) -> dict:
    return await service.import_profiles_bundle(upload=upload, file_format=file_format)


@app.get("/api/profiles/catalog")
async def profiles_catalog() -> dict:
    return {
        "profile_catalog": service.profile_catalog(),
        "profile_summary": service.profile_summary(),
        "assignment_options": service.list_assignment_options(),
    }


@app.get("/api/profiles/{profile_type}/_new")
async def new_profile(profile_type: str) -> dict:
    return {
        "record": service.empty_profile_record(profile_type),
    }


@app.get("/api/profiles/{profile_type}/{profile_id}")
async def load_profile(profile_type: str, profile_id: str) -> dict:
    return {
        "record": service.load_profile_record(profile_type, profile_id),
    }


@app.post("/api/profiles/{profile_type}")
async def save_profile(profile_type: str, request: ProfileSaveRequest) -> dict:
    return service.save_profile_record(profile_type, request.record)


@app.post("/api/preview")
async def preview_document(request: PreviewRequest) -> dict:
    response = service.preview_payload(request.paystub)
    if request.generation_plan:
        response["generation_plan"] = service.generation_plan_payload(request.paystub, request.generation_plan)
    return response


@app.post("/api/generate")
async def generate_document(request: GenerateRequest) -> dict:
    plan_mode = str(request.generation_plan.get("mode", "single")).lower() if request.generation_plan else "single"
    if plan_mode in ("multiple", "full_year"):
        result = service.generate_pdf_batch(
            request.paystub,
            template=request.template,
            plan=request.generation_plan,
        )
        return {
            **result,
            "download_url": f"/api/downloads/{result['filename']}",
        }

    result = service.generate_pdf_document(request.paystub, template=request.template)
    return {
        **result,
        "download_url": f"/api/downloads/{result['filename']}",
    }


@app.get("/api/downloads/{filename}")
async def download_document(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = service.WEB_OUTPUT_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = "application/pdf"
    if safe_name.endswith(".zip"):
        media_type = "application/zip"
    return FileResponse(file_path, filename=safe_name, media_type=media_type)


@app.get("/api/profile-exports/{filename}")
async def download_profile_export(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = service.PROFILE_EXPORT_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = service.PROFILE_EXPORT_MEDIA_TYPES["json"]
    if safe_name.endswith(".xlsx"):
        media_type = service.PROFILE_EXPORT_MEDIA_TYPES["excel"]
    elif safe_name.endswith(".zip"):
        media_type = service.PROFILE_EXPORT_MEDIA_TYPES["csv"]
    return FileResponse(file_path, filename=safe_name, media_type=media_type)
