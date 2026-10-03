"""The Dungeon Master: Claude narrates and calls engine tools to resolve mechanics."""
from __future__ import annotations

import copy
import os

import anthropic

from .engine import TOOLS, GameState

MODEL = os.environ.get("DND_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("DND_EFFORT", "medium")  # low = snappier/cheaper, high = smarter
MAX_TOOL_ROUNDS = 12

SYSTEM_PROMPT = """You are the Dungeon Master for a solo, text-based Dungeons & Dragons 5e-style adventure.

## Your role
- Narrate vivid, concise scenes (usually 1-3 short paragraphs). Second person, present tense.
- Play every NPC and monster with distinct voices and motives. The world reacts to the player's choices.
- End each response by giving the player a clear situation to react to. Do not list numbered options unless asked; let them be creative.
- Never decide what the player character says, feels, or does beyond what the player stated.
- Keep the tone adventurous with humor where it fits. Violence can be described but not gratuitous.

## Mechanics: the game engine is the source of truth
You have tools that roll dice and change the game state. Always use them; never invent roll results or silently change HP, items, gold, XP, or location.
- Uncertain player action → ability_check with a fair DC. Trivial actions need no roll.
- Combat: start_combat with level-appropriate enemies (a level-1 PC should face roughly 1-3 weak foes, ~5-12 HP total each, AC 11-13, +3 to hit, 1d6 damage). Each round: resolve the player's action (player_attack, or cast_spell + roll_dice), then each living enemy acts (usually enemy_attack). Then describe the round and ask what the player does next.
- Items found, bought, used up → update_inventory. Player can only use items they actually have.
- Defeated foes, clever play, finished quests → award_xp (weak foe ≈ 25-50 XP, quest ≈ 100-300).
- New area → set_location. New goals → update_quest.
- At 0 HP the player rolls death_save at the start of each of their turns; if they die, narrate a fitting end.
- If a tool returns an error, adjust (e.g. the item isn't there, no spell slots) and narrate accordingly.

Resolve all the mechanics for a turn with tools first, then write the narration once at the end, weaving in the results naturally (the player already sees the raw dice results, so don't repeat the numbers verbatim).

Each player turn ends with a system message containing the current game state. Trust it over your memory.
If the player tries to do something impossible for their character, tell them in-world. Out-of-character questions (in parentheses or starting with OOC) get brief out-of-character answers."""


class DungeonMaster:
    def __init__(self, state: GameState, messages: list | None = None, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.state = state
        # Append-only history of plain dicts (content blocks are passed back unchanged).
        self.messages: list = messages or []

    def take_turn(self, player_input: str, on_text, on_roll) -> str | None:
        """Run one player turn. Streams narration via on_text, mechanic results via on_roll.

        The turn is transactional: on a refusal or error, history and game state roll back.
        Returns an error string, or None on success.
        """
        checkpoint = (len(self.messages), copy.deepcopy(self.state.to_dict()))
        self.state.turn += 1
        self.messages.append({"role": "user", "content": player_input})
        self.messages.append({"role": "system", "content": "[GAME STATE]\n" + self.state.summary()})
        try:
            err = self._run_loop(on_text, on_roll)
        except anthropic.APIError:
            self._rollback(checkpoint)
            raise
        if err:
            self._rollback(checkpoint)
        return err

    def _rollback(self, checkpoint):
        n, state = checkpoint
        del self.messages[n:]
        restored = GameState.from_dict(state)
        restored.rng = self.state.rng
        self.state.__dict__.update(restored.__dict__)

    def _run_loop(self, on_text, on_roll) -> str | None:
        for _ in range(MAX_TOOL_ROUNDS):
            with self.client.beta.messages.stream(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT},
                cache_control={"type": "ephemeral"},  # history grows each turn; cache the prefix
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # re-run on another model if a safety classifier declines
            ) as stream:
                for text in stream.text_stream:
                    on_text(text)
                response = stream.get_final_message()

            content = _echoable(response.content)

            if response.stop_reason == "refusal":
                return "The DM declined that action. Try something else."
            self.messages.append({"role": "assistant", "content": content})

            if response.stop_reason == "pause_turn":
                continue
            tool_uses = [b for b in content if b["type"] == "tool_use"]
            if response.stop_reason == "max_tokens" and tool_uses:
                return "The DM's response was cut off. Try again."
            if not tool_uses:
                return None

            results = []
            for tu in tool_uses:
                try:
                    out = self.state.execute(tu["name"], tu["input"])
                    on_roll(out)
                    results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": out})
                except (ValueError, TypeError, KeyError) as e:
                    results.append({"type": "tool_result", "tool_use_id": tu["id"],
                                    "content": f"Error: {e}", "is_error": True})
            self.messages.append({"role": "user", "content": results})
            on_text("\n")
        return "The DM got stuck resolving that turn. Try rephrasing."


def _echoable(blocks) -> list[dict]:
    """Convert response blocks to dicts for history.

    After a mid-output fallback, model-internal blocks before the last `fallback` marker must not be
    echoed back (and those tool calls must not run).
    """
    dicts = [b.to_dict() for b in blocks]
    last_fb = max((i for i, b in enumerate(dicts) if b["type"] == "fallback"), default=-1)
    drop = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [b for i, b in enumerate(dicts) if i > last_fb or b["type"] not in drop]


def opening_prompt(state: GameState, premise: str) -> str:
    pc = state.pc
    return (f"(OOC) Start a new adventure for {pc.name}, a level 1 {pc.race} {pc.cls}. "
            f"Premise: {premise or 'your choice — something classic with a twist'}. "
            "Set the opening scene, give them a hook, set_location, and create the first quest with update_quest.")

