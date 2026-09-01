import pytest
from httpx import AsyncClient
from app.api.telemetry import get_telemetry_stream


@pytest.mark.asyncio
async def test_dashboard_endpoint_serves_html(async_client: AsyncClient):
    res = await async_client.get("/dashboard")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")

    html_content = res.text
    assert "<title>AegisGateway - Developer Dashboard</title>" in html_content
    assert 'id="latencyChart"' in html_content
    assert 'id="piiChart"' in html_content
    assert 'id="stream-log-body"' in html_content
    assert 'EventSource(' in html_content


@pytest.mark.asyncio
async def test_static_asset_delivery(async_client: AsyncClient):
    res = await async_client.get("/static/index.html")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert "AegisGateway" in res.text


@pytest.mark.asyncio
async def test_root_redirection_to_dashboard(async_client: AsyncClient):
    res = await async_client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers.get("location") == "/dashboard"


@pytest.mark.asyncio
async def test_sse_responsiveness_during_dashboard_access(async_client: AsyncClient):
    # Access dashboard endpoint
    dash_res = await async_client.get("/dashboard")
    assert dash_res.status_code == 200

    # Ensure SSE telemetry stream remains functional
    stream_res = await get_telemetry_stream()
    assert stream_res.status_code == 200
    assert stream_res.media_type == "text/event-stream"
