'''
File containing algorithm to automatically build and test gear sets for set finding.

Uses a partially-exhaustive search of all possible combinations of gear involving at most 2 swaps at a time.

Each WS set typically has one deep global minima that this algorithm finds relatively well.

Critical hit weapon skills (and those with Shining One equipped) can have two minima (one Crit build, one WS damage build). 
This algorithm may get caught in a crit build if starting from a crit build, but this only affects crit WSs.
    
Author: Kastra (Asura server)
'''
from create_player import *
import numpy as np
from actions import *
import sys
from concurrent.futures import ProcessPoolExecutor, wait
from contextlib import redirect_stdout
from datetime import datetime # For timestamping new sets to put on BG Wiki
from itertools import product
from io import StringIO

# Use an external gear.py file
# https://stackoverflow.com/questions/47350078/importing-external-module-in-single-file-exe-created-with-pyinstaller
import sys
import os
import re
import time
import queue as queue_module
sys.path.append(os.path.dirname(sys.executable))
from gear import *


class OptimizerStopped(RuntimeError):
    """Raised when the GUI requests a cooperative optimizer stop."""

    def __init__(self, message="Optimizer stopped by user.", *, partial_player=None,
                 partial_output=None, partial_metric=None, results=None):
        super().__init__(message)
        self.partial_player = partial_player
        self.partial_output = partial_output
        self.partial_metric = partial_metric
        self.results = list(results or [])


class CombinedSetResult:
    """Pickle-safe result containing the independently optimized TP and WS sets."""

    def __init__(self, tp_player, ws_player):
        self.tp_player = tp_player
        self.ws_player = ws_player
        # Keep the legacy single-player consumer pointed at the TP set.
        self.gearset = tp_player.gearset


def _stop_requested(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


def _combined_tp_ws_metric(player, enemy, ws_name, min_tp, ws_type):
    """Score one set by complete TP-to-WS cycle DPS.

    This intentionally uses the existing TP-round and WS calculation paths;
    it only combines their outputs so Store TP/haste is not rewarded at the
    expense of an excessive loss in WS damage.
    """
    return average_tp_ws_cycle(
        player, player, enemy, ws_name, min_tp, ws_type
    )


def _combined_tp_ws_metric_pair(tp_player, ws_player, enemy, ws_name, min_tp, ws_type):
    """Score a TP gearset and WS gearset as one complete cycle."""
    return average_tp_ws_cycle(
        tp_player, ws_player, enemy, ws_name, min_tp, ws_type
    )

def format_bgwiki(ws_name, tp, player, best_metric):
    #
    # Input: A player class containing job and gear info.
    # Output: None
    #
    # Prints to the terminal the player gearset in BG Wiki format, ignoring augments.
    #
    buffs = "High"


    # Certain items have shortened names on BG Wiki. Use the item_list.txt file to find and replace these names for BG Wiki.
    item_list = np.loadtxt("item_list.csv", unpack=False, dtype=str, delimiter=';', usecols=(1,2), skiprows=1)
    name_map = {k[0].lower():k[1] for k in item_list}

    backaugs = []
    for stat in player.gearset["back"]:
        if stat.lower() in ["str","dex","vit","agi","int","mnd","chr","da","store tp","dual wield","crit rate","weapon skill damage", "magic attack"]:
            backaugs.append(stat)

    linosaugs = []
    for stat in player.gearset["ranged"]:
        if stat.lower() in ["str","dex","vit","agi","int","mnd","chr","da","store tp","dual wield","crit rate","weapon skill damage", "magic attack","qa","da","ta"]:
            linosaugs.append(stat)

    # Moonshade natually looks best in the left ear slot.
    if "moonshade" in player.gearset["ear2"]["Name"].lower():
        ear2 = player.gearset["ear2"]
        ear1 = player.gearset["ear1"]
        player.gearset["ear1"] = ear2
        player.gearset["ear2"] = ear1

    # JSE earrings work in the right ear slot
    jse_ears1 = [k + " Earring +1" for k in ["Hattori", "Heathen's", "Lethargy", "Ebers", "Wicce", "Peltast's", "Boii", "Bhikku", "Skulker's", "Chevalier's", "Nukumi", "Fili", "Amini", "Kasuga", "Beckoner's", "Hashishin", "Chasseur's", "Karagoz", "Maculele", "Arbatel", "Azimuth", "Erilaz"]]
    jse_ears2 = [k + " Earring +2" for k in ["Hattori", "Heathen's", "Lethargy", "Ebers", "Wicce", "Peltast's", "Boii", "Bhikku", "Skulker's", "Chevalier's", "Nukumi", "Fili", "Amini", "Kasuga", "Beckoner's", "Hashishin", "Chasseur's", "Karagoz", "Maculele", "Arbatel", "Azimuth", "Erilaz"]]
    if player.gearset["ear1"]["Name2"] in jse_ears1 or player.gearset["ear1"]["Name2"] in jse_ears2:
        ear2 = player.gearset["ear2"]
        ear1 = player.gearset["ear1"]
        player.gearset["ear1"] = ear2
        player.gearset["ear2"] = ear1

    # Do it again because the above doesn't always work??
    empy = ["Hattori", "Heathen", "Lethargy", "Eber", "Wicce", "Peltast", "Boii", "Bhikku", "Skulker", "Chevalier", "Nukumi", "Fili", "Amini", "Kasuga", "Beckoner", "Hashishin", "Chasseur", "Karagoz", "Maculele", "Arbatel", "Azimuth", "Erilaz"]
    for name in empy:
        if name.lower() in player.gearset["ear1"]["Name"].lower():
            ear2 = player.gearset["ear2"]
            ear1 = player.gearset["ear1"]
            player.gearset["ear1"] = ear2
            player.gearset["ear2"] = ear1

    # Epami looks best in the left ring slot, but only if sroda is not also equipped.
    if "epami" in player.gearset["ring2"]["Name"].lower():
        if "sroda" not in player.gearset["ring1"]["Name"].lower():
            ring2 = player.gearset["ring2"]
            ring1 = player.gearset["ring1"]
            player.gearset["ring1"] = ring2
            player.gearset["ring2"] = ring1

    # Sroda looks best in the left ring slot.
    if "sroda" in player.gearset["ring2"]["Name"].lower():
            ring2 = player.gearset["ring2"]
            ring1 = player.gearset["ring1"]
            player.gearset["ring1"] = ring2
            player.gearset["ring2"] = ring1

    # Niqmaddu and Regal look best in the right ring slot
    if ("niqmaddu" in player.gearset["ring1"]["Name"].lower() and "regal" not in player.gearset["ring2"]["Name"].lower()) or ("regal" in player.gearset["ring1"]["Name"].lower() and "niqmaddu" not in player.gearset["ring2"]["Name"].lower()):
            ring2 = player.gearset["ring2"]
            ring1 = player.gearset["ring1"]
            player.gearset["ring1"] = ring2
            player.gearset["ring2"] = ring1

    # player.gearset[slot]["Name"] = name_map[player.gearset[slot]["Name"].lower()]

    hardcode_gearset = {slot:name_map[player.gearset[slot]["Name"].lower()] for slot in player.gearset}
    for slot in hardcode_gearset:
        hardcode_gearset[slot] = "" if hardcode_gearset[slot].lower()=="empty" else hardcode_gearset[slot]


            # |RangeAug = {", ".join(linosaugs)}
    bgwiki_text = f"""
    {'{'}{'{'}
        Guide Equipment Set
        |Set Name Background=#604028
        |Set Name Text Color=
        |Set Name Text Shadow=#000080
        |Set Name= {ws_name}[[{ws_name}|*]]
        |Set Border Color=#51414F
        |Equipment Set=
        {'{'}{'{'}
            Equipment Set
            |CaptionTop = {buffs} buff
            |CaptionBottom = {best_metric:.0f} damage
            |Main = {' '.join(k.capitalize() for k in hardcode_gearset["main"].split())} (Level 119 III)
            |Sub = {' '.join(k.capitalize() for k in hardcode_gearset["sub"].split())}
            |Range = {' '.join(k.capitalize() for k in hardcode_gearset["ranged"].split())}
            |Ammo = {' '.join(k.capitalize() for k in hardcode_gearset["ammo"].split())}
            |Head = {' '.join(k.capitalize() for k in hardcode_gearset["head"].split())}
            |Neck = {' '.join(k.capitalize() for k in hardcode_gearset["neck"].split())}
            |Ear1 = {' '.join(k.capitalize() for k in hardcode_gearset["ear1"].split())}
            |Ear2 = {' '.join(k.capitalize() for k in hardcode_gearset["ear2"].split())}
            |Body = {' '.join(k.capitalize() for k in hardcode_gearset["body"].split())}
            |Hands = {' '.join(k.capitalize() for k in hardcode_gearset["hands"].split())}
            |Ring1 = {' '.join(k.capitalize() for k in hardcode_gearset["ring1"].split())}
            |Ring2 = {' '.join(k.capitalize() for k in hardcode_gearset["ring2"].split())}
            |Back = {' '.join(k.capitalize() for k in hardcode_gearset["back"].split())}
            |BackAug = {", ".join(backaugs)}
            |Waist = {' '.join(k.capitalize() for k in hardcode_gearset["waist"].split())}
            |Legs = {' '.join(k.capitalize() for k in hardcode_gearset["legs"].split())}
            |Feet = {' '.join(k.capitalize() for k in hardcode_gearset["feet"].split())}
            |List = Y
            |Background =
        {'}'}{'}'}
        |Equipment Set Notes=ML{player.master_level} {player.main_job.upper()}/{player.sub_job.upper()}: {int(tp)} TP
        Updated {datetime.now().strftime("%Y %b. %d")}
    {'}'}{'}'}\n
    """
    print(bgwiki_text)

def _job_eligible(item, main_job):
    jobs = item.get("Jobs", ())
    if isinstance(jobs, str):
        jobs = (jobs,)
    return str(main_job).lower() in {str(job).lower() for job in jobs}


def _prepare_candidates(check_gear, main_job, ws_type):
    """Copy and discard candidates the current main job cannot equip."""
    candidates = {
        slot: [item for item in items if _job_eligible(item, main_job)]
        for slot, items in check_gear.items()
    }
    if ws_type == "melee" and main_job not in ("rng", "cor"):
        candidates["ranged"] = [
            item for item in candidates["ranged"]
            if item.get("Type") not in ("Crossbow", "Gun", "Bow")
        ]
        candidates["ammo"] = [
            item for item in candidates["ammo"]
            if item.get("Type") not in ("Bolt", "Bullet", "Arrow")
            and "antitail" not in item.get("Name2", "").lower()
        ]
    return candidates


def estimate_candidate_checks(check_gear, main_job, ws_type="None"):
    """Estimate raw one/two-slot candidates in one optimizer pass."""
    candidates = _prepare_candidates(check_gear, main_job.lower(), ws_type)
    counts = [len(items) for items in candidates.values()]
    return sum(counts) + sum(
        left * right for index, left in enumerate(counts) for right in counts[index + 1:]
    )


# Items whose help text forces other armor slots empty.  The bridge parser also
# reads this rule from an exported description when it is available, so new
# GearSetBuilder item data does not need a code change merely to carry the
# restriction forward.
_KNOWN_FORCED_EMPTY_SLOTS = {
    "onca suit": ("hands", "legs", "feet"),
    "adenium suit": ("hands", "legs", "feet"),
    "magh bihu's suit": ("hands", "legs", "feet"),
    "mandragora suit": ("legs",),
    "mandragora suit +1": ("legs",),
    "wyrmking suit": ("legs",),
    "wyrmking suit +1": ("legs",),
    "overalls": ("legs",),
    "chocobo suit": ("hands", "feet"),
    "chocobo suit +1": ("hands", "feet"),
    "cohort cloak": ("head",),
    "cohort cloak +1": ("head",),
    "crepuscular cloak": ("head",),
    "twilight cloak": ("head",),
}
_RESTRICTED_ARMOR_SLOTS = {
    "head": "head", "hand": "hands", "leg": "legs", "foot": "feet",
}


def forced_empty_slots(item):
    """Return armor slots that must be empty while this item is equipped."""
    name = str(item.get("Name") or "").casefold()
    blocked = set(_KNOWN_FORCED_EMPTY_SLOTS.get(name, ()))
    explicit = item.get("Forced Empty Slots") or item.get("Blocked Slots") or ()
    if isinstance(explicit, str):
        explicit = re.split(r"[,;/]", explicit)
    for slot in explicit:
        slot = str(slot).strip().casefold()
        if slot in _RESTRICTED_ARMOR_SLOTS:
            blocked.add(_RESTRICTED_ARMOR_SLOTS[slot])
        elif slot in _RESTRICTED_ARMOR_SLOTS.values():
            blocked.add(slot)

    text = " ".join(str(item.get(key) or "") for key in (
        "Description", "Help Text", "description", "help_text",
    ))
    for phrase in re.findall(r"(?:cannot|unable to)\s+equip\s+([^.;]+?gear)", text, re.I):
        phrase = phrase.casefold()
        for word, slot in _RESTRICTED_ARMOR_SLOTS.items():
            if word in phrase:
                blocked.add(slot)
    return blocked


def apply_forced_empty_slots(gearset):
    """Apply all equipped-item slot restrictions and return slots changed."""
    blocked = set()
    for item in gearset.values():
        blocked.update(forced_empty_slots(item))
    changed = set()
    for slot in blocked:
        if slot in gearset and gearset[slot].get("Name") != "Empty":
            gearset[slot] = Empty
            changed.add(slot)
    return changed


def starting_item_candidates(items):
    """Prefer normal-slot items for randomized optimizer starting sets."""
    unrestricted = [item for item in items if not forced_empty_slots(item)]
    return unrestricted or list(items)


_LOWER_IS_BETTER_STATS = {
    "Delay", "DT", "DT2", "PDT", "PDT2", "MDT", "MDT2",
    "Damage Taken", "Damage Taken%",
}
_NON_COMBAT_METADATA = {
    "Item ID", "Item Level", "ItemLevel", "Rank", "Accessible Count",
    "Total Count", "Model Complete",
}


def _numeric_combat_stats(item: dict) -> dict[str, float]:
    return {
        key: float(value) for key, value in item.items()
        if key not in _NON_COMBAT_METADATA and not isinstance(value, bool)
        and isinstance(value, (int, float))
    }


def _dominates_item(candidate: dict, other: dict) -> bool:
    """Return true only when candidate cannot be worse for any modeled stat."""
    if (candidate.get("Type", "None"), candidate.get("Skill Type", "None")) != (
        other.get("Type", "None"), other.get("Skill Type", "None")
    ):
        return False
    # A multi-slot item cannot safely dominate a normal item (or vice versa)
    # from its own stat row alone. Its empty-slot footprint is part of its cost.
    if forced_empty_slots(candidate) != forced_empty_slots(other):
        return False
    left = _numeric_combat_stats(candidate)
    right = _numeric_combat_stats(other)
    keys = set(left) | set(right)
    if not keys:
        return False
    strict = False
    for key in keys:
        candidate_value = left.get(key, 0.0)
        other_value = right.get(key, 0.0)
        if key in _LOWER_IS_BETTER_STATS:
            if candidate_value > other_value:
                return False
        elif candidate_value < other_value:
            return False
        if candidate_value != other_value:
            strict = True
    return strict


def prune_dominated_candidates(check_gear: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], int]:
    """Remove obvious Pareto-dominated items within each slot/type group.

    This is deliberately conservative: items are compared only to another
    item with the same equipment type and skill type, and all numeric modeled
    stats must be no worse.  It cannot remove a tradeoff item or alter any
    calculation formula.
    """
    pruned = {}
    removed = 0
    for slot, items in check_gear.items():
        kept = []
        for index, item in enumerate(items):
            if any(_dominates_item(other, item) for other_index, other in enumerate(items) if other_index != index):
                removed += 1
            else:
                kept.append(item)
        pruned[slot] = kept or list(items[:1])
    return pruned, removed


