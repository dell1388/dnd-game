"""Web API tests with a fake Claude client."""
import json

import pytest
from fastapi.testclient import TestClient

from dnd import web
from test_game import Block, FakeClient, msg


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "SAVE_DIR", tmp_path)
    web._games.clear()
    fake = FakeClient([
        msg("tool_use", Block(type="tool_use", id="a", name="set_location", input={"name": "Docks"})),
        msg("end_turn", Block(type="text", text="Gulls cry.")),
        msg("tool_use", Block(type="tool_use", id="b", name="ability_check",
                              input={"ability": "WIS", "dc": 5, "skill": "perception", "advantage": "none"})),
        msg("end_turn", Block(type="text", text="You spot a smuggler.")),
    ])
    monkeypatch.setattr(web, "get_client", lambda: fake)
    return TestClient(web.app)


def events(r):
    return [json.loads(line) for line in r.text.splitlines()]


def test_full_flow(client):
    assert client.get("/").status_code == 200
    gid = client.post("/api/games", json={"name": "Tess", "race": "elf", "cls": "wizard"}).json()["id"]
    assert client.get(f"/api/games/{gid}").json()["started"] is False

    ev = events(client.post(f"/api/games/{gid}/start", json={"premise": "pirates"}))
    assert [e["kind"] for e in ev] == ["roll", "text", "text", "state"]
    assert ev[-1]["state"]["location"] == "Docks"
    assert client.post(f"/api/games/{gid}/start", json={}).status_code == 409

    ev = events(client.post(f"/api/games/{gid}/turn", json={"text": "I look around"}))
    assert any("perception" in e.get("text", "") for e in ev)

    # reload from disk, transcript hides the OOC opener
    web._games.clear()
    g = client.get(f"/api/games/{gid}").json()
    assert [t["kind"] for t in g["transcript"]] == ["roll", "dm", "player", "roll", "dm"]
    assert g["state"]["pc"]["cls"] == "wizard"


def test_bad_input(client):
    assert client.post("/api/games", json={"name": "X", "race": "orc", "cls": "bard"}).status_code == 400
    assert client.get("/api/games/../../etc/passwd").status_code == 404
    assert client.get("/api/games/" + "a" * 20).status_code == 404
