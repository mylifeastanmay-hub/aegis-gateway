import asyncio
from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from app.core.security import verify_api_key
from app.schemas.telemetry import TelemetrySummary
from app.services.telemetry import telemetry_service

router = APIRouter(prefix="/api/v1/telemetry", tags=["Telemetry & Analytics"])


@router.get("/stats", response_model=TelemetrySummary, status_code=status.HTTP_200_OK)
async def get_telemetry_stats():
    """
    Returns current JSON snapshot of aggregated operational, financial, and security metrics.
    """
    return await telemetry_service.get_metrics_summary()


@router.get("/stream", status_code=status.HTTP_200_OK)
async def get_telemetry_stream():
    """
    Server-Sent Events (SSE) streaming endpoint pushing real-time telemetry updates
    to connected dashboards every 1 second or upon new completion events.
    """
    queue = telemetry_service.subscribe()

    async def sse_generator():
        try:
            # Yield initial snapshot immediately upon connection
            initial_summary = await telemetry_service.get_metrics_summary()
            yield f"data: {initial_summary.model_dump_json()}\n\n"

            while True:
                await asyncio.sleep(0.1)
                try:
                    item_json = queue.get_nowait()
                    yield f"data: {item_json}\n\n"
                except asyncio.QueueEmpty:
                    pass
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            telemetry_service.unsubscribe(queue)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/reset", status_code=status.HTTP_200_OK)
async def reset_telemetry_stats(authenticated_key: str = Depends(verify_api_key)):
    """
    Protected admin endpoint to reset current telemetry counters.
    Requires Bearer or X-API-Key authentication.
    """
    await telemetry_service.reset()
    return {
        "status": "success",
        "message": "Telemetry metrics successfully reset."
    }
