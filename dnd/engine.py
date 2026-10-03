"""Game rules and state. The LLM narrates; this module decides what actually happens.

Every mechanic (dice, HP, inventory, XP) lives here so the DM can't fudge results.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field

ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]

SKILLS = {
    "athletics": "STR",
    "acrobatics": "DEX", "sleight_of_hand": "DEX", "stealth": "DEX",
    "arcana": "INT", "history": "INT", "investigation": "INT", "nature": "INT", "religion": "INT",
    "animal_handling": "WIS", "insight": "WIS", "medicine": "WIS", "perception": "WIS", "survival": "WIS",
    "deception": "CHA", "intimidation": "CHA", "performance": "CHA", "persuasion": "CHA",
}

# Standard array assigned per class, plus starting kit.
CLASSES = {
    "fighter": {
        "stats": {"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 12, "CHA": 10},
        "hit_die": 10, "ac": 16, "skills": ["athletics", "intimidation"],
        "inventory": ["longsword (1d8)", "chain mail", "shield", "explorer's pack"],
        "spell_slots": 0,
    },
    "rogue": {
        "stats": {"STR": 8, "DEX": 15, "CON": 13, "INT": 12, "WIS": 10, "CHA": 14},
        "hit_die": 8, "ac": 14, "skills": ["stealth", "sleight_of_hand", "deception", "perception"],
        "inventory": ["rapier (1d8)", "shortbow (1d6)", "leather armor", "thieves' tools"],
        "spell_slots": 0,
    },
    "wizard": {
        "stats": {"STR": 8, "DEX": 13, "CON": 14, "INT": 15, "WIS": 12, "CHA": 10},
        "hit_die": 6, "ac": 11, "skills": ["arcana", "investigation"],
        "inventory": ["quarterstaff (1d6)", "spellbook", "component pouch"],
        "spell_slots": 2,
    },
    "cleric": {
        "stats": {"STR": 13, "DEX": 8, "CON": 14, "INT": 10, "WIS": 15, "CHA": 12},
        "hit_die": 8, "ac": 18, "skills": ["medicine", "religion"],
        "inventory": ["mace (1d6)", "scale mail", "shield", "holy symbol"],
        "spell_slots": 2,
    },
    "ranger": {
        "stats": {"STR": 12, "DEX": 15, "CON": 13, "INT": 8, "WIS": 14, "CHA": 10},
        "hit_die": 10, "ac": 14, "skills": ["survival", "perception", "stealth"],
        "inventory": ["longbow (1d8)", "two shortswords (1d6)", "leather armor"],
        "spell_slots": 0,
    },
}

RACES = {
    "human": {a: 1 for a in ABILITIES},
    "elf": {"DEX": 2, "INT": 1},
    "dwarf": {"CON": 2, "WIS": 1},
    "halfling": {"DEX": 2, "CHA": 1},
    "half-orc": {"STR": 2, "CON": 1},
    "tiefling": {"CHA": 2, "INT": 1},
}

XP_LEVELS = [0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000]

DICE_RE = re.compile(r"^\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*$", re.I)


def mod(score: int) -> int:
    return (score - 10) // 2


def prof_bonus(level: int) -> int:
    return 2 + (level - 1) // 4


def roll(expr: str, rng: random.Random) -> tuple[int, list[int], int]:
    """Roll 'NdM+K'. Returns (total, individual rolls, modifier)."""
    m = DICE_RE.match(expr)
    if not m:
        raise ValueError(f"bad dice expression {expr!r}; use e.g. '2d6+3'")
    n, sides = int(m.group(1) or 1), int(m.group(2))
    if not (1 <= n <= 100 and 2 <= sides <= 1000):
        raise ValueError("dice out of range")
    bonus = int(m.group(3).replace(" ", "")) if m.group(3) else 0
    rolls = [rng.randint(1, sides) for _ in range(n)]
    return sum(rolls) + bonus, rolls, bonus


def d20(rng: random.Random, advantage: str = "none") -> tuple[int, list[int]]:
    rolls = [rng.randint(1, 20)]
    if advantage in ("advantage", "disadvantage"):
        rolls.append(rng.randint(1, 20))
    pick = max if advantage == "advantage" else min
    return pick(rolls), rolls


@dataclass
class Enemy:
    name: str
    hp: int
    max_hp: int
    ac: int
    attack_bonus: int
    damage: str


@dataclass
class Character:
    name: str
    race: str
    cls: str
    level: int = 1
    xp: int = 0
    hp: int = 0
    max_hp: int = 0
    ac: int = 10
    stats: dict = field(default_factory=dict)
    skills: list = field(default_factory=list)
    inventory: list = field(default_factory=list)
    gold: int = 10
    spell_slots: int = 0
    max_spell_slots: int = 0
    death_successes: int = 0
    death_failures: int = 0
    dead: bool = False


@dataclass
class GameState:
    pc: Character
    location: str = "Unknown"
    quests: dict = field(default_factory=dict)  # title -> {"status", "notes"}
    enemies: list = field(default_factory=list)  # list[Enemy]
    turn: int = 0
    log: list = field(default_factory=list)  # human-readable mechanic results this turn
    seed: int | None = None

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    # ---------- persistence ----------
    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("log")
        d.pop("seed")  # don't replay the same rolls after a load
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        d = dict(d)
        d["pc"] = Character(**d["pc"])
        d["enemies"] = [Enemy(**e) for e in d.get("enemies", [])]
        return cls(**d)

    # ---------- display ----------
    def summary(self) -> str:
        pc = self.pc
        stats = " ".join(f"{a} {pc.stats[a]}({mod(pc.stats[a]):+d})" for a in ABILITIES)
        lines = [
            f"{pc.name} — level {pc.level} {pc.race} {pc.cls}  XP {pc.xp}/{XP_LEVELS[min(pc.level, 9)]}",
            f"HP {pc.hp}/{pc.max_hp}  AC {pc.ac}  Gold {pc.gold}"
            + (f"  Spell slots {pc.spell_slots}/{pc.max_spell_slots}" if pc.max_spell_slots else ""),
            stats,
            f"Proficient skills: {', '.join(pc.skills)} (prof +{prof_bonus(pc.level)})",
            f"Inventory: {', '.join(pc.inventory) or 'nothing'}",
            f"Location: {self.location}",
        ]
        if pc.hp == 0 and not pc.dead:
            lines.append(f"UNCONSCIOUS — death saves: {pc.death_successes} successes, {pc.death_failures} failures")
        if self.quests:
            lines.append("Quests: " + "; ".join(f"{t} [{q['status']}]" for t, q in self.quests.items()))
        if self.enemies:
            lines.append("In combat with: " + ", ".join(
                f"{e.name} (HP {e.hp}/{e.max_hp}, AC {e.ac})" for e in self.enemies))
        return "\n".join(lines)

    # ---------- tool implementations ----------
    def _note(self, text: str) -> str:
        self.log.append(text)
        return text

    def _enemy(self, name: str) -> Enemy:
        for e in self.enemies:
            if e.name.lower() == name.lower():
                return e
        raise ValueError(f"no enemy named {name!r}; active: {[e.name for e in self.enemies] or 'none'}")

    def roll_dice(self, expression: str, reason: str) -> str:
        total, rolls, bonus = roll(expression, self.rng)
        return self._note(f"{reason}: {expression} → {rolls}{bonus:+d} = {total}" if bonus
                          else f"{reason}: {expression} → {rolls} = {total}")

    def ability_check(self, ability: str, dc: int, skill: str | None = None,
                      advantage: str = "none") -> str:
        pc = self.pc
        if skill and skill.lower() != "none":
            skill = skill.lower().replace(" ", "_")
            ability = SKILLS.get(skill, ability)
        else:
            skill = None
        ability = ability.upper()
        if ability not in ABILITIES:
            raise ValueError(f"unknown ability {ability!r}")
        bonus = mod(pc.stats[ability]) + (prof_bonus(pc.level) if skill in pc.skills else 0)
        nat, rolls = d20(self.rng, advantage)
        total = nat + bonus
        ok = total >= dc
        label = f"{ability}" + (f" ({skill})" if skill else "")
        return self._note(f"{label} check: d20 {rolls} {bonus:+d} = {total} vs DC {dc} → "
                          f"{'SUCCESS' if ok else 'FAILURE'}" + (" (nat 20!)" if nat == 20 else "")
                          + (" (nat 1!)" if nat == 1 else ""))

    def start_combat(self, enemies: list[dict]) -> str:
        for e in enemies:
            name, n = e["name"], 2
            while any(x.name.lower() == name.lower() for x in self.enemies):
                name = f"{e['name']} {n}"
                n += 1
            self.enemies.append(Enemy(name, e["hp"], e["hp"], e["ac"], e["attack_bonus"], e["damage"]))
        init, _ = d20(self.rng)
        init += mod(self.pc.stats["DEX"])
        return self._note(f"Combat! {', '.join(e.name for e in self.enemies)}. "
                          f"{self.pc.name} initiative {init}.")

    def player_attack(self, target: str, damage_dice: str, ability: str = "STR",
                      advantage: str = "none") -> str:
        enemy = self._enemy(target)
        bonus = mod(self.pc.stats[ability.upper()]) + prof_bonus(self.pc.level)
        nat, rolls = d20(self.rng, advantage)
        total = nat + bonus
        if nat == 1 or (total < enemy.ac and nat != 20):
            return self._note(f"{self.pc.name} attacks {enemy.name}: d20 {rolls} {bonus:+d} = {total} "
                              f"vs AC {enemy.ac} → MISS")
        dmg, drolls, _ = roll(damage_dice, self.rng)
        if nat == 20:  # crit: roll damage dice again
            extra, erolls, eb = roll(damage_dice, self.rng)
            dmg += extra - eb
            drolls += erolls
        dmg += mod(self.pc.stats[ability.upper()])
        dmg = max(1, dmg)
        enemy.hp = max(0, enemy.hp - dmg)
        msg = (f"{self.pc.name} attacks {enemy.name}: d20 {rolls} {bonus:+d} = {total} vs AC {enemy.ac} → "
               f"{'CRITICAL HIT' if nat == 20 else 'HIT'} for {dmg} damage. {enemy.name} HP {enemy.hp}/{enemy.max_hp}")
        if enemy.hp == 0:
            self.enemies.remove(enemy)
            msg += f" — {enemy.name} is defeated!"
            if not self.enemies:
                msg += " All enemies defeated."
        return self._note(msg)

    def enemy_attack(self, attacker: str, advantage: str = "none") -> str:
        enemy = self._enemy(attacker)
        pc = self.pc
        nat, rolls = d20(self.rng, advantage)
        total = nat + enemy.attack_bonus
        if nat == 1 or (total < pc.ac and nat != 20):
            return self._note(f"{enemy.name} attacks: d20 {rolls} {enemy.attack_bonus:+d} = {total} "
                              f"vs AC {pc.ac} → MISS")
        dmg, _, _ = roll(enemy.damage, self.rng)
        if nat == 20:
            extra, _, eb = roll(enemy.damage, self.rng)
            dmg += extra - eb
        return self._note(f"{enemy.name} attacks: d20 {rolls} {enemy.attack_bonus:+d} = {total} vs AC {pc.ac} → "
                          f"{'CRITICAL HIT' if nat == 20 else 'HIT'}. " + self._damage_pc(max(1, dmg)))

    def _damage_pc(self, dmg: int) -> str:
        pc = self.pc
        if pc.hp == 0:
            pc.death_failures += 1
            return self._check_death(f"{pc.name} takes damage while down: +1 death save failure.")
        pc.hp = max(0, pc.hp - dmg)
        if pc.hp == 0:
            if dmg >= pc.max_hp:  # massive damage
                pc.dead = True
                return f"{pc.name} takes {dmg} damage and is killed outright."
            return f"{pc.name} takes {dmg} damage and falls UNCONSCIOUS (0 HP). Death saves begin."
        return f"{pc.name} takes {dmg} damage. HP {pc.hp}/{pc.max_hp}"

    def _check_death(self, msg: str) -> str:
        pc = self.pc
        if pc.death_failures >= 3:
            pc.dead = True
            msg += f" {pc.name} has DIED."
        return msg

    def end_combat(self, reason: str) -> str:
        names = [e.name for e in self.enemies]
        self.enemies.clear()
        return self._note(f"Combat ends ({reason})." + (f" Removed: {', '.join(names)}" if names else ""))

    def change_hp(self, amount: int, reason: str) -> str:
        pc = self.pc
        if amount < 0:
            return self._note(f"{reason}: " + self._damage_pc(-amount))
        was_down = pc.hp == 0
        pc.hp = min(pc.max_hp, pc.hp + amount)
        if was_down and pc.hp > 0:
            pc.death_successes = pc.death_failures = 0
        return self._note(f"{reason}: {pc.name} heals {amount}. HP {pc.hp}/{pc.max_hp}")

    def death_save(self) -> str:
        pc = self.pc
        if pc.hp > 0 or pc.dead:
            raise ValueError("death saves only apply while the player is at 0 HP and alive")
        nat, _ = d20(self.rng)
        if nat == 20:
            pc.hp, pc.death_successes, pc.death_failures = 1, 0, 0
            return self._note(f"Death save: natural 20! {pc.name} regains 1 HP and wakes up.")
        if nat == 1:
            pc.death_failures += 2
        elif nat >= 10:
            pc.death_successes += 1
        else:
            pc.death_failures += 1
        msg = f"Death save: d20 = {nat}. Successes {pc.death_successes}/3, failures {pc.death_failures}/3."
        if pc.death_successes >= 3:
            pc.death_successes = pc.death_failures = 0
            msg += f" {pc.name} is STABLE (still unconscious at 0 HP)."
        return self._note(self._check_death(msg))

    def update_inventory(self, add: list[str] | None = None, remove: list[str] | None = None,
                         gold_change: int = 0) -> str:
        pc = self.pc
        missing = []
        for item in remove or []:
            match = next((i for i in pc.inventory if i.lower() == item.lower()), None) \
                or next((i for i in pc.inventory if item.lower() in i.lower()), None)
            if match:
                pc.inventory.remove(match)
            else:
                missing.append(item)
        pc.inventory.extend(add or [])
        if pc.gold + gold_change < 0:
            raise ValueError(f"not enough gold: has {pc.gold}, change {gold_change}")
        pc.gold += gold_change
        parts = []
        if add:
            parts.append(f"gained {', '.join(add)}")
        if remove and len(missing) < len(remove):
            parts.append(f"lost {', '.join(i for i in remove if i not in missing)}")
        if gold_change:
            parts.append(f"gold {gold_change:+d} (now {pc.gold})")
        msg = "Inventory: " + ("; ".join(parts) or "no change")
        if missing:
            msg += f". NOT IN INVENTORY: {', '.join(missing)}"
        return self._note(msg)

    def award_xp(self, amount: int, reason: str) -> str:
        pc = self.pc
        pc.xp += amount
        msg = f"+{amount} XP ({reason}). Total {pc.xp}."
        while pc.level < 10 and pc.xp >= XP_LEVELS[pc.level]:
            pc.level += 1
            gain = max(1, CLASSES[pc.cls]["hit_die"] // 2 + 1 + mod(pc.stats["CON"]))
            pc.max_hp += gain
            pc.hp += gain
            if pc.max_spell_slots:
                pc.max_spell_slots += 1
                pc.spell_slots += 1
            msg += f" LEVEL UP! Now level {pc.level}, +{gain} max HP."
        return self._note(msg)

    def cast_spell(self, spell: str, uses_slot: bool) -> str:
        pc = self.pc
        if uses_slot:
            if pc.spell_slots <= 0:
                raise ValueError(f"{pc.name} has no spell slots left; cantrips only until a long rest")
            pc.spell_slots -= 1
        return self._note(f"{pc.name} casts {spell}" +
                          (f" (slots left {pc.spell_slots}/{pc.max_spell_slots})" if uses_slot else " (cantrip)"))

    def rest(self, kind: str) -> str:
        pc = self.pc
        if self.enemies:
            raise ValueError("can't rest during combat")
        if kind == "long":
            pc.hp, pc.spell_slots = pc.max_hp, pc.max_spell_slots
            pc.death_successes = pc.death_failures = 0
            return self._note(f"Long rest: HP and spell slots fully restored. HP {pc.hp}/{pc.max_hp}")
        heal, _, _ = roll(f"1d{CLASSES[pc.cls]['hit_die']}", self.rng)
        heal = max(1, heal + mod(pc.stats["CON"]))
        return self.change_hp(heal, "Short rest")

    def set_location(self, name: str) -> str:
        self.location = name
        return self._note(f"Location: {name}")

    def update_quest(self, title: str, status: str, note: str = "") -> str:
        q = self.quests.setdefault(title, {"status": status, "notes": []})
        q["status"] = status
        if note:
            q["notes"].append(note)
        return self._note(f"Quest '{title}': {status}" + (f" — {note}" if note else ""))

    # ---------- dispatch ----------
    def execute(self, name: str, args: dict) -> str:
        fn = getattr(self, name, None)
        if name not in TOOL_NAMES or fn is None:
            raise ValueError(f"unknown tool {name}")
        return fn(**args)


def new_character(name: str, race: str, cls: str, seed: int | None = None) -> GameState:
    race, cls = race.lower(), cls.lower()
    spec = CLASSES[cls]
    stats = dict(spec["stats"])
    for a, b in RACES[race].items():
        stats[a] += b
    hp = spec["hit_die"] + mod(stats["CON"])
    pc = Character(name=name, race=race, cls=cls, hp=hp, max_hp=hp, ac=spec["ac"], stats=stats,
                   skills=list(spec["skills"]), inventory=list(spec["inventory"]),
                   spell_slots=spec["spell_slots"], max_spell_slots=spec["spell_slots"])
    return GameState(pc=pc, seed=seed)


def save_game(path: str, state: GameState, messages: list) -> None:
    with open(path, "w") as f:
        json.dump({"state": state.to_dict(), "messages": messages}, f, indent=1)


def load_game(path: str) -> tuple[GameState, list]:
    with open(path) as f:
        d = json.load(f)
    return GameState.from_dict(d["state"]), d["messages"]


# ---------- tool schemas for the DM ----------
_ADV = {"type": "string", "enum": ["none", "advantage", "disadvantage"]}
_ABILITY = {"type": "string", "enum": ABILITIES}


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": props, "required": required,
                         "additionalProperties": False},
    }


TOOLS = [
    _tool("ability_check", "Roll a d20 ability/skill check for the player against a DC (5 easy, 10 medium, "
          "15 hard, 20 very hard). Use whenever the outcome of a player action is uncertain.",
          {"ability": _ABILITY, "dc": {"type": "integer"},
           "skill": {"type": "string", "enum": ["none", *SKILLS],
                     "description": "Skill if relevant (overrides ability), else 'none'"},
           "advantage": _ADV},
          ["ability", "dc", "skill", "advantage"]),
    _tool("roll_dice", "Roll arbitrary dice (e.g. '2d6+1') for anything not covered by other tools: "
          "spell damage, random tables, NPC checks.",
          {"expression": {"type": "string"}, "reason": {"type": "string"}}, ["expression", "reason"]),
    _tool("start_combat", "Add enemies and start combat. Give each enemy fair stats for the player's level.",
          {"enemies": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["name", "hp", "ac", "attack_bonus", "damage"],
              "properties": {"name": {"type": "string"}, "hp": {"type": "integer"}, "ac": {"type": "integer"},
                             "attack_bonus": {"type": "integer"},
                             "damage": {"type": "string", "description": "dice, e.g. '1d6+2'"}}}}},
          ["enemies"]),
    _tool("player_attack", "Resolve the player's weapon attack on an active enemy. Engine rolls to-hit "
          "and damage and applies it. Use STR for melee, DEX for ranged/finesse.",
          {"target": {"type": "string"}, "damage_dice": {"type": "string", "description": "weapon die, e.g. '1d8'"},
           "ability": {"type": "string", "enum": ["STR", "DEX"]}, "advantage": _ADV},
          ["target", "damage_dice", "ability", "advantage"]),
    _tool("enemy_attack", "Resolve one active enemy's attack on the player; damage is applied automatically.",
          {"attacker": {"type": "string"}, "advantage": _ADV}, ["attacker", "advantage"]),
    _tool("end_combat", "End combat (enemies fled, surrendered, or player escaped). Not needed if all "
          "enemies were defeated.", {"reason": {"type": "string"}}, ["reason"]),
    _tool("change_hp", "Heal (positive) or damage (negative) the player outside of enemy_attack: "
          "traps, falls, potions, healing spells.",
          {"amount": {"type": "integer"}, "reason": {"type": "string"}}, ["amount", "reason"]),
    _tool("death_save", "Roll a death saving throw. Call once at the start of each player turn while "
          "they are at 0 HP.", {}, []),
    _tool("update_inventory", "Add/remove items and change gold. Call whenever the player picks up, buys, "
          "uses up, gives away or loses items.",
          {"add": {"type": "array", "items": {"type": "string"}},
           "remove": {"type": "array", "items": {"type": "string"}},
           "gold_change": {"type": "integer"}}, ["add", "remove", "gold_change"]),
    _tool("award_xp", "Award XP for defeating enemies, clever solutions, or completing quests. Handles level-ups.",
          {"amount": {"type": "integer"}, "reason": {"type": "string"}}, ["amount", "reason"]),
    _tool("cast_spell", "Record a spell the player casts. uses_slot=false for cantrips. Fails if no slots. "
          "Then use roll_dice / ability_check / change_hp for its effect.",
          {"spell": {"type": "string"}, "uses_slot": {"type": "boolean"}}, ["spell", "uses_slot"]),
    _tool("rest", "Short or long rest. Not allowed in combat.",
          {"kind": {"type": "string", "enum": ["short", "long"]}}, ["kind"]),
    _tool("set_location", "Update the player's current location when they move somewhere new.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("update_quest", "Create or update a quest in the quest log.",
          {"title": {"type": "string"}, "status": {"type": "string", "enum": ["active", "completed", "failed"]},
           "note": {"type": "string"}}, ["title", "status", "note"]),
]
TOOL_NAMES = {t["name"] for t in TOOLS}
