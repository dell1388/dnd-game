# Text D&D with Claude as Dungeon Master

Solo, terminal-based D&D 5e-lite. Claude narrates; a Python rules engine owns every number.

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python -m dnd
```

## Design

```
player input ──► DungeonMaster (dnd/dm.py) ──► Claude (streams narration)
                       ▲                            │ tool calls
                       │ tool results               ▼
                       └────────────── GameState (dnd/engine.py)
                                       dice, HP, combat, inventory, XP, quests
```

- **LLM narrates, engine decides.** Claude can't invent rolls or quietly heal you: it must call tools
  (`ability_check`, `player_attack`, `enemy_attack`, `update_inventory`, `award_xp`, …). The engine
  rolls, applies results, and returns the truth. Rolls are shown to the player (🎲 lines).
- **State injected each turn** as a mid-conversation system message, so the DM always sees current
  HP/inventory and the player can't spoof it.
- **Transactional turns.** If the model refuses or errors, history and game state roll back.
- **Prompt caching** on the growing history keeps long campaigns cheap; **server-side fallbacks** keep
  play going if a safety classifier declines a scene.
- **Saves**: autosave after each turn, plus `/save name` / `/load name` (`~/.dnd-saves/`).

## Rules covered
6 races, 5 classes (fighter, rogue, wizard, cleric, ranger), ability/skill checks with proficiency,
advantage/disadvantage, attack rolls vs AC, crits, death saves, spell slots, short/long rests,
XP + level-ups to 10, inventory/gold, quest log.

## Config
| env | default | |
|---|---|---|
| `DND_MODEL` | `claude-opus-5-5` | e.g. `claude-sonnet-5-5` for cheaper/faster |
| `DND_EFFORT` | `medium` | `low` = snappier, `high` = smarter DM |
| `DND_SAVE_DIR` | `~/.dnd-saves` | |

## Tests
`pip install pytest && python -m pytest` — offline; engine rules + DM tool loop against a fake client.

## Ideas to extend
Party companions (extra NPC stat blocks), a world-state/lore memory file, a web UI (FastAPI + SSE
reusing `DungeonMaster.take_turn`), images per scene, multiplayer turn order.