def _substat_value(player, stat_name):
    """Return a numeric player stat for secondary-stat optimization."""
    try:
        value = player.stats.get(str(stat_name), 0)
        if isinstance(value, (list, tuple)):
            value = value[-1] if value else 0
        return float(value or 0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _substat_player(player):
    """Use the TP component when a combined result wraps two players."""
    return getattr(player, "tp_player", player)


def _substat_constraints_met(player, constraints):
    return all(
        _substat_value(player, stat_name) >= float(minimum)
        for stat_name, minimum in (constraints or ())
    )


def build_set(main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, spell_name, action_type, min_tp, check_gear, starting_gearset, pdt_requirement, mdt_requirement, input_metric, print_swaps, next_best_percent, *, dt_requirement=0, seed=None, n_iter=10, return_details=False, progress_callback=None, stop_event=None, slot_pair_filter=None, preserve_starting_gearset=False, single_outer_pass=False, combined_ws_player=None, substat_spec=None):
    #
    # Build a valid gear set, test it, and return the best set found.
    #
    # action_type = "weapon skill", "attack round", "spell cast", "combined tp/ws"
    #
    fitn = 2

    main_job = main_job.lower()
    sub_job = sub_job.lower()
    verbose_swaps = abilities.get("Verbose Swaps", False)
    damage_taken_item_cache = {}
    rng = np.random.default_rng(seed) if seed is not None else np.random
    best_set = None
    best_output = None
    best_metric = None
    best_primary_metric = None

    def report_progress(message):
        if progress_callback is not None:
            progress_callback(message)

    def check_stopped():
        if _stop_requested(stop_event):
            partial_player = None
            if best_set is not None and best_output is not None:
                try:
                    partial_player = create_player(
                        main_job, sub_job, master_level, best_set, buffs, abilities
                    )
                except Exception:
                    partial_player = None
            raise OptimizerStopped(
                "Optimizer stopped by user.",
                partial_player=partial_player,
                partial_output=best_output,
                partial_metric=best_metric,
            )

    def progress_results_text():
        """Format already-calculated combat output for live status updates."""
        if best_output is None or best_metric is None:
            return "current results: waiting for a valid set"
        if action_type == "combined tp/ws":
            return (
                f"current results: DPS {best_metric:.3f}; "
                f"WS damage {best_output[3]:,.0f}; TP time {best_output[2]:.2f}s"
            )
        if action_type == "attack round":
            round_damage, tp_per_round, round_time = best_output[:3]
            dps = round_damage / round_time if round_time > 0 else 0.0
            result = (
                f"current results: TP DPS {dps:.3f}; round damage {round_damage:,.1f}; "
                f"TP/round {tp_per_round:.1f}"
            )
            # Time to WS is the selected attack-round metric when its inverse
            # is used. Reconstruct the displayed metric without another call
            # into the combat engine.
            if best_output[-1] < 0:
                result += f"; time to WS {best_metric ** best_output[-1]:.2f}s"
            return result
        if action_type == "weapon skill":
            return (
                f"current results: WS damage {best_output[0]:,.0f}; "
                f"TP return {best_output[1]:.1f}"
            )
        return (
            f"current results: spell damage {best_output[0]:,.0f}; "
            f"TP return {best_output[1]:.1f}"
        )

    check_stopped()
    report_progress(f"Search started (seed {seed if seed is not None else 'random'}).")
    # Keep caller-owned selections stable and remove impossible candidates once,
    # before the hot loop. Formula evaluation is unchanged for every valid set.
    check_gear = {slot: list(items) for slot, items in check_gear.items()}
    starting_gearset = starting_gearset.copy()

    def duplicate_allowed(item):
        count = item.get("Accessible Count")
        return item.get("Name", "Empty") == "Empty" or count is None or int(count) >= 2

    def duplicate_tp_bonus_weapon(main, sub):
        """Return whether main/sub are the same +1000 TP Bonus weapon.

        The static gear lists use a second dictionary (for example,
        ``Centovente2``) when an item can be offered in the sub slot.  Those
        dictionaries have different ``Name2`` values, so comparing the full
        dictionaries does not catch an impossible duplicate weapon pair.
        Keeping this restriction limited to weapons that provide +1000 TP
        Bonus preserves valid duplicate ordinary weapons and normal off-hand
        TP Bonus behavior.
        """
        if (main.get("Name", "Empty") == "Empty"
                or sub.get("Name", "Empty") == "Empty"):
            return False
        if main.get("Type") != "Weapon" or sub.get("Type") != "Weapon":
            return False
        main_name = str(main.get("Name") or "").strip().casefold()
        sub_name = str(sub.get("Name") or "").strip().casefold()
        if not main_name or main_name != sub_name:
            return False
        try:
            return (float(main.get("TP Bonus", 0)) >= 1000
                    and float(sub.get("TP Bonus", 0)) >= 1000)
        except (TypeError, ValueError):
            return False

    ws_dict = {"Katana": ["Blade: Retsu", "Blade: Teki", "Blade: To", "Blade: Chi", "Blade: Ei", "Blade: Jin", "Blade: Ten", "Blade: Ku", "Blade: Yu", "Blade: Metsu", "Blade: Kamu", "Blade: Hi", "Blade: Shun", "Zesho Meppo",],
        "Great Katana": ["Tachi: Enpi", "Tachi: Goten", "Tachi: Kagero", "Tachi: Jinpu", "Tachi: Koki","Tachi: Yukikaze", "Tachi: Gekko", "Tachi: Kasha", "Tachi: Ageha","Tachi: Kaiten", "Tachi: Rana", "Tachi: Fudo", "Tachi: Shoha", "Tachi: Mumei"],
        "Dagger": [ "Viper Bite", "Dancing Edge", "Shark Bite", "Evisceration", "Aeolian Edge", "Mercy Stroke", "Mandalic Stab", "Mordant Rime", "Pyrrhic Kleos", "Rudra's Storm", "Exenterator", "Ruthless Stroke"],
        "Sword": ["Fast Blade", "Fast Blade II", "Burning Blade", "Red Lotus Blade", "Seraph Blade", "Circle Blade", "Swift Blade", "Savage Blade", "Sanguine Blade", "Knights of Round", "Death Blossom", "Expiacion", "Chant du Cygne", "Requiescat", "Imperator"],
        "Scythe": ["Slice", "Dark Harvest", "Shadow of Death", "Nightmare Scythe", "Spinning Scythe", "Guillotine", "Cross Reaper", "Spiral Hell", "Infernal Scythe", "Catastrophe", "Quietus", "Insurgency", "Entropy", "Origin", ], 
        "Great Sword":["Hard Slash", "Freezebite", "Shockwave", "Sickle Moon", "Spinning Slash", "Ground Strike", "Herculean Slash", "Resolution", "Scourge", "Dimidiation", "Torcleaver", "Fimbulvetr", ], 
        "Club":["Shining Strike", "Seraph Strike", "Skullbreaker", "True Strike", "Judgment", "Hexa Strike", "Black Halo", "Randgrith", "Exudation", "Mystic Boon", "Realmrazer", "Dagda"], 
        "Polearm":["Double Thrust", "Thunder Thrust", "Raiden Thrust", "Penta Thrust", "Wheeling Thrust", "Impulse Drive", "Sonic Thrust", "Geirskogul", "Drakesbane", "Camlann's Torment", "Stardiver", "Diarmuid", ], 
        "Staff":["Heavy Swing", "Rock Crusher", "Earth Crusher", "Starburst", "Sunburst", "Shell Crusher", "Full Swing", "Cataclysm", "Retribution", "Gate of Tartarus", "Omniscience", "Vidohunir", "Garland of Bliss", "Shattersoul", "Oshala"], 
        "Great Axe":["Iron Tempest", "Shield Break", "Armor Break", "Weapon Break", "Raging Rush", "Full Break", "Steel Cyclone", "Fell Cleave", "Metatron Torment", "King's Justice", "Ukko's Fury", "Upheaval", "Disaster"], 
        "Axe":["Raging Axe", "Spinning Axe", "Rampage", "Calamity", "Mistral Axe", "Decimation", "Bora Axe", "Onslaught", "Primal Rend", "Cloudsplitter", "Ruinator", "Blitz", ], 
        "Archery":["Flaming Arrow", "Piercing Arrow", "Dulling Arrow", "Sidewinder", "Blast Arrow", "Empyreal Arrow", "Refulgent Arrow", "Namas Arrow", "Jishnu's Radiance", "Apex Arrow", "Sarv"], 
        "Marksmanship":["Hot Shot", "Split Shot", "Sniper Shot", "Slug Shot", "Blast Shot", "Detonator", "Coronach", "Leaden Salute", "Trueflight", "Wildfire", "Last Stand", "Terminus", ], 
        "Hand-to-Hand":["Combo","One Inch Punch","Raging Fists","Spinning Attack","Howling Fist","Dragon Kick","Asuran Fists","Tornado Kick","Ascetic's Fury","Stringing Pummel","Final Heaven","Victory Smite","Shijin Spiral","Maru Kala","Dragon Blow",],
        }

    melee_ws = [ws for skill in ws_dict if skill not in ["Archery","Marksmanship"] for ws in ws_dict[skill]]
    ranged_ws = [ws for skill in ws_dict if skill in ["Archery","Marksmanship"] for ws in ws_dict[skill]]
    
    ws_type = "melee" if ws_name in melee_ws else "ranged" if ws_name in ranged_ws else "None"
    # When combined TP optimization uses an external WS player, the TP set's
    # weapon may legitimately differ from the WS weapon. Apply WS weapon
    # restrictions only to the set whose WS is actually being calculated.
    ws_action = action_type == "weapon skill" or (
        action_type == "combined tp/ws" and combined_ws_player is None
    )
    
    if " Shot" in spell_name:
        spell_type = "Quick Draw"
    elif spell_name=="Ranged Attack":
        spell_type="Ranged Attack"
    elif (": Ichi" in spell_name) or (": Ni" in spell_name) or (": San" in spell_name):
        spell_type = "Ninjutsu"
    else:
        spell_type = "Elemental Magic"

    # List of weapon skills and their associated weapons.
    restricted_ws = {"Blade: Metsu":"Kikoku",
                    "Final Heaven":"Spharai",
                    "Mercy Stroke":"Mandau",
                    "Knights of Round":"Excalibur",
                    "Scourge":"Ragnarok",
                    "Onslaught":"Guttler",
                    "Metatron Torment":"Bravura",
                    "Catastrophe":"Apocalypse",
                    "Geirskogul":"Gungnir",
                    "Tachi: Kaiten":"Amanomurakumo",
                    "Randgrith":"Mjollnir",
                    "Gate of Tartarus":"Claustrum",
                    "Namas Arrow":"Yoichinoyumi",
                    "Coronach":"Annihilator",
                    "Fast Blade II":"Onion Sword III",
                    "Dragon Blow":"Dragon Fangs",
                    "Imperator":"Caliburnus",
                    "Zesho Meppo":"Dokoku",
                    "Terminus":"Earp",
                    "Origin":"Foenaria",
                    "Diarmuid":"Gae Buide",
                    "Fimbulvetr":"Helheim",
                    "Tachi: Mumei":"Kusanagi-no-Tsurugi",
                    "Disaster":"Laphria",
                    "Dagda":"Lorg Mor",
                    "Ruthless Stroke":"Mpu Gandring",
                    "Oshala":"Opashoro",
                    "Sarv":"Pinaka",
                    "Blitz":"Spalirisos",
                    "Maru Kala":"Varga Purnikawa",
                    }

    check_gear = _prepare_candidates(check_gear, main_job, ws_type)
    for items in check_gear.values():
        rng.shuffle(items)
    candidate_counts = [len(items) for items in check_gear.values()]
    estimated_checks = sum(candidate_counts) + sum(
        left * right for index, left in enumerate(candidate_counts)
        for right in candidate_counts[index + 1:]
    )

    # Rather than start with an empty slot, randomly build a set from the selected gear so we likely start with some accuracy+ and avoid getting stuck.
    # Do not adjust slots that are not being checked.
    for slot in starting_gearset:

        # Unequip gear you can't wear if it's already equipped, even if the slot is "frozen"
        if not _job_eligible(starting_gearset[slot], main_job):
            starting_gearset[slot] = Empty

        frozen_slot = (len(check_gear[slot]) == 0)
        candidate_names = {
            str(item.get("Name2") or item.get("Name") or "Empty")
            for item in check_gear[slot]
        }
        starting_name = str(
            starting_gearset[slot].get("Name2")
            or starting_gearset[slot].get("Name")
            or "Empty"
        )
        if (not frozen_slot
                and (not preserve_starting_gearset or starting_name not in candidate_names)):
            starting_gearset[slot] = rng.choice(starting_item_candidates(check_gear[slot]))
            
            # Avoid wearing two rare items in initial gearset to prevent "unphysical" sets.
            if slot == "ring2" and (starting_gearset["ring1"]["Name2"] == starting_gearset["ring2"]["Name2"]
                                     and not duplicate_allowed(starting_gearset["ring2"])):
                starting_gearset["ring2"] = Empty
            if slot == "ear2" and (starting_gearset["ear1"]["Name2"] == starting_gearset["ear2"]["Name2"]
                                    and not duplicate_allowed(starting_gearset["ear2"])):
                starting_gearset["ear2"] = Empty

    apply_forced_empty_slots(starting_gearset)
    # Do not let the random starting point contain two copies of a unique
    # +1000 TP Bonus weapon (Centovente/Centovente2, Hitaki/Hitaki2, etc.).
    # The candidate loop applies the same rule to every later swap.
    if duplicate_tp_bonus_weapon(starting_gearset["main"], starting_gearset["sub"]):
        starting_gearset["sub"] = Empty
        report_progress("Removed duplicate +1000 TP Bonus weapon from sub slot.")

    best_set =  starting_gearset.copy()

    # Define JSE earrings now. We'll use them later to prevent Balder's Earring+1 and a JSE+2 being equipped at the same time since we ignore right_ear requirement for testing.
    jse_ears1 = [k + " Earring +1" for k in ["Hattori", "Heathen's", "Lethargy", "Ebers", "Wicce", "Peltast's", "Boii", "Bhikku", "Skulker's", "Chevalier's", "Nukumi", "Fili", "Amini", "Kasuga", "Beckoner's", "Hashishin", "Chasseur's", "Karagoz", "Maculele", "Arbatel", "Azimuth", "Erilaz"]]
    jse_ears2 = [k + " Earring +2" for k in ["Hattori", "Heathen's", "Lethargy", "Ebers", "Wicce", "Peltast's", "Boii", "Bhikku", "Skulker's", "Chevalier's", "Nukumi", "Fili", "Amini", "Kasuga", "Beckoner's", "Hashishin", "Chasseur's", "Karagoz", "Maculele", "Arbatel", "Azimuth", "Erilaz"]]
    jse_ears = jse_ears1+jse_ears2
    one_handed = ("Axe", "Club", "Dagger", "Sword", "Katana")
    two_handed = ("Great Sword", "Great Katana", "Great Axe", "Polearm", "Scythe", "Staff")
    archery_ws = ("Empyreal Arrow", "Flaming Arrow", "Namas Arrow", "Jishnu's Radiance", "Apex Arrow", "Refulgent Arrow", "Sidewinder", "Blast Arrow", "Piercing Arrow")
    marksmanship_ws = ("Last Stand", "Hot Shot", "Leaden Salute", "Wildfire", "Coronach", "Trueflight", "Detonator", "Blast Shot", "Slug Shot", "Split Shot")

    pdt = 200 # How much PDT the set has
    mdt = 200
    dt = 200

    conditional_converge_count = 0 # Break out of the loop if converged.
    pdt_old = 200 # Used to check if the automatic set finder gets stuck trying to find a set that doesn't exist. Compare this value to the old value. If no change in 3 consecutive iterations, then break out.
    mdt_old = 200
    dt_old = 200

    pdt_thresh = pdt_requirement # How much PDT the final set is aiming for, taken from the user input.
    mdt_thresh = mdt_requirement
    dt_thresh = dt_requirement

    def defensive_deficit(candidate_pdt, candidate_mdt, candidate_dt):
        """Rank how far a set is from the requested defensive minimums."""
        pdt_gap = max(0.0, candidate_pdt - pdt_thresh)
        mdt_gap = max(0.0, candidate_mdt - mdt_thresh)
        dt_gap = max(0.0, candidate_dt - dt_thresh)
        return (pdt_gap + mdt_gap + dt_gap, max(pdt_gap, mdt_gap, dt_gap),
                pdt_gap, mdt_gap, dt_gap)

    # First find a legal set that satisfies requested PDT/MDT/DT totals.
    # This avoids expensive DPS/WS evaluation for candidates that cannot
    # possibly be returned. Split chunks keep their strict one-pass gate; the
    # coordinator already supplies a full-search fallback when needed.
    defense_phase = (not single_outer_pass and any(
        threshold < 0 for threshold in (pdt_thresh, mdt_thresh, dt_thresh)
    ))
    metric_pass_pending = False

    # Split-worker passes enforce final thresholds immediately. Regular runs
    # use a feasibility phase first, then score only fully defensive sets.
    pdt_thresh_temp = pdt_thresh if (single_outer_pass or defense_phase) else 200
    mdt_thresh_temp = mdt_thresh if (single_outer_pass or defense_phase) else 200
    dt_thresh_temp = dt_thresh if (single_outer_pass or defense_phase) else 200
    while (metric_pass_pending or pdt > pdt_thresh
           or mdt > mdt_thresh or dt > dt_thresh):
        check_stopped()
        # print(f"\nChecking conditions: PDT:{pdt_thresh_temp},  MDT:{mdt_thresh_temp}")

        for z in range(n_iter):
            check_stopped()
            print(f"Current iteration: {z+1}")
            report_progress(
                f"Iteration {z + 1}/{n_iter} started; planned ~{estimated_checks:,} combinations."
            )
            tested_count = 0
            valid_count = 0
            last_progress = time.monotonic()
            current_phase = "initializing"
            last_improvement = "none"
            
            # Every candidate in this pass is compared to this immutable baseline.
            # This makes a full one/two-slot neighborhood pass independent of the
            # order in which slot pairs happen to be evaluated.
            converged_set = best_set.copy()
            base_damage_taken_totals = damage_taken_totals(
                converged_set, buffs, damage_taken_item_cache
            )
            base_pdt, base_mdt = damage_taken_from_totals(
                base_damage_taken_totals, converged_set["main"], abilities
            )
            base_dt = damage_taken_dt_from_totals(
                base_damage_taken_totals, converged_set["main"], abilities
            )

            if defense_phase and (base_pdt <= pdt_thresh and base_mdt <= mdt_thresh
                                  and base_dt <= dt_thresh):
                defense_phase = False
                pdt_thresh_temp = pdt_thresh
                mdt_thresh_temp = mdt_thresh
                dt_thresh_temp = dt_thresh
                report_progress(
                    "Defensive minimums satisfied; evaluating damage only among valid sets."
                )

            best_defense_score = None
            if defense_phase:
                best_defense_score = defensive_deficit(base_pdt, base_mdt, base_dt)
                best_metric = None
                best_output = None
                last_improvement = (
                    f"defensive baseline PDT:{base_pdt:g}, MDT:{base_mdt:g}, DT:{base_dt:g}"
                )
            elif (base_pdt <= pdt_thresh_temp and base_mdt <= mdt_thresh_temp
                    and base_dt <= dt_thresh_temp):
                base_player = create_player(main_job, sub_job, master_level, converged_set, buffs, abilities)
                if action_type == "weapon skill":
                    decimals = 1
                    nondecimals = 8
                    metric_base, best_output = average_ws(base_player, enemy, ws_name, min_tp, ws_type, input_metric)
                elif action_type == "combined tp/ws":
                    decimals = 1
                    nondecimals = 8
                    if combined_ws_player is None:
                        metric_base, best_output = _combined_tp_ws_metric(
                            base_player, enemy, ws_name, min_tp, ws_type
                        )
                    else:
                        metric_base, best_output = _combined_tp_ws_metric_pair(
                            base_player, combined_ws_player, enemy, ws_name, min_tp, ws_type
                        )
                elif action_type == "spell cast":
                    decimals = 1
                    nondecimals = 8
                    metric_base, best_output = cast_spell(base_player, enemy, spell_name, spell_type, input_metric)
                elif action_type == "attack round":
                    decimals = 3
                    nondecimals = 8
                    metric_base, best_output, _ = average_attack_round(base_player, enemy, 0, min_tp, input_metric)
                else:
                    raise ValueError(f"Unknown action_type ({action_type})")
                invert = best_output[-1]
                best_metric = max(0.0001, metric_base ** invert)
                best_primary_metric = best_metric
                if substat_spec:
                    primary_floor = float(substat_spec.get("primary_floor", 0.0))
                    target_stat = substat_spec.get("target")
                    eligible = (
                        best_metric >= primary_floor
                        and _substat_constraints_met(
                            _substat_player(base_player), substat_spec.get("constraints")
                        )
                    )
                    if eligible:
                        best_metric = _substat_value(_substat_player(base_player), target_stat)
                    else:
                        best_metric = -float("inf")
                        best_output = None
                        best_primary_metric = None
            else:
                # The current set no longer meets the tightened PDT/MDT gate, so
                # the first valid neighbor establishes the new baseline.
                best_metric = 0.0001

            # A list of items in each slot that are within some % of the best item in that slot.
            swaps = {"ammo":[],"head":[],"neck":[],"ear1":[],"ear2":[],"body":[],"hands":[],"ring1":[],"ring2":[],"waist":[],"legs":[],"feet":[]}

            # Randomize slot order per pass. Item order is randomized once per
            # restart; reshuffling it for every pair only consumed CPU.
            check_slots = np.array([k for k in check_gear])
            rng.shuffle(check_slots)

            # For now, the code will only support two simultaneous swaps. Adding a third requires only adding a new for loop, but it adds a significant amount of computation time.
            found_feasible_neighbor = False
            for i1, slot1 in enumerate(check_slots): 
                check_stopped()
                for slot2 in check_slots[i1:]:
                    check_stopped()
                    current_phase = f"{slot1} + {slot2}"
                    pair_key = tuple(sorted((str(slot1), str(slot2))))
                    if slot_pair_filter is not None and pair_key not in slot_pair_filter:
                        continue
                    
                    # Only check single item swaps if fitn==1
                    if fitn==1:
                        if slot2 != slot1:
                            continue

                    if slot1 == slot2:
                        item_pairs = ((item, item) for item in check_gear[slot1])
                    else:
                        item_pairs = product(check_gear[slot1], check_gear[slot2])

                    for pair_index, (item1, item2) in enumerate(item_pairs):
                            # Start every candidate from the immutable baseline.
                            # Forced-empty items such as Onca Suit must not leave
                            # hands/legs/feet empty for the next candidate.
                            test_set = converged_set.copy()
                            tested_count += 1
                            if pair_index % 64 == 0:
                                check_stopped()

                            if tested_count % 256 == 0:
                                now = time.monotonic()
                            else:
                                now = last_progress
                            if now - last_progress >= 5.0:
                                best_text = f"{best_metric:.4f}" if best_metric is not None else "n/a"
                                report_progress(
                                    f"Iteration {z + 1}/{n_iter}; phase {current_phase}; "
                                    f"tested {tested_count:,}/{estimated_checks:,}; "
                                    f"valid {valid_count:,}; last improvement {last_improvement}; "
                                    f"best {best_text}; {progress_results_text()}."
                                )
                                last_progress = now

                            if (item1==converged_set[slot1]) or (item2==converged_set[slot2]): # Do not retest the baseline set.
                                continue

                            # Equip the items and check that the test_set is valid.
                            test_set[slot1] = item1
                            test_set[slot2] = item2
                            forced_empty = apply_forced_empty_slots(test_set)
                            # A candidate forcibly removed by another equipped
                            # piece would only duplicate an already-valid empty
                            # slot state, so skip that redundant combination.
                            if ((slot1 in forced_empty and item1.get("Name") != "Empty")
                                    or (slot2 in forced_empty and item2.get("Name") != "Empty")):
                                continue


                            if (test_set["ring1"]==test_set["ring2"]) and (test_set["ring1"]["Name"]!="Empty") and not duplicate_allowed(test_set["ring1"]):
                                continue
                            if (test_set["ear1"]==test_set["ear2"]) and (test_set["ear1"]["Name"]!="Empty") and not duplicate_allowed(test_set["ear1"]):
                                continue
                            if (test_set["main"]==test_set["sub"]) and (test_set["main"]["Name"]!="Empty") and not duplicate_allowed(test_set["main"]):
                                continue
                            if duplicate_tp_bonus_weapon(test_set["main"], test_set["sub"]):
                                continue
                            #print("test1")

                            # Do not test 1-handed weapons with grips.
                            if (test_set["main"]["Skill Type"] in one_handed) and (test_set["sub"]["Type"] == "Grip"):
                                continue
                            #print("test2")

                            # Do not allow 2-handed weapons with shields or 1-handed weapons.
                            if (test_set["main"]["Skill Type"] in two_handed) and (test_set["sub"]["Type"]=="Weapon" or test_set["sub"]["Type"]=="Shield"):
                                continue
                            #print("test3")

                            # Do not allow a hand-to-hand weapon with an off-hand item.
                            if (test_set["main"]["Skill Type"] == "Hand-to-Hand") and (test_set["sub"]["Name"] != "Empty"):
                                continue

                            #print("test4")

                            if ws_action and (ws_name in archery_ws + marksmanship_ws):
                                # If using a ranged weapon skill, ensure that the weapon and ammo type match the weapon skill.

                                if (ws_name in archery_ws) and (test_set["ranged"]["Skill Type"]!="Archery" or test_set["ammo"]["Type"]!="Arrow"):
                                    continue
                                
                                if (ws_name in marksmanship_ws) and (test_set["ranged"]["Skill Type"]!="Marksmanship" or test_set["ammo"]["Type"] not in ["Bolt", "Bullet"]):
                                    continue

                                if (test_set["ranged"]["Type"]=="Crossbow") and (test_set["ammo"]["Type"]!="Bolt"):
                                    continue
                                if (test_set["ranged"]["Type"]=="Gun") and (test_set["ammo"]["Type"]!="Bullet"):
                                    continue
                            #print("test5")

                            # Ranged TP attacks require a ranged weapon and ammo to be equipped. We check that the ammo matches the weapon later.
                            if (action_type=="spell cast") and (spell_name=="Ranged Attack"):
                                if (test_set["ranged"]["Type"] not in ["Gun","Bow","Crossbow"]) or (test_set["ammo"]["Type"] not in ["Bullet","Arrow","Bolt"]):
                                    continue
                            #print("test6")

                            # Do not equip an ammo incompatible with your ranged weapon
                            if (test_set["ranged"]["Type"]=="Gun") and (test_set["ammo"].get("Type","None") not in ["Bullet","None"]):
                                continue
                            if (test_set["ranged"]["Type"]=="Bow") and (test_set["ammo"].get("Type","None") not in ["Arrow","None"]):
                                continue
                            if (test_set["ranged"]["Type"]=="Crossbow") and (test_set["ammo"].get("Type","None") not in ["Bolt","None"]):
                                continue
                            #print("test7")

                            # Do not equip a ranged weapon incompatible with your ammo
                            if (test_set["ammo"].get("Type","None")=="Bullet") and (test_set["ranged"].get("Type","None")!="Gun"):
                                continue
                            if (test_set["ammo"].get("Type","None")=="Arrow") and (test_set["ranged"].get("Type","None")!="Bow"):
                                continue
                            if (test_set["ammo"].get("Type","None")=="Bolt") and (test_set["ranged"].get("Type","None")!="Crossbow"):
                                continue
                            #print("test8")

                            if (test_set["ranged"].get("Type","None")=="Instrument") and (test_set["ammo"].get("Type","None")!="None"):
                                continue
                            #print("test9")

                            # Do not allow dual wielding unless the selected main job has native dual wield.
                            if (main_job not in ["nin", "dnc", "thf", "blu"] and sub_job not in ["nin", "dnc"]) and (test_set["sub"]["Type"] == "Weapon"):
                                    continue
                            #print("test10")

                            # Do not equip Balder Earring +1 and the JSE +2 ears at the same time. They both only work if in the right ear.
                            if (test_set["ear1"]["Name"] in jse_ears) and (test_set["ear2"]["Name"]=="Balder Earring +1"):
                                continue
                            if (test_set["ear2"]["Name"] in jse_ears) and (test_set["ear1"]["Name"]=="Balder Earring +1"):
                                continue
                            #print("test11")

                            # "Cannot equip headgear" armor is checked here.
                            if (test_set["body"]["Name"] in ["Cohort Cloak","Cohort Cloak +1","Crepuscular Cloak","Twilight Cloak"]) and (test_set["head"]["Name"]!="Empty"):
                                continue
                            #print("test12")

                            # Impact can only be casted with Twilight Cloak or Crepuscular Cloak
                            if action_type == "spell cast":
                                if (spell_name=="Impact") and (test_set["body"]["Name"] not in ["Crepuscular Cloak","Twilight Cloak"]):
                                    continue
                            #print("test13")

                            if ws_action:
                                # Some weapon skills can only be used with certain weapons.
                                if ws_name in restricted_ws:
                                    if (restricted_ws[ws_name]!=test_set["main"]["Name"]) and (restricted_ws[ws_name]!=test_set["ranged"]["Name"]):
                                        continue
                                #print("test14")

                                # Reject sets if their main-hand weapon or ranged weapon can't use the selected weapon skill.
                                if (ws_name not in ws_dict.get(test_set["main"]["Skill Type"],[])) and (ws_name not in ws_dict.get(test_set["ranged"]["Skill Type"],[])):
                                    continue
                                #print("test15")
                            
                            # At this point, the code should have a valid gear set to play with.



                            # Only one or two slots differ from the immutable
                            # baseline, so update its PDT/MDT totals by delta.
                            candidate_totals = base_damage_taken_totals.copy()
                            changed_slots = {slot1, slot2, *forced_empty}
                            changed_items = {
                                slot: test_set[slot] for slot in changed_slots
                                if test_set[slot] != converged_set[slot]
                            }
                            for changed_slot, changed_item in changed_items.items():
                                previous_values = damage_taken_item_values(
                                    converged_set[changed_slot], damage_taken_item_cache
                                )
                                next_values = damage_taken_item_values(
                                    changed_item, damage_taken_item_cache
                                )
                                for index, (previous, next_value) in enumerate(zip(previous_values, next_values)):
                                    candidate_totals[index] += next_value - previous
                            pdt, mdt = damage_taken_from_totals(
                                candidate_totals, test_set["main"], abilities
                            )
                            dt = damage_taken_dt_from_totals(
                                candidate_totals, test_set["main"], abilities
                            )
                            if defense_phase:
                                candidate_defense_score = defensive_deficit(pdt, mdt, dt)
                                if candidate_defense_score < best_defense_score:
                                    best_set = test_set.copy()
                                    best_defense_score = candidate_defense_score
                                    valid_count += 1
                                    last_improvement = (
                                        f"defense {slot1} + {slot2}: "
                                        f"PDT:{pdt:g}, MDT:{mdt:g}, DT:{dt:g}"
                                    )
                                # This phase deliberately does no player or
                                # action calculation: only defensive totals
                                # decide the next feasible baseline.
                                continue
                            if (pdt > pdt_thresh_temp or mdt > mdt_thresh_temp
                                    or dt > dt_thresh_temp):
                                continue
                            found_feasible_neighbor = True
                            valid_count += 1

                            # Sets that survive this long are valid and satisfy the temporary PDT/MDT requirements.
                            player = create_player(main_job, sub_job, master_level, test_set, buffs, abilities)


                            # Prepare to test the set.

                            if action_type=="weapon skill":
                                decimals = 1
                                nondecimals = 8
                                metric_base, output = average_ws(player, enemy, ws_name, min_tp, ws_type, input_metric)
                                invert = output[-1]
                                metric = metric_base**invert
                            elif action_type=="spell cast":
                                decimals = 1
                                nondecimals = 8
                                metric_base, output = cast_spell(player, enemy, spell_name, spell_type, input_metric)
                                invert = output[-1]
                                metric = metric_base**invert
                            elif action_type=="attack round":
                                decimals = 3 # How many decimals to show in the output.
                                nondecimals = 8
                                metric_base, output, _ = average_attack_round(player, enemy, 0, min_tp, input_metric)
                                invert = output[-1]
                                metric = metric_base**invert

                            elif action_type == "combined tp/ws":
                                decimals = 1
                                nondecimals = 8
                                if combined_ws_player is None:
                                    metric_base, output = _combined_tp_ws_metric(
                                        player, enemy, ws_name, min_tp, ws_type
                                    )
                                else:
                                    metric_base, output = _combined_tp_ws_metric_pair(
                                        player, combined_ws_player, enemy, ws_name, min_tp, ws_type
                                    )
                                invert = 1.0
                                metric = metric_base

                            else:
                                print(f"Unknown action_type  ({action_type})")
                                import sys; sys.exit()

                            primary_metric = metric
                            if substat_spec:
                                primary_floor = float(substat_spec.get("primary_floor", 0.0))
                                if primary_metric < primary_floor:
                                    continue
                                if not _substat_constraints_met(
                                    _substat_player(player), substat_spec.get("constraints")
                                ):
                                    continue
                                metric = _substat_value(
                                    _substat_player(player), substat_spec.get("target")
                                )

                            metric = 0.0001 if metric <= 0 else metric # Prevent divide-by-zero errors
                            better_substat = metric > best_metric
                            tied_substat_better_damage = (
                                substat_spec
                                and metric == best_metric
                                and (best_primary_metric is None or primary_metric > best_primary_metric)
                            )
                            if better_substat or tied_substat_better_damage or (not substat_spec and metric > best_metric):
                                if item1==item2:
                                    print(f"[{slot1:<15s}]: [{best_set[slot1]['Name2']} ->  {item1['Name2']}   [{best_metric**invert:>{nondecimals}.{decimals}f} -> {metric**invert:>{nondecimals}.{decimals}f}]") if verbose_swaps else None
                                else:
                                    print(f"[{slot1:<6s} & {slot2:<6s}]: [{best_set[slot1]['Name2']} & {best_set[slot2]['Name2']}] -> [{item1['Name2']} & {item2['Name2']}] [{best_metric**invert:>{nondecimals}.{decimals}f} -> {metric**invert:>{nondecimals}.{decimals}f}]") if verbose_swaps else None
                                best_set = test_set.copy()
                                best_metric = metric
                                best_primary_metric = primary_metric
                                best_output = output
                                last_improvement = (
                                    f"{slot1} + {slot2}: {item1.get('Name', 'item')}"
                                    if item1 != item2
                                    else f"{slot1}: {item1.get('Name', 'item')}"
                                )
    
                            elif (item1==item2):
                                relative_difference = (best_metric - metric) / best_metric
                                if (relative_difference <= float(next_best_percent) / 100
                                        and slot1 not in ["main", "sub", "ranged", "back"]):
                                    swaps[slot1].append([item1["Name2"], metric**invert, relative_difference])

            best_text = f"{best_metric:.4f}" if best_metric is not None else "n/a"
            report_progress(
                f"Iteration {z + 1}/{n_iter} finished; tested {tested_count:,}/"
                f"{estimated_checks:,}; valid {valid_count:,}; "
                f"last improvement {last_improvement}; best {best_text}; "
                f"{progress_results_text()}."
            )

        if defense_phase:
            pdt, mdt = calculate_damage_taken(best_set, buffs, abilities, damage_taken_item_cache)
            dt = damage_taken_dt_from_totals(
                damage_taken_totals(best_set, buffs, damage_taken_item_cache),
                best_set["main"], abilities,
            )
            if pdt > pdt_thresh or mdt > mdt_thresh or dt > dt_thresh:
                if best_set == converged_set:
                    raise ValueError(
                        "Optimizer could not reach the requested PDT/MDT/DT minimums from the "
                        "current legal candidates. Try more selected defensive gear or additional search runs."
                    )
                report_progress(
                    f"Defensive search improving: PDT:{pdt:g}, MDT:{mdt:g}, DT:{dt:g}; "
                    "continuing without damage simulation."
                )
                continue

            # Force one following outer pass so the now-feasible baseline can
            # be scored with the requested damage metric.
            defense_phase = False
            metric_pass_pending = True
            pdt_thresh_temp = pdt_thresh
            mdt_thresh_temp = mdt_thresh
            dt_thresh_temp = dt_thresh
            report_progress(
                "Defensive minimums satisfied; starting damage optimization from the feasible set."
            )
            continue

        if single_outer_pass:
            if best_output is None:
                raise ValueError("No valid gear set satisfies the current PDT/MDT/DT requirements.")
            pdt, mdt = calculate_damage_taken(
                best_set, buffs, abilities, damage_taken_item_cache
            )
            dt = damage_taken_dt_from_totals(
                damage_taken_totals(best_set, buffs, damage_taken_item_cache),
                best_set["main"], abilities,
            )
            break

        if (base_pdt > pdt_thresh_temp or base_mdt > mdt_thresh_temp
                or base_dt > dt_thresh_temp) and not found_feasible_neighbor:
            raise ValueError(
                "Optimizer could not reach the requested PDT/MDT/DT minimums from the current search neighborhood. "
                "Try more selected defensive gear or additional search runs."
            )

        # The candidate loop leaves pdt/mdt/dt containing the last candidate
        # examined, not necessarily the selected set. Recalculate before any
        # convergence exit so a valid set is never reported as failing its
        # defensive requirements.
        pdt, mdt = calculate_damage_taken(best_set, buffs, abilities, damage_taken_item_cache)
        dt = damage_taken_dt_from_totals(
            damage_taken_totals(best_set, buffs, damage_taken_item_cache),
            best_set["main"], abilities,
        )

        if substat_spec and best_output is None:
            raise ValueError(
                "No gear set satisfies the requested damage floor and secondary-stat constraints."
            )

        if best_set==converged_set: # If no improvement is found after one full iteration.
            # best_player = create_player(main_job, sub_job, master_level, best_set, buffs, abilities)
            # for k in best_player.gearset:
            #     print(k,best_player.gearset[k]["Name2"])
            # print(best_output)
            break # Break out of the main loop and check PDT/MDT conditions.


        if best_output is None:
            raise ValueError("No valid gear set satisfies the current PDT/MDT/DT requirements.")
        metric_pass_pending = False


        # Compare the pdt and mdt values from this iteration with the previous iteration.
        if pdt == pdt_old and mdt == mdt_old and dt == dt_old:
            conditional_converge_count += 1
            if conditional_converge_count >= 3:
                print("Unable to find a set which satisfies the conditions better than the current set. Exiting.")
                break
        else:
            conditional_converge_count = 0

        # Save the PDT and MDT values from this iteration to compare with the next iteration.
        pdt_old = pdt
        mdt_old = mdt
        dt_old = dt

        # Update the temporary PDT and MDT requirements so that the next set is slightly closer to the true requirements.
        pdt_thresh_temp = pdt - 1 if pdt-1 > pdt_thresh else pdt_thresh
        mdt_thresh_temp = mdt - 1 if mdt-1 > mdt_thresh else mdt_thresh
        dt_thresh_temp = dt - 1 if dt-1 > dt_thresh else dt_thresh
        
        print(f"Current best set: PDT:{pdt}, MDT:{mdt}, DT:{dt}")
        report_progress(f"Current best PDT:{pdt:g}, MDT:{mdt:g}, DT:{dt:g}.")


    if pdt > pdt_thresh or mdt > mdt_thresh or dt > dt_thresh:
        raise ValueError("Optimizer could not find a set satisfying the requested PDT/MDT/DT minimums.")

    # At this point, we've found the best conditional set.

    # Swap the earrings to make sure the "Right Ear:" effect earrings show up in the ear2 slot.
    if best_set["ear1"]["Name"] in jse_ears+["Balder Earring +1"]:
        best_set["ear1"],best_set["ear2"] = best_set["ear2"],best_set["ear1"]

    # Record the stats for the best gear set.
    best_player = create_player(main_job, sub_job, master_level, best_set, buffs, abilities)


    header = {
        "weapon skill": ws_name, "combined tp/ws": f"{ws_name} + TP cycle",
        "spell cast": spell_name, "attack round": "Melee TP set",
    }[action_type]
    # Print a fancy output.
    print("==============================================================")
    print(f"Best   \"{input_metric}\"   \"{header}\"   set")
    print("==============================================================")
    for k in best_player.gearset:
        print(f"{k:>10s}  {best_player.gearset[k]['Name2']:<50s}")
    print()
    if action_type == "combined tp/ws":
        print(f"Combined TP + WS DPS = {best_metric:.3f}")
        print(f"Avg TP time = {best_output[2]:.3f} s; Avg WS damage = {best_output[3]:.1f}")
        print(f"Cycle time including WS delay = {best_output[4]:.3f} s")
    elif action_type=="attack round":
        if input_metric=="Time to WS":
            print(f"Avg WS Time = {best_metric**invert:<{nondecimals}.{decimals}f} s")
            print(f"Avg TP per round = {best_output[1]:<5.1f} TP")
        else:
            print(f"Avg Damage per round = {best_output[0]:<{nondecimals}.{decimals}f} damage")
            print(f"Avg time per round = {best_output[2]:<5.1f} s")
            print(f"Avg TP per round = {best_output[1]:<5.1f} TP")
    else:
        print(f"Avg Damage = {best_output[0]:<{nondecimals}.{decimals}f} damage")
        print(f"Avg TP return = {best_output[1]:<5.1f} TP")
    print("==============================================================")
    print("==============================================================")

    if print_swaps:
        print(f"\nList of potential swaps within {next_best_percent}% of the best set ({float(best_metric)**invert:<{nondecimals}.{decimals}f}):")
        for slot in swaps:
            for swap in swaps[slot]:
                line = f"{slot:<6s} {swap[0]:<50s} {float(swap[1]):<{nondecimals}.{decimals}f} {swap[2] * 100:>5.1f}%"
                print(line)

    # Print additional output formatted for BG Wiki item sets.
    if False:
        format_bgwiki(header, (min_tp), best_player, best_metric)

    result_metric = best_primary_metric if substat_spec else best_metric
    if return_details:
        report_progress("Search completed.")
        return best_player, best_output, result_metric
    report_progress("Search completed.")
    return(best_player, best_output)


def _build_set_restart_worker(request, progress_callback=None):
    """Run one independent optimizer restart in a process-safe top-level worker."""
    output_buffer = StringIO()
    try:
        build_kwargs = request["kwargs"].copy()
        progress_queue = build_kwargs.pop("progress_queue", None)
        if progress_callback is not None:
            build_kwargs["progress_callback"] = progress_callback
        elif progress_queue is not None:
            label = request.get("progress_label", f"Search run {request['index']}")
            build_kwargs["progress_callback"] = lambda message: progress_queue.put(
                f"{label}: {message}"
            )
        with redirect_stdout(output_buffer):
            player, output, metric = build_set(*request["args"], **build_kwargs)
    except OptimizerStopped as error:
        return {
            "index": request["index"],
            "seed": request["seed"],
            "stopped": True,
            "error": str(error),
            "player": error.partial_player,
            "output": error.partial_output,
            "metric": error.partial_metric,
            "log": output_buffer.getvalue(),
        }
    except Exception as error:
        return {
            "index": request["index"],
            "seed": request["seed"],
            "error": str(error),
            "log": output_buffer.getvalue(),
        }
    else:
        return {
            "index": request["index"],
            "player": player,
            "output": output,
            "metric": metric,
            "seed": request["seed"],
            "log": output_buffer.getvalue(),
        }


def _rank_optimizer_results(results, limit=5):
    usable = [
        result for result in results
        if result.get("player") is not None and result.get("metric") is not None
    ]
    usable.sort(key=lambda result: result["metric"], reverse=True)
    return [
        {
            "rank": rank,
            "player": result["player"],
            "output": result.get("output"),
            "metric": result["metric"],
            "seed": result.get("seed"),
            "index": result.get("index"),
        }
        for rank, result in enumerate(usable[:limit], start=1)
    ]


def _gearset_key(player):
    return tuple(
        (slot, str(item.get("Name2") or item.get("Name") or "Empty"))
        for slot, item in sorted(player.gearset.items())
    )


def _gearset_mapping_key(gearset):
    return tuple(
        (slot, str(item.get("Name2") or item.get("Name") or "Empty"))
        for slot, item in sorted(gearset.items())
    )


def _unique_ranked_results(results, limit=5):
    ranked = _rank_optimizer_results(results, limit=max(limit, len(results)))
    unique, seen = [], set()
    for result in ranked:
        key = _gearset_key(result["player"])
        if key in seen:
            continue
        seen.add(key)
        result["rank"] = len(unique) + 1
        unique.append(result)
        if len(unique) >= limit:
            break
    return unique


def _optimize_combined_pair(main_job, sub_job, master_level, buffs, abilities, enemy,
                            ws_name, min_tp, check_gear, starting_gearset,
                            pdt_requirement, mdt_requirement, dt_requirement,
                            print_swaps, next_best_percent, *, tp_starting_gearset=None,
                            ws_starting_gearset=None, restarts=1, workers=0, seed=None,
                            n_iter=10, return_details=False, return_top_results=False,
                            parallel_mode="search_runs", progress_callback=None,
                            progress_queue=None, stop_event=None,
                            combined_defense_both=True):
    """Find the best WS first, then optimize TP around its fixed weapon pair."""
    tp_start = dict(tp_starting_gearset or starting_gearset)
    ws_start = dict(ws_starting_gearset or starting_gearset)

    if progress_callback is not None:
        progress_callback("Combined optimization: searching WS set first...")
    ws_pdt = pdt_requirement if combined_defense_both else 199
    ws_mdt = mdt_requirement if combined_defense_both else 199
    ws_dt = dt_requirement if combined_defense_both else 199
    ws_result = optimize_set(
        main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, "None",
        "weapon skill", min_tp, check_gear, ws_start, ws_pdt, ws_mdt,
        "Damage Dealt", print_swaps, next_best_percent, dt_requirement=ws_dt,
        restarts=restarts, workers=workers, seed=seed, n_iter=n_iter,
        return_details=True, return_top_results=True, parallel_mode=parallel_mode,
        progress_callback=progress_callback, progress_queue=progress_queue,
        stop_event=stop_event,
    )
    if progress_callback is not None:
        progress_callback(
            "Combined optimization: optimizing TP armor against the same WS main/sub pair..."
        )
    # A TP+WS result is one equipment pair.  The TP set may improve every
    # armor/accessory slot, but its main and sub weapons must remain the same
    # weapons used by the selected WS set.  WS optimization still considers
    # all eligible weapon combinations before this constraint is applied.
    ws_player = ws_result[0]
    tp_check_gear = {slot: list(items) for slot, items in check_gear.items()}
    tp_start["main"] = ws_player.gearset["main"]
    tp_start["sub"] = ws_player.gearset["sub"]
    for slot in ("main", "sub"):
        tp_check_gear[slot] = [ws_player.gearset[slot]]
    tp_result = optimize_set(
        main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, "None",
        "combined tp/ws", min_tp, tp_check_gear, tp_start, pdt_requirement, mdt_requirement,
        "Combined DPS", print_swaps, next_best_percent, dt_requirement=dt_requirement,
        restarts=restarts, workers=workers, seed=seed, n_iter=n_iter,
        return_details=True, return_top_results=True, parallel_mode=parallel_mode,
        progress_callback=progress_callback, progress_queue=progress_queue,
        stop_event=stop_event, combined_ws_player=ws_player,
    )
    tp_top = list(tp_result[4] or [])
    ws_top = [{"player": ws_result[0], "metric": ws_result[2], "seed": ws_result[3]}]
    if not tp_top:
        tp_top = [{"player": tp_result[0], "metric": tp_result[2], "seed": tp_result[3]}]

    ranged_ws_names = {
        "Empyreal Arrow", "Flaming Arrow", "Namas Arrow", "Jishnu's Radiance",
        "Apex Arrow", "Refulgent Arrow", "Sidewinder", "Blast Arrow", "Piercing Arrow",
        "Last Stand", "Hot Shot", "Leaden Salute", "Wildfire", "Coronach",
        "Trueflight", "Detonator", "Blast Shot", "Slug Shot", "Split Shot",
    }
    ws_type = "ranged" if ws_name in ranged_ws_names else "melee"
    pair_results = []
    for tp_entry in tp_top:
        for ws_entry in ws_top:
            metric, output = _combined_tp_ws_metric_pair(
                tp_entry["player"], ws_entry["player"], enemy, ws_name, min_tp, ws_type
            )
            pair_results.append({
                "player": CombinedSetResult(tp_entry["player"], ws_entry["player"]),
                "tp_player": tp_entry["player"], "ws_player": ws_entry["player"],
                "output": output, "metric": metric,
                "tp_metric": tp_entry.get("metric"), "ws_metric": ws_entry.get("metric"),
                "tp_seed": tp_entry.get("seed"), "ws_seed": ws_entry.get("seed"),
            })
    pair_results.sort(key=lambda result: result["metric"], reverse=True)
    for rank, result in enumerate(pair_results[:5], start=1):
        result["rank"] = rank
    if not pair_results:
        raise ValueError("Combined TP + WS optimization returned no usable set pair.")
    winner = pair_results[0]
    winning_seed = f"TP {winner.get('tp_seed')} / WS {winner.get('ws_seed')}"
    if progress_callback is not None:
        progress_callback(
            f"Combined optimization complete: DPS {winner['metric']:.6f} "
            f"(TP set + WS set)."
        )
    if return_details:
        result = winner["player"], winner["output"], winner["metric"], winning_seed
        if return_top_results:
            return (*result, pair_results[:5])
        return result
    return winner["player"], winner["output"]


def _optimize_single_run_parallel(main_job, sub_job, master_level, buffs, abilities, enemy,
                                  ws_name, spell_name, action_type, min_tp, check_gear,
                                  starting_gearset, pdt_requirement, mdt_requirement,
                                  input_metric, print_swaps, next_best_percent, *, dt_requirement=0, workers,
                                  seed, n_iter, return_details, return_top_results,
                                  progress_callback, progress_queue, stop_event,
                                  combined_ws_player=None):
    """Split one seeded optimizer run into independent slot-pair worker chunks.

    Each round evaluates the same immutable baseline in parallel, merges the
    best result, then uses it as the next baseline.  This retains the existing
    per-candidate formulas while making a single large search use multiple CPU
    processes.
    """
    def drain_progress():
        if progress_queue is not None and progress_callback is not None:
            while True:
                try:
                    progress_callback(progress_queue.get_nowait())
                except queue_module.Empty:
                    break

    def notify(message):
        drain_progress()
        if progress_callback is not None:
            progress_callback(message)

    def check_stopped():
        if _stop_requested(stop_event):
            raise OptimizerStopped("Optimizer stopped by user.", results=_unique_ranked_results(history))

    slot_names = sorted(check_gear)
    slot_pairs = [
        tuple(sorted((left, right)))
        for index, left in enumerate(slot_names)
        for right in slot_names[index:]
    ]
    if not slot_pairs:
        raise ValueError("No optimizer candidate slots were selected.")
    worker_count = int(workers)
    if worker_count <= 0:
        worker_count = max(1, (os.cpu_count() or 2) - 1)
    worker_count = min(worker_count, len(slot_pairs))
    # Slot pairs are not equally expensive: a pair of slots with 200
    # candidates each has 40,000 combinations, while a pair with 10 each has
    # only 100.  Round-robin assignment left one process working on a huge
    # pair while most workers went idle.  Greedy weighted bin-packing keeps
    # the same formulas and baseline merge behavior, but gives each worker a
    # comparable amount of candidate work.
    pair_weights = []
    for left, right in slot_pairs:
        left_count = len(check_gear.get(left, ()))
        right_count = len(check_gear.get(right, ()))
        weight = left_count if left == right else left_count * right_count
        pair_weights.append((weight, (left, right)))
    pair_weights.sort(key=lambda entry: entry[0], reverse=True)
    partitions = [set() for _ in range(worker_count)]
    partition_loads = [0] * worker_count
    for weight, pair in pair_weights:
        index = min(range(worker_count), key=partition_loads.__getitem__)
        partitions[index].add(pair)
        partition_loads[index] += weight

    run_seed = int(np.random.SeedSequence(seed).generate_state(1)[0])
    baseline = dict(starting_gearset)
    history = []
    latest = []
    max_rounds = max(1, int(n_iter))
    shared_args = (
        main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, spell_name,
        action_type, min_tp, check_gear, baseline, pdt_requirement, mdt_requirement,
        input_metric, print_swaps, next_best_percent,
    )
    notify(
        f"Search run 1/1 started in split-worker mode with {worker_count} worker "
        f"chunks (estimated loads {', '.join(f'{load:,}' for load in partition_loads)}; "
        f"seed {run_seed})."
    )
    for round_index in range(max_rounds):
        check_stopped()
        requests = []
        for index, partition in enumerate(partitions, start=1):
            requests.append({
                "args": shared_args[:11] + (baseline,) + shared_args[12:],
                "kwargs": {
                    "seed": run_seed,
                    "n_iter": 1,
                    "return_details": True,
                    "dt_requirement": dt_requirement,
                    "combined_ws_player": combined_ws_player,
                    "stop_event": stop_event,
                    "progress_queue": progress_queue,
                    "slot_pair_filter": partition,
                    "preserve_starting_gearset": round_index > 0,
                    "single_outer_pass": True,
                },
                "seed": run_seed,
                "index": index,
                "progress_label": f"Search run 1/1 · chunk {index}/{worker_count}",
            })
        notify(f"Search run 1/1 · split pass {round_index + 1}/{max_rounds} started.")
        if worker_count == 1:
            latest = [_build_set_restart_worker(requests[0])]
        else:
            with ProcessPoolExecutor(max_workers=worker_count) as executor:
                futures = [executor.submit(_build_set_restart_worker, request) for request in requests]
                latest = []
                pending = set(futures)
                while pending:
                    # Worker processes report through the manager queue. Drain
                    # it before and after each wait so progress reaches Qt while
                    # futures are still running.
                    drain_progress()
                    done, pending = wait(pending, timeout=5.0)
                    drain_progress()
                    notify(
                        f"Search run 1/1 · split pass {round_index + 1}/{max_rounds} "
                        f"is evaluating {len(pending)} worker chunks."
                    )
                    for future in done:
                        latest.append(future.result())
        history.extend(latest)
        ranked = _unique_ranked_results(latest, limit=1)
        if not ranked:
            if any(result.get("stopped") for result in latest):
                raise OptimizerStopped("Optimizer stopped by user.", results=_unique_ranked_results(history))
            # A worker owns only part of the one/two-slot neighborhood.  With
            # PDT/MDT/DT minimums enabled, no *individual* partition may be
            # able to meet the final target even though successive swaps from
            # several partitions form a valid set.  Do one complete, seeded
            # search instead of reporting that false negative to the user.
            notify(
                "Split-worker chunks could not individually satisfy the defensive "
                "minimums; retrying the full search so defensive slots can combine."
            )
            fallback_request = {
                "args": shared_args[:11] + (baseline,) + shared_args[12:],
                "kwargs": {
                    "seed": run_seed,
                    # ``build_set`` already repeats its outer defensive
                    # convergence passes.  One full neighborhood pass per
                    # step keeps this fallback responsive and avoids treating
                    # split-worker round count as duplicate inner iterations.
                    "n_iter": 1,
                    "return_details": True,
                    "dt_requirement": dt_requirement,
                    "stop_event": stop_event,
                    "preserve_starting_gearset": True,
                },
                "seed": run_seed,
                "index": 1,
            }
            fallback = _build_set_restart_worker(
                fallback_request,
                lambda message: notify(f"Search run 1/1 · full-search fallback: {message}"),
            )
            history.append(fallback)
            if fallback.get("stopped"):
                raise OptimizerStopped("Optimizer stopped by user.", results=_unique_ranked_results(history))
            if "error" in fallback:
                errors = "; ".join(
                    str(result.get("error", "unknown worker failure")) for result in latest
                )
                raise ValueError(
                    "Split-worker chunks could not form a feasible set, and the full-search "
                    f"fallback also failed: {fallback['error']} (chunk errors: {errors})"
                )
            fallback_ranked = _unique_ranked_results([fallback], limit=1)
            winner = fallback_ranked[0]
            top_results = _unique_ranked_results(history)
            notify("Search run 1/1 · full-search fallback completed.")
            if return_details:
                result = winner["player"], winner["output"], winner["metric"], winner["seed"]
                return (*result, top_results) if return_top_results else result
            return winner["player"], winner["output"]
        winner = ranked[0]
        previous_key = _gearset_mapping_key(baseline)
        next_key = _gearset_key(winner["player"])
        baseline = dict(winner["player"].gearset)
        notify(
            f"Search run 1/1 · split pass {round_index + 1}/{max_rounds} merged; "
            f"best metric {winner['metric']:.6f}."
        )
        if next_key == previous_key:
            break

    check_stopped()
    top_results = _unique_ranked_results(history)
    if not top_results:
        raise ValueError("Split-worker search returned no usable gear set.")
    winner = top_results[0]
    if return_details:
        result = winner["player"], winner["output"], winner["metric"], winner["seed"]
        return (*result, top_results) if return_top_results else result
    return winner["player"], winner["output"]


def optimize_set(main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, spell_name, action_type, min_tp, check_gear, starting_gearset, pdt_requirement, mdt_requirement, input_metric, print_swaps, next_best_percent, *, dt_requirement=0, tp_starting_gearset=None, ws_starting_gearset=None, restarts=1, workers=0, seed=None, n_iter=10, return_details=False, return_top_results=False, parallel_mode="search_runs", progress_callback=None, progress_queue=None, stop_event=None, combined_ws_player=None, combined_defense_both=True):
    """Run independent seeded searches or one split-worker search.

    ``workers=0`` selects available CPU cores while leaving one core free.
    ``parallel_mode='single_run'`` assigns disjoint slot-pair groups to those
    workers and merges each pass against the same seeded baseline.
    """
    if action_type == "combined tp/ws" and combined_ws_player is None:
        return _optimize_combined_pair(
            main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, min_tp,
            check_gear, starting_gearset, pdt_requirement, mdt_requirement,
            dt_requirement, print_swaps, next_best_percent,
            tp_starting_gearset=tp_starting_gearset,
            ws_starting_gearset=ws_starting_gearset, restarts=restarts, workers=workers,
            seed=seed, n_iter=n_iter, return_details=return_details,
            return_top_results=return_top_results, parallel_mode=parallel_mode,
            progress_callback=progress_callback, progress_queue=progress_queue,
            stop_event=stop_event, combined_defense_both=combined_defense_both,
        )
    if parallel_mode == "single_run":
        return _optimize_single_run_parallel(
            main_job, sub_job, master_level, buffs, abilities, enemy, ws_name,
            spell_name, action_type, min_tp, check_gear, starting_gearset,
            pdt_requirement, mdt_requirement, input_metric, print_swaps,
            next_best_percent, dt_requirement=dt_requirement, workers=workers, seed=seed, n_iter=n_iter,
            return_details=return_details, return_top_results=return_top_results,
            progress_callback=progress_callback, progress_queue=progress_queue,
            stop_event=stop_event, combined_ws_player=combined_ws_player,
        )
    # Independent runs are deliberately bounded.  Beyond ten, the extra process
    # and result-management overhead is rarely useful, and the GUI presents one
    # persistent status card per run.
    restarts = max(1, min(10, int(restarts)))
    workers = int(workers)
    seed_sequence = np.random.SeedSequence(seed)
    restart_seeds = [int(value) for value in seed_sequence.generate_state(restarts)]
    shared_args = (
        main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, spell_name,
        action_type, min_tp, check_gear, starting_gearset, pdt_requirement,
        mdt_requirement, input_metric, print_swaps, next_best_percent,
    )
    requests = [
        {
            "args": shared_args,
            "kwargs": {
                "seed": restart_seed, "n_iter": n_iter, "return_details": True,
                "dt_requirement": dt_requirement,
                "combined_ws_player": combined_ws_player,
                "stop_event": stop_event,
                "progress_queue": progress_queue,
            },
            "seed": restart_seed,
            "index": index,
        }
        for index, restart_seed in enumerate(restart_seeds, start=1)
    ]

    def drain_progress():
        if progress_queue is not None and progress_callback is not None:
            while True:
                try:
                    progress_callback(progress_queue.get_nowait())
                except queue_module.Empty:
                    break

    def notify(message):
        drain_progress()
        if progress_callback is not None:
            progress_callback(message)

    def check_stopped():
        if _stop_requested(stop_event):
            raise OptimizerStopped("Optimizer stopped by user.")

    def run_serial(request):
        check_stopped()
        notify(f"Search run {request['index']}/{restarts} started (seed {request['seed']}).")
        callback = lambda message: notify(f"Search run {request['index']}: {message}")
        result = _build_set_restart_worker(request, callback)
        if result.get("stopped"):
            notify(f"Search run {request['index']} stopped after the current calculation.")
        elif "error" in result:
            notify(f"Search run {request['index']} failed: {result['error']}")
        else:
            notify(f"Search run {request['index']}/{restarts} completed.")
        return result

    if restarts == 1:
        results = [run_serial(requests[0])]
    else:
        max_workers = workers
        if max_workers <= 0:
            max_workers = min(restarts, max(1, (os.cpu_count() or 2) - 1))
        max_workers = min(restarts, max_workers)
        if max_workers == 1:
            results = [run_serial(request) for request in requests]
        else:
            notify(
                f"Independent mode using {max_workers} worker processes for "
                f"{restarts} search runs."
            )
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                check_stopped()
                for request in requests:
                    notify(f"Search run {request['index']}/{restarts} started (seed {request['seed']}).")
                futures = {
                    executor.submit(_build_set_restart_worker, request): request
                    for request in requests
                }
                results = []
                pending = set(futures)
                while pending:
                    stopping = _stop_requested(stop_event)
                    # Do not wait for a process to finish before forwarding its
                    # queue messages to the GUI.
                    drain_progress()
                    done, pending = wait(pending, timeout=1.0 if stopping else 5.0)
                    drain_progress()
                    for future in done:
                        request = futures[future]
                        result = future.result()
                        results.append(result)
                        if result.get("stopped"):
                            notify(f"Search run {request['index']} stopped after the current calculation.")
                        elif "error" in result:
                            notify(f"Search run {request['index']} failed: {result['error']}")
                        else:
                            notify(f"Search run {request['index']}/{restarts} completed.")

    results.sort(key=lambda result: result["index"])

    successful_results = [result for result in results if "error" not in result]
    top_results = _rank_optimizer_results(results)
    if not top_results:
        if any(result.get("stopped") for result in results):
            raise OptimizerStopped("Optimizer stopped before a usable set was completed.")
        # Restarts begin from randomized candidate sets.  When every restart
        # rejects a defensive target, give the exact user-selected starting
        # set one complete convergence search before declaring the target
        # impossible.  This preserves the PDT/MDT/DT gate; it only avoids a
        # random-start false negative.
        notify(
            "All independent search runs missed the defensive minimums; retrying "
            "a full search from the selected set."
        )
        fallback_seed = restart_seeds[0]
        fallback_request = {
            "args": shared_args[:11] + (dict(starting_gearset),) + shared_args[12:],
            "kwargs": {
                "seed": fallback_seed,
                "n_iter": 1,
                "return_details": True,
                "dt_requirement": dt_requirement,
                "stop_event": stop_event,
                "preserve_starting_gearset": True,
            },
            "seed": fallback_seed,
            "index": 0,
        }
        fallback = _build_set_restart_worker(
            fallback_request,
            lambda message: notify(f"Full-search fallback: {message}"),
        )
        results.append(fallback)
        if fallback.get("stopped"):
            raise OptimizerStopped("Optimizer stopped by user.")
        if "error" in fallback:
            errors = "; ".join(
                f"seed {result['seed']}: {result.get('error', 'unknown worker failure')}"
                for result in results
            )
            raise ValueError(
                "All optimizer search runs failed, and the full-search fallback also failed. "
                f"{errors}"
            )
        successful_results = [fallback]
        top_results = _rank_optimizer_results([fallback])
        notify("Full-search fallback completed.")

    if _stop_requested(stop_event):
        raise OptimizerStopped("Optimizer stopped by user.", results=top_results)

    winner = top_results[0]
    winner_result = max(successful_results, key=lambda result: result["metric"])
    print(winner_result["log"], end="")
    if winner.get("index") == 0:
        notify(f"Selected full-search fallback (seed {winner['seed']}; metric {winner['metric']:.6f}).")
        print(f"Selected full-search fallback (seed {winner['seed']}; metric {winner['metric']:.6f}).")
    elif restarts > 1:
        notify(
            f"Selected search run {winner['index']}/{restarts} "
            f"(seed {winner['seed']}; metric {winner['metric']:.6f})."
        )
        print(
            f"Selected search run {winner['index']}/{restarts} "
            f"(seed {winner['seed']}; metric {winner['metric']:.6f})."
        )
    if return_details:
        result = winner["player"], winner["output"], winner["metric"], winner["seed"]
        if return_top_results:
            return (*result, top_results)
        return result
    return winner["player"], winner["output"]


def optimize_substats(main_job, sub_job, master_level, buffs, abilities, enemy,
                      ws_name, spell_name, base_action_type, min_tp, check_gear,
                      starting_gearset, pdt_requirement, mdt_requirement,
                      input_metric, print_swaps, next_best_percent, substat_specs,
                      *, dt_requirement=0, restarts=1, workers=0, seed=None,
                      n_iter=10, return_details=False, return_top_results=False,
                      parallel_mode="search_runs", progress_callback=None,
                      progress_queue=None, stop_event=None):
    """Optimize damage first, then trade a bounded amount for prioritized stats.

    The damage floor is fixed from the first optimization.  Each secondary
    stat is then maximized in order, while preserving that floor and all
    previously selected stat floors.  This makes row 1 more important than
    row 2, and row 2 more important than row 3, without turning the result
    into an arbitrary weighted score.
    """
    specs = [
        {"target": str(spec.get("target") or "").strip()}
        for spec in (substat_specs or ())
        if str(spec.get("target") or "").strip()
    ]
    if not specs:
        raise ValueError("Select at least one secondary stat to optimize.")
    if progress_callback is not None:
        progress_callback("Sub-stat optimization: finding the damage baseline...")
    baseline = optimize_set(
        main_job, sub_job, master_level, buffs, abilities, enemy, ws_name,
        spell_name, base_action_type, min_tp, check_gear, starting_gearset,
        pdt_requirement, mdt_requirement, input_metric, print_swaps,
        next_best_percent, dt_requirement=dt_requirement, restarts=restarts,
        workers=workers, seed=seed, n_iter=n_iter, return_details=True,
        return_top_results=True, parallel_mode=parallel_mode,
        progress_callback=progress_callback, progress_queue=progress_queue,
        stop_event=stop_event,
    )
    current_player, current_output, damage_metric, winning_seed, _ = baseline
    damage_floor = float(damage_metric) * (
        1.0 - max(0.0, min(100.0, float(substat_specs[0].get("loss_percent", 15.0)))) / 100.0
    )
    constraints = []
    summary = []
    current_gearset = dict(current_player.gearset)
    for index, spec in enumerate(specs, start=1):
        if _stop_requested(stop_event):
            raise OptimizerStopped("Optimizer stopped by user.")
        target = spec["target"]
        if progress_callback is not None:
            progress_callback(
                f"Sub-stat optimization: prioritizing {target} ({index}/{len(specs)})..."
            )
        phase = dict(
            target=target,
            primary_floor=damage_floor,
            constraints=list(constraints),
        )
        phase_seed = None if seed is None else int(seed) + index
        phase_result = build_set(
            main_job, sub_job, master_level, buffs, abilities, enemy, ws_name,
            spell_name, base_action_type, min_tp, check_gear, current_gearset,
            pdt_requirement, mdt_requirement, input_metric, False,
            next_best_percent, dt_requirement=dt_requirement, seed=phase_seed,
            n_iter=n_iter, return_details=True, progress_callback=progress_callback,
            stop_event=stop_event, preserve_starting_gearset=True,
            substat_spec=phase,
        )
        current_player, current_output, damage_metric = phase_result
        value = _substat_value(_substat_player(current_player), target)
        constraints.append((target, value))
        summary.append({
            "stat": target,
            "value": value,
            "damage": float(damage_metric),
            "damage_floor": damage_floor,
        })
        current_gearset = dict(current_player.gearset)

    if progress_callback is not None:
        progress_callback("Sub-stat optimization complete.")
    top_results = [{
        "rank": 1, "player": current_player, "output": current_output,
        "metric": damage_metric, "seed": winning_seed, "index": 1,
    }]
    if return_details:
        result = current_player, current_output, damage_metric, winning_seed, top_results, summary
        return result if return_top_results else result[:4]
    return current_player, current_output


def rank_weapon_skills(main_job, sub_job, master_level, buffs, abilities, enemy,
                       weapon_skill_names, ws_type, check_gear, starting_gearset,
                       pdt_requirement, mdt_requirement, *, dt_requirement=0,
                       tp_values=(1000, 2000, 3000), restarts=1, workers=0,
                       seed=None, n_iter=10, parallel_mode="search_runs",
                       progress_callback=None, progress_queue=None, stop_event=None):
    """Optimize and rank every supplied WS independently at each TP tier.

    Weapon-slot locking belongs to the caller because the GUI knows which
    selected melee/ranged setup defines the comparison.  Failed or restricted
    weapon skills are returned as diagnostics so one unsupported WS does not
    discard the useful rankings for the rest of the weapon type.
    """
    names = list(dict.fromkeys(str(name) for name in weapon_skill_names if name))
    tiers = tuple(int(value) for value in tp_values)
    if not names:
        raise ValueError("The selected weapon type has no modeled weapon skills.")
    if not tiers or any(value < 1000 or value > 3000 for value in tiers):
        raise ValueError("Weapon-skill ranking TP values must be between 1000 and 3000.")

    total = len(names) * len(tiers)
    completed = 0
    rankings = {value: [] for value in tiers}
    errors = []

    def notify(message):
        if progress_callback is not None:
            progress_callback(message)

    for tier_index, tp_value in enumerate(tiers):
        for ws_index, ws_name in enumerate(names):
            if _stop_requested(stop_event):
                raise OptimizerStopped("Weapon-skill ranking stopped by user.")
            cell_seed = None if seed is None else int(seed) + tier_index * len(names) + ws_index
            notify(
                f"WS ranking {completed + 1}/{total}: optimizing {ws_name} at "
                f"{tp_value:,} TP."
            )
            try:
                player, output, metric, winning_seed = optimize_set(
                    main_job, sub_job, master_level, buffs, abilities, enemy,
                    ws_name, "", "weapon skill", tp_value, check_gear,
                    dict(starting_gearset), pdt_requirement, mdt_requirement,
                    "Damage dealt", False, 2, dt_requirement=dt_requirement,
                    restarts=restarts, workers=workers, seed=cell_seed,
                    n_iter=n_iter, return_details=True,
                    parallel_mode=parallel_mode,
                    progress_callback=(
                        lambda message, name=ws_name, tp=tp_value:
                        notify(f"{name} @ {tp:,} TP: {message}")
                    ),
                    progress_queue=progress_queue, stop_event=stop_event,
                )
                damage = float(output[0])
                rankings[tp_value].append({
                    "ws_name": ws_name,
                    "tp": tp_value,
                    "damage": damage,
                    "player": player,
                    "output": output,
                    "metric": float(metric),
                    "seed": winning_seed,
                })
            except OptimizerStopped:
                raise
            except Exception as error:
                errors.append({
                    "ws_name": ws_name,
                    "tp": tp_value,
                    "error": str(error),
                })
                notify(f"WS ranking skipped {ws_name} at {tp_value:,} TP: {error}")
            completed += 1

    for tp_value in tiers:
        rankings[tp_value].sort(
            key=lambda row: (-row["damage"], row["ws_name"].casefold())
        )
        for rank, row in enumerate(rankings[tp_value], start=1):
            row["rank"] = rank
    if not any(rankings.values()):
        detail = errors[0]["error"] if errors else "No usable result was returned."
        raise ValueError(f"No weapon skill could be ranked: {detail}")
    notify(
        f"WS ranking complete: {completed - len(errors)}/{total} optimized "
        f"successfully."
    )
    return {"tp_values": tiers, "rankings": rankings, "errors": errors}

if __name__ == "__main__":

    if len(sys.argv) > 1:
        main_job = sys.argv[1]
    else:
        main_job = "nin"

    if len(sys.argv) > 2:
        sub_job = sys.argv[2]
    else:
        sub_job = "war"

    if len(sys.argv) > 3:
        master_level = int(sys.argv[3])
    else:
        master_level = 50

    buffs = {}
    abilities = {}
    enemy = create_enemy(preset_enemies["Apex Toad"])
    enemy.stats["Base Defense"] = preset_enemies["Apex Toad"]["Defense"]
    enemy.stats["Magic Defense"] = max(-50, enemy.stats.get("Magic Defense", 0))
    ws_name = "Blade: Metsu"
    spell_name = "Waterja"
    action_type = "weapon skill"
    min_tp = 1000
    check_gear = gear_dict
    starting_gearset = { "main" : Heishi,
                        'sub' : Crepuscular_Knife,
                        'ranged' : Empty,
                        'ammo' : Seki,
                        'head' : Malignance_Chapeau,
                        'body' : Tatenashi_Haramaki,
                        'hands' : Malignance_Gloves,
                        'legs' : Samnuha_Tights,
                        'feet' : Malignance_Boots,
                        'neck' : Ninja_Nodowa,
                        'waist' : Sailfi_Belt,
                        'ear1' : Dedition_Earring,
                        'ear2' : Telos_Earring,
                        'ring1' : Gere_Ring,
                        'ring2' : Epona_Ring,
                        'back' : np.random.choice([k for k in capes if "nin" in k["Jobs"] and "DEX Store TP" in k["Name2"] and "Ranged" not in k])}
    pdt_requirement = -50
    mdt_requirement = -21
    print_swaps = True
    next_best_percent = 1

    metric = "Damage Dealt"

    player, output = build_set(main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, spell_name, action_type, min_tp, check_gear, starting_gearset, pdt_requirement, mdt_requirement, metric, print_swaps, next_best_percent)
    print(player.stats)


    # TODO: If hit rate is < 20% in initial set, then begin by finding and equipping the max accuracy piece in each slot before finding the best set.
