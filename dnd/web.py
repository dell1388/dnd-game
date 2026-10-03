"""Web front end. Run with: uvicorn dnd.web:app  (then open http://localhost:8000)"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .dm import DungeonMaster, opening_prompt
from .engine import CLASSES, RACES, GameState, load_game, mod, new_character, save_game

SAVE_DIR = Path(os.path.expanduser(os.environ.get("DND_SAVE_DIR", "~/.dnd-saves"))) / "web"
STATIC = Path(__file__).parent / "static"
GAME_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

app = FastAPI(title="Text D&D")
_games: dict[str, tuple[DungeonMaster, threading.Lock]] = {}
_games_lock = threading.Lock()


@lru_cache
def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _path(game_id: str) -> Path:
    if not GAME_ID_RE.match(game_id):
        raise HTTPException(404, "game not found")
    return SAVE_DIR / f"{game_id}.json"


def _get(game_id: str) -> tuple[DungeonMaster, threading.Lock]:
    path = _path(game_id)
    with _games_lock:
        if game_id not in _games:
            if not path.exists():
                raise HTTPException(404, "game not found")
            state, messages = load_game(str(path))
            _games[game_id] = (DungeonMaster(state, messages, get_client()), threading.Lock())
        return _games[game_id]


def _save(game_id: str, dm: DungeonMaster) -> None:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    save_game(str(_path(game_id)), dm.state, dm.messages)


def state_json(state: GameState) -> dict:
    pc = asdict(state.pc)
    pc["mods"] = {a: mod(v) for a, v in pc["stats"].items()}
    return {"pc": pc, "location": state.location, "quests": state.quests,
            "enemies": [asdict(e) for e in state.enemies]}


def transcript(messages: list) -> list[dict]:
    """Flatten API history into what the player saw: their inputs, dice results, narration."""
    out = []
    for m in messages:
        c = m["content"]
        if m["role"] == "user" and isinstance(c, str):
            if not c.startswith("(OOC) Start a new adventure"):
                out.append({"kind": "player", "text": c})
        elif m["role"] == "user":
            out += [{"kind": "roll", "text": b["content"]} for b in c
                    if b["type"] == "tool_result" and not b.get("is_error")]
        elif m["role"] == "assistant":
            out += [{"kind": "dm", "text": b["text"]} for b in c if b["type"] == "text" and b["text"].strip()]
    return out


# ---------- API ----------
class NewGame(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    race: str
    cls: str


class Start(BaseModel):
    premise: str = Field("", max_length=500)


class Turn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/options")
def options():
    return {"races": list(RACES), "classes": list(CLASSES)}


@app.post("/api/games")
def create_game(body: NewGame):
    if body.race.lower() not in RACES or body.cls.lower() not in CLASSES:
        raise HTTPException(400, "unknown race or class")
    game_id = secrets.token_urlsafe(16)
    dm = DungeonMaster(new_character(body.name.strip(), body.race, body.cls), client=get_client())
    with _games_lock:
        _games[game_id] = (dm, threading.Lock())
    _save(game_id, dm)
    return {"id": game_id}


@app.get("/api/games/{game_id}")
def get_game(game_id: str):
    dm, _ = _get(game_id)
    return {"state": state_json(dm.state), "transcript": transcript(dm.messages),
            "started": bool(dm.messages)}


@app.post("/api/games/{game_id}/start")
async def start_game(game_id: str, body: Start):
    dm, _ = _get(game_id)
    if dm.messages:
        raise HTTPException(409, "game already started")
    return _stream_turn(game_id, opening_prompt(dm.state, body.premise.strip()))


@app.post("/api/games/{game_id}/turn")
async def take_turn(game_id: str, body: Turn):
    dm, _ = _get(game_id)
    if dm.state.pc.dead:
        raise HTTPException(409, "your character is dead")
    return _stream_turn(game_id, body.text.strip())


def _stream_turn(game_id: str, text: str) -> StreamingResponse:
    """Run the (blocking) DM turn in a thread and stream its events as NDJSON."""
    dm, lock = _get(game_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "a turn is already in progress")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(kind: str, **data):
        loop.call_soon_threadsafe(queue.put_nowait, {"kind": kind, **data})

    def worker():
        try:
            err = dm.take_turn(text, on_text=lambda t: emit("text", text=t),
                               on_roll=lambda r: emit("roll", text=r))
            if err:
                emit("error", text=err)
            else:
                _save(game_id, dm)
        except anthropic.APIError as e:
            emit("error", text=f"API error: {getattr(e, 'message', e)}")
        except Exception as e:  # keep the stream well-formed
            emit("error", text=f"Server error: {e}")
        finally:
            emit("state", state=state_json(dm.state))
            lock.release()
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=worker, daemon=True).start()

    async def body():
        while (event := await queue.get()) is not None:
            yield json.dumps(event) + "\n"

    return StreamingResponse(body(), media_type="application/x-ndjson")
