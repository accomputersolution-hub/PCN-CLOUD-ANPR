from __future__ import annotations

"""Development-only ANPR image test endpoint. Does NOT create events."""

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import get_tenant, require_permission
from app.core.exceptions import ValidationAppError
from app.core.rbac import Permission
from app.services.tenant import TenantContext

router = APIRouter(prefix="/anpr", tags=["anpr-dev"])


class AnprTestPlate(BaseModel):
    raw_text: str = ""
    normalized_text: str = ""
    confidence: float = 0.0
    ocr_confidence: float = 0.0
    plate_confidence: float = 0.0
    matches_pattern: bool = False
    bbox: list[int] = Field(default_factory=list)


class AnprTestResponse(BaseModel):
    """Structured ANPR inference result. No database side effects."""

    development_only: bool = True
    vehicle_detected: bool
    plate_detected: bool
    vehicles: list[dict] = Field(default_factory=list)
    plates: list[AnprTestPlate] = Field(default_factory=list)
    processing_ms: int = 0
    error: str | None = None
    note: str = "Dev/test endpoint — does not create ANPR events or visits."


@router.post(
    "/test-image",
    response_model=AnprTestResponse,
    summary="[DEV] Run ANPR on an uploaded image",
    description="Development/test only. Requires authentication. Does not write events to PostgreSQL.",
)
async def test_anpr_image(
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.ANPR_TEST)),
) -> AnprTestResponse:
    # Tenant context ensures a logged-in user; no cross-tenant data is accessed (stateless inference).
    _ = ctx
    data = await file.read()
    if not data:
        raise ValidationAppError("Empty upload")
    if len(data) > 15 * 1024 * 1024:
        raise ValidationAppError("Image too large (max 15MB)")

    import tempfile
    from pathlib import Path

    suffix = Path(file.filename or "upload.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    try:
        from pcn_anpr.factory import build_pipeline

        pipeline = build_pipeline()
        result = pipeline.process_image(tmp_path)
    except Exception as exc:  # noqa: BLE001
        return AnprTestResponse(
            vehicle_detected=False,
            plate_detected=False,
            error=str(exc),
        )
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    plates = [AnprTestPlate.model_validate(p) for p in (result.get("plates") or [])]
    return AnprTestResponse(
        vehicle_detected=bool(result.get("vehicle_detected")),
        plate_detected=bool(result.get("plate_detected")),
        vehicles=list(result.get("vehicles") or []),
        plates=plates,
        processing_ms=int(result.get("processing_ms") or 0),
        error=result.get("error"),
    )
