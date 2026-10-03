"""Offline tests: engine rules + DM tool loop against a fake Claude client."""
import os
import tempfile
from types import SimpleNamespace

import pytest

from dnd.dm import DungeonMaster
from dnd.engine import TOOLS, load_game, new_character, roll, save_game


def game(seed=1):
    return new_character("Tess", "halfling", "rogue", seed=seed)


def test_character_creation():
    s = game()
    assert s.pc.stats["DEX"] == 17 and s.pc.stats["CHA"] == 15
    assert s.pc.hp == s.pc.max_hp == 9  # d8 + CON 13(+1)


def test_roll_parsing():
    import random
    total, rolls, bonus = roll("3d6+2", random.Random(0))
    assert len(rolls) == 3 and bonus == 2 and total == sum(rolls) + 2
    with pytest.raises(ValueError):
        roll("lots of dice", random.Random(0))


def test_combat_to_death():
    s = game()
    s.start_combat([{"name": "Ogre", "hp": 50, "ac": 1, "attack_bonus": 30, "damage": "1d4"}])
    for _ in range(100):
        if s.pc.hp == 0:
            break
        s.enemy_attack("ogre", "none")
    assert s.pc.hp == 0 and not s.pc.dead
    while not s.pc.dead:
        s.enemy_attack("Ogre", "none")  # hits while down add death-save failures
    assert s.pc.death_failures >= 3


def test_player_kills_enemy_and_xp_levels():
    s = game()
    s.start_combat([{"name": "Rat", "hp": 1, "ac": 1, "attack_bonus": 0, "damage": "1d1"}])
    for _ in range(20):
        if not s.enemies:
            break
        s.player_attack("Rat", "1d4", "DEX", "advantage")
    assert not s.enemies
    s.award_xp(300, "rats")
    assert s.pc.level == 2 and s.pc.max_hp > 9


def test_inventory_and_errors():
    s = game()
    s.update_inventory(add=["rope"], remove=["rapier"], gold_change=-5)
    assert "rope" in s.pc.inventory and not any("rapier" in i for i in s.pc.inventory) and s.pc.gold == 5
    assert "NOT IN INVENTORY" in s.update_inventory(remove=["dragon"])
    with pytest.raises(ValueError):
        s.update_inventory(gold_change=-1000)
    with pytest.raises(ValueError):
        s.cast_spell("Fireball", True)  # rogue has no slots


def test_save_load_roundtrip():
    s = game()
    s.update_quest("Find the cat", "active", "last seen in the sewers")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        save_game(p, s, [{"role": "user", "content": "hi"}])
        s2, msgs = load_game(p)
    assert s2.pc == s.pc and s2.quests == s.quests and msgs[0]["content"] == "hi"


def test_tool_schemas_match_methods():
    s = game()
    for t in TOOLS:
        assert callable(getattr(s, t["name"]))
        assert t["input_schema"]["additionalProperties"] is False


# ---------- fake Claude client ----------
class Block(SimpleNamespace):
    def to_dict(self):
        return dict(self.__dict__)


class FakeStream:
    def __init__(self, msg):
        self.msg = msg
        self.text_stream = [b.text for b in msg.content if b.type == "text"]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        return FakeStream(self.responses.pop(0))


def msg(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


def test_dm_turn_runs_tools_then_narrates():
    s = game()
    client = FakeClient([
        msg("tool_use",
            Block(type="tool_use", id="t1", name="ability_check",
                  input={"ability": "DEX", "dc": 10, "skill": "stealth", "advantage": "none"}),
            Block(type="tool_use", id="t2", name="update_inventory",
                  input={"add": ["silver key"], "remove": [], "gold_change": 0})),
        msg("end_turn", Block(type="text", text="You slip past the guard.")),
    ])
    dm = DungeonMaster(s, client=client)
    out, rolls = [], []
    assert dm.take_turn("I sneak past", out.append, rolls.append) is None
    assert "You slip past the guard." in "".join(out)
    assert len(rolls) == 2 and "silver key" in s.pc.inventory
    roles = [m["role"] for m in dm.messages]
    assert roles == ["user", "system", "assistant", "user", "assistant"]
    assert all(r["type"] == "tool_result" for r in dm.messages[3]["content"])
    # history is append-only: second call's messages start with the first call's
    assert client.calls[1]["messages"][:3] == dm.messages[:3]


def test_dm_tool_error_is_reported_not_raised():
    s = game()
    client = FakeClient([
        msg("tool_use", Block(type="tool_use", id="t1", name="enemy_attack",
                              input={"attacker": "Ghost", "advantage": "none"})),
        msg("end_turn", Block(type="text", text="Nothing there.")),
    ])
    dm = DungeonMaster(s, client=client)
    assert dm.take_turn("look", lambda t: None, lambda r: None) is None
    assert dm.messages[3]["content"][0]["is_error"] is True


def test_refusal_rolls_back_state_and_history():
    s = game()
    client = FakeClient([
        msg("tool_use", Block(type="tool_use", id="t1", name="update_inventory",
                              input={"add": ["gem"], "remove": [], "gold_change": 100})),
        msg("refusal"),
    ])
    dm = DungeonMaster(s, client=client)
    err = dm.take_turn("bad thing", lambda t: None, lambda r: None)
    assert err and dm.messages == [] and s.pc.gold == 10 and "gem" not in s.pc.inventory
