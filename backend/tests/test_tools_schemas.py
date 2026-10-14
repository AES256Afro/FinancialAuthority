"""The tool schemas in OpenAI's and Anthropic's shapes, and the prompt pack, cover every MCP tool."""

from fastapi.testclient import TestClient

from app.mcp_server import mcp_server


def test_schemas_cover_every_tool_and_need_a_credential(owner: TestClient, client: TestClient):
    names = {t.name for t in mcp_server._tool_manager.list_tools()}
    openai = owner.get("/api/tools/openai.json").json()
    assert {t["function"]["name"] for t in openai["tools"]} == names
    assert all(t["function"]["parameters"]["type"] == "object" for t in openai["tools"])
    anthropic = owner.get("/api/tools/anthropic.json").json()
    assert {t["name"] for t in anthropic["tools"]} == names and all("input_schema" in t for t in anthropic["tools"])
    assert set(openai["rest"]) >= names
    pack = owner.get("/api/tools/prompt.md").text
    assert "System prompt" in pack and "finance_snapshot" in pack and "{plan}" not in pack
    assert TestClient(client.app).get("/api/tools/openai.json").status_code == 401
