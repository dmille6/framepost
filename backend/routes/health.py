from fastapi import APIRouter
from fastapi.responses import JSONResponse

from services.health import collect_health

router = APIRouter()


@router.get("/health")
def health():
    payload = collect_health()
    code = 503 if payload["status"] == "down" else 200
    return JSONResponse(payload, status_code=code)
