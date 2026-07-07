"""
测试网络请求记录：存储、查询、REST 端点。
"""
from fastapi.testclient import TestClient

from app.main import app
from app.mcp.core.logs import get_network_records, save_network_record
from app.mcp.tools.network_api import tool_get_network_trace, tool_ingest_network
from app.schemas.context import NetworkRecord


def test_save_and_get_network_record(fresh_db):
    record = NetworkRecord(
        timestamp=0,
        direction="outbound",
        method="GET",
        url="http://example.com/api?token=secret123",
        status_code=200,
        duration_ms=12.3,
    )
    record_id = save_network_record(record, trace_id="t1")
    assert record_id

    records = get_network_records("t1")
    assert len(records) == 1
    assert records[0].method == "GET"
    # 脱敏验证
    assert "secret123" not in records[0].url
    assert "***" in records[0].url


def test_get_network_trace_tool(fresh_db):
    tool_ingest_network(
        {"direction": "outbound", "method": "POST", "url": "/api/order", "status_code": 201, "timestamp": 0},
        trace_id="t2",
    )
    result = tool_get_network_trace("t2")
    assert result["found"] is True
    assert result["count"] == 1
    assert result["records"][0]["method"] == "POST"


def test_ingest_network_endpoint(fresh_db):
    client = TestClient(app)
    resp = client.post(
        "/ingest/network",
        json={
            "record": {"direction": "outbound", "method": "GET", "url": "/api/items", "status_code": 200, "timestamp": 0},
            "trace_id": "t3",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["saved"]

    resp2 = client.post("/mcp/tools/get_network_trace", json={"arguments": {"trace_id": "t3"}})
    assert resp2.status_code == 200
    payload = resp2.json()
    assert payload["content"]["found"] is True
    assert payload["content"]["count"] == 1
