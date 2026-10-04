# Roadmap

Where the game is, where it's going, and in what order. Phases are sequential; items within a phase
are roughly priority-ordered. Each phase ends with a "done when" bar.

## Today (v0.1)
- Python rules engine owns all mechanics; Claude DMs via 14 tools
- 6 races, 5 classes, checks, attacks, crits, death saves, spell slots, rests, XP/levels, inventory, quests
- Terminal client + web client (streamed narration, inline dice, live character sheet, mobile layout)
- Saves to JSON; offline tests with a fake Claude client
- **Not yet played against the real API**

---

## Phase 0 — Prove it (days)
Play real sessions and fix what breaks before building more.

- [ ] Live smoke test with an API key: character creation → opening → combat → death/level-up
- [ ] Log tokens + cost per turn (`response.usage`); confirm prompt-cache hits are non-zero
- [ ] Tune DM prompt from real transcripts (verbosity, enemy balance, forgetting to call tools)
- [ ] Pick default model/effort from measured quality vs cost (Opus 5.5 `medium` vs `low`, Sonnet 5.5)
- [ ] Add an "undo last turn" button (history is already transactional per turn)
- [ ] Error UX: retry button on API errors, clear message when the key is missing

**Done when:** 3 full play sessions (~1 hr each) with no engine/DM desyncs and a known cost per hour.

## Phase 1 — Deeper solo game (1–3 weeks)
Make the rules feel like D&D rather than "D&D-flavored".

- [ ] **Weapons & armor table** — attacks use the equipped weapon's real dice/properties (finesse,
      ranged, two-handed) instead of the DM choosing damage dice
- [ ] **Spell lists per class** (SRD 5.1) with real effects resolved by the engine: save DCs,
      spell attack rolls, damage/healing, concentration
- [ ] **Conditions**: poisoned, prone, frightened, restrained, etc., auto-applying adv/disadv
- [ ] **Initiative order** tracked by the engine; enforce enemy turns each round
- [ ] **Character builder**: 4d6-drop-lowest or point-buy, choose skills, backgrounds
- [ ] **Hidden DCs** option (show only success/failure)
- [ ] **"I rolled" mode** — enter physical dice results instead of the engine rolling
- [ ] **Campaign memory**: engine-kept journal of NPCs, places, facts the DM writes via a tool and
      sees each turn → consistency over long campaigns
- [ ] **Long-campaign context**: server-side compaction for sessions that grow very large
- [ ] Levels 1–10 class features (Extra Attack, Sneak Attack, Channel Divinity, …)

**Done when:** a level 1→5 campaign plays with no rules the DM has to fake.

## Phase 2 — Shippable web app (2–4 weeks)
Let other people play without running Python.

- [ ] **Accounts** (username/password or magic link) and a games list per user
- [ ] **SQLite/Postgres** instead of JSON files
- [ ] **Cost controls**: per-user daily turn/token cap, rate limiting, optional bring-your-own-key
- [ ] **Deploy** (Fly.io / Render / a VPS) behind HTTPS
- [ ] **Polish**: dice-roll animation, Cinzel/EB Garamond restyle, sound toggle, PWA install
- [ ] Transcript export (Markdown/PDF) — "read your adventure as a story"
- [ ] Basic analytics: sessions, turns, cost, where players drop off

**Done when:** a friend can sign up from a link and play on their phone; spend is capped.

## Phase 3 — Multiplayer party (3–6 weeks)
The big one: 2–6 players, one Claude DM.

- [ ] **Rooms** with invite codes; each player has their own sheet
- [ ] **Live updates** over WebSocket (narration, rolls, sheets on every screen)
- [ ] **Turn modes**: strict initiative in combat; "everyone submits, DM resolves together" outside it
- [ ] **DM-called rolls** — prompt appears on the target player's screen (roll for me / I rolled)
- [ ] **Private DM messages** (whispers, secret perception results)
- [ ] Party inventory/gold; split loot
- [ ] Spectator link
- [ ] Reconnect/resume; AFK handling (skip turn, DM controls absent PC)

**Done when:** a 4-player group finishes a 2-hour session without anyone waiting on a bug.

## Phase 4 — Depth & modes (ongoing)
Pick based on what players actually ask for.

- [ ] **Grid battle map** (optional tactical combat): positions, range, cover, terrain
- [ ] **Scene art** generated per location
- [ ] **AI companions**: Claude-played party members with their own sheets for solo players
- [ ] **Adventure modules**: authored starting scenarios, maps and NPCs the DM follows
- [ ] **Human-GM mode**: a human runs the game, Claude assists (NPC voices, rules lookups,
      encounter building, recap writing)
- [ ] Shops/economy, crafting, downtime between sessions
- [ ] Discord bot client reusing the same engine + DM

---

## Open decisions
| Question | Options | Affects |
|---|---|---|
| Solo-first or multiplayer-first? | Phase 1 before 3 (current plan) / swap them | Order of phases 1–3 |
| Who pays for API usage? | You host + cap / bring-your-own-key / paid tier | Phase 2 design |
| Default model | Opus 5.5 (best DM) / Sonnet 5.5 (cheaper, faster) | Cost per hour |
| Rules fidelity | Strict SRD 5.1 / simplified "5e-lite" | Phase 1 scope |
| Content licensing | SRD 5.1 is CC-BY-4.0 — fine to use with attribution; avoid non-SRD book content | Spells, monsters |

## Architecture notes
- Keep the split: **engine = truth, LLM = narration**. Every new mechanic becomes an engine method +
  tool, never a prompt instruction alone.
- History stays **append-only** (required for preserved thinking and good for caching); per-turn
  state goes in as a system message.
- `DungeonMaster.take_turn` is the single entry point; every client (terminal, web, multiplayer,
  Discord) is a thin layer over it.
