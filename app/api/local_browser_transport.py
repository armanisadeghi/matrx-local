"""Grant-authenticated private ingress for local-browser lifecycle callbacks."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.local_browser_transport import TransportRefusal, execute_lifecycle, read_execute_body

router = APIRouter(prefix="/local-browser", tags=["local-browser"])
_NO_STORE = {"Cache-Control": "no-store"}


@router.post("/execute")
async def execute(request: Request) -> JSONResponse:
    """Run only the closed local lifecycle relay, never normal engine auth."""
    operation = "unknown"
    try:
        body = await read_execute_body(request)
        operation = body["operation"]
        address = request.client.host if request.client is not None else "unknown"
        result = await execute_lifecycle(body, address=address)
        return JSONResponse(result, headers=_NO_STORE)
    except TransportRefusal as refusal:
        return JSONResponse(
            {"status": "refused", "operation": operation, "reason": refusal.reason},
            status_code=refusal.status_code,
            headers=_NO_STORE,
        )
    except Exception:
        # No exception text, body, grant, or request URL reaches this surface.
        return JSONResponse(
            {"status": "refused", "operation": "unknown", "reason": "transport_unavailable"},
            status_code=503,
            headers=_NO_STORE,
        )
