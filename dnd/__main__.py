"""Terminal front end. Run with: python -m dnd"""
from __future__ import annotations

import os
import sys
import textwrap

import anthropic

from .dm import DungeonMaster, opening_prompt
from .engine import CLASSES, RACES, load_game, new_character, save_game

SAVE_DIR = os.path.expanduser(os.environ.get("DND_SAVE_DIR", "~/.dnd-saves"))

DIM, BOLD, YELLOW, RED, RESET = ("\033[2m", "\033[1m", "\033[33m", "\033[31m", "\033[0m") \
    if sys.stdout.isatty() else ("",) * 5

HELP = """Commands:
  /stats         character sheet          /save [name]   save game
  /inv           inventory & gold         /load [name]   load game
  /quests        quest log                /quit          exit
Anything else is your action, e.g. "I sneak up to the door and listen."
Out-of-character questions: start with OOC or wrap in (parentheses)."""


def choose(prompt: str, options: list[str]) -> str:
    opts = "/".join(options)
    while True:
        ans = input(f"{prompt} [{opts}]: ").strip().lower()
        if ans in options:
            return ans
        print(f"Pick one of: {opts}")


def save_path(name: str) -> str:
    os.makedirs(SAVE_DIR, exist_ok=True)
    return os.path.join(SAVE_DIR, f"{name or 'autosave'}.json")


def create_or_load() -> tuple[DungeonMaster, bool]:
    saves = sorted(f[:-5] for f in os.listdir(SAVE_DIR) if f.endswith(".json")) if os.path.isdir(SAVE_DIR) else []
    if saves and choose("Load a saved game?", ["y", "n"]) == "y":
        name = choose("Which save?", saves)
        state, messages = load_game(save_path(name))
        return DungeonMaster(state, messages), False
    name = input("Character name: ").strip() or "Adventurer"
    race = choose("Race", list(RACES))
    cls = choose("Class", list(CLASSES))
    return DungeonMaster(new_character(name, race, cls)), True


def run_turn(dm: DungeonMaster, text: str) -> None:
    def on_roll(line: str):
        print(f"\n{YELLOW}🎲 {line}{RESET}", flush=True)

    print()
    try:
        err = dm.take_turn(text, on_text=lambda t: print(t, end="", flush=True), on_roll=on_roll)
    except anthropic.APIError as e:
        err = f"API error: {e}"
    print()
    if err:
        print(f"{RED}{err}{RESET}")
    else:
        save_game(save_path("autosave"), dm.state, dm.messages)


def main() -> None:
    print(f"{BOLD}⚔  Text D&D — Claude as your Dungeon Master ⚔{RESET}\n{DIM}{HELP}{RESET}\n")
    dm, is_new = create_or_load()
    if is_new:
        print(f"\n{dm.state.summary()}\n")
        premise = input("Adventure premise (blank = surprise me): ").strip()
        run_turn(dm, opening_prompt(dm.state, premise))
    else:
        print(f"\n{dm.state.summary()}\n")
        last = next((b["text"] for m in reversed(dm.messages) if m["role"] == "assistant"
                     for b in m["content"] if b["type"] == "text"), "")
        if last:
            print(textwrap.shorten(last, 600, placeholder=" …"))

    while not dm.state.pc.dead:
        try:
            text = input(f"\n{BOLD}> {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        cmd, _, arg = text.partition(" ")
        if cmd == "/quit":
            break
        elif cmd == "/help":
            print(HELP)
        elif cmd == "/stats":
            print(dm.state.summary())
        elif cmd == "/inv":
            pc = dm.state.pc
            print(f"Gold: {pc.gold}\n" + "\n".join(f"  - {i}" for i in pc.inventory))
        elif cmd == "/quests":
            if not dm.state.quests:
                print("(none)")
            for title, q in dm.state.quests.items():
                print(f"{title} [{q['status']}]" + "".join(f"\n  - {n}" for n in q["notes"]))
        elif cmd == "/save":
            save_game(save_path(arg.strip()), dm.state, dm.messages)
            print(f"Saved to {save_path(arg.strip())}")
        elif cmd == "/load":
            try:
                state, messages = load_game(save_path(arg.strip()))
            except FileNotFoundError:
                print("No such save.")
                continue
            dm = DungeonMaster(state, messages, dm.client)
            print(f"Loaded.\n{dm.state.summary()}")
        elif cmd.startswith("/"):
            print("Unknown command. /help for help.")
        else:
            run_turn(dm, text)

    if dm.state.pc.dead:
        print(f"\n{RED}{BOLD}☠  {dm.state.pc.name} has fallen. Game over.{RESET}")
    print("Farewell, adventurer.")


if __name__ == "__main__":
    main()
