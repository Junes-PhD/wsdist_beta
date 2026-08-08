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
import threading
import time
sys.path.append(os.path.dirname(sys.executable))
from gear import *

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

def _prepare_candidates(check_gear, main_job, ws_type):
    """Copy and discard candidates the current main job cannot equip."""
    candidates = {
        slot: [item for item in items if main_job in item.get("Jobs", ())]
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


def build_set(main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, spell_name, action_type, min_tp, check_gear, starting_gearset, pdt_requirement, mdt_requirement, input_metric, print_swaps, next_best_percent, *, seed=None, n_iter=10, return_details=False, progress_callback=None):
    #
    # Build a valid gear set, test it, and return the best set found.
    #
    # action_type = "ranged attack", "weapon skill", "tp round", "spell cast"
    #
    fitn = 2

    main_job = main_job.lower()
    sub_job = sub_job.lower()
    verbose_swaps = abilities.get("Verbose Swaps", False)
    damage_taken_item_cache = {}
    rng = np.random.default_rng(seed) if seed is not None else np.random

    def report_progress(message):
        if progress_callback is not None:
            progress_callback(message)

    report_progress(f"Search started (seed {seed if seed is not None else 'random'}).")
    # Keep caller-owned selections stable and remove impossible candidates once,
    # before the hot loop. Formula evaluation is unchanged for every valid set.
    check_gear = {slot: list(items) for slot, items in check_gear.items()}
    starting_gearset = starting_gearset.copy()

    def duplicate_allowed(item):
        count = item.get("Accessible Count")
        return item.get("Name", "Empty") == "Empty" or count is None or int(count) >= 2

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

    # Rather than start with an empty slot, randomly build a set from the selected gear so we likely start with some accuracy+ and avoid getting stuck.
    # Do not adjust slots that are not being checked.
    for slot in starting_gearset:

        # Unequip gear you can't wear if it's already equipped, even if the slot is "frozen"
        if main_job not in starting_gearset[slot]["Jobs"]:
            starting_gearset[slot] = Empty

        frozen_slot = (len(check_gear[slot]) == 0)
        if not frozen_slot:
            starting_gearset[slot] = rng.choice(check_gear[slot])
            
            # Avoid wearing two rare items in initial gearset to prevent "unphysical" sets.
            if slot == "ring2" and (starting_gearset["ring1"]["Name2"] == starting_gearset["ring2"]["Name2"]
                                     and not duplicate_allowed(starting_gearset["ring2"])):
                starting_gearset["ring2"] = Empty
            if slot == "ear2" and (starting_gearset["ear1"]["Name2"] == starting_gearset["ear2"]["Name2"]
                                    and not duplicate_allowed(starting_gearset["ear2"])):
                starting_gearset["ear2"] = Empty


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

    conditional_converge_count = 0 # Break out of the loop if converged.
    pdt_old = 200 # Used to check if the automatic set finder gets stuck trying to find a set that doesn't exist. Compare this value to the old value. If no change in 3 consecutive iterations, then break out.
    mdt_old = 200

    pdt_thresh = pdt_requirement # How much PDT the final set is aiming for, taken from the user input.
    mdt_thresh = mdt_requirement

    pdt_thresh_temp = 200 # How much PDT the current new set must have to be accepted. The starting values are high to ensure that the code enters the loop to begin with.
    mdt_thresh_temp = 200
    best_output = None
    best_metric = None


    while pdt > pdt_thresh or mdt > mdt_thresh:
        # print(f"\nChecking conditions: PDT:{pdt_thresh_temp},  MDT:{mdt_thresh_temp}")

        for z in range(n_iter):
            print(f"Current iteration: {z+1}")
            report_progress(f"Iteration {z + 1}/{n_iter}.")
            
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

            if base_pdt <= pdt_thresh_temp and base_mdt <= mdt_thresh_temp:
                base_player = create_player(main_job, sub_job, master_level, converged_set, buffs, abilities)
                if action_type == "weapon skill":
                    decimals = 1
                    nondecimals = 8
                    metric_base, best_output = average_ws(base_player, enemy, ws_name, min_tp, ws_type, input_metric)
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
                for slot2 in check_slots[i1:]:
                    
                    # Only check single item swaps if fitn==1
                    if fitn==1:
                        if slot2 != slot1:
                            continue

                    test_set = converged_set.copy()
                    
                    if slot1 == slot2:
                        item_pairs = ((item, item) for item in check_gear[slot1])
                    else:
                        item_pairs = product(check_gear[slot1], check_gear[slot2])

                    for item1, item2 in item_pairs:

                            if (item1==converged_set[slot1]) or (item2==converged_set[slot2]): # Do not retest the baseline set.
                                continue

                            # Equip the items and check that the test_set is valid.
                            test_set[slot1] = item1
                            test_set[slot2] = item2


                            if (test_set["ring1"]==test_set["ring2"]) and (test_set["ring1"]["Name"]!="Empty") and not duplicate_allowed(test_set["ring1"]):
                                continue
                            if (test_set["ear1"]==test_set["ear2"]) and (test_set["ear1"]["Name"]!="Empty") and not duplicate_allowed(test_set["ear1"]):
                                continue
                            if (test_set["main"]==test_set["sub"]) and (test_set["main"]["Name"]!="Empty") and not duplicate_allowed(test_set["main"]):
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

                            if (action_type=="weapon skill") and (ws_name in archery_ws + marksmanship_ws):
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

                            if action_type == "weapon skill":
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
                            changed_items = {slot1: item1, slot2: item2}
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
                            if pdt > pdt_thresh_temp or mdt > mdt_thresh_temp:
                                continue
                            found_feasible_neighbor = True

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

                            else:
                                print(f"Unknown action_type  ({action_type})")
                                import sys; sys.exit()

                            metric = 0.0001 if metric <= 0 else metric # Prevent divide-by-zero errors
                            if (metric > best_metric):
                                if item1==item2:
                                    print(f"[{slot1:<15s}]: [{best_set[slot1]['Name2']} ->  {item1['Name2']}   [{best_metric**invert:>{nondecimals}.{decimals}f} -> {metric**invert:>{nondecimals}.{decimals}f}]") if verbose_swaps else None
                                else:
                                    print(f"[{slot1:<6s} & {slot2:<6s}]: [{best_set[slot1]['Name2']} & {best_set[slot2]['Name2']}] -> [{item1['Name2']} & {item2['Name2']}] [{best_metric**invert:>{nondecimals}.{decimals}f} -> {metric**invert:>{nondecimals}.{decimals}f}]") if verbose_swaps else None
                                best_set = test_set.copy()
                                best_metric = metric
                                best_output = output
    
                            elif (item1==item2):
                                relative_difference = (best_metric - metric) / best_metric
                                if (relative_difference <= float(next_best_percent) / 100
                                        and slot1 not in ["main", "sub", "ranged", "back"]):
                                    swaps[slot1].append([item1["Name2"], metric**invert, relative_difference])

            if (base_pdt > pdt_thresh_temp or base_mdt > mdt_thresh_temp) and not found_feasible_neighbor:
                raise ValueError(
                    "Optimizer could not reach the requested PDT/MDT requirements from the current search neighborhood. "
                    "Try more selected defensive gear or additional restarts."
                )

            if best_set==converged_set: # If no improvement is found after one full iteration.
                # best_player = create_player(main_job, sub_job, master_level, best_set, buffs, abilities)
                # for k in best_player.gearset:
                #     print(k,best_player.gearset[k]["Name2"])
                # print(best_output)
                break # Break out of the main loop and check PDT/MDT conditions.


        if best_output is None:
            raise ValueError("No valid gear set satisfies the current PDT/MDT requirements.")

        pdt, mdt = calculate_damage_taken(best_set, buffs, abilities, damage_taken_item_cache)


        # Compare the pdt and mdt values from this iteration with the previous iteration.
        if pdt == pdt_old and mdt == mdt_old:
            conditional_converge_count += 1
            if conditional_converge_count >= 3:
                print("Unable to find a set which satisfies the conditions better than the current set. Exiting.")
                break
        else:
            conditional_converge_count = 0

        # Save the PDT and MDT values from this iteration to compare with the next iteration.
        pdt_old = pdt
        mdt_old = mdt

        # Update the temporary PDT and MDT requirements so that the next set is slightly closer to the true requirements.
        pdt_thresh_temp = pdt - 1 if pdt-1 > pdt_thresh else pdt_thresh
        mdt_thresh_temp = mdt - 1 if mdt-1 > mdt_thresh else mdt_thresh
        
        print(f"Current best set: PDT:{pdt},  MDT:{mdt}")
        report_progress(f"Current best PDT:{pdt:g}, MDT:{mdt:g}.")


    if pdt > pdt_thresh or mdt > mdt_thresh:
        raise ValueError("Optimizer could not find a set satisfying the requested PDT/MDT requirements.")

    # At this point, we've found the best conditional set.

    # Swap the earrings to make sure the "Right Ear:" effect earrings show up in the ear2 slot.
    if best_set["ear1"]["Name"] in jse_ears+["Balder Earring +1"]:
        best_set["ear1"],best_set["ear2"] = best_set["ear2"],best_set["ear1"]

    # Record the stats for the best gear set.
    best_player = create_player(main_job, sub_job, master_level, best_set, buffs, abilities)


    header = {"weapon skill":ws_name,"spell cast":spell_name,"attack round":"Melee TP set"}[action_type]
    # Print a fancy output.
    print("==============================================================")
    print(f"Best   \"{input_metric}\"   \"{header}\"   set")
    print("==============================================================")
    for k in best_player.gearset:
        print(f"{k:>10s}  {best_player.gearset[k]['Name2']:<50s}")
    print()
    if action_type=="attack round":
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

    if return_details:
        report_progress("Search completed.")
        return best_player, best_output, best_metric
    report_progress("Search completed.")
    return(best_player, best_output)


def _build_set_restart_worker(request, progress_callback=None):
    """Run one independent optimizer restart in a process-safe top-level worker."""
    output_buffer = StringIO()
    try:
        build_kwargs = request["kwargs"].copy()
        if progress_callback is not None:
            build_kwargs["progress_callback"] = progress_callback
        with redirect_stdout(output_buffer):
            player, output, metric = build_set(*request["args"], **build_kwargs)
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


def optimize_set(main_job, sub_job, master_level, buffs, abilities, enemy, ws_name, spell_name, action_type, min_tp, check_gear, starting_gearset, pdt_requirement, mdt_requirement, input_metric, print_swaps, next_best_percent, *, restarts=1, workers=0, seed=None, n_iter=10, return_details=False, progress_callback=None):
    """Run independent seeded searches and return the best valid result.

    ``workers=0`` selects up to one process per restart while leaving one CPU
    core free. A single restart runs in-process, preserving the prior UI path.
    """
    restarts = max(1, int(restarts))
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
            "kwargs": {"seed": restart_seed, "n_iter": n_iter, "return_details": True},
            "seed": restart_seed,
            "index": index,
        }
        for index, restart_seed in enumerate(restart_seeds, start=1)
    ]

    def notify(message):
        if progress_callback is not None:
            progress_callback(message)

    def run_serial(request):
        notify(f"Restart {request['index']}/{restarts} started (seed {request['seed']}).")
        callback = lambda message: notify(f"Restart {request['index']}: {message}")
        stop_heartbeat = threading.Event()
        started_at = time.monotonic()

        def heartbeat():
            while not stop_heartbeat.wait(5.0):
                elapsed = time.monotonic() - started_at
                notify(
                    f"Restart {request['index']}/{restarts} active "
                    f"({elapsed:.0f}s elapsed; search is still progressing)."
                )

        heartbeat_thread = threading.Thread(
            target=heartbeat, name=f"optimizer-heartbeat-{request['index']}", daemon=True
        )
        heartbeat_thread.start()
        try:
            result = _build_set_restart_worker(request, callback)
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1.0)
        if "error" in result:
            notify(f"Restart {request['index']} failed: {result['error']}")
        else:
            notify(f"Restart {request['index']}/{restarts} completed.")
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
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                for request in requests:
                    notify(f"Restart {request['index']}/{restarts} started (seed {request['seed']}).")
                futures = {
                    executor.submit(_build_set_restart_worker, request): request
                    for request in requests
                }
                results = []
                pending = set(futures)
                started_at = {future: time.monotonic() for future in pending}
                while pending:
                    done, pending = wait(pending, timeout=5.0)
                    if not done:
                        now = time.monotonic()
                        for future in sorted(pending, key=lambda value: futures[value]["index"]):
                            request = futures[future]
                            elapsed = now - started_at[future]
                            notify(
                                f"Restart {request['index']}/{restarts} active "
                                f"({elapsed:.0f}s elapsed; worker is still progressing)."
                            )
                    for future in done:
                        request = futures[future]
                        result = future.result()
                        results.append(result)
                        if "error" in result:
                            notify(f"Restart {request['index']} failed: {result['error']}")
                        else:
                            notify(f"Restart {request['index']}/{restarts} completed.")

    results.sort(key=lambda result: result["index"])

    successful_results = [result for result in results if "error" not in result]
    if not successful_results:
        errors = "; ".join(
            f"seed {result['seed']}: {result['error']}" for result in results
        )
        raise ValueError(f"All optimizer restarts failed. {errors}")

    winner = max(successful_results, key=lambda result: result["metric"])
    print(winner["log"], end="")
    if restarts > 1:
        notify(
            f"Selected restart {winner['index']}/{restarts} "
            f"(seed {winner['seed']}; metric {winner['metric']:.6f})."
        )
        print(
            f"Selected restart {winner['index']}/{restarts} "
            f"(seed {winner['seed']}; metric {winner['metric']:.6f})."
        )
    if return_details:
        return winner["player"], winner["output"], winner["metric"], winner["seed"]
    return winner["player"], winner["output"]

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
