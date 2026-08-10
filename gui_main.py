'''
File containing code to build the GUI

    TODO:
        Finish Automaton Tab (merge with quicklook frame?)
        Add tooltips throughout.
    
Author: Kastra (Asura server)
'''

import numpy as np
import os, sys
import multiprocessing
import queue
import threading
sys.path.append(os.path.dirname(sys.executable))

import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox, simpledialog
from PIL import Image, ImageTk

from idlelib.tooltip import Hovertip # https://stackoverflow.com/questions/3221956/how-do-i-display-tooltips-in-tkinter

import pickle

import importlib


def resource_path(*parts):
    """Return a path to a source-tree or PyInstaller-bundled resource."""
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, *parts)

# Import other code related to this project.
import enemies as enemies_pyfile
import gear as gear_pyfile
import create_player as create_player_pyfile
import actions as actions_pyfile
import buffs as buffs_pyfile
import wsdist as wsdist_pyfile
from wsdist_bridge import BridgeStore
from lac_profile import write_reload_request, write_set
import fancy_plot as fancy_plot_pyfile
from lumo_scrollablelabelframe import ScrollableLabelFrame # TODO: Replace with ChatGPT's virtual_frames
from gpt_manage_defaults import *

from virtual_frames import VirtualCheckboxFrame, VirtualRadioFrame

class application(tk.Tk):

    def _set_optimizer_busy(self, busy):
        state = "disabled" if busy else "normal"
        for button in getattr(self, "optimizer_action_buttons", []):
            button.configure(state=state)
        self.config(cursor="watch" if busy else "")
        self.update_idletasks()

    def _append_optimizer_status(self, message):
        status_text = getattr(self, "optimizer_status_text", None)
        if status_text is None:
            return
        status_text.configure(state="normal")
        status_text.insert("end", f"{message}\n")
        status_text.see("end")
        status_text.configure(state="disabled")

    def _start_optimizer(self, optimizer_args, optimizer_kwargs):
        """Run the CPU-bound optimizer away from Tk's event loop."""
        if getattr(self, "_optimizer_running", False):
            return

        self._optimizer_running = True
        self._optimizer_results = queue.Queue()
        self._optimizer_events = queue.Queue()
        if self.optimizer_status_text is not None:
            self.optimizer_status_text.configure(state="normal")
            self.optimizer_status_text.delete("1.0", "end")
            self.optimizer_status_text.configure(state="disabled")
        optimizer_kwargs = optimizer_kwargs.copy()
        optimizer_kwargs["progress_callback"] = self._optimizer_events.put
        self._append_optimizer_status("Starting optimizer...")
        self._set_optimizer_busy(True)

        def run_optimizer():
            try:
                result = wsdist_pyfile.optimize_set(*optimizer_args, **optimizer_kwargs)
            except Exception as error:
                self._optimizer_results.put(("error", error))
            else:
                self._optimizer_results.put(("success", result))

        self._optimizer_thread = threading.Thread(
            target=run_optimizer, name="wsdist-optimizer", daemon=True
        )
        self._optimizer_thread.start()
        self.after(100, self._poll_optimizer)

    def _poll_optimizer(self):
        while True:
            try:
                self._append_optimizer_status(self._optimizer_events.get_nowait())
            except queue.Empty:
                break
        try:
            status, result = self._optimizer_results.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_optimizer)
            return

        self._optimizer_running = False
        self._set_optimizer_busy(False)
        if status == "error":
            self._append_optimizer_status(f"Optimizer failed: {result}")
            messagebox.showerror("WSDist optimizer", str(result))
            return

        self.best_player, _, _, winning_seed = result
        self._append_optimizer_status(f"Completed. Winning seed: {winning_seed}.")
        print(f"Optimizer completed with winning seed {winning_seed}.")
        self.equip_best_set_button.configure(state="active")

    def _bridge_character_label(self, data):
        character = data.get("character", {})
        return f"{character.get('name', '?')} ({character.get('key', '?')})"

    def _install_bridge_items(self):
        if not self.bridge_store.data:
            return
        character = self.bridge_store.data.get("character", {})
        character_name = character.get("name") or character.get("key") or "unknown"
        eligible_count = sum(1 for item in self.bridge_store.catalog.values() if item.get("Eligible"))
        self.bridge_status_value.set(
            f"WSDist bridge: {character_name} | {len(self.bridge_store.catalog)} variants | "
            f"{eligible_count} modeled/accessible candidates"
        )
        # Keep the original model as a fallback for food and profile-only gear,
        # while making the character-scoped records available to every existing
        # picker and simulator path that uses gear_pyfile.all_gear.
        gear_pyfile.all_gear.update(self.bridge_store.catalog)
        self.all_equipment_dict = self.bridge_store.equipment_dict()
        for slot, items in self.all_equipment_dict.items():
            names = sorted({item.get("Name2", item.get("Name", "Empty")) for item in items})
            for frames in (getattr(self, "quicklook_scrollframes", {}),
                           getattr(self, "tp_quicklook_scrollframes", {}),
                           getattr(self, "ws_quicklook_scrollframes", {}),
                           getattr(self, "optimize_scrollframes", {})):
                if slot in frames:
                    frames[slot].master_data = names
                    if hasattr(frames[slot], "selection_state"):
                        old_selection = frames[slot].selection_state
                        frames[slot].selection_state = {name: old_selection.get(name, False) for name in names}
                    frames[slot].set_visible_data(names)
        self.update_job("main static")
        self.update_job("sub")

    def choose_wsdist_bridge(self):
        directory = filedialog.askdirectory(title="Select Ashita installation directory")
        if not directory:
            return
        self.bridge_store.set_root(directory)
        paths = self.bridge_store.discover()
        if not paths:
            messagebox.showwarning("WSDist", "No GearSetBuilder bridge files were found under this Ashita installation.")
            return
        if len(paths) == 1:
            selected = paths[0]
        else:
            selected = filedialog.askopenfilename(title="Select character bridge", initialdir=str(paths[0].parent),
                                                  filetypes=[("WSDist bridge", "wsdist_bridge.json")])
            if not selected:
                return
        try:
            data = self.bridge_store.load(selected)
            self._install_bridge_items()
            messagebox.showinfo("WSDist", f"Loaded {self._bridge_character_label(data)} with "
                                f"{len(self.bridge_store.catalog)} owned item variants.")
        except Exception as error:
            messagebox.showerror("WSDist bridge", str(error))

    def refresh_wsdist_bridge(self):
        if self.bridge_store.bridge_path is None:
            return self.choose_wsdist_bridge()
        try:
            data = self.bridge_store.load(self.bridge_store.bridge_path)
            self._install_bridge_items()
            messagebox.showinfo("WSDist", f"Refreshed {self._bridge_character_label(data)}.")
        except Exception as error:
            messagebox.showerror("WSDist bridge", str(error))

    def _profile_set_choices(self):
        choices = []
        for profile in self.bridge_store.profile_records():
            for profile_set in profile.get("sets", []):
                choices.append((str(profile.get("job", "")), profile_set))
        return choices

    def import_luashitacast_set(self):
        if not self.bridge_store.data:
            messagebox.showwarning("WSDist", "Load a WSDist bridge first.")
            return
        choices = self._profile_set_choices()
        if not choices:
            messagebox.showwarning("WSDist", "The selected character has no readable job sets.")
            return
        window = tk.Toplevel(self)
        window.title("Import LuAshitacast set")
        window.transient(self)
        jobs = sorted({job for job, _ in choices})
        job_value = tk.StringVar(value=jobs[0])
        set_value = tk.StringVar()
        destination_value = tk.StringVar(value="Quick Look")
        ttk.Label(window, text="Job:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        job_combo = ttk.Combobox(window, textvariable=job_value, values=jobs, state="readonly", width=12)
        job_combo.grid(row=0, column=1, padx=6, pady=6)
        ttk.Label(window, text="Set:").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        set_combo = ttk.Combobox(window, textvariable=set_value, state="readonly", width=32)
        set_combo.grid(row=1, column=1, padx=6, pady=6)
        ttk.Label(window, text="Destination:").grid(row=2, column=0, padx=6, pady=6, sticky="w")
        ttk.Combobox(window, textvariable=destination_value, values=["Quick Look", "TP", "WS"],
                     state="readonly", width=12).grid(row=2, column=1, padx=6, pady=6)

        def update_sets(*_):
            names = [profile_set.get("name", "") for job, profile_set in choices if job == job_value.get()]
            set_combo["values"] = names
            if names:
                set_value.set(names[0])
        job_combo.bind("<<ComboboxSelected>>", update_sets)
        update_sets()

        def import_selected():
            selected = next((item for job, item in choices if job == job_value.get() and item.get("name") == set_value.get()), None)
            if selected is None:
                return
            destination = {"Quick Look": (self.quicklook_equipped_dict, "quicklook"),
                           "TP": (self.tp_quicklook_equipped_dict, "tp"),
                           "WS": (self.ws_quicklook_equipped_dict, "ws")}[destination_value.get()]
            missing = []
            for lac_slot, profile_item in selected.get("slots", {}).items():
                if not profile_item:
                    continue
                slot = lac_slot.lower()
                if slot not in destination[0]:
                    continue
                item = self.bridge_store.resolve_profile_item(profile_item)
                if item is None or not item.get("Model Complete", True):
                    missing.append(lac_slot)
                    continue
                gear_pyfile.all_gear[item["Name2"]] = item
                self.update_quicklook_equipment((slot, item["Name2"], destination[1]))
            window.destroy()
            if missing:
                messagebox.showwarning("WSDist", "Imported with unresolved slots: " + ", ".join(missing))

        ttk.Button(window, text="Import", command=import_selected).grid(row=3, column=0, columnspan=2, pady=8)

    def save_luashitacast_set(self, source_name="quicklook"):
        if not self.bridge_store.data or self.bridge_store.bridge_path is None:
            messagebox.showwarning("WSDist", "Load a WSDist bridge first.")
            return
        source = {"quicklook": self.quicklook_equipped_dict, "tp": self.tp_quicklook_equipped_dict,
                  "ws": self.ws_quicklook_equipped_dict}[source_name]
        job = self.jobs_dict.get(self.main_job_value.get(), self.main_job_value.get()).upper()
        profile = next((item for item in self.bridge_store.profile_records() if str(item.get("job", "")).upper() == job), None)
        if profile is None:
            messagebox.showerror("WSDist", f"No character-specific {job}.lua profile was published.")
            return
        set_name = simpledialog.askstring("LuAshitacast set", f"Set name for {source_name.upper()} equipment:", parent=self)
        if not set_name:
            return
        existing = any(item.get("name") == set_name for item in profile.get("sets", []))
        if existing and not messagebox.askyesno("Overwrite set", f"Replace {set_name} in {job}.lua?", parent=self):
            return
        equipment = {slot: value.get("item") for slot, value in source.items()}
        try:
            path = self.bridge_store.profile_path(job)
            backup, new_hash = write_set(path, set_name, equipment,
                                         expected_hash=str(profile.get("source_hash", "")), overwrite=existing)
            profile["source_hash"] = new_hash
            request = {"schema_version": 1, "character_key": self.bridge_store.data.get("character", {}).get("key"),
                       "job": job, "profile": path.name, "profile_hash": new_hash, "set": set_name}
            write_reload_request(self.bridge_store.bridge_path.parent, request)
            messagebox.showinfo("WSDist", f"Saved {set_name} to {path.name}.\nBackup: {backup.name}\n"
                                "GearSetBuilder will request /lac load when this is the active job.")
        except Exception as error:
            messagebox.showerror("LuAshitacast write", str(error))

    def poll_wsdist_bridge(self):
        try:
            if self.bridge_store.bridge_path and self.bridge_store.bridge_path.exists():
                if self.bridge_store.bridge_path.stat().st_mtime > self.bridge_store.bridge_mtime() + 0.001:
                    if messagebox.askyesno("WSDist", "GearSetBuilder published newer character data. Reload it now?"):
                        self.refresh_wsdist_bridge()
        finally:
            self.after(1500, self.poll_wsdist_bridge)

    def reload_gear_pyfile(self,):
        '''
        Reloads gear.py and enemies.py to allow seeing live changes to them with the GUI open.
        '''
        importlib.reload(gear_pyfile)

        # Automatically re-equip gear to see changes immediately.
        for slot in self.equipment_button_positions:
            self.update_quicklook_equipment((slot, self.quicklook_equipped_dict[slot]["item"]["Name2"], self.quicklook_equipped_dict, "quicklook"))
            self.update_quicklook_equipment((slot, self.tp_quicklook_equipped_dict[slot]["item"]["Name2"], self.tp_quicklook_equipped_dict, "tp"))
            self.update_quicklook_equipment((slot, self.ws_quicklook_equipped_dict[slot]["item"]["Name2"], self.ws_quicklook_equipped_dict, "ws"))

        # Automatically reset main and subjobs to trigger refreshing the gear selection lists.
        self.update_job("main static")
        self.update_job("sub")

    def save_defaults(self,):
        '''
        When clicking the "Save Defaults" button.
        Save relevant GUI selections to an output pickle file to be read in later.
        The outfile file is a dictionary with keys being jobs and values being dictionaries containing the relevant GUI parameters to be loaded for each job.
        '''
        main_job = self.main_job_value.get()
        bridge_key = self.bridge_store.data.get("character", {}).get("key", "default") if hasattr(self, "bridge_store") else "default"
        profile_key = f"{bridge_key}|{main_job}"

        state = {}
        for w in walk_widgets(self): # Get a list of ALL widgets in the GUI
            
            key = getattr(w, "save_name", w.winfo_name()) # Use save_name attribute if present, otherwise use the widget's name
            if "defaults_" in key: # Only save widgets for items manually named "defaults_{stuff}"
                value = get_widget_state(w)
                state[key] = value # key and value are both string representations of the widget and its value.

        for slot in self.quicklook_equipped_dict:
            state[f"quicklook_{slot}_item"] = self.quicklook_equipped_dict[slot]["item"].get("Bridge Key", self.quicklook_equipped_dict[slot]["item"]["Name2"])
            state[f"tp_quicklook_{slot}_item"] = self.tp_quicklook_equipped_dict[slot]["item"].get("Bridge Key", self.tp_quicklook_equipped_dict[slot]["item"]["Name2"])
            state[f"ws_quicklook_{slot}_item"] = self.ws_quicklook_equipped_dict[slot]["item"].get("Bridge Key", self.ws_quicklook_equipped_dict[slot]["item"]["Name2"])

        self.states["default"] = state
        self.states[main_job] = state
        self.states[profile_key] = state

        with open("defaults.pkl", "wb") as f:
            pickle.dump(self.states, f)
        print(f"File updated: defaults.pkl (default, {main_job})")

    def _resolve_saved_item(self, value):
        if hasattr(self, "bridge_store") and value in self.bridge_store.by_key:
            item = self.bridge_store.by_key[value]
            gear_pyfile.all_gear[item["Name2"]] = item
            return item
        return gear_pyfile.all_gear[value]

    def load_defaults(self, type="default"):
        '''
        When clicking the "Load Defaults" button or when changing main jobs.
        Read the defaults.pkl file and load the state saved in it for the currently selected main job.
        '''
        with open("defaults.pkl", "rb") as f:
            self.states = pickle.load(f)
        try:
            main_job = self.main_job_value.get()

            bridge_key = self.bridge_store.data.get("character", {}).get("key", "default") if hasattr(self, "bridge_store") else "default"
            profile_key = f"{bridge_key}|{main_job}"
            selection = "default" if type=="default" else (profile_key if profile_key in self.states else main_job)
            if len(self.states[selection]) == 0:
                print(f"No defaults profile found for {selection}")
                return

            state = self.states[selection]

            # Check all of the widgets in the GUI and update those with saved states.
            for w in walk_widgets(self):
                key = getattr(w, "save_name", w.winfo_name())
                if key in state:
                    set_widget_state(w, state[key])

            # Build the quicklook equipment.
            for slot in self.quicklook_equipped_dict:
                try:
                    saved_item = self._resolve_saved_item(state[f"quicklook_{slot}_item"])
                    self.quicklook_equipped_dict[slot]["item"] = saved_item
                    self.quicklook_equipped_dict[slot]["icon"] = self.get_equipment_icon(saved_item["Name"])
                    self.quicklook_equipped_dict[slot]["button"].config(image = self.quicklook_equipped_dict[slot]["icon"])
                    self.quicklook_equipped_dict[slot]["button tooltip"].text = self.format_tooltip_stats(saved_item)
                    self.quicklook_equipped_dict[slot]["radio_variable"].set(saved_item["Name2"])
                except Exception as err:
                    print(err)

                try:
                    saved_item = self._resolve_saved_item(state[f"tp_quicklook_{slot}_item"])
                    self.tp_quicklook_equipped_dict[slot]["item"] = saved_item
                    self.tp_quicklook_equipped_dict[slot]["icon"] = self.get_equipment_icon(saved_item["Name"])
                    self.tp_quicklook_equipped_dict[slot]["button"].config(image = self.tp_quicklook_equipped_dict[slot]["icon"])
                    self.tp_quicklook_equipped_dict[slot]["button tooltip"].text = self.format_tooltip_stats(saved_item)
                    self.tp_quicklook_equipped_dict[slot]["radio_variable"].set(saved_item["Name2"])
                except Exception as err:
                    print(err)

                try:
                    saved_item = self._resolve_saved_item(state[f"ws_quicklook_{slot}_item"])
                    self.ws_quicklook_equipped_dict[slot]["item"] = saved_item
                    self.ws_quicklook_equipped_dict[slot]["icon"] = self.get_equipment_icon(saved_item["Name"])
                    self.ws_quicklook_equipped_dict[slot]["button"].config(image = self.ws_quicklook_equipped_dict[slot]["icon"])
                    self.ws_quicklook_equipped_dict[slot]["button tooltip"].text = self.format_tooltip_stats(saved_item)
                    self.ws_quicklook_equipped_dict[slot]["radio_variable"].set(saved_item["Name2"])
                except Exception as err:
                    print(err)
                    
            # Call the "update" functions to ensure everything is up to date.
            for input in ("main", "sub", "master_level"):
                if type != input: # Do not run update_job("main") if load_defaults was called from "main", since this would be an infinite loop.
                    self.update_job(input) 
            for input in [f"set {k}" for k in ["dia", "haste", "boost", "storm", "Indi-", "Geo-", "Entrust-", "food"]+[f"Song{i+1}" for i in range(4)]+[f"Roll{i+1}" for i in range(4)]]:
                self.update_buffs(input)
            for slot in self.equipment_button_positions:
                self.update_quicklook_equipment((slot, self.quicklook_equipped_dict[slot]["item"]["Name2"], "quicklook"))
                self.update_quicklook_equipment((slot, self.tp_quicklook_equipped_dict[slot]["item"]["Name2"], "tp"))
                self.update_quicklook_equipment((slot, self.ws_quicklook_equipped_dict[slot]["item"]["Name2"], "ws"))
            self.quicklook("show stats quicklook")
        except Exception as err:
            print(err)
            print(f"Failed to load default values for {selection}")
            return

    def format_tooltip_stats(self, item):
        '''
        Given a dictionary containing an item's stats, create a string to display with that item's icon as a tooltip.
        Returns a string.
        '''
        ignore_stats = ["Jobs","Name","Name2","Type","Skill Type","Rank"] # Do not include these stats in the tooltip
        wpn_stats = ["DMG","Delay"] # DMG and Delay show up first if available
        base_stats = ["STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR"] # Base parameters show up on their own line.
        main_stats = ["Accuracy","Attack","Ranged Accuracy","Ranged Attack","Magic Accuracy","Magic Damage","Magic Attack"]
        all_stats = ["Striking Crit Rate","Climactic Crit Damage","Klimaform Damage%","Ebullience Bonus","Occult Acumen","Futae Bonus","WSC","Zanshin OA2","Recycle","Double Shot Damage%","Triple Shot Damage%","Ranged Crit Damage","Blood Pact Damage","Rank", "Kick Attacks", "Kick Attacks DMG", "Martial Arts", "Sneak Attack Bonus", "Trick Attack Bonus", "Double Shot", "True Shot","Zanshin", "Hasso", "Quick Draw Damage", "Quick Draw Magic Accuracy", "Quick Draw Damage%", "Triple Shot","Magic Crit Rate II","Magic Burst Accuracy","Fencer","JA Haste","Accuracy", "AGI", "Attack", "Axe Skill", "CHR", "Club Skill", "Crit Damage", "Crit Rate", "DA", "DA Damage%", "Dagger Skill", "Daken", "Dark Affinity", "Dark Elemental Bonus", "Delay", "DEX", "DMG", "Dual Wield", "Earth Affinity", "Earth Elemental Bonus", "Elemental Bonus", "Elemental Magic Skill", "Fire Affinity", "Fire Elemental Bonus", "ftp", "Gear Haste", "Great Axe Skill", "Great Katana Skill", "Great Sword Skill", "Hand-to-Hand Skill", "Ice Affinity", "Ice Elemental Bonus", "INT", "Jobs", "Katana Skill", "Light Affinity", "Light Elemental Bonus", "Magic Accuracy Skill", "Magic Accuracy", "Magic Attack", "Magic Burst Damage II", "Magic Burst Damage", "Magic Damage", "MND", "Name", "Name2", "Ninjutsu Damage%", "Ninjutsu Magic Attack","Ninjutsu Magic Accuracy", "Ninjutsu Skill", "OA2", "OA3", "OA4", "OA5", "OA6", "OA7", "OA8", "PDL", "Polearm Skill", "QA", "Ranged Accuracy", "Ranged Attack", "Scythe Skill", "Skill Type", "Skillchain Bonus", "Staff Skill", "Store TP", "STR", "Sword Skill", "TA", "TA Damage%", "Throwing Skill", "Thunder Affinity", "Thunder Elemental Bonus", "TP Bonus", "Type", "VIT", "Water Affinity", "Water Elemental Bonus", "Weapon Skill Accuracy", "Weapon Skill Damage", "Weather", "Wind Affinity", "Wind Elemental Bonus","Polearm Skill","Marksmanship Skill","Archery Skill"]
        if "Double Damage" not in all_stats:
            all_stats.append("Double Damage")
        def_stats = ["Evasion","Magic Evasion", "Magic Defense","DT","MDT","PDT","MDT2","PDT2","Subtle Blow","Subtle Blow II",]

        tooltip = f"{item['Name2' if 'Name2' in item else 'Name']}\n" # Start with the item's unique name

        nl = False # nl = NL = New Line: insert a new line to force a line break
        for k in wpn_stats:
            if item.get(k,False):
                tooltip += f"{k}:{item[k]},"
                nl = True
            if k=="Delay" and nl:
                tooltip += "\n"

        nl = False
        for k in base_stats:
            if item.get(k,False):
                tooltip += f"{k}:{item[k]},"
                nl = True
            if nl and k=="CHR":
                tooltip += "\n"

        nl = False
        for k in main_stats:
            if item.get(k,False):
                tooltip += f"{k}:{item[k]},"
                nl = True
            if "Attack" in k and nl:
                tooltip += "\n"
                nl = False
        for k in item:
            if k in base_stats or k in ignore_stats or k in main_stats or k in wpn_stats or k in def_stats:
                continue
            tooltip += f"{k}:{item[k]}\n"

        nl = False
        for k in def_stats:
            if item.get(k,False):
                tooltip += f"{k}:{item[k]},"
                nl = True
            if "Def" in k and nl:
                tooltip += "\n"
                nl = False

        return tooltip.strip()

    def get_equipment_icon(self, item_name="Empty"):
        try:
            if isinstance(item_name, dict):
                item_id = item_name.get("Item ID", item_name.get("item_id", 0))
                item_name = item_name.get("Name", "Empty")
            else:
                dynamic = gear_pyfile.all_gear.get(item_name, {})
                item_id = dynamic.get("Item ID", 0)
                if not item_id:
                    dynamic = next((value for value in gear_pyfile.all_gear.values()
                                    if value.get("Name", "").lower() == str(item_name).lower()), {})
                    item_id = dynamic.get("Item ID", 0)
            if not item_id:
                item_id = self.item_id_dict["id"][self.item_id_dict["name"]==item_name.lower()][0]

            bundled_icon = resource_path("icons32", f"{item_id}.png")
            if os.path.isfile(bundled_icon):
                icon = tk.PhotoImage(file=bundled_icon)
            else:
                icon = self._load_equipviewer_icon(item_id)
                if icon is None:
                    raise FileNotFoundError(f"No bundled or EquipViewer icon for item {item_id}")
        except Exception as err:
            print(f"Missing icon image file for \"{item_name}\"")
            # print(err)
            icon = self.get_equipment_icon(np.random.choice(["fire", "earth", "water", "wind", "ice", "thunder", "light", "dark"]) + " attachment")
        return icon

    def _load_equipviewer_icon(self, item_id):
        """Load an icon generated by Windower's EquipViewer addon, if present."""
        icon_dirs = []
        configured_dir = os.environ.get("WSDIST_EQUIPVIEWER_ICONS")
        if configured_dir:
            icon_dirs.append(configured_dir)

        # GearSetBuilder exports Ashita resource icons beside the selected
        # character bridge. This keeps the Python app independent of the
        # Ashita installation's absolute path.
        bridge_path = getattr(getattr(self, "bridge_store", None), "bridge_path", None)
        if bridge_path is not None:
            icon_dirs.append(os.path.join(os.path.dirname(str(bridge_path)), "icons32"))

        app_dir = os.path.dirname(os.path.abspath(__file__))
        icon_dirs.extend([
            resource_path("icons32"),
            os.path.join(app_dir, "equipviewer", "icons"),
            os.path.join(app_dir, "icons"),
            # Development checkout layout: GitHub/Windower/Lua/addons/equipviewer/icons.
            os.path.abspath(os.path.join(
                app_dir, "..", "..", "..", "Windower", "Lua", "addons", "equipviewer", "icons"
            )),
        ])

        for icon_dir in dict.fromkeys(icon_dirs):
            for extension in (".bmp", ".png"):
                icon_path = os.path.join(icon_dir, f"{item_id}{extension}")
                if not os.path.isfile(icon_path):
                    continue
                try:
                    with Image.open(icon_path) as image:
                        return ImageTk.PhotoImage(image.convert("RGBA"))
                except Exception:
                    continue
        return None

    def validate_numerical(self, user_input): # TODO: Different Validate functions for different entry locations (TP input, vs enemy stats, vs enemy MDT, etc)
        '''
        Only allow numerical integers up to four digits to be entered.
        Used to validate user input for TP and enemy stats
        '''
        if len(user_input) > 4:
            return False
        if user_input in ["", "-"] or user_input.isdigit() or (user_input[1:].isdigit() and user_input[0]=="-"):
            return True
        return False

    def validate_tp_value(self, event=None):
        '''
        After the user clicks off the TP entry box, force the final value to be between 1000 and 3000.
        If the box is empty, use 1000 by default.
        '''
        try:
            tp_value = int(self.tp_entry_box.get())
            if tp_value < 1000:
                self.tp_entry_value.set(1000)
            elif tp_value > 3000:
                self.tp_entry_value.set(3000)
        except Exception as err:
            self.tp_entry_value.set(1000)

    def select_enemy(self,):
        '''
        When selecting a new enemy from the enemy input combobox
        Loop through the stats and update the user-input values based on the preset_enemies dictionaries from enemies.py
        '''
        self.selected_enemy = enemies_pyfile.preset_enemies[self.selected_enemy_name.get()] 
        for i,stat in enumerate(self.enemy_stats_list1 + self.enemy_stats_list2):
            self.enemy_input_obj[stat].delete(0, tk.END)
            self.enemy_input_obj[stat].insert(0, self.selected_enemy[stat])

        self.enemy_level_location_label.config(text=f"{self.selected_enemy['Location']} (Lv.{self.selected_enemy['Level']})")

    def update_job(self, trigger):
        '''
        When selecting a new main or sub job
        Update the available spell list, special ability list, and equipment lists.
        Enact various other restrictions throughout the GUI based on job selections.
        '''

        main_job_shorthand = self.jobs_dict[self.main_job_value.get()]
        sub_job_shorthand = self.jobs_dict.get(self.sub_job_value.get(), "None")
        dual_wield = (main_job_shorthand in ["nin", "dnc", "thf", "blu"] or sub_job_shorthand in ["nin", "dnc"])

        if trigger=="master level":
            self.sub_job_level = tk.IntVar(value=49 + self.master_level_value.get()//5)

        elif "main" in trigger.lower():

            # Update the spell list options.
            if main_job_shorthand in self.spells_dict:
                new_spell_list = self.spells_dict[main_job_shorthand]
                self.spell_selection_combobox.config(values=new_spell_list)
                if self.spell_selection_value.get() not in new_spell_list:
                    self.spell_selection_value.set(new_spell_list[0])
            else:
                self.spell_selection_combobox.config(values=["None"])
                self.spell_selection_value.set("None")

            # Update the subjob selections
            if self.main_job_value.get() == self.sub_job_value.get():
                # Remove the subjob if the new main job selection matches the current sub job.
                self.sub_job_value.set("None")

            # Hide the newly selected main job from the sub job list.
            new_subjob_options = [k for k in sorted(self.jobs_dict) if k != self.main_job_value.get()] + ["None"]
            self.sub_job_selection_combobox.config(values=new_subjob_options)

            # Update the virtual scrollframes to show radiobuttons and checkbuttons for items equippable by the selected job.
            for slot in self.all_equipment_dict:
                if slot == "sub":
                    allowed_subtypes = ["Shield", "Grip", "None"]
                    if dual_wield:
                        allowed_subtypes += ["Weapon"]
                    filtered_equipment_list = sorted([k["Name2" if "Name2" in k else "Name"] for k in self.all_equipment_dict["sub"] if (main_job_shorthand in k["Jobs"]) and (k["Type"] in allowed_subtypes)])
                else:
                    filtered_equipment_list = sorted([k["Name2" if "Name2" in k else "Name"] for k in self.all_equipment_dict[slot] if main_job_shorthand.lower() in k["Jobs"]])
                self.quicklook_scrollframes[slot].set_visible_data(filtered_equipment_list)
                self.tp_quicklook_scrollframes[slot].set_visible_data(filtered_equipment_list)
                self.ws_quicklook_scrollframes[slot].set_visible_data(filtered_equipment_list)
                self.optimize_scrollframes[slot].set_visible_data(filtered_equipment_list)
                self.optimize_scrollframes[slot].deselect("all")


            # Red Mage uses Boost-STAT instead of Gain-STAT. Update the corresponding buff drop-down menu and selection here.
            old_boost_stat = "None" if self.boost_value.get()=="None" else self.boost_value.get().split("-")[-1]
            
            if main_job_shorthand.lower() == "rdm":
                if old_boost_stat != "None":
                    self.boost_value.set(value=f"Gain-{old_boost_stat}")
                self.boost_combobox.config(values=["Gain-"+k for k in ["STR", "DEX", "VIT", "AGI", "MND", "INT", "CHR"]] + ["None"])
                self.enhancing_skill_label.config(text="Enhancing Skill:")
                self.enhancing_skill_value.set(value=650)

            else:
                if old_boost_stat != "None":
                    self.boost_value.set(value=f"Boost-{old_boost_stat}")
                self.boost_combobox.config(values=["Boost-"+k for k in ["STR", "DEX", "VIT", "AGI", "MND", "INT", "CHR"]] + ["None"])
                
                if main_job_shorthand.lower() == "run":
                    self.enhancing_skill_value.set(value=570)
                elif main_job_shorthand.lower() == "drk":
                    self.enhancing_skill_value.set(value=600)
                    self.enhancing_skill_label.config(text="Dark Skill:")
                elif main_job_shorthand.lower() == "pld":
                    self.enhancing_skill_value.set(value=600)
                    self.enhancing_skill_label.config(text="Divine Skill:")
                else:
                    self.enhancing_skill_label.config(text="Enhancing Skill:")
                    self.enhancing_skill_value.set(value=500)
        
            if os.path.isfile("defaults.pkl") and "static" not in trigger:
                self.load_defaults("main") # Update GUI to reflect the new main job
            
        elif trigger=="sub":
            # Hide weapons in off-hand slot unless new main+sub combo allows dual wielding.
            allowed_subtypes = ["Shield", "Grip", "None"]
            if dual_wield:
                allowed_subtypes += ["Weapon"]
            else:
                restricted_items = [k["Name2" if "Name2" in k else "Name"] for k in self.all_equipment_dict["sub"] if (main_job_shorthand in k["Jobs"]) and (k["Type"] == "Weapon")]
            new_off_hand_equipment_list = sorted([k["Name2" if "Name2" in k else "Name"] for k in self.all_equipment_dict["sub"] if (main_job_shorthand in k["Jobs"]) and (k["Type"] in allowed_subtypes)])
            

            self.quicklook_scrollframes["sub"].set_visible_data(new_off_hand_equipment_list)
            self.tp_quicklook_scrollframes["sub"].set_visible_data(new_off_hand_equipment_list)
            self.ws_quicklook_scrollframes["sub"].set_visible_data(new_off_hand_equipment_list)
            self.optimize_scrollframes["sub"].set_visible_data(new_off_hand_equipment_list)
            if not dual_wield:
                self.optimize_scrollframes["sub"].deselect(restricted_items)

            # Unequip off-hand weapon if not able to dual-wield.
            if not dual_wield and self.quicklook_equipped_dict["sub"]["item"]["Type"]=="Weapon":
                new_sub_item = gear_pyfile.all_gear["Empty"]
                self.quicklook_equipped_dict["sub"]["item"] = new_sub_item
                self.quicklook_equipped_dict["sub"]["icon"] = self.get_equipment_icon(new_sub_item["Name"])
                self.quicklook_equipped_dict["sub"]["button"].config(image = self.quicklook_equipped_dict["sub"]["icon"])
                self.quicklook_equipped_dict["sub"]["button tooltip"].text = self.format_tooltip_stats(new_sub_item)
                self.quicklook_equipped_dict["sub"]["radio_variable"].set("Empty")

        # Hide abilities not accessible to the selected main/sub/ML combo.
        main_job = self.jobs_dict[self.main_job_value.get()]
        sub_job = self.jobs_dict[self.sub_job_value.get()] if self.sub_job_value.get() != "None" else "None"
        for ability_name in self.all_special_toggles_dict:

            self.all_special_toggles_dict[ability_name]["checkbox"].pack_forget() # Hide all abilities so we can show them again in order and retain alphabetical ordering.

            if (main_job.lower() in self.all_special_toggles_dict[ability_name]["job requirement"]) or (self.sub_job_level.get() >= self.all_special_toggles_dict[ability_name]["level requirement"] and sub_job.lower() in self.all_special_toggles_dict[ability_name]["job requirement"]):
                if (main_job.lower()=="dnc" and ability_name.lower() in ["haste samba (sub)", "box step (sub)"]) or (main_job.lower()=="war" and ability_name.lower()=="warcry (sub)"):
                    continue
                self.all_special_toggles_dict[ability_name]["checkbox"].pack(expand=True, fill="both")
            else:
                self.all_special_toggles_dict[ability_name]["boolvar"].set(False)

    def select_gear_opt(self, event):
        '''
        When clicking one of the "Select X" buttons in the optimize tab.
        Selects or unselects all equipment based on input.
        '''
        tvr_ring_names = [k.lower()+" ring" for k in self.tvr_rings]
        soa_ring_names = [k.lower()+" ring +1" for k in self.soa_rings]

        empyrean_names = ["Hattori", "Heathen's", "Wicce", "Lethargy", "Peltast's", "Ebers", "Kasuga", "Arbatel", "Boii", "Chasseur's", "Fili", "Skulker's", "Bhikku", "Maculele", "Nukumi", "Azimuth", "Chevalier's", "Amini", "Hashishin", "Erilaz", "Karagoz", "Beckoner's"]
        relic_names = ["Pedagogy", "Hesychast", "Vitiation", "Mochizuki", "Fallen", "Horos", "Pitre", "Luhlaza", "Plunderer", "Bagua", "Archmage", "Piety", "Agoge", "Caballarius", "Wakido", "Ankusa", "Bihu", "Glyphic", "Lanun", "Arcadian", "Pteroslaver", "Futhark"]
        af_names = ["Academic", "Anchorite", "Atrophy", "Hachiya", "Ignominy", "Maxixi", "Foire", "Assimilator", "Pillager", "Geomancy", "Spaekona", "Theophany", "Pummeler", "Reverence", "Sakonji", "Totemic", "Brioso", "Convoker", "Laksamana", "Orion", "Vishap", "Runeist"]


        input_items_full = [] # Full item names
        if event == "select all file":
            # Select items from a "//gs export all" file. Items must be in the item_list.csv file.
            # TODO: Redo the item_list.csv file to include NQ items. item_list.csv currently only includes items in the gear.py file.
            # TODO: Re-add NQ item icons.
            filename = filedialog.askopenfilename(title='Select file', initialdir='./')

            if len(filename) > 0:
                with open(filename, "r") as ifile:
                    for line in ifile:
                        try:
                            item_name_abbreviated = line.split('"')[1]
                            item_index = np.flatnonzero(np.char.lower(self.item_id_dict["name2"]) == item_name_abbreviated.lower())
                            if len(item_index) > 0:
                                input_items_full.append(str(self.item_id_dict["name"][item_index[0]]))
                            else:
                                continue
                        except Exception as err:
                            # print(f"Failed to include item   {line}\n{err}")
                            continue

        for slot in self.quicklook_equipped_dict:

            # Only consider the selected slot when using slot-specific buttons.
            if event in ["select all slot", "unselect all slot"] and self.visible_optimize_frame_slot != slot:
                continue

            # Start by removing all selections.
            self.optimize_scrollframes[slot].deselect("all")

            # Enable all selections if using "select all" buttons.
            if event in ["select all slot", "select all"]:
                self.optimize_scrollframes[slot].select("visible")

            # Adjust specific item selections based on filters.
            for item_name in self.optimize_scrollframes[slot].visible_data:

                # Create the full-stats item dictionary for reference
                item = gear_pyfile.all_gear[item_name]

                if event == "select all file":                        
                    if (item["Name"].lower() in input_items_full): # Select direct matches first
                        self.optimize_scrollframes[slot].select(item_name)
                    elif (item["Name"].lower().split(" +")[0] in input_items_full): # Only select close match if direct match not found.
                        self.optimize_scrollframes[slot].select(item_name)


                # Deselect if the item's Odyssey rank does not match your selected Odyssey Rank.
                if str(item.get("Rank", self.ody_rank_value.get())) != self.ody_rank_value.get():
                    self.optimize_scrollframes[slot].deselect(item_name)

                # Swap Nyame R30B for R25B if specific checkbox is enabled. Deselect Nyame Paths "not B"
                if "nyame" in item_name.lower():
                    if "B" != item_name[-1]:
                        self.optimize_scrollframes[slot].deselect(item_name)
                    elif self.ody_rank_value.get()=="30" and self.nyame25_checkbox_value.get():
                        if "30B" in item_name:
                            self.optimize_scrollframes[slot].deselect(item_name)
                        elif "25B" in item_name and event in ["select all", "select all slot"]:
                            self.optimize_scrollframes[slot].select(item_name)

                if slot in ["main", "sub", "ranged"]:
                    if item_name.split()[0] in self.rema_weapons and "R15" not in item_name:
                        self.optimize_scrollframes[slot].deselect(item_name)
                    if item_name.split()[-1] == "V":
                        self.optimize_scrollframes[slot].deselect(item_name)
                    if "kraken" in item_name.lower():
                        self.optimize_scrollframes[slot].deselect(item_name)

                if slot in ["ammo"]:
                    if "Hoxne" in item_name or "Antitail" in item_name:
                        self.optimize_scrollframes[slot].deselect(item_name)

                if slot in ["head", "body", "hands", "legs", "feet"]:
                    if event != "select all file":
                        if item_name.split()[0] in relic_names+af_names and "+4" not in item_name:
                            self.optimize_scrollframes[slot].deselect(item_name)
                        if item_name.split()[0] in empyrean_names and "+3" not in item_name:
                            self.optimize_scrollframes[slot].deselect(item_name)
                    for limbus_set_name in ["hope", "perfection", "revelation", "trust", "prestige", "sworn", "bravery", "intrepid", "indomitable", "justice", "magnificent", "duty", "mercy", "grace", "clemency"]:
                        if limbus_set_name in item_name.lower() and "R30" in item_name: # Only select R0 versions of the limbus equipment (at least for now)
                            self.optimize_scrollframes[slot].deselect(item_name)

                if slot in ["neck"]:
                    if event != "select all file":
                        if "R20" in item_name and "+1" in item_name:
                            self.optimize_scrollframes[slot].deselect(item_name)

                if slot in ["ear1", "ear2"]:
                    if item_name.split()[0] in empyrean_names and "+2" in item_name:
                        self.optimize_scrollframes[slot].deselect(item_name)
                    if "Hoxne" in item_name and self.mastery_rank_value.get().lower() not in item_name.lower():
                        self.optimize_scrollframes[slot].deselect(item_name)
                    if "Balder" in item_name:
                        self.optimize_scrollframes[slot].deselect(item_name)
                    if "(night)" in item_name.lower():
                        self.optimize_scrollframes[slot].deselect(item_name)

                if slot in ["ring1", "ring2"]:
                    if item_name.lower() in tvr_ring_names and item_name.lower() != self.tvr_selection_value.get().lower() + " ring":
                        self.optimize_scrollframes[slot].deselect(item_name)

                    if item_name.lower() in soa_ring_names and item_name.lower() != self.soa_selection_value.get().lower() + " ring +1":
                        self.optimize_scrollframes[slot].deselect(item_name)

                # if "Murky" in item_name or "Alabaster" in item_name:
                #     self.optimize_scrollframes[slot].deselect(item_name)

    def equip_best_set(self):
        '''
        When clicking the "Equip best set" button.
        Take the best gear set from an optimize run and equip it to the quicklook gear.
        '''
        for slot in self.best_player.gearset:
            self.quicklook_equipped_dict[slot]["item"] = self.best_player.gearset[slot]
            self.quicklook_equipped_dict[slot]["icon"] = self.get_equipment_icon(self.quicklook_equipped_dict[slot]["item"]["Name"])
            self.quicklook_equipped_dict[slot]["button"].config(image = self.quicklook_equipped_dict[slot]["icon"])
            self.quicklook_equipped_dict[slot]["button tooltip"].text = self.format_tooltip_stats(self.quicklook_equipped_dict[slot]["item"])
            self.quicklook_equipped_dict[slot]["radio_variable"].set(self.quicklook_equipped_dict[slot]["item"]["Name2"])

            best_item_name = self.best_player.gearset[slot]["Name2"]
            self.quicklook_scrollframes[slot].set_selected(best_item_name)

            self.notebook.select("0")



    def copy_gearset_dict(self, event):
        '''
        When clicking the Copy to TP/WS/Quickook buttons
        Copy the gearset displayed at the source to the destination.
        '''
        if event=="quicklook to tp":
            source_dict = self.quicklook_equipped_dict
            destination_dict = self.tp_quicklook_equipped_dict
            destination_tab = "2"
            destination_scrollframe = self.tp_quicklook_scrollframes
        elif event=="quicklook to ws":
            source_dict = self.quicklook_equipped_dict
            destination_dict = self.ws_quicklook_equipped_dict
            destination_tab = "2"
            destination_scrollframe = self.ws_quicklook_scrollframes
        elif event=="tp to quicklook":
            source_dict = self.tp_quicklook_equipped_dict
            destination_dict = self.quicklook_equipped_dict
            destination_scrollframe = self.quicklook_scrollframes
            destination_tab = "0"
        elif event=="ws to quicklook":
            source_dict = self.ws_quicklook_equipped_dict
            destination_dict = self.quicklook_equipped_dict
            destination_scrollframe = self.quicklook_scrollframes
            destination_tab = "0"


        for slot in destination_dict:
            destination_dict[slot]["item"] = source_dict[slot]["item"]
            destination_dict[slot]["icon"] = self.get_equipment_icon(destination_dict[slot]["item"]["Name"])
            destination_dict[slot]["button"].config(image = destination_dict[slot]["icon"])
            destination_dict[slot]["button tooltip"].text = self.format_tooltip_stats(destination_dict[slot]["item"])
            destination_dict[slot]["radio_variable"].set(destination_dict[slot]["item"]["Name2"]) # TODO: Is "radio_variable" used anywhere essential after virtualization?

            source_item_name = source_dict[slot]["item"]["Name2"]
            destination_scrollframe[slot].set_selected(source_item_name)


        self.notebook.select(destination_tab)


    def copy_to_clipboard(self, type):
        '''
        When clicking the "Copy to Clipboard" button.
        Build a set that can be copy-pasted into a gearswap lua (ignoring augments)
        '''
        if type=="quicklook":
            equipped_gear_dict = self.quicklook_equipped_dict
        elif type=="tp":
            equipped_gear_dict = self.tp_quicklook_equipped_dict
        elif type=="ws":
            equipped_gear_dict = self.ws_quicklook_equipped_dict

        output_string = "new_set = {\n"
        for gear_slot in self.quicklook_equipped_dict:
            item_name = equipped_gear_dict[gear_slot]['item'].get('Name', 'Empty')
            output_string = output_string + f"    {gear_slot}=\"{item_name}\",\n"
        output_string = output_string + "}"

        self.clipboard_clear()
        self.clipboard_append(output_string)

    def update_quicklook_equipment(self, selection):
        '''
        When selecting a new equipment piece from the radio button lists in the quicklook frame.
        Update the item stored in the quicklook_equipped_dict object
        Update the icon shown on the 4x4 button grid.
        Update the icon HoverTip to show the new selection's stats
        Remove equipment in other slots if the new combination is not possible.


        slot:                equipment slot under consideration
        new_item_name:       The name of the radio button clicked to trigger this function
        equipped_items_dict: The equipment dictionary to be modified by the selection
        source:              A way to track which part of the code called this function to avoid TP/WS sets affecting quicklook tab.
        '''
        # Older callers passed the destination equipment dictionary as a
        # fourth tuple value.  VirtualRadioFrame emits the newer compact
        # (slot, item, source) form; accept both while callers migrate.
        if len(selection) == 4:
            slot, new_item_name, _legacy_equipment_dict, source = selection
        else:
            slot, new_item_name, source = selection

        if source == "quicklook":
            equipped_items_dict = self.quicklook_equipped_dict
            scrollframe = self.quicklook_scrollframes
        elif source == "tp":
            equipped_items_dict = self.tp_quicklook_equipped_dict
            scrollframe = self.tp_quicklook_scrollframes
        elif source == "ws":
            equipped_items_dict = self.ws_quicklook_equipped_dict
            scrollframe = self.ws_quicklook_scrollframes

        new_item = gear_pyfile.all_gear[new_item_name] # New item (dictionary of stats)
        old_item = equipped_items_dict[slot]["item"]   # Old item (dictionary of stats)

        if slot in ["ring1", "ring2", "ear1", "ear2"]: # Prepare for swapping rings/earrings later for convenience.
            ring1_item_before = equipped_items_dict["ring1"]["item"]
            ring2_item_before = equipped_items_dict["ring2"]["item"]
            ear1_item_before = equipped_items_dict["ear1"]["item"]
            ear2_item_before = equipped_items_dict["ear2"]["item"]

        # Equip the new item now.
        equipped_items_dict[slot]["item"] = new_item
        equipped_items_dict[slot]["icon"] = self.get_equipment_icon(new_item["Name"])
        equipped_items_dict[slot]["button"].config(image = equipped_items_dict[slot]["icon"])
        equipped_items_dict[slot]["button tooltip"].text = self.format_tooltip_stats(new_item)

        # Remove equipment in other slots if the new combination is not possible
        main_skill_type = equipped_items_dict["main"]["item"]["Skill Type"]
        sub_item_type = equipped_items_dict["sub"]["item"]["Type"]
        if slot == "main":
            main_job_shorthand = self.jobs_dict[self.main_job_value.get()]
            sub_job_shorthand = self.jobs_dict.get(self.sub_job_value.get(), "None")
            dual_wield = (main_job_shorthand in ["nin", "dnc", "thf", "blu"] or sub_job_shorthand in ["nin", "dnc"])
            if (main_skill_type in ["Great Sword", "Great Katana", "Great Axe", "Polearm", "Scythe", "Staff"] and sub_item_type not in ["Grip", "None"]) or (main_skill_type=="Hand-to-Hand") or (main_skill_type in ["Axe", "Club", "Dagger", "Sword", "Katana"] and (sub_item_type not in ["Shield", "None"]) and not dual_wield):
                new_sub_item = gear_pyfile.all_gear["Empty"]
                equipped_items_dict["sub"]["item"] = new_sub_item
                equipped_items_dict["sub"]["icon"] = self.get_equipment_icon(new_sub_item["Name"])
                equipped_items_dict["sub"]["button"].config(image = equipped_items_dict["sub"]["icon"])
                equipped_items_dict["sub"]["button tooltip"].text = self.format_tooltip_stats(new_sub_item)
                equipped_items_dict["sub"]["radio_variable"].set("Empty")
                scrollframe["sub"].set_selected("Empty")


            if source=="quicklook":
                self.wpn_type_main = equipped_items_dict["main"]["item"]["Skill Type"]
                self.ws_selection_combobox.config(values=self.ws_dict[self.wpn_type_main] + (self.ws_dict[self.wpn_type_ranged] if self.wpn_type_ranged not in ["None", "Instrument"] else []))
                if old_item["Skill Type"] != new_item["Skill Type"]:
                    if self.ws_selection_value.get() not in list(self.ws_selection_combobox.cget("values")):
                        self.ws_selection_value.set(self.ws_dict[self.wpn_type_main][0])

        if slot == "sub":
            if (sub_item_type=="Grip" and main_skill_type not in ["Great Sword", "Great Katana", "Great Axe", "Polearm", "Scythe", "Staff"]) or (sub_item_type=="Shield" and main_skill_type not in ["None", "Axe", "Club", "Dagger", "Sword", "Katana"]):
                new_main_item = gear_pyfile.all_gear["Empty"]
                equipped_items_dict["main"]["item"] = new_main_item
                equipped_items_dict["main"]["icon"] = self.get_equipment_icon(new_main_item["Name"])
                equipped_items_dict["main"]["button"].config(image = equipped_items_dict["main"]["icon"])
                equipped_items_dict["main"]["button tooltip"].text = self.format_tooltip_stats(new_main_item)
                equipped_items_dict["main"]["radio_variable"].set("Empty")
                scrollframe["main"].set_selected("Empty")

        ranged_item_type = equipped_items_dict["ranged"]["item"]["Type"]
        ammo_item_type = equipped_items_dict["ammo"]["item"]["Type"]
        if slot == "ranged":
            if (ranged_item_type=="Gun" and ammo_item_type != "Bullet") or (ranged_item_type=="Bow" and ammo_item_type != "Arrow") or (ranged_item_type=="Crossbow" and ammo_item_type != "Bolt") or (ranged_item_type in ["Instrument", "Equipment"]):
                new_ammo_item = gear_pyfile.all_gear["Empty"]
                equipped_items_dict["ammo"]["item"] = new_ammo_item
                equipped_items_dict["ammo"]["icon"] = self.get_equipment_icon(new_ammo_item["Name"])
                equipped_items_dict["ammo"]["button"].config(image = equipped_items_dict["ammo"]["icon"])
                equipped_items_dict["ammo"]["button tooltip"].text = self.format_tooltip_stats(new_ammo_item)
                equipped_items_dict["ammo"]["radio_variable"].set("Empty")
                scrollframe["ammo"].set_selected("Empty")

            if source=="quicklook":
                self.wpn_type_ranged = equipped_items_dict["ranged"]["item"]["Skill Type"]
                self.ws_selection_combobox.config(values=self.ws_dict[self.wpn_type_main] + (self.ws_dict[self.wpn_type_ranged] if self.wpn_type_ranged not in ["None", "Instrument"] else []))

                if (old_item["Skill Type"] != new_item["Skill Type"]) and (new_item["Skill Type"] not in ["Instrument"]):
                    if self.ws_selection_value.get() not in self.ws_dict[self.wpn_type_main] + self.ws_dict[self.wpn_type_ranged]:
                        self.ws_selection_value.set(self.ws_dict[self.wpn_type_main][0])

        if slot == "ammo":
            if (ammo_item_type=="Bullet" and ranged_item_type != "Gun") or (ammo_item_type=="Arrow" and ranged_item_type != "Bow") or (ammo_item_type=="Bolt" and ranged_item_type != "Crossbow") or (ammo_item_type in ["Equipment", "Shuriken"]):
                new_ammo_item = gear_pyfile.all_gear["Empty"]
                equipped_items_dict["ranged"]["item"] = new_ammo_item
                equipped_items_dict["ranged"]["icon"] = self.get_equipment_icon(new_ammo_item["Name"])
                equipped_items_dict["ranged"]["button"].config(image = equipped_items_dict["ranged"]["icon"])
                equipped_items_dict["ranged"]["button tooltip"].text = self.format_tooltip_stats(new_ammo_item)
                equipped_items_dict["ranged"]["radio_variable"].set("Empty")
                scrollframe["ranged"].set_selected("Empty")

        # Swap the rings if selecting ring1/ring2 to be the item in ring2/ring1 slot.
        if ((slot == "ring1" and (new_item == ring2_item_before)) or (slot == "ring2" and (new_item == ring1_item_before))) and (new_item["Name"] != "Empty"):
            equipped_items_dict["ring1"]["item"] = ring2_item_before
            equipped_items_dict["ring1"]["icon"] = self.get_equipment_icon(ring2_item_before["Name"])
            equipped_items_dict["ring1"]["button"].config(image = equipped_items_dict["ring1"]["icon"])
            equipped_items_dict["ring1"]["button tooltip"].text = self.format_tooltip_stats(ring2_item_before)
            equipped_items_dict["ring1"]["radio_variable"].set(ring2_item_before["Name2"])
            scrollframe["ring1"].set_selected(ring2_item_before["Name2"])

            equipped_items_dict["ring2"]["item"] = ring1_item_before
            equipped_items_dict["ring2"]["icon"] = self.get_equipment_icon(ring1_item_before["Name"])
            equipped_items_dict["ring2"]["button"].config(image = equipped_items_dict["ring2"]["icon"])
            equipped_items_dict["ring2"]["button tooltip"].text = self.format_tooltip_stats(ring1_item_before)
            equipped_items_dict["ring2"]["radio_variable"].set(ring1_item_before["Name2"])
            scrollframe["ring2"].set_selected(ring1_item_before["Name2"])

        # Swap the earrings if selecting ear1/ear2 to be the item in ear2/ear1 slot.
        if ((slot == "ear1" and (new_item == ear2_item_before)) or (slot == "ear2" and (new_item == ear1_item_before))) and (new_item["Name"] != "Empty"):
            equipped_items_dict["ear1"]["item"] = ear2_item_before
            equipped_items_dict["ear1"]["icon"] = self.get_equipment_icon(ear2_item_before["Name"])
            equipped_items_dict["ear1"]["button"].config(image = equipped_items_dict["ear1"]["icon"])
            equipped_items_dict["ear1"]["button tooltip"].text = self.format_tooltip_stats(ear2_item_before)
            equipped_items_dict["ear1"]["radio_variable"].set(ear2_item_before["Name2"])
            scrollframe["ear1"].set_selected(ear2_item_before["Name2"])

            equipped_items_dict["ear2"]["item"] = ear1_item_before
            equipped_items_dict["ear2"]["icon"] = self.get_equipment_icon(ear1_item_before["Name"])
            equipped_items_dict["ear2"]["button"].config(image = equipped_items_dict["ear2"]["icon"])
            equipped_items_dict["ear2"]["button tooltip"].text = self.format_tooltip_stats(ear1_item_before)
            equipped_items_dict["ear2"]["radio_variable"].set(ear1_item_before["Name2"])
            scrollframe["ear2"].set_selected(ear1_item_before["Name2"])

        # Can't equip a cloak with a hat
        if slot == "body":
            if ("cloak" in new_item_name.lower()):
                equipped_items_dict["head"]["item"] = gear_pyfile.all_gear["Empty"]
                equipped_items_dict["head"]["icon"] = self.get_equipment_icon(gear_pyfile.all_gear["Empty"]["Name"])
                equipped_items_dict["head"]["button"].config(image = equipped_items_dict["head"]["icon"])
                equipped_items_dict["head"]["button tooltip"].text = self.format_tooltip_stats(gear_pyfile.all_gear["Empty"])
                equipped_items_dict["head"]["radio_variable"].set("Empty")
                scrollframe["head"].set_selected("Empty")

        if slot == "head":
            if ("cloak" in equipped_items_dict["body"]["item"]["Name"].lower()):
                equipped_items_dict["body"]["item"] = gear_pyfile.all_gear["Empty"]
                equipped_items_dict["body"]["icon"] = self.get_equipment_icon(gear_pyfile.all_gear["Empty"]["Name"])
                equipped_items_dict["body"]["button"].config(image = equipped_items_dict["body"]["icon"])
                equipped_items_dict["body"]["button tooltip"].text = self.format_tooltip_stats(gear_pyfile.all_gear["Empty"])
                equipped_items_dict["body"]["radio_variable"].set("Empty")
                scrollframe["body"].set_selected("Empty")

        # Update the radio button selections based on the newly equipped gear.
        scrollframe[slot].set_selected(new_item_name)


    def update_buffs(self, event):
        '''
        When enabling/disabling/selecting buffs from WHM, COR, BRD, GEO selections.
        Ensure that no two buffs are identical (do not allow double chaos roll)
        Ensure that Bolster is not enabled with BoG.

        Inputs:
            set {dia, haste, boost, storm}
            set {Song1, Song2, Song3, Song4}
            set {Roll1, Roll2, Roll3, Roll4}
            set {Indi-, Geo-, Entrust-}
        '''

        if event == "soul voice":
            if self.soul_voice_checkbox_value.get() == True:
                self.marcato_checkbox_value.set(False)

        elif event == "marcato":
            if self.marcato_checkbox_value.get() == True:
                self.soul_voice_checkbox_value.set(False)

        elif event == "bolster":
            if self.bolster_checkbox_value.get() == True:
                self.bog_checkbox_value.set(False)
                
        elif event == "blaze of glory":
            if self.bog_checkbox_value.get() == True:
                self.bolster_checkbox_value.set(False)

        elif "set song" in event.lower():
            new_song_slot = event.split()[-1]
            new_song_name = self.song_selections_dict[new_song_slot]["combobox"].get()
            for song_slot in self.song_selections_dict:
                other_song_name_in_use = self.song_selections_dict[song_slot]["combobox"].get()
                if (new_song_slot != song_slot) and (other_song_name_in_use == new_song_name) and (other_song_name_in_use != "None"):
                    self.song_selections_dict[song_slot]["combobox"].set("None")

        elif "set roll" in event.lower():
            new_roll_slot = event.split()[-1]
            new_roll_name = self.roll_selections_dict[new_roll_slot]["name combobox"].get()
            for roll_slot in self.roll_selections_dict:
                other_roll_name_in_use = self.roll_selections_dict[roll_slot]["name combobox"].get()
                if (new_roll_slot != roll_slot) and (other_roll_name_in_use == new_roll_name) and (other_roll_name_in_use != "None"):
                    self.roll_selections_dict[roll_slot]["name combobox"].set("None")

        elif event.lower() in ["set indi-", "set geo-", "set entrust-"]:
            new_bubble_slot = event.split()[-1]
            new_bubble_name = self.bubble_selections_dict[new_bubble_slot]["combobox"].get().split("-")[-1]
            for bubble_slot in self.bubble_selections_dict:
                other_bubble_name_in_use = self.bubble_selections_dict[bubble_slot]["combobox"].get().split("-")[-1]
                if (new_bubble_slot != bubble_slot) and (other_bubble_name_in_use == new_bubble_name) and (other_bubble_name_in_use != "None"):
                    self.bubble_selections_dict[bubble_slot]["combobox"].set("None")

        elif event == "set dia":
            self.whm_selections_dict["Dia"] = self.dia_value.get()
        elif event == "set haste":
            self.whm_selections_dict["Haste"] = self.haste_value.get()
        elif event == "set boost":
            self.whm_selections_dict["Boost"] = self.boost_value.get()
        elif event == "set storm":
            self.whm_selections_dict["Storm"] = self.storm_value.get()

        elif event == "set food":
            self.food_selections_dict["item"] = gear_pyfile.all_food[self.food_selections_dict["stringvar"].get()]
            self.food_selections_dict["combobox tooltip"].text = self.format_tooltip_stats(self.food_selections_dict["item"])

        elif "haste samba" in event.lower():
            if "sub" in event.lower() and self.all_special_toggles_dict["Haste Samba (sub)"]["boolvar"].get():
                self.all_special_toggles_dict["Haste Samba"]["boolvar"].set(False)
            if "sub" not in event.lower() and self.all_special_toggles_dict["Haste Samba"]["boolvar"].get():
                self.all_special_toggles_dict["Haste Samba (sub)"]["boolvar"].set(False)

        elif "warcry" in event.lower():
            if "sub" in event.lower() and self.all_special_toggles_dict["Warcry (sub)"]["boolvar"].get():
                self.all_special_toggles_dict["Warcry"]["boolvar"].set(False)
            if "sub" not in event.lower() and self.all_special_toggles_dict["Warcry"]["boolvar"].get():
                self.all_special_toggles_dict["Warcry (sub)"]["boolvar"].set(False)

        elif "box step" in event.lower():
            if "sub" in event.lower() and self.all_special_toggles_dict["Box Step (sub)"]["boolvar"].get():
                self.all_special_toggles_dict["Box Step"]["boolvar"].set(False)
            if "sub" not in event.lower() and self.all_special_toggles_dict["Box Step"]["boolvar"].get():
                self.all_special_toggles_dict["Box Step (sub)"]["boolvar"].set(False)

    def aggregate_buffs(self,):
        '''
        Called by quicklook function
        Reads GUI values to determine active buffs and debuffs.
        Returns dictionary containing the sum of enabled buffs.
        '''
        buffs = {"brd":{}, "cor":{}, "geo":{}, "whm":{}, "food":{}}
        debuffs = {"cor":{}, "geo":{}, "whm":{}, "other":{}}

        # BRD buffs
        if self.brd_checkbox_value.get() == True:
            for song_slot in self.song_selections_dict:
                song_name = self.song_selections_dict[song_slot]["stringvar"].get()
                if song_name in buffs_pyfile.brd:
                    song_bonus = int(self.song_bonus_value.get().split("+")[-1]) # "Songs +7" etc
                    song_bonus_limit = buffs_pyfile.brd_song_limits[song_name]   # Some songs are limited to Songs+X due to instrument requirements or other gear limitations.
                    soul_voice = 1.0 + 1.0*self.soul_voice_checkbox_value.get() # Soul Voice affects all songs. 
                    marcato = 1.0 + 0.5*self.marcato_checkbox_value.get() if song_slot in ["Song1"] else 1.0 # Marcato only affects song in slot 1
                    for stat in buffs_pyfile.brd[song_name]:
                        values = buffs_pyfile.brd[song_name][stat]
                        buffs["brd"][stat] = buffs["brd"].get(stat, 0) + soul_voice * marcato * (values[0] + min(song_bonus_limit, song_bonus)*values[1]) + 20*("minuet" in song_name.lower() and stat.lower() in ["attack", "ranged attack"]) # +20 Attack to Minuets from Job Point gifts (assuming it applies to all players, not just the BRD)

        buffs["brd"]["Attack"] = int(buffs["brd"].get("Attack",0))
        buffs["brd"]["Ranged Attack"] = int(buffs["brd"].get("Ranged Attack",0))

        # COR buffs
        if self.cor_checkbox_value.get() == True:
            for roll_slot in self.roll_selections_dict:
                roll_name = self.roll_selections_dict[roll_slot]["name stringvar"].get().split()[0]
                if roll_name in buffs_pyfile.cor:
                    roll_bonus = int(self.roll_bonus_value.get().split("+")[-1]) # "Rolls +7" etc
                    roll_value = self.roll_selections_dict[roll_slot]["potency stringvar"].get() # I, II, III, IV, etc
                    crooked_cards = 1.0 + 0.2*self.crooked_checkbox_value.get() if roll_slot in ["Roll1", "Roll3"] else 1.0 # Crooked Cards only affects rolls 1 and 3 here. 
                    for stat in buffs_pyfile.cor[roll_name]:
                        values = buffs_pyfile.cor[roll_name][stat]
                        job_bonus = self.job_bonus_checkbox_value.get() * (values[2])
                        buffs["cor"][stat] = buffs["cor"].get(stat, 0) + crooked_cards * (values[0][roll_value] + roll_bonus*values[1] + job_bonus)

        # COR debuffs
        if self.light_shot_checkbox_value.get() and self.whm_checkbox_value.get() and ("dia" in self.whm_selections_dict["Dia"].lower()):
            for stat in buffs_pyfile.cor_debuffs["Light Shot"]:
                debuffs["cor"][stat] = debuffs["cor"].get(stat, 0) + buffs_pyfile.cor_debuffs["Light Shot"][stat]

        # GEO buffs and debuffs
        if self.geo_checkbox_value.get() == True:
            for bubble_slot in self.bubble_selections_dict:
                bubble_name = self.bubble_selections_dict[bubble_slot]["stringvar"].get().split("-")[-1]

                bubble_bonus = int(self.bubble_bonus_value.get().split("+")[-1]) if (bubble_slot in ["Indi-", "Geo-"]) else 0 # "Bubbles +7" etc. Entrust spells do not benefit from Bubbles+ gear.
                bolster = 1.0 + 1.0*self.bolster_checkbox_value.get() if (bubble_slot in ["Indi-", "Geo-"]) else 1.0 # Bolster only affects "Indi-" and "Geo-" spells.
                bog = 1.0 + 0.5*self.bog_checkbox_value.get() if (bubble_slot in ["Geo-"]) else 1.0 # Blaze of Glory (bog) only affects "Geo-" bubbles
                
                # GEO Buffs
                if bubble_name in buffs_pyfile.geo:
                    for stat in buffs_pyfile.geo[bubble_name]:
                        values = buffs_pyfile.geo[bubble_name][stat]
                        buffs["geo"][stat] = buffs["geo"].get(stat, 0) + bolster * bog * (values[0] + bubble_bonus*values[1]) 

                # GEO Debuffs
                if bubble_name in buffs_pyfile.geo_debuffs:

                    # Debuffing bubbles are frequently reduced to 10~70% of their original potency.
                    bubble_potency = max(0, self.bubble_potency_value.get()/100)

                    for stat in buffs_pyfile.geo_debuffs[bubble_name]:
                        values = buffs_pyfile.geo_debuffs[bubble_name][stat]
                        debuffs["geo"][stat] = debuffs["geo"].get(stat, 0) + bolster * bog * (values[0] + bubble_bonus*values[1]) * bubble_potency

        # WHM buffs and debuffs
        if self.whm_checkbox_value.get() == True:
            for spell_slot in self.whm_selections_dict:
                spell_name = self.whm_selections_dict[spell_slot]

                if spell_name in buffs_pyfile.whm:
                    for stat in buffs_pyfile.whm[spell_name]:
                        buffs["whm"][stat] = buffs["whm"].get(stat, 0) + buffs_pyfile.whm[spell_name][stat]

                # WHM Debuffs
                if spell_name in buffs_pyfile.whm_debuffs:
                    for stat in buffs_pyfile.whm_debuffs[spell_name]:
                        debuffs["whm"][stat] = debuffs["whm"].get(stat, 0) + buffs_pyfile.whm_debuffs[spell_name][stat]

        # WHM Shell V
        if self.shell5_value.get():
            for stat in buffs_pyfile.whm["Shell V"]:
                buffs["whm"][stat] = buffs["whm"].get(stat, 0) + buffs_pyfile.whm["Shell V"][stat]

        # Food buffs
        if self.food_selections_dict["stringvar"].get() in gear_pyfile.all_food:
            active_food = gear_pyfile.all_food[self.food_selections_dict["stringvar"].get()] # Dictionary of stats.
            for stat in active_food:
                if stat not in ["Name", "Name2", "Type"]:
                    # Attack from food is added after Attack% from COR/Berserk/etc, so it needs a different name here.
                    buffs["food"][stat] = buffs["food"].get(stat, 0) + active_food[stat]
        if "Attack" in buffs["food"]:
            buffs["food"]["Food Attack"] = buffs["food"].pop("Attack")
        if "Ranged Attack" in buffs["food"]:
            buffs["food"]["Food Ranged Attack"] = buffs["food"].pop("Ranged Attack")

        # Other debuffs from the special toggles checkboxes
        for debuff_name in self.all_special_toggles_dict:
            if debuff_name in buffs_pyfile.misc_debuffs:
                for stat in self.all_special_toggles_dict[debuff_name]:
                    if not any(k in stat.lower() for k in ["requirement", "checkbox", "boolvar"]) and (self.all_special_toggles_dict[debuff_name]["boolvar"].get()):
                        debuffs["other"][stat] = debuffs["other"].get(stat, 0) + self.all_special_toggles_dict[debuff_name][stat]

        # Combine debuffs into a simpler dictionary
        combined_debuffs = {}
        for source in debuffs:
            for stat in debuffs[source]:
                combined_debuffs[stat] = combined_debuffs.get(stat, 0) + debuffs[source][stat]
        # print(buffs)
        return buffs, combined_debuffs

    def quicklook(self, trigger):
        '''
        When clicking the "Quicklook WS" button.
        Compile the player stats from selected buffs and equipment.
        Run create_player() to build a player character with complete stats.
        Run average_ws() with the given player and selected WS parameters.
        '''
        main_job = self.jobs_dict[self.main_job_value.get()]
        sub_job = self.jobs_dict.get(self.sub_job_value.get(), "None")
        master_level = self.master_level_value.get()

        ws_name = self.ws_selection_value.get()
        ws_type = "ranged" if ws_name in (self.ws_dict["Marksmanship"]+self.ws_dict["Archery"]) else "melee"
        spell_name = self.spell_selection_value.get()

        tp_entry_value = self.tp_entry_value.get()

        special_toggles_dict = {k:self.all_special_toggles_dict[k]["boolvar"].get() for k in self.all_special_toggles_dict if k not in buffs_pyfile.misc_debuffs}
        special_toggles_dict["Enhancing Skill"] = self.enhancing_skill_value.get()
        special_toggles_dict["Aftermath"] = self.aftermath_value.get()
        special_toggles_dict["Storm spell"] = self.whm_selections_dict["Storm"]
        special_toggles_dict["Enemy Resist Rank"] = self.selected_enemy_variables["Resist Rank"].get()
        special_toggles_dict["99999"] = self.damage_limit99999.get()

        active_buffs, active_debuffs = self.aggregate_buffs()

        equipped_gearset = {slot:self.quicklook_equipped_dict[slot]["item"] for slot in self.quicklook_equipped_dict}
        player = create_player_pyfile.create_player(main_job, sub_job, master_level, gearset=equipped_gearset, buffs=active_buffs, abilities=special_toggles_dict,)

        useful_enemy_stats = {stat:self.selected_enemy_variables[stat].get() for stat in self.selected_enemy_variables if stat not in ["Level", "Location", "Name", "Resist Rank"]}
        enemy = create_player_pyfile.create_enemy(useful_enemy_stats)
        for stat in active_debuffs:
            if "requirement" in stat.lower():
                continue
            if stat == "Defense":
                enemy.stats["Defense"] *= (1 - active_debuffs.get("Defense", 0))
            else:
                enemy.stats[stat] -= active_debuffs[stat]
        enemy.stats["Base Defense"] = useful_enemy_stats["Defense"]
        enemy.stats["Defense"] = max(1, enemy.stats.get("Defense", 1)) # Enemy Defense can not be brought lower than 1
        enemy.stats["Magic Defense"] = max(-50, enemy.stats.get("Magic Defense", 0)) # Enemy Magic Defense can not be brought lower than -50 (magic damage taken x2)
        enemy.stats["Magic Damage Taken"] = enemy.stats.pop("Magic DT%")


        if trigger=="run one ws":

            print()
            print()
            actions_pyfile.average_ws(player, enemy, ws_name, tp_entry_value, ws_type, "Damage dealt", simulation=True, single=True, verbose=True)
            print()
            print()
            return

        elif trigger=="ws":
            output = actions_pyfile.average_ws(player, enemy, self.ws_selection_value.get(), tp_entry_value, ws_type, "Damage dealt")
            self.quicklook_results_damage_label1.config(text="Average Damage =")
            self.quicklook_results_tp_label1.config(text="Average TP =")
            self.quicklook_damage_value.set(output[1][0])
            self.quicklook_results_damage_label2.configure(text=f"{self.quicklook_damage_value.get():.0f}", anchor="e", font="courier", width=6)
            self.quicklook_tp_value.set(output[1][1])
            self.quicklook_results_tp_label2.configure(text=f"{self.quicklook_tp_value.get():.1f}", anchor="e", font="courier", width=6)

        elif trigger=="spell":

            assert spell_name != "None", "No spell selected."
                

            if "ton: " in spell_name.lower():
                spell_type = "Ninjutsu"
            elif spell_name.split()[-1].lower() == "shot":
                spell_type = "Quick Draw"
            elif "Banish" in spell_name or "Holy" in spell_name:
                spell_type = "Divine Magic"
            elif spell_name in ["Ranged Attack", "Barrage"]:
                spell_type = "Ranged Attack"
            else:
                spell_type = "Elemental Magic"

            output = actions_pyfile.cast_spell(player, enemy, spell_name, spell_type, "Damage dealt")
            self.quicklook_results_damage_label1.config(text="Average Damage =")
            self.quicklook_results_tp_label1.config(text="Average TP =")
            self.quicklook_damage_value.set(output[1][0])
            self.quicklook_results_damage_label2.configure(text=f"{self.quicklook_damage_value.get():.0f}", anchor="e", font="courier", width=6)
            self.quicklook_tp_value.set(output[1][1])
            self.quicklook_results_tp_label2.configure(text=f"{self.quicklook_tp_value.get():.1f}", anchor="e", font="courier", width=6)

        elif trigger=="tp":
            output = actions_pyfile.average_attack_round(player, enemy, 0, tp_entry_value, "Time to WS")
            self.quicklook_results_damage_label1.config(text="Time per WS =")
            self.quicklook_results_tp_label1.config(text="TP/round =")
            self.quicklook_damage_value.set(output[0])
            self.quicklook_results_damage_label2.configure(text=f"{self.quicklook_damage_value.get():.3f}", anchor="e", font="courier", width=6)
            self.quicklook_tp_value.set(output[1][1])
            self.quicklook_results_tp_label2.configure(text=f"{self.quicklook_tp_value.get():.1f}", anchor="e", font="courier", width=6)


        elif "optimize" in trigger:

            # Build the list of equipment to check based on the checkbox selections in each slot.
            # check_gear_dict is a dictionary containing lists of full gear.py item dictionaries
            
            check_gear_dict = {slot:[gear_pyfile.all_gear[item_name] for item_name in self.optimize_scrollframes[slot].get_selected()] for slot in self.quicklook_equipped_dict}

            # Print what is being considered in each slot.
            if False:
                for slot in check_gear_dict:
                    for item in check_gear_dict[slot]:
                        print(slot, item["Name2"])

            assert any([len(check_gear_dict[k])>1 for k in check_gear_dict]), "At least two items must be selected in at least one slot to find the best set."

            if "ws" in trigger:
                if not ws_name or ws_name=="None":
                    print("No weapon skill selected.")
                    return
                
            if "spell" in trigger:
                if not spell_name or spell_name=="None":
                    print("No spell selected.")
                    return

            special_toggles_dict["Verbose Swaps"] = self.verbose_swaps.get()
            starting_gearset = {slot:self.quicklook_equipped_dict[slot]["item"] for slot in self.quicklook_equipped_dict}

            actions = {
                        "optimize ws":    ["weapon skill", self.ws_metric_value.get()],
                        "optimize spell": ["spell cast", self.spell_metric_value.get()],
                        "optimize tp":    ["attack round", self.tp_metric_value.get()],
                    }

            try:
                restarts = max(1, self.optimizer_restarts_value.get())
                workers = max(0, self.optimizer_workers_value.get())
                seed_text = self.optimizer_seed_value.get().strip()
                seed = int(seed_text) if seed_text else None
            except ValueError:
                messagebox.showerror("WSDist optimizer", "Optimizer seed must be a whole number or blank.")
                return
            estimated_checks = wsdist_pyfile.estimate_candidate_checks(
                check_gear_dict, main_job, ws_type
            )
            print(
                f"Running optimizer with {restarts} restart(s); workers: "
                f"{'Auto' if workers == 0 else workers}; approximately "
                f"{estimated_checks:,} raw candidates per pass."
            )
            optimizer_args = (
                main_job, sub_job, master_level, active_buffs, special_toggles_dict, enemy,
                ws_name, spell_name, actions[trigger][0], tp_entry_value, check_gear_dict,
                starting_gearset, self.pdt_requirements_value.get(),
                self.mdt_requirements_value.get(), actions[trigger][1],
                self.show_similar_results_checkbox_value.get(), self.show_similar_results_entry_value.get(),
            )
            self._start_optimizer(
                optimizer_args,
                {"restarts": restarts, "workers": workers, "seed": seed, "return_details": True},
            )

        elif trigger == "run dps simulations":

            equipped_tp_gearset = {slot:self.tp_quicklook_equipped_dict[slot]["item"] for slot in self.tp_quicklook_equipped_dict}
            tp_player = create_player_pyfile.create_player(main_job, sub_job, master_level, gearset=equipped_tp_gearset, buffs=active_buffs, abilities=special_toggles_dict,)

            equipped_ws_gearset = {slot:self.ws_quicklook_equipped_dict[slot]["item"] for slot in self.ws_quicklook_equipped_dict}
            ws_player = create_player_pyfile.create_player(main_job, sub_job, master_level, gearset=equipped_ws_gearset, buffs=active_buffs, abilities=special_toggles_dict,)

            actions_pyfile.run_simulation(tp_player, ws_player, enemy, tp_entry_value, ws_name, ws_type, self.plot_dps_checkbox_value.get())

        elif trigger == "build distribution":

            equipped_ws_gearset = {slot:self.ws_quicklook_equipped_dict[slot]["item"] for slot in self.ws_quicklook_equipped_dict}
            ws_player = create_player_pyfile.create_player(main_job, sub_job, master_level, gearset=equipped_ws_gearset, buffs=active_buffs, abilities=special_toggles_dict,)

            damage_list = []
            tp_list = []
            for k in range(20000): # Sample 20,000 WSs with a fixed TP value
                outputs = actions_pyfile.average_ws(ws_player, enemy, ws_name, tp_entry_value, ws_type, "Damage dealt", simulation=True, single=True, verbose=False)
                damage_list.append(outputs[0])
                tp_list.append(outputs[1])

            fancy_plot_pyfile.plot_final(damage_list, ws_player, tp_entry_value, ws_name)

        elif trigger == "compare tp ws stats":
            # Build player stats
            equipped_tp_gearset = {slot:self.tp_quicklook_equipped_dict[slot]["item"] for slot in self.tp_quicklook_equipped_dict}
            tp_player = create_player_pyfile.create_player(main_job, sub_job, master_level, gearset=equipped_tp_gearset, buffs=active_buffs, abilities=special_toggles_dict,)

            equipped_ws_gearset = {slot:self.ws_quicklook_equipped_dict[slot]["item"] for slot in self.ws_quicklook_equipped_dict}
            ws_player = create_player_pyfile.create_player(main_job, sub_job, master_level, gearset=equipped_ws_gearset, buffs=active_buffs, abilities=special_toggles_dict,)

            # Calculte damage dealt by selected WS
            ws_output_tp = actions_pyfile.average_ws(tp_player, enemy, self.ws_selection_value.get(), tp_entry_value, ws_type, "Damage dealt")
            tp_player_wsdmg = int(ws_output_tp[0])
            ws_output_ws = actions_pyfile.average_ws(ws_player, enemy, self.ws_selection_value.get(), tp_entry_value, ws_type, "Damage dealt")
            ws_player_wsdmg = int(ws_output_ws[0])

            # Calculate time per WS
            tp_output_tp = actions_pyfile.average_attack_round(tp_player, enemy, 0, tp_entry_value, "Time to WS")
            tp_player_time = np.round(tp_output_tp[0], 3)
            tp_output_ws = actions_pyfile.average_attack_round(ws_player, enemy, 0, tp_entry_value, "Time to WS")
            ws_player_time = np.round(tp_output_ws[0], 3)

            if spell_name != "None":
                # Calculate damage dealt by selected spell
                if "ton: " in spell_name.lower():
                    spell_type = "Ninjutsu"
                elif spell_name.split()[-1].lower() == "shot":
                    spell_type = "Quick Draw"
                elif "Banish" in spell_name or "Holy" in spell_name:
                    spell_type = "Divine Magic"
                elif spell_name in ["Ranged Attack", "Barrage"]:
                    spell_type = "Ranged Attack"
                else:
                    spell_type = "Elemental Magic"
                spell_output_tp = actions_pyfile.cast_spell(tp_player, enemy, spell_name, spell_type, "Damage dealt")
                tp_player_spell = spell_output_tp[0]
                spell_output_ws = actions_pyfile.cast_spell(ws_player, enemy, spell_name, spell_type, "Damage dealt")
                ws_player_spell = spell_output_ws[0]

            max_stat_name_length = 0
            all_stats = ["Regain"]
            for stat in tp_player.stats:
                if stat not in all_stats:
                    all_stats.append(stat)
                if len(stat) > max_stat_name_length:
                    max_stat_name_length = len(stat)

            for stat in ws_player.stats:
                if stat not in all_stats:
                    all_stats.append(stat)
                if len(stat) > max_stat_name_length:
                    max_stat_name_length = len(stat)


            all_stats = sorted(all_stats)
            all_stats.append(f"Time per WS")
            all_stats.append(f"{ws_name} Damage")


            tp_player.stats[f"{ws_name} Damage"] = tp_player_wsdmg
            ws_player.stats[f"{ws_name} Damage"] = ws_player_wsdmg
            tp_player.stats["Time per WS"] = tp_player_time
            ws_player.stats["Time per WS"] = ws_player_time
            if spell_name != "None":
                all_stats.append(f"{spell_name} Damage")
                tp_player.stats[f"{spell_name} Damage"] = tp_player_spell
                ws_player.stats[f"{spell_name} Damage"] = ws_player_spell

            print("================================================")
            for stat in all_stats:

                tp_stat = tp_player.stats.get(stat, 0)
                ws_stat = ws_player.stats.get(stat, 0)
                
                # TODO: Move Gokotai regain to create_player.py and remove it from everywhere else.
                if stat.lower() == "regain":
                    tp_stat += tp_player.stats.get("Dual Wield",0)*(tp_player.gearset["main"]["Name"]=="Gokotai")
                    ws_stat += ws_player.stats.get("Dual Wield",0)*(ws_player.gearset["main"]["Name"]=="Gokotai")


                pet_stat = "pet:"==stat.lower()[:4]
                if pet_stat:
                    stat = stat[4:]

                if "haste" in stat.lower() or "delay reduction"==stat.lower() or "attack%" in stat.lower():
                    tp_stat = f"{tp_stat*100:.1f}%"
                    ws_stat = f"{ws_stat*100:.1f}%"
                
                elif "elemental bonus" in stat.lower() or "crit" in stat.lower() or stat.lower() in ["zanshin", "zanshin oa2", "daken", "kick attacks", "pdt", "mdt", "dt", "da", "ta", "qa", "double shot", "triple shot", "quad shot", *[f"oa{k} {j}" for j in ["main", "sub"] for k in range(2,9)]] or "pdl" in stat.lower() or "magic burst" in stat.lower() or "%" in stat.lower() or "weapon skill damage" in stat.lower() or "skillchain" in stat.lower():
                    tp_stat = f"{tp_stat:.0f}%"
                    ws_stat = f"{ws_stat:.0f}%"
                
                elif "attack" in stat.lower() or "accuracy" in stat.lower() or "evasion" in stat.lower() or "store tp"==stat.lower():
                    tp_stat = f"{tp_stat:.0f}"
                    ws_stat = f"{ws_stat:.0f}"

                if pet_stat:
                    stat = "Pet:" + stat

                if stat.lower() == "wsc":
                
                    if isinstance(tp_stat, list):
                        param, tp_stat = tp_stat[0]
                    else:
                        tp_stat = 0
                
                    if isinstance(ws_stat, list):
                        param, ws_stat = ws_stat[0]
                    else:
                        ws_stat = 0
                    tp_stat = f"{tp_stat:.0f}%"
                    ws_stat = f"{ws_stat:.0f}%"

                    stat = f"{stat}:{param}"

                print(f"{stat:>{max_stat_name_length}}   {tp_stat:>10}   {ws_stat:<10}")

        if "show stats" in trigger:

            if "quicklook" in trigger:
                equipped_gearset = {slot:self.quicklook_equipped_dict[slot]["item"] for slot in self.quicklook_equipped_dict}

            elif "tp" in trigger:
                equipped_gearset = {slot:self.tp_quicklook_equipped_dict[slot]["item"] for slot in self.tp_quicklook_equipped_dict}

            elif "ws" in trigger:
                equipped_gearset = {slot:self.ws_quicklook_equipped_dict[slot]["item"] for slot in self.ws_quicklook_equipped_dict}

            player = create_player_pyfile.create_player(main_job, sub_job, master_level, gearset=equipped_gearset, buffs=active_buffs, abilities=special_toggles_dict,)

            for stat in self.stats_dict:

                value = player.stats.get(stat, 0)
                if "haste" in stat.lower() or "delay reduction"==stat.lower():
                    if stat.lower() in ["gear haste", "ja haste"]:
                        value = 256./1024 if value > 256./1024 else value
                    elif stat.lower() == "magic haste":
                        value = 448./1024 if value > 448./1024 else value

                    value = f"{value*100:.1f}%"
                
                elif "crit" in stat.lower() or stat.lower() in ["zanshin", "zanshin oa2", "daken", "kick attacks", "pdt", "mdt", "dt", "da", "ta", "qa", "double shot", "triple shot", "quad shot", *[f"oa{k} {j}" for j in ["main", "sub"] for k in range(2,9)]] or "pdl" in stat.lower() or "magic burst" in stat.lower() or "%" in stat.lower() or "weapon skill damage" in stat.lower() or "skillchain" in stat.lower():
                    value = f"{value:.0f}%"
                
                elif "attack" in stat.lower() or "accuracy" in stat.lower() or "evasion" in stat.lower() or "store tp"==stat.lower():
                    value = f"{value:.0f}"

                if stat.lower() == "regain":
                    value += player.stats.get("Dual Wield",0)*(player.gearset["main"]["Name"]=="Gokotai")

                self.stats_dict[stat]["stringvar"].set(value)


    def update_visible_quicklook_frame(self, slot):
        '''
        When clicking the quicklook equipment icons.
        Map only the selected slot's frame. Keeping all overlapping virtual
        frames mapped makes Windows redraw every hidden picker while moving the
        main window.
        Update the self.visible_quicklook_frame_slot variable
        '''
        for frame in self.quicklook_scrollframes.values():
            frame.place_forget()
        self.quicklook_scrollframes[slot].place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
        self.visible_quicklook_frame_slot = slot

    def update_visible_quicklook_frame_tp(self, slot):
        '''
        When clicking the quicklook equipment icons.
        Use tkraise() to raise the selected slot's frame to the top and make it visible.
        Update the self.visible_quicklook_frame_slot variable
        '''
        for frame in self.tp_quicklook_scrollframes.values():
            frame.place_forget()
        self.tp_quicklook_scrollframes[slot].place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
        self.tp_visible_quicklook_frame_slot = slot

    def update_visible_quicklook_frame_ws(self, slot):
        '''
        When clicking the quicklook equipment icons.
        Use tkraise() to raise the selected slot's frame to the top and make it visible.
        Update the self.visible_quicklook_frame_slot variable
        '''
        for frame in self.ws_quicklook_scrollframes.values():
            frame.place_forget()
        self.ws_quicklook_scrollframes[slot].place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
        self.ws_visible_quicklook_frame_slot = slot

    def update_visible_optimize_frame(self, slot):
        '''
        When clicking the slot buttons in the optimize tab.
        Use tkraise() to raise the selected slot's frame to the top and make it visible.
        Update the self.visible_optimize_frame_slot variable
        '''
        for frame in self.optimize_scrollframes.values():
            frame.place_forget()
        self.optimize_scrollframes[slot].place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
        self.visible_optimize_frame_slot = slot


    def test_gui(self,):
        '''
        Run a series of generic tests to ensure there are no errors
        TODO: Determine proper tests to implement...
        '''
        
        main_job = self.jobs_dict[self.main_job_value.get()]
        sub_job = self.jobs_dict.get(self.sub_job_value.get(), "None")
        master_level = self.master_level_value.get()

        ws_name = self.ws_selection_value.get()
        ws_type = "ranged" if ws_name in (self.ws_dict["Marksmanship"]+self.ws_dict["Archery"]) else "melee"
        spell_name = self.spell_selection_value.get()

        tp_entry_value = self.tp_entry_value.get()

        special_toggles_dict = {k:self.all_special_toggles_dict[k]["boolvar"].get() for k in self.all_special_toggles_dict if k not in buffs_pyfile.misc_debuffs}
        special_toggles_dict["Enhancing Skill"] = self.enhancing_skill_value.get()
        special_toggles_dict["Aftermath"] = self.aftermath_value.get()
        special_toggles_dict["Storm spell"] = self.whm_selections_dict["Storm"]
        special_toggles_dict["Enemy Resist Rank"] = self.selected_enemy_variables["Resist Rank"].get()
        special_toggles_dict["99999"] = True

        active_buffs, active_debuffs = self.aggregate_buffs()

        # Build a player character with one piece of gear equipped, checking all possible items one at a time.
        for slot in gear_pyfile.gear_dict:
            for item in gear_pyfile.gear_dict[slot]:
                equipped_gearset = {slot0:gear_pyfile.Empty for slot0 in gear_pyfile.gear_dict}
                equipped_gearset[slot] = item
                player = create_player_pyfile.create_player(main_job, sub_job, master_level, gearset=equipped_gearset, buffs=active_buffs, abilities=special_toggles_dict,)

        random_stats = ["Magic Haste", "Attack%", "Ranged Attack%"]
        for slot in gear_pyfile.gear_dict:
            if slot not in ["main", "sub", "ranged", "ammo"]:
                for item in gear_pyfile.gear_dict[slot]:
                    for stat in item:
                        if stat not in random_stats and stat not in ["Name", "Name2", "Jobs"]:
                            random_stats.append(stat)

        spell_list = [k for job in self.spells_dict for k in self.spells_dict[job]]

        # Build N random ML, job, buffs, toggles, ws, spell, combinations and return WS damage
        for i in range(10000):
            special_toggles_dict = {k:self.all_special_toggles_dict[k]["boolvar"].get() for k in self.all_special_toggles_dict if k not in buffs_pyfile.misc_debuffs}
            special_toggles_dict["99999"] = True
            special_toggles_dict_random = {}
            for ability_name in special_toggles_dict:
                if isinstance(special_toggles_dict[ability_name], bool):
                    special_toggles_dict_random[ability_name] = np.random.uniform() < 0.5
            
                special_toggles_dict_random["Enhancing Skill"] = np.random.randint(0, 700)
                special_toggles_dict_random["Aftermath"] = np.random.choice([0,1,2,3])
                special_toggles_dict_random["Storm spell"] = np.random.choice(self.storm_spell_list)
                special_toggles_dict_random["Enemy Resist Rank"] = np.random.choice(self.enemy_resist_ranks_list)
                

            main_job = np.random.choice(list(self.jobs_dict.values()))
            sub_job = np.random.choice(list(self.jobs_dict.values()) + ["None"])
            master_level = np.random.randint(0, 51)

            equipped_gearset = {slot:np.random.choice(gear_pyfile.gear_dict[slot]) for slot in gear_pyfile.gear_dict}
            while equipped_gearset["main"]["Name"] == "Empty":
                equipped_gearset["main"] = np.random.choice(gear_pyfile.gear_dict["main"])

            main_skill_type = equipped_gearset["main"]["Skill Type"]
            ranged_skill_type = equipped_gearset["ranged"].get("Skill Type", "None")

            ws_list = list(self.ws_dict[main_skill_type])
            if ranged_skill_type in self.ws_dict and ranged_skill_type != "None":
                ws_list = ws_list + self.ws_dict[ranged_skill_type]

                ranged_type = equipped_gearset["ranged"].get("Type", "None")
                if ranged_type == "Crossbow":
                    ammo_type = "Bolt"
                elif ranged_type == "Gun":
                    ammo_type = "Bullet"
                elif ranged_type == "Bow":
                    ammo_type = "Arrow"
                
                forced_ammo = np.random.choice([k for k in gear_pyfile.ammos if k.get("Type", "None")==ammo_type])
                equipped_gearset["ammo"] = forced_ammo

            ws_name = np.random.choice(ws_list)

            ws_type = "ranged" if ws_name in (self.ws_dict["Marksmanship"]+self.ws_dict["Archery"]) else "melee"

            tp_entry_value = np.random.randint(1000,3001)

            player = create_player_pyfile.create_player(main_job, sub_job, master_level, gearset=equipped_gearset, buffs=active_buffs, abilities=special_toggles_dict_random,)

            useful_enemy_stats = {stat:self.selected_enemy_variables[stat].get() for stat in self.selected_enemy_variables if stat not in ["Level", "Location", "Name", "Resist Rank"]}
            enemy = create_player_pyfile.create_enemy(useful_enemy_stats)
            for stat in active_debuffs:
                if "requirement" in stat.lower():
                    continue
                if stat == "Defense":
                    enemy.stats["Defense"] *= (1 - active_debuffs.get("Defense", 0))
                else:
                    enemy.stats[stat] -= active_debuffs[stat]
            enemy.stats["Base Defense"] = useful_enemy_stats["Defense"]
            enemy.stats["Magic Defense"] = max(-50, enemy.stats.get("Magic Defense", 0)) # Enemy Magic Defense can not be brought lower than -50 (magic damage taken x2)
            enemy.stats["Magic Damage Taken"] = enemy.stats.pop("Magic DT%")

            active_buffs = {"cor":{}, "brd":{}, "whm":{}}
            for job in active_buffs:
                if np.random.uniform() < 0.1:
                    continue
                for stat in random_stats:
                    if np.random.uniform() < 0.8:
                        continue
                    if stat in ["Crit Rate", "Crit Damage", "ftp"] or "%" in stat or "haste" in stat.lower():
                        active_buffs[job][stat] = np.random.uniform(0, 0.5)
                    elif stat in ["DA", "TA", "QA", *[f"OA{x} {j}" for x in [2,3,4,5,6,7,8] for j in ["main", "sub"]]]:
                        active_buffs[job][stat] = np.random.uniform(0, 50)
                    elif stat == "Fencer":
                        active_buffs[job][stat] = np.random.randint(0,9)
                    else:
                        active_buffs[job][stat] = np.random.uniform(0, 200)

                                             
            spell_name = np.random.choice(np.unique(spell_list))
            while spell_name in ["Barrage", "Ranged Attack"] and equipped_gearset["ranged"]["Skill Type"] not in ["Marksmanship", "Archery"]:
                spell_name = np.random.choice(np.unique(spell_list))

            if "ton: " in spell_name.lower():
                spell_type = "Ninjutsu"
            elif spell_name.split()[-1].lower() == "shot":
                spell_type = "Quick Draw"
            elif "Banish" in spell_name or "Holy" in spell_name:
                spell_type = "Divine Magic"
            elif spell_name in ["Ranged Attack", "Barrage"]:
                spell_type = "Ranged Attack"
            else:
                spell_type = "Elemental Magic"

            output_ws = actions_pyfile.average_ws(player, enemy, ws_name, tp_entry_value, ws_type, "Damage dealt")
            output_spell = actions_pyfile.cast_spell(player, enemy, spell_name, spell_type, "Damage dealt")
            output_tp = actions_pyfile.average_attack_round(player, enemy, 0, tp_entry_value, "Time to WS")

            # print(main_job, sub_job, ws_name, spell_name, output_tp[0], output_ws[0], output_spell[0])

    def __init__(self):
        super().__init__()
        self.bridge_store = BridgeStore()

        # Bind the "q" key to close the application.
        self.bind_all("q", lambda e: self.destroy())

        mystyle = ttk.Style()
        mystyle.theme_use('vista') # 'winnative', 'clam', 'alt', 'default', 'classic', 'vista', 'xpnative'

        self.title("Kastra FFXI Damage Simulator  (2026 May 21a)") # pyinstaller --exclude-module gear --exclude-module enemies --clean --onefile --icon=icons32/23937.ico gui_main.py
        self.geometry("700x850")
        self.resizable(False, False)
        app_icon_path = resource_path("icons32", "23937.png")
        if os.path.isfile(app_icon_path):
            self.app_icon = tk.PhotoImage(file=app_icon_path)
            self.iconphoto(True, self.app_icon)

        # Define the GUI tabs as a "notebook" of pages
        # https://www.pythontutorial.net/tkinter/tkinter-notebook/
        self.bridge_status_value = tk.StringVar(value="WSDist bridge: not loaded")
        ttk.Label(self, textvariable=self.bridge_status_value, anchor="w").pack(
            side="bottom", fill="x", padx=5, pady=(0, 2)
        )
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(side="top", expand=True, fill="both")

        inputs_tab = ttk.Frame(self.notebook)
        self.notebook.add(inputs_tab, text="Quicklook")

        inputs_tab.rowconfigure((0,1,2), weight=1)
        inputs_tab.columnconfigure(0, weight=1)
        self.bind_all("<Control-Key-1>", lambda e: self.notebook.select(inputs_tab))

        optimize_tab = ttk.Frame(self.notebook)
        self.notebook.add(optimize_tab, text="Optimize")
        self.bind_all("<Control-Key-2>", lambda e: self.notebook.select(optimize_tab))

        # tkinter Menu  https://blog.teclado.com/how-to-add-menu-to-tkinter-app/
        self.menu_bar = tk.Menu(self)
        self.file_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.file_menu.add_command(label="Save Defaults", command=self.save_defaults)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Load WSDist character bridge...", command=self.choose_wsdist_bridge)
        self.file_menu.add_command(label="Refresh WSDist character bridge", command=self.refresh_wsdist_bridge)
        self.file_menu.add_command(label="Import LuAshitacast set...", command=self.import_luashitacast_set)
        self.file_menu.add_command(label="Save Quick Look to LuAshitacast...", command=lambda: self.save_luashitacast_set("quicklook"))
        self.file_menu.add_command(label="Save TP set to LuAshitacast...", command=lambda: self.save_luashitacast_set("tp"))
        self.file_menu.add_command(label="Save WS set to LuAshitacast...", command=lambda: self.save_luashitacast_set("ws"))
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Reload gear.py", command=self.reload_gear_pyfile)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Close GUI (q)", command=self.destroy)
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)

        self.settings_menu = tk.Menu(self.menu_bar, tearoff=False)

        self.damage_limit99999 = tk.BooleanVar()
        self.settings_menu.add_checkbutton(label="Damage Limit (DPS)", onvalue=True, offvalue=False, variable=self.damage_limit99999)

        self.verbose_swaps = tk.BooleanVar()
        self.settings_menu.add_checkbutton(label="Verbose Swaps", onvalue=True, offvalue=False, variable=self.verbose_swaps)

        self.menu_bar.add_cascade(label="Settings", menu=self.settings_menu)
        
        # self.debug_menu = tk.Menu(self.menu_bar, tearoff=False)
        # self.debug_menu.add_command(label="Test GUI", command=self.test_gui)
        # self.menu_bar.add_cascade(label="Test", menu=self.debug_menu, state="active")

        self.config(menu=self.menu_bar)


        '''
        ===============================================
            Define re-usable lists and dictionaries
        ===============================================
        '''

        item_tmp = np.loadtxt(resource_path("item_list.csv"), delimiter=";", skiprows=1, dtype=str, unpack=True)
        self.item_id_dict = {"id":item_tmp[0], "name":item_tmp[1], "name2":item_tmp[2]}

        self.all_equipment_dict = {
            "main":gear_pyfile.mains,
            "sub":gear_pyfile.subs + gear_pyfile.grips,
            "ranged":gear_pyfile.ranged,
            "ammo":gear_pyfile.ammos,
            "head":gear_pyfile.heads,
            "neck":gear_pyfile.necks,
            "ear1":gear_pyfile.ears,
            "ear2":gear_pyfile.ears2,
            "body":gear_pyfile.bodies,
            "hands":gear_pyfile.hands,
            "ring1":gear_pyfile.rings,
            "ring2":gear_pyfile.rings2,
            "back":gear_pyfile.capes,
            "waist":gear_pyfile.waists,
            "legs":gear_pyfile.legs,
            "feet":gear_pyfile.feet,
            }

        self.ws_dict = {
            "Katana": ["Blade: Retsu", "Blade: Teki", "Blade: To", "Blade: Chi", "Blade: Ei", "Blade: Jin", "Blade: Ten", "Blade: Ku", "Blade: Yu", "Blade: Metsu", "Blade: Kamu", "Blade: Hi", "Blade: Shun", "Zesho Meppo",],
            "Great Katana": ["Tachi: Enpi", "Tachi: Goten", "Tachi: Kagero", "Tachi: Jinpu", "Tachi: Koki", "Tachi: Yukikaze", "Tachi: Gekko", "Tachi: Kasha", "Tachi: Ageha", "Tachi: Kaiten", "Tachi: Rana", "Tachi: Fudo", "Tachi: Shoha", "Tachi: Mumei",],
            "Dagger": [ "Viper Bite", "Dancing Edge", "Shark Bite", "Evisceration", "Aeolian Edge", "Mercy Stroke", "Mandalic Stab", "Mordant Rime", "Pyrrhic Kleos", "Rudra's Storm", "Exenterator", "Ruthless Stroke",],
            "Sword": ["Fast Blade", "Fast Blade II", "Burning Blade", "Red Lotus Blade", "Seraph Blade", "Circle Blade", "Swift Blade", "Savage Blade", "Sanguine Blade", "Knights of Round", "Death Blossom", "Expiacion", "Chant du Cygne", "Requiescat", "Imperator",],
            "Scythe": ["Slice", "Dark Harvest", "Shadow of Death", "Nightmare Scythe", "Spinning Scythe", "Guillotine", "Cross Reaper", "Spiral Hell", "Infernal Scythe", "Catastrophe", "Quietus", "Insurgency", "Entropy", "Origin",], 
            "Great Sword": ["Hard Slash", "Freezebite", "Shockwave", "Sickle Moon", "Spinning Slash", "Ground Strike", "Herculean Slash", "Resolution", "Scourge", "Dimidiation", "Torcleaver", "Fimbulvetr",], 
            "Club": ["Shining Strike", "Seraph Strike", "Skullbreaker", "True Strike", "Judgment", "Hexa Strike", "Black Halo", "Randgrith", "Exudation", "Mystic Boon", "Realmrazer", "Dagda",], 
            "Polearm": ["Double Thrust", "Thunder Thrust", "Raiden Thrust", "Penta Thrust", "Wheeling Thrust", "Impulse Drive", "Sonic Thrust", "Geirskogul", "Drakesbane", "Camlann's Torment", "Stardiver", "Diarmuid",], 
            "Staff": ["Heavy Swing", "Rock Crusher", "Earth Crusher", "Starburst", "Sunburst", "Shell Crusher", "Full Swing", "Cataclysm", "Retribution", "Gate of Tartarus", "Omniscience", "Vidohunir", "Garland of Bliss", "Shattersoul", "Oshala",], 
            "Great Axe": ["Iron Tempest", "Shield Break", "Armor Break", "Weapon Break", "Raging Rush", "Full Break", "Steel Cyclone", "Fell Cleave", "Metatron Torment", "King's Justice", "Ukko's Fury", "Upheaval", "Disaster",], 
            "Axe": ["Raging Axe", "Spinning Axe", "Rampage", "Calamity", "Mistral Axe", "Decimation", "Bora Axe", "Onslaught", "Primal Rend", "Cloudsplitter", "Ruinator", "Blitz",], 
            "Archery": ["Flaming Arrow", "Piercing Arrow", "Dulling Arrow", "Sidewinder", "Blast Arrow", "Empyreal Arrow", "Refulgent Arrow", "Namas Arrow", "Jishnu's Radiance", "Apex Arrow", "Sarv",], 
            "Marksmanship": ["Hot Shot", "Split Shot", "Sniper Shot", "Slug Shot", "Blast Shot", "Detonator", "Coronach", "Leaden Salute", "Trueflight", "Wildfire", "Last Stand", "Terminus",], 
            "Hand-to-Hand": ["Combo", "One Inch Punch", "Raging Fists", "Spinning Attack", "Howling Fist", "Dragon Kick", "Asuran Fists", "Tornado Kick", "Ascetic's Fury", "Stringing Pummel", "Final Heaven", "Victory Smite", "Shijin Spiral", "Maru Kala", "Dragon Blow",],
            "None": ["None"],
            }

        self.jobs_dict = {"Ninja":"nin", "Dark Knight":"drk", "Scholar":"sch", "Red Mage":"rdm", "Black Mage":"blm", "Samurai":"sam", "Dragoon":"drg", "White Mage":"whm", "Warrior":"war", "Corsair":"cor", "Bard":"brd", "Thief":"thf", "Monk":"mnk", "Dancer":"dnc", "Beastmaster":"bst", "Rune Fencer":"run", "Ranger":"rng", "Puppetmaster":"pup", "Blue Mage":"blu", "Geomancer":"geo", "Paladin":"pld", "Summoner":"smn"}

        self.rema_weapons = [
                        "Amanomurakumo", "Annihilator", "Apocalypse", "Bravura", "Excalibur", "Gungnir", "Guttler", "Kikoku", "Mandau", "Mjollnir", "Ragnarok", "Spharai", "Yoichinoyumi",
                        "Almace", "Armageddon", "Caladbolg", "Farsha", "Gandiva", "Kannagi", "Masamune", "Redemption", "Rhongomiant", "Twashtar", "Ukonvasara", "Verethragna", "Hvergelmir",
                        "Aymur", "Burtgang", "Carnwenhan", "Conqueror", "Death Penalty", "Gastraphetes", "Glanzfaust", "Kenkonken", "Kogarasumaru", "Laevateinn", "Liberator", "Murgleis", "Nagi", "Ryunohige", "Terpsichore", "Tizona", "Tupsimati", "Nirvana", "Vajra", "Yagrush", 
                        "Epeolatry", "Idris",
                        "Aeneas", "Anguta", "Chango", "Dojikiri Yasutsuna", "Fail-not", "Fomalhaut", "Godhands", "Heishi Shorinken", "Khatvanga", "Lionheart", "Sequence", "Tishtrya", "Tri-edge", "Trishula",
                        ]



        self.spells_dict = {
                    "nin":["Doton: Ichi", "Doton: Ni", "Doton: San",
                          "Suiton: Ichi", "Suiton: Ni", "Suiton: San",
                          "Huton: Ichi", "Huton: Ni", "Huton: San",
                          "Katon: Ichi", "Katon: Ni", "Katon: San",
                          "Hyoton: Ichi", "Hyoton: Ni", "Hyoton: San",
                          "Raiton: Ichi", "Raiton: Ni", "Raiton: San",
                          "Ranged Attack"],
                    "blm":["Stone", "Stone II", "Stone III", "Stone IV", "Stone V", "Stone VI", "Stoneja",
                           "Water", "Water II", "Water III", "Water IV", "Water V", "Water VI", "Waterja",
                           "Aero", "Aero II", "Aero III", "Aero IV", "Aero V", "Aero VI", "Aeroja",
                           "Fire", "Fire II", "Fire III", "Fire IV", "Fire V", "Fire VI", "Firaja",
                           "Blizzard", "Blizzard II", "Blizzard III", "Blizzard IV", "Blizzard V", "Blizzard VI", "Blizzaja",
                           "Thunder", "Thunder II", "Thunder III", "Thunder IV", "Thunder V", "Thunder VI", "Thundaja", "Impact",
                           "Ranged Attack"],
                    "rdm":["EnSpell", 
                           "Stone", "Stone II", "Stone III", "Stone IV", "Stone V",
                           "Water", "Water II", "Water III", "Water IV", "Water V",
                           "Aero", "Aero II", "Aero III", "Aero IV", "Aero V",
                           "Fire", "Fire II", "Fire III", "Fire IV", "Fire V",
                           "Blizzard", "Blizzard II", "Blizzard III", "Blizzard IV", "Blizzard V",
                           "Thunder", "Thunder II", "Thunder III", "Thunder IV", "Thunder V", "Impact", "Ranged Attack"],
                    "geo":["Stone", "Stone II", "Stone III", "Stone IV", "Stone V",
                           "Water", "Water II", "Water III", "Water IV", "Water V",
                           "Aero", "Aero II", "Aero III", "Aero IV", "Aero V",
                           "Fire", "Fire II", "Fire III", "Fire IV", "Fire V",
                           "Blizzard", "Blizzard II", "Blizzard III", "Blizzard IV", "Blizzard V",
                           "Thunder", "Thunder II", "Thunder III", "Thunder IV", "Thunder V", "Impact"],
                    "sch":["Stone", "Stone II", "Stone III", "Stone IV", "Stone V", "Geohelix II",
                           "Water", "Water II", "Water III", "Water IV", "Water V", "Hydrohelix II",
                           "Aero", "Aero II", "Aero III", "Aero IV", "Aero V", "Anemohelix II",
                           "Fire", "Fire II", "Fire III", "Fire IV", "Fire V", "Pyrohelix II",
                           "Blizzard", "Blizzard II", "Blizzard III", "Blizzard IV", "Blizzard V", "Cryohelix II",
                           "Thunder", "Thunder II", "Thunder III", "Thunder IV", "Thunder V", "Ionohelix II",
                           "Luminohelix II", "Noctohelix II", "Kaustra", "Impact",],
                    "drk":["Stone", "Stone II", "Stone III",
                           "Water", "Water II", "Water III",
                           "Aero", "Aero II", "Aero III",
                           "Fire", "Fire II", "Fire III",
                           "Blizzard", "Blizzard II", "Blizzard III",
                           "Thunder", "Thunder II", "Thunder III", "Impact"],
                    "cor":["Ranged Attack", "Earth Shot", "Water Shot", "Wind Shot", "Fire Shot", "Ice Shot", "Thunder Shot"],
                    "rng":["Ranged Attack"],
                    "sam":["Ranged Attack"],
                    "thf":["Ranged Attack"],
                    }

        self.equipment_button_positions = {
            "main":  [0,0],
            "sub":   [0,1],
            "ranged":[0,2],
            "ammo":  [0,3],
            "head":  [1,0],
            "neck":  [1,1],
            "ear1":  [1,2],
            "ear2":  [1,3],
            "body":  [2,0],
            "hands": [2,1],
            "ring1": [2,2],
            "ring2": [2,3],
            "back":  [3,0],
            "waist": [3,1],
            "legs":  [3,2],
            "feet":  [3,3]
        }

        '''
        ==============================================================================================
            Build the frame containing player inputs
        ==============================================================================================
        '''
        inputs_frame = ttk.Frame(inputs_tab)
        inputs_frame.grid(row=0, column=0,)
        basic_inputs_frame = ttk.LabelFrame(inputs_frame, borderwidth=2, padding=5, text="  Basic Inputs  ", width=210, height=210, labelanchor="nw")
        basic_inputs_frame.grid(row=0, column=0, sticky="NEWS", padx=2)
        basic_inputs_frame.rowconfigure(0, weight=1)
        basic_inputs_frame.columnconfigure((0,1,2), weight=1)

        self.master_level_value = tk.IntVar(value=30)
        master_level_text = ttk.Label(basic_inputs_frame, text="Master Lv: ", justify="right", width=13)
        master_level_text.grid(row=0, column=0, pady=2)
        self.master_level_combobox = ttk.Combobox(basic_inputs_frame, textvariable=self.master_level_value, values=tuple(np.arange(50, -1, -1)), state="readonly", width=18, name="defaults_ml_combobox")
        self.master_level_combobox.bind('<<ComboboxSelected>>', lambda event, entry=self.master_level_combobox: self.update_job("master level"))
        self.master_level_combobox.grid(row=0, column=1, padx=0, pady=2, sticky="w")

        self.main_job_value = tk.StringVar(value="Scholar")
        main_job_selection_text = ttk.Label(basic_inputs_frame, text="Main Job: ", width=13)
        main_job_selection_text.grid(row=1, column=0, pady=2)
        main_job_selection_combobox = ttk.Combobox(basic_inputs_frame, textvariable=self.main_job_value, values=sorted(self.jobs_dict), state="readonly", width=18, name="defaults_mainjob_combobox")
        main_job_selection_combobox.grid(row=1, column=1, padx=0, pady=2, sticky="w")
        main_job_selection_combobox.bind('<<ComboboxSelected>>', lambda event, entry=main_job_selection_combobox: self.update_job("main"))

        sub_job_selection_text = ttk.Label(basic_inputs_frame, text="Sub Job: ", width=13)
        self.sub_job_value = tk.StringVar(value="Red Mage")
        self.sub_job_level = tk.IntVar(value=49 + self.master_level_value.get()//5)
        sub_job_selection_text.grid(row=2, column=0, pady=2)
        self.sub_job_selection_combobox = ttk.Combobox(basic_inputs_frame, textvariable=self.sub_job_value, values=sorted(self.jobs_dict)+["None"], state="readonly", width=18, name="defaults_subjob_combobox")
        self.sub_job_selection_combobox.grid(row=2, column=1, padx=0, pady=2, sticky="w")
        self.sub_job_selection_combobox.bind('<<ComboboxSelected>>', lambda event, entry=self.sub_job_selection_combobox: self.update_job("sub"))

        self.aftermath_value = tk.IntVar(value=0)
        aftermath_text = ttk.Label(basic_inputs_frame, text="Aftermath Lv: ", justify="right", width=13)
        aftermath_text.grid(row=3, column=0, pady=2)
        aftermath_combobox = ttk.Combobox(basic_inputs_frame, textvariable=self.aftermath_value, values=[3, 2, 1, 0], state="readonly", width=18, name="defaults_aftermath_combobox")
        aftermath_combobox.grid(row=3, column=1, padx=0, pady=2, sticky="w")

        self.spell_selection_value = tk.StringVar(value="")
        spell_selection_text = ttk.Label(basic_inputs_frame, text="Spell: ", width=13)
        spell_selection_text.grid(row=4, column=0, pady=2)

        spell_list = list(self.spells_dict[self.jobs_dict[self.main_job_value.get()]])
        self.spell_selection_combobox = ttk.Combobox(basic_inputs_frame, textvariable=self.spell_selection_value, values=spell_list, state="readonly", width=18, name="defaults_spell_combobox")
        self.spell_selection_combobox.grid(row=4, column=1, padx=0, pady=2, sticky="w")

        self.wpn_type_main = "None"
        self.wpn_type_ranged = "None"
        self.ws_selection_value = tk.StringVar(value="")
        ws_selection_text = ttk.Label(basic_inputs_frame, text="Weapon Skill: ", width=13)
        ws_selection_text.grid(row=5, column=0, pady=2)
        self.ws_selection_combobox = ttk.Combobox(basic_inputs_frame, textvariable=self.ws_selection_value, values=self.ws_dict[self.wpn_type_main] + self.ws_dict[self.wpn_type_ranged], state="readonly", width=18, name="defaults_ws_combobox")
        self.ws_selection_combobox.grid(row=5, column=1, padx=0, pady=2, sticky="w")

        self.tp_entry_value = tk.IntVar(value=1900)
        tp_entry_text = ttk.Label(basic_inputs_frame, text="TP Value: ", width=13)
        tp_entry_text.grid(row=6, column=0, pady=2,)
        self.tp_entry_box = ttk.Entry(basic_inputs_frame, textvariable=self.tp_entry_value, width=6, justify="center", name="defaults_tp_entry", validate="key", validatecommand=(self.register(self.validate_numerical), "%P")) # validate="key" activates the validates on individual keystrokes
        self.tp_entry_box.grid(row=6, column=1, padx=0, pady=2, sticky="ew")
        self.tp_entry_box.bind("<FocusOut>", self.validate_tp_value)
        self.tp_entry_box.bind("<MouseWheel>", lambda event:
                                                self.tp_entry_value.set( 3000 if (self.tp_entry_value.get() + (event.delta/120)*100) > 3000
                                                                        else 1000 if (self.tp_entry_value.get() + (event.delta/120)*100) < 1000
                                                                        else int(self.tp_entry_value.get() + (event.delta/120)*100)
                                                                        )
                                )

        '''
        ===============================================
          Build the frame containing player abilities
        ===============================================
        '''
        sf = ScrollableLabelFrame(inputs_frame, text="  Special Toggles  ", width=200, height=210)
        sf.grid_propagate(False)

        # Be careful here. The buff names here must match exactly what is presented in the "buffs.py" file under "misc_buffs" dict. # TODO: move this to buffs.py with debuffs
        self.all_special_toggles_dict = dict(sorted({
            "Aggressor":          {"level requirement":45, "job requirement":["war"]}, 
            "Barrage":            {"level requirement":30, "job requirement":["rng"]}, 
            "Berserk":            {"level requirement":15, "job requirement":["war"]}, 
            "Blood Rage":         {"level requirement":87, "job requirement":["war"]}, 
            "Building Flourish":  {"level requirement":50, "job requirement":["dnc"]}, 
            "Chainspell":         {"level requirement":99, "job requirement":["rdm"]}, 
            "Climactic Flourish": {"level requirement":80, "job requirement":["dnc"]}, 
            "Closed Position":    {"level requirement":99, "job requirement":["dnc"]}, 
            "Composure":          {"level requirement":99, "job requirement":["rdm"]}, 
            "Conspirator":        {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Crimson Howl":       {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Crystal Blessing":   {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Divine Emblem":      {"level requirement":78, "job requirement":["pld"]}, 
            "Double Shot":        {"level requirement":79, "job requirement":["rng"]}, 
            "Ebullience":         {"level requirement":55, "job requirement":["sch"]}, 
            "EnSpell":            {"level requirement":27, "job requirement":["rdm"]}, 
            "Endark II":          {"level requirement":99, "job requirement":["drk"]}, 
            "Enlight II":         {"level requirement":99, "job requirement":["pld"]}, 
            "Enlightenment":      {"level requirement":99, "job requirement":["sch"]}, 
            "Focus":              {"level requirement":25, "job requirement":["mnk"]}, 
            "Footwork":           {"level requirement":65, "job requirement":["mnk"]}, 
            "Frenzied Rage":      {"level requirement":99, "job requirement":["bst"]}, 
            "Futae":              {"level requirement":99, "job requirement":["nin"]}, 
            "Hasso":              {"level requirement":25, "job requirement":["sam"]}, 
            "Haste Samba":        {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Haste Samba (sub)":  {"level requirement":45, "job requirement":["dnc"]}, 
            "Hover Shot":         {"level requirement":99, "job requirement":["rng"]}, 
            "Ifrit's Favor":      {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Impetus":            {"level requirement":88, "job requirement":["mnk"]}, 
            "Innin":              {"level requirement":99, "job requirement":["nin"]}, 
            "Klimaform":          {"level requirement":46, "job requirement":["sch"]}, 
            "Last Resort":        {"level requirement":15, "job requirement":["drk"]}, 
            "Magic Burst":        {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Manafont":           {"level requirement":99, "job requirement":["blm"]}, 
            "Manawell":           {"level requirement":99, "job requirement":["blm"]}, 
            "Mighty Guard":       {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Mighty Strikes":     {"level requirement":99, "job requirement":["war"]}, 
            "Nature's Meditation":{"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Overwhelm":          {"level requirement":99, "job requirement":["sam"]}, 
            "Rage":               {"level requirement":99, "job requirement":["bst"]}, 
            "Ramuh's Favor":      {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Saber Dance":        {"level requirement":99, "job requirement":["dnc"]}, 
            "Sange":              {"level requirement":99, "job requirement":["nin"]}, 
            "Sharpshot":          {"level requirement":1,  "job requirement":["rng"]}, 
            "Shiva's Favor":      {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Sneak Attack":       {"level requirement":15, "job requirement":["thf"]}, 
            "Striking Flourish":  {"level requirement":89, "job requirement":["dnc"]}, 
            "Swordplay":          {"level requirement":20, "job requirement":["run"]}, 
            "Temper":             {"level requirement":99, "job requirement":["run"]}, 
            "Temper II":          {"level requirement":99, "job requirement":["rdm"]}, 
            "Ternary Flourish":   {"level requirement":93, "job requirement":["dnc"]}, 
            "Theurgic Focus":     {"level requirement":80, "job requirement":["geo"]}, 
            "Trick Attack":       {"level requirement":30, "job requirement":["thf"]}, 
            "Triple Shot":        {"level requirement":87, "job requirement":["cor"]}, 
            "True Shot":          {"level requirement":0,  "job requirement":["rng", "cor"]}, 
            "Velocity Shot":      {"level requirement":99, "job requirement":["rng"]}, 
            "Warcry":             {"level requirement":0,  "job requirement":list(self.jobs_dict.values())}, 
            "Warcry (sub)":       {"level requirement":35, "job requirement":["war"]}, 
            **buffs_pyfile.misc_debuffs,
        }.items()))
            
        for ability_name in self.all_special_toggles_dict:
            self.all_special_toggles_dict[ability_name]["boolvar"] = tk.BooleanVar(value=False)
            self.all_special_toggles_dict[ability_name]["checkbox"] = ttk.Checkbutton(sf.interior, text=ability_name, variable=self.all_special_toggles_dict[ability_name]["boolvar"], name=f"defaults_{ability_name}_checkbox")
            self.all_special_toggles_dict[ability_name]["checkbox"].pack(anchor="w", pady=0)

        # Disallow mainjob and subjob versions to be selected at the same time.
        self.all_special_toggles_dict["Haste Samba"]["checkbox"].config(command=lambda event="haste samba": self.update_buffs(event))
        self.all_special_toggles_dict["Haste Samba (sub)"]["checkbox"].config(command=lambda event="haste samba (sub)": self.update_buffs(event))

        self.all_special_toggles_dict["Warcry"]["checkbox"].config(command=lambda event="warcry": self.update_buffs(event))
        self.all_special_toggles_dict["Warcry (sub)"]["checkbox"].config(command=lambda event="warcry (sub)": self.update_buffs(event))

        self.all_special_toggles_dict["Box Step"]["checkbox"].config(command=lambda event="box step": self.update_buffs(event))
        self.all_special_toggles_dict["Box Step (sub)"]["checkbox"].config(command=lambda event="box step (sub)": self.update_buffs(event))

        sf.grid(row=0, column=1, padx=2, sticky="N")

        '''
        ===============================================
            Build the frame containing enemy stats
        ===============================================
        '''
        enemy_inputs_frame = ttk.LabelFrame(inputs_frame, text="  Enemy Inputs  ", width=230, height=210)
        enemy_inputs_frame.grid_propagate(False)
        enemy_inputs_frame.grid(row=0, column=2, padx=2, sticky="NE")
        enemy_inputs_frame.grid_columnconfigure(0, weight=1) # Enforce the "sticky" for all elements in the 0th column of enemy_inputs_frame. stick="" is center-XY

        # Split the frame into two subframes:
        # Create subframe for selecting a preset enemy.
        enemy_selection_frame = ttk.Frame(enemy_inputs_frame)
        enemy_selection_frame.grid(row=0, column=0)

        enemy_names_list = [k for k in enemies_pyfile.preset_enemies.keys()] # Read directly from the enemies.py file
        self.selected_enemy_name = tk.StringVar(value=enemy_names_list[-3])
        enemy_selection_combobox = ttk.Combobox(enemy_selection_frame, textvariable=self.selected_enemy_name, values=enemy_names_list, state="readonly", width=30, justify="center", name="defaults_enemy_combobox")
        enemy_selection_combobox.grid(row=0, column=0, padx=0, pady=1)
        enemy_selection_combobox.bind('<<ComboboxSelected>>', lambda event: self.select_enemy())

        self.selected_enemy_dict = enemies_pyfile.preset_enemies[self.selected_enemy_name.get()]
        self.selected_enemy_variables = {stat:0 for stat in self.selected_enemy_dict.keys()}

        enemy_level_location_text = f"{self.selected_enemy_dict['Location']} (Lv.{self.selected_enemy_dict['Level']})"
        self.enemy_level_location_label = ttk.Label(enemy_selection_frame, text=enemy_level_location_text, width=30, anchor="center", name="defaults_enemy_level_location_label")
        self.enemy_level_location_label.grid(row=1, column=0, pady=(0, 10))

        # Create a subframe for manually inputing enemy stats.
        enemy_stats_frame = ttk.Frame(enemy_inputs_frame)
        enemy_stats_frame.grid(row=1, column=0)

        # Create more subframes to allow two column stat inputs.
        self.enemy_stats_subframe1 = ttk.Frame(enemy_stats_frame)
        self.enemy_stats_subframe1.grid(row=0, column=0,)
        self.enemy_stats_subframe2 = ttk.Frame(enemy_stats_frame)
        self.enemy_stats_subframe2.grid(row=0, column=1,)

        # Define lists of enemy stats. Loop over the lists to build the user input boxes cleanly.
        self.enemy_input_obj = {}
        self.enemy_stats_list1 = ["Evasion", "Defense", "Magic Evasion", "Magic Defense", "Magic DT%"]
        for i,stat in enumerate(self.enemy_stats_list1):
            self.selected_enemy_variables[stat] = tk.IntVar(value=self.selected_enemy_dict[stat])
            label_obj = ttk.Label(self.enemy_stats_subframe1, text=stat + ":", anchor="e", width=14,)
            label_obj.grid(row=i, column=0, padx=1, pady=1,)
            self.enemy_input_obj[stat] = ttk.Entry(self.enemy_stats_subframe1, textvariable=self.selected_enemy_variables[stat], width=8, justify="left", name=f"defaults_enemy_{stat}_entry", validate="key", validatecommand=(self.register(self.validate_numerical), "%P"))
            self.enemy_input_obj[stat].grid(row=i, column=1, padx=1, pady=1)

        self.enemy_stats_list2 = ["VIT", "AGI", "INT", "MND", "CHR"]
        for i,stat in enumerate(self.enemy_stats_list2):
            self.selected_enemy_variables[stat] = tk.IntVar(value=self.selected_enemy_dict[stat])
            label_obj = ttk.Label(self.enemy_stats_subframe2, text=stat + ":", anchor="e", width=5)
            label_obj.grid(row=i, column=0, padx=1, pady=1)
            self.enemy_input_obj[stat] = ttk.Entry(self.enemy_stats_subframe2, textvariable=self.selected_enemy_variables[stat], width=5, justify="left", validate="key", name=f"defaults_enemy_{stat}_entry", validatecommand=(self.register(self.validate_numerical), "%P"))
            self.enemy_input_obj[stat].grid(row=i, column=1, padx=1, pady=1)

        self.enemy_resist_ranks_list = ["150%", "130%", "115%", "100%", "85%", "70%", "60%", "50%", "40%", "30%", "25%", "20%", "15%", "10%", "5%"]
        resist_rank_label = ttk.Label(self.enemy_stats_subframe1, text="Resist Rank:", width=14, anchor="e")
        self.selected_enemy_variables["Resist Rank"] = tk.StringVar(value="100%")
        resist_rank_label.grid(row=i+1, column=0, padx=1, pady=1)
        resist_rank_combobox = ttk.Combobox(self.enemy_stats_subframe1, textvariable=self.selected_enemy_variables["Resist Rank"], values=self.enemy_resist_ranks_list, state="readonly", width=5, justify="left", name=f"defaults_enemy_resist_rank_combobox",)
        resist_rank_combobox.grid(row=i+1, column=1, padx=1, pady=1)

        '''
        ===============================================
            Build the frame containing player buffs
        ===============================================
        '''
        player_buffs_frame = ttk.LabelFrame(inputs_tab, text="Active Buffs")
        player_buffs_frame.grid(row=1, column=0, padx=2, pady=1, sticky="news")
        player_buffs_frame.rowconfigure(0, weight=1)
        player_buffs_frame.columnconfigure((0,1,2,3), weight=1)
        player_buff_subframe_width = 166
        player_buff_subframe_height = 210
        '''
            ===============================================
                Build the White Magic and Food subframe
            ===============================================
        '''
        whm_buffs_frame = ttk.Frame(player_buffs_frame, width=player_buff_subframe_width, height=player_buff_subframe_height)
        whm_buffs_frame.grid(row=0, column=0, padx=1, pady=10)
        whm_buffs_frame.grid_propagate(False)
        whm_buffs_frame.columnconfigure(0, weight=1)
        self.whm_checkbox_value = tk.BooleanVar(value=True)
        whm_checkbox = ttk.Checkbutton(whm_buffs_frame, text="White Magic", variable=self.whm_checkbox_value, width=13, name="defaults_whm_checkbox")
        whm_checkbox.grid(row=0, column=0, columnspan=2)

        self.whm_selections_dict = {}

        self.shell5_value = tk.BooleanVar(value=True)
        shell5_checkbox = ttk.Checkbutton(whm_buffs_frame, text="Shell V", variable=self.shell5_value, width=13, name="defaults_shell5_checkbox")
        shell5_checkbox.grid(row=1, column=0, columnspan=2)

        self.dia_value = tk.StringVar(value="Dia II")
        dia_combobox = ttk.Combobox(whm_buffs_frame, textvariable=self.dia_value, values=["Dia III", "Dia II", "Dia", "None"], state="readonly", width=20, justify="left", name="defaults_dia_combobox")
        dia_combobox.grid(row=2, column=0, padx=1, pady=1, columnspan=2)
        dia_combobox.bind('<<ComboboxSelected>>', lambda event, entry=f"set dia": self.update_buffs(entry))
        self.whm_selections_dict["Dia"] = self.dia_value.get()

        self.haste_value = tk.StringVar(value="Haste")
        haste_combobox = ttk.Combobox(whm_buffs_frame, textvariable=self.haste_value, values=["Haste II", "Haste", "None"], state="readonly", width=20, justify="left", name="defaults_haste_combobox")
        haste_combobox.grid(row=3, column=0, padx=1, pady=1, columnspan=2)
        haste_combobox.bind('<<ComboboxSelected>>', lambda event, entry=f"set haste": self.update_buffs(entry))
        self.whm_selections_dict["Haste"] = self.haste_value.get()

        boost_spell_list = ["Boost-"+k for k in ["STR", "DEX", "VIT", "AGI", "MND", "INT", "CHR"]] + ["None"]
        self.boost_value = tk.StringVar(value="Boost-STR")
        self.boost_combobox = ttk.Combobox(whm_buffs_frame, textvariable=self.boost_value, values=boost_spell_list, state="readonly", width=20, justify="left", name="defaults_boost_combobox")
        self.boost_combobox.grid(row=4, column=0, padx=1, pady=1, columnspan=2)
        self.boost_combobox.bind('<<ComboboxSelected>>', lambda event, entry=f"set boost": self.update_buffs(entry))
        self.whm_selections_dict["Boost"] = self.boost_value.get()

        self.storm_spell_list = [spell_name for sublist in [[k + "storm II", k+"storm"] for k in ["Aurora", "Void", "Fire", "Sand", "Rain", "Wind", "Hail", "Thunder"]] for spell_name in sublist] + ["None"]
        self.storm_value = tk.StringVar(value="Aurorastorm")
        storm_combobox = ttk.Combobox(whm_buffs_frame, textvariable=self.storm_value, values=self.storm_spell_list, state="readonly", width=20, justify="left", name="defaults_storm_combobox")
        storm_combobox.grid(row=5, column=0, padx=1, pady=1, columnspan=2)
        storm_combobox.bind('<<ComboboxSelected>>', lambda event, entry=f"set storm": self.update_buffs(entry))
        self.whm_selections_dict["Storm"] = self.storm_value.get()

        self.enhancing_skill_value = tk.IntVar(value=500)
        self.enhancing_skill_label = ttk.Label(whm_buffs_frame, text="Enhancing Skill:", width=14, anchor="e")
        self.enhancing_skill_label.grid(row=6, column=0, padx=1, pady=1, sticky="e")
        enhancing_skill_entry = ttk.Entry(whm_buffs_frame, textvariable=self.enhancing_skill_value, width=8, justify="left", name="defaults_enhancing_skill_entry", validate="key", validatecommand=(self.register(self.validate_numerical), "%P"))
        enhancing_skill_entry.grid(row=6, column=1, padx=0, pady=1, sticky="w")
        whm_buffs_frame.columnconfigure(1, weight=1)

        '''
            ===============================================
                  Build a separate subframe for food
            ===============================================
        '''
        food_subframe = ttk.LabelFrame(whm_buffs_frame, text="  Active Food  ", labelanchor="n")
        food_subframe.grid(row=7, column=0, sticky="esw", pady=1, columnspan=2)
        whm_buffs_frame.rowconfigure(7, weight=1) # Add a weight to the food subframe and use sticky="s" to force it to the bottom of the whm_buffs_frame

        food_list = [k for k in gear_pyfile.all_food] + ["None"]
        self.food_selections_dict = {}
        self.food_selections_dict["stringvar"] = tk.StringVar(value="Grape Daifuku")
        self.food_selections_dict["item"] = gear_pyfile.all_food[self.food_selections_dict["stringvar"].get()]
        self.food_selections_dict["combobox"] = ttk.Combobox(food_subframe, textvariable=self.food_selections_dict["stringvar"], values=food_list, state="readonly", width=0, justify="left", name="defaults_food_combobox")
        self.food_selections_dict["combobox tooltip"] = Hovertip(self.food_selections_dict["combobox"], self.format_tooltip_stats(self.food_selections_dict["item"]))
        self.food_selections_dict["combobox"].bind('<<ComboboxSelected>>', lambda event, entry=f"set food": self.update_buffs(entry))
        self.food_selections_dict["combobox"].grid(row=0, column=0, padx=1, pady=1, sticky="we")
        food_subframe.columnconfigure(0, weight=1) # Use a zero-width Combobox and have its rendered width be defined by the width of the enclosing cell through stretching.

        '''
            ===============================================
                     Build the Bard Songs subframe
            ===============================================
        '''
        brd_buffs_frame = ttk.Frame(player_buffs_frame, width=player_buff_subframe_width, height=player_buff_subframe_height)
        brd_buffs_frame.grid(row=0, column=1, padx=1, pady=10,)
        brd_buffs_frame.grid_propagate(False)

        self.brd_checkbox_value = tk.BooleanVar(value=True)
        brd_checkbox = ttk.Checkbutton(brd_buffs_frame, text="Bard Songs", variable=self.brd_checkbox_value, name="defaults_brd_checkbox")
        brd_checkbox.grid(row=0, column=0)
        brd_buffs_frame.columnconfigure(0, weight=1)

        self.song_bonus_value = tk.StringVar(value="Songs +7")
        song_bonus_combobox = ttk.Combobox(brd_buffs_frame, textvariable=self.song_bonus_value, state="readonly", values=["Songs +"+str(i) for i in range(9, -1, -1)], width=20, name="defaults_song_bonus_combobox")
        song_bonus_combobox.grid(row=1, column=0)

        song_list = [song_name for song_name in buffs_pyfile.brd] + ["None"]
        number_of_songs = 5
        self.song_selections_dict = {f"Song{i+1}":{} for i in range(number_of_songs)}
        for i,song_slot in enumerate(self.song_selections_dict):
            self.song_selections_dict[song_slot]["stringvar"] = tk.StringVar(value="None")
            self.song_selections_dict[song_slot]["combobox"] = ttk.Combobox(brd_buffs_frame, textvariable=self.song_selections_dict[song_slot]["stringvar"], values=song_list, state="readonly", width=20, justify="left", name=f"defaults_{song_slot}_combobox")
            self.song_selections_dict[song_slot]["combobox"].bind('<<ComboboxSelected>>', lambda event, entry=f"set {song_slot}": self.update_buffs(entry))
            self.song_selections_dict[song_slot]["combobox"].grid(row=i+2, column=0, padx=1, pady=1,)

        self.marcato_checkbox_value = tk.BooleanVar(value=True)
        self.marcato_checkbox = ttk.Checkbutton(brd_buffs_frame, text="Marcato", variable=self.marcato_checkbox_value, width=13, command=lambda event="marcato": self.update_buffs(event), name="defaults_marcato_checkbox")
        self.marcato_checkbox.grid(row=i+3, column=0, sticky="s")
        brd_buffs_frame.rowconfigure(i+3, weight=1)

        self.soul_voice_checkbox_value = tk.BooleanVar(value=False)
        self.soul_voice_checkbox = ttk.Checkbutton(brd_buffs_frame, text="Soul Voice", variable=self.soul_voice_checkbox_value, width=13, command=lambda event="soul voice": self.update_buffs(event), name="defaults_soul_voice_checkbox")
        self.soul_voice_checkbox.grid(row=i+4, column=0, sticky="s")

        '''
            ===============================================
                     Build the Corsair Rolls subframe
            ===============================================
        '''
        cor_buffs_frame = ttk.Frame(player_buffs_frame, width=player_buff_subframe_width, height=player_buff_subframe_height)
        cor_buffs_frame.grid(row=0, column=2, padx=1, pady=1)
        cor_buffs_frame.grid_propagate(False)

        self.cor_checkbox_value = tk.BooleanVar(value=True)
        cor_checkbox = ttk.Checkbutton(cor_buffs_frame, text="Corsair Rolls", variable=self.cor_checkbox_value, name="defaults_cor_checkbox")
        cor_checkbox.grid(row=0, column=0, columnspan=2)
        cor_buffs_frame.columnconfigure(0, weight=1)

        self.roll_bonus_value = tk.StringVar(value="Rolls +7")
        roll_bonus_combobox = ttk.Combobox(cor_buffs_frame, textvariable=self.roll_bonus_value, state="readonly", values=["Rolls +"+str(i) for i in range(8, -1, -1)], width=20, name="defaults_roll_bonus_combobox")
        roll_bonus_combobox.grid(row=1, column=0, columnspan=2, sticky="ew", padx=1)

        roll_list = [roll_name for roll_name in buffs_pyfile.cor]
        roll_potency_list = ["XI", "X", "IX", "VIII", "VII", "VI", "V", "IV", "III", "II", "I"]
        number_of_rolls = 4
        self.roll_selections_dict = {f"Roll{i+1}":{} for i in range(number_of_rolls)}
        for i,roll_slot in enumerate(self.roll_selections_dict):
            self.roll_selections_dict[roll_slot]["potency stringvar"] = tk.StringVar(value="IX")
            self.roll_selections_dict[roll_slot]["potency combobox"] = ttk.Combobox(cor_buffs_frame, textvariable=self.roll_selections_dict[roll_slot]["potency stringvar"], values=roll_potency_list, state="readonly", width=4, justify="left", name=f"defaults_{roll_slot}_potency_combobox")
            self.roll_selections_dict[roll_slot]["potency combobox"].grid(row=i+2, column=0, padx=1, pady=2, sticky="w")

            self.roll_selections_dict[roll_slot]["name stringvar"] = tk.StringVar(value="None")
            self.roll_selections_dict[roll_slot]["name combobox"] = ttk.Combobox(cor_buffs_frame, textvariable=self.roll_selections_dict[roll_slot]["name stringvar"], values=[k + " Roll" for k in roll_list] + ["None"], state="readonly", width=15, justify="left", name=f"defaults_{roll_slot}_name_combobox")
            self.roll_selections_dict[roll_slot]["name combobox"].bind('<<ComboboxSelected>>', lambda event, entry=f"set {roll_slot}": self.update_buffs(entry))
            self.roll_selections_dict[roll_slot]["name combobox"].grid(row=i+2, column=1, padx=1, pady=2, sticky="ew")
        cor_buffs_frame.columnconfigure(1, weight=1)

        self.crooked_checkbox_value = tk.BooleanVar(value=True)
        crooked_checkbox = ttk.Checkbutton(cor_buffs_frame, text="Crooked Cards", variable=self.crooked_checkbox_value, width=13, name="defaults_crooked_cards_checkbox")
        crooked_checkbox.grid(row=i+3, column=0, columnspan=2, pady=1, sticky="")
        cor_buffs_frame.rowconfigure(i+3, weight=1)

        self.job_bonus_checkbox_value = tk.BooleanVar(value=True)
        job_bonus_checkbox = ttk.Checkbutton(cor_buffs_frame, text="Job Bonus", variable=self.job_bonus_checkbox_value, width=13, name="defaults_cor_job_bonus_checkbox")
        job_bonus_checkbox.grid(row=i+4, column=0, columnspan=2, pady=1)

        self.light_shot_checkbox_value = tk.BooleanVar(value=True)
        light_shot_checkbox = ttk.Checkbutton(cor_buffs_frame, text="Light Shot", variable=self.light_shot_checkbox_value, width=13, name="defaults_light_shot_checkbox")
        light_shot_checkbox.grid(row=i+5, column=0, columnspan=2, pady=1)

        '''
            ===============================================
                     Build the Geomancy Bubbles subframe
            ===============================================
        '''
        geo_buffs_frame = ttk.Frame(player_buffs_frame, width=player_buff_subframe_width, height=player_buff_subframe_height)
        geo_buffs_frame.grid(row=0, column=3, padx=1, pady=1)
        geo_buffs_frame.grid_propagate(False)

        self.geo_checkbox_value = tk.BooleanVar(value=False)
        geo_checkbox = ttk.Checkbutton(geo_buffs_frame, text="Geomancy Bubbles", variable=self.geo_checkbox_value, name="defaults_geo_checkbox")
        geo_checkbox.grid(row=0, column=0, columnspan=2)
        geo_buffs_frame.columnconfigure(0, weight=1)

        self.bubble_bonus_value = tk.StringVar(value="Geomancy +10")
        bubble_bonus_combobox = ttk.Combobox(geo_buffs_frame, textvariable=self.bubble_bonus_value, state="readonly", values=["Geomancy +"+str(i) for i in range(10, -1, -1)], width=20, name="defaults_bubble_bonus_checkbox")
        bubble_bonus_combobox.grid(row=1, column=0, columnspan=2)

        bubble_list = [bubble_name for bubble_name in sorted(list(buffs_pyfile.geo.keys()) + list(buffs_pyfile.geo_debuffs.keys()))]
        bubble_prefix_list = ["Indi-", "Geo-", "Entrust-"]
        self.bubble_selections_dict = {}
        self.bubble_selections_dict = {f"{k}":{} for k in bubble_prefix_list}
        for i,bubble_slot in enumerate(self.bubble_selections_dict):
            self.bubble_selections_dict[bubble_slot]["stringvar"] = tk.StringVar(value="None")
            self.bubble_selections_dict[bubble_slot]["combobox"] = ttk.Combobox(geo_buffs_frame, textvariable=self.bubble_selections_dict[bubble_slot]["stringvar"], values=[bubble_prefix_list[i]+k for k in bubble_list] + ["None"], state="readonly", width=20, justify="left", name=f"defaults_{bubble_slot}_combobox")
            self.bubble_selections_dict[bubble_slot]["combobox"].grid(row=i+2, column=0, padx=1, pady=1, columnspan=2)
            self.bubble_selections_dict[bubble_slot]["combobox"].bind('<<ComboboxSelected>>', lambda event, entry=f"set {bubble_slot}": self.update_buffs(entry))
        geo_buffs_frame.columnconfigure(0, weight=1)

        self.bubble_potency_value = tk.IntVar(value=50)
        bubble_potency_label = ttk.Label(geo_buffs_frame, text="Bubble Potency:", anchor="w")
        bubble_potency_label.grid(row=i+3, column=0, sticky="e", pady=5)
        bubble_potency_entry = ttk.Entry(geo_buffs_frame, textvariable=self.bubble_potency_value, width=6, justify="center", name="defaults_bubble_potency_entry", validate="key", validatecommand=(self.register(self.validate_numerical), "%P"))
        bubble_potency_entry.grid(row=i+3, column=1, sticky="w", pady=5)
        geo_buffs_frame.columnconfigure(1, weight=1)


        self.bog_checkbox_value = tk.BooleanVar(value=True)
        self.bog_checkbox = ttk.Checkbutton(geo_buffs_frame, text="Blaze of Glory", variable=self.bog_checkbox_value, width=13, command=lambda event="blaze of glory": self.update_buffs(event), name="defaults_bog_checkbox")
        self.bog_checkbox.grid(row=i+4, column=0, columnspan=2, pady=2, sticky="s")
        geo_buffs_frame.rowconfigure(i+4, weight=1)

        self.bolster_checkbox_value = tk.BooleanVar(value=False)
        self.bolster_checkbox = ttk.Checkbutton(geo_buffs_frame, text="Bolster", variable=self.bolster_checkbox_value, width=13, command=lambda event="bolster": self.update_buffs(event), name="defaults_bolster_checkbox")
        self.bolster_checkbox.grid(row=i+5, column=0, columnspan=2, pady=2, sticky="s")

        '''
        ===============================================
                  Build the Quicklook subframe
        ===============================================
        '''
        quicklook_frame = ttk.Frame(inputs_tab)
        quicklook_frame.grid(row=2, column=0, sticky="news") 

        quicklook_frame.rowconfigure((0,1), weight=1)
        quicklook_frame.columnconfigure(0, weight=1)
        quicklook_frame.columnconfigure(1, weight=2) # weight=2 expands to fill empty space twice as fast as weight=1

        quicklook_subframe_left = ttk.LabelFrame(quicklook_frame, text="  Equipped Items  ", labelanchor="n")
        quicklook_subframe_left.grid(row=0, column=0, sticky="news")
        quicklook_subframe_left.rowconfigure(0, weight=1)
        quicklook_subframe_left.columnconfigure(0, weight=1)

        copy_set_frame = ttk.Frame(quicklook_subframe_left) # Contains the three buttons for copying the displayed set to TP, WS, and clipboard.
        copy_set_frame.grid(row=0, column=0, padx=(0,1), pady=(1,1))
        
        copy2tp_button = ttk.Button(copy_set_frame, text="Copy to TP Set", width=15, command=lambda event="quicklook to tp": self.copy_gearset_dict(event))
        copy2tp_button.grid(row=0, column=0, padx=(0,1), pady=(1,1))

        copy2tp_button = ttk.Button(copy_set_frame, text="Copy to WS Set", width=15, command=lambda event="quicklook to ws": self.copy_gearset_dict(event))
        copy2tp_button.grid(row=0, column=1, padx=(1,0), pady=(1,1))

        copy2clipboard_button = ttk.Button(copy_set_frame, text="Copy to Clipboard", command=lambda event="quicklook": self.copy_to_clipboard(event))
        copy2clipboard_button.grid(row=1, column=0, columnspan=2, sticky="ew")


        quicklook_gear_frame = ttk.Frame(quicklook_subframe_left, relief=None, borderwidth=2) # Contains the 4x4 grid of equipment buttons
        quicklook_gear_frame.grid(row=1, column=0, padx=0, pady=3)

        self.quicklook_equipped_dict = {slot: {"radio_variable":tk.StringVar(value="Empty"), "icon":self.get_equipment_icon(), "item":gear_pyfile.Empty} for slot in self.all_equipment_dict}

        for slot in self.all_equipment_dict:
            self.quicklook_equipped_dict[slot]["button"] = ttk.Button(quicklook_gear_frame, image=self.quicklook_equipped_dict[slot]["icon"], command=lambda event=slot: self.update_visible_quicklook_frame(event))
            self.quicklook_equipped_dict[slot]["button tooltip"] = Hovertip(self.quicklook_equipped_dict[slot]["button"], self.format_tooltip_stats(self.quicklook_equipped_dict[slot]["item"]), hover_delay=500)
            self.quicklook_equipped_dict[slot]["button"].grid(row=self.equipment_button_positions[slot][0], column=self.equipment_button_positions[slot][1])


        quicklook_buttons_frame = ttk.Frame(quicklook_subframe_left) # Contains the four "quicklook" buttons: TP, WS, Spell, and Simulate Once
        quicklook_buttons_frame.grid(row=2, column=0)

        quicklook_tp_button = ttk.Button(quicklook_buttons_frame, text="Quicklook TP", width=15, command=lambda event="tp": self.quicklook(event))
        quicklook_tp_button.grid(row=0, column=0, padx=(0,1), pady=(1,1))

        quicklook_ws_button = ttk.Button(quicklook_buttons_frame, text="Quicklook WS", width=15, command=lambda event="ws": self.quicklook(event))
        quicklook_ws_button.grid(row=0, column=1, padx=(1,0), pady=(1,1))

        quicklook_spell_button = ttk.Button(quicklook_buttons_frame, text="Quicklook Spell", width=15, command=lambda event="spell": self.quicklook(event))
        quicklook_spell_button.grid(row=1, column=0, padx=(0,1), pady=(1,1))

        quicklook_sim_ws_button = ttk.Button(quicklook_buttons_frame, text="Simulate WS", width=15, command=lambda event="run one ws": self.quicklook(event))
        quicklook_sim_ws_button.grid(row=1, column=1, padx=(1,0), pady=(1,1))
        

        quicklook_results_frame = ttk.Frame(quicklook_subframe_left) # Contains the four cells (2x2) showing the outputs from the quicklook buttons. Two of these are variable for damage and TP return: (Damage - value) (TP - value)
        quicklook_results_frame.grid(row=3, column=0)

        self.quicklook_results_damage_label1 = ttk.Label(quicklook_results_frame, text="Average Damage =", anchor="e", font="courier", width=17)
        self.quicklook_results_damage_label1.grid(row=0, column=0)

        self.quicklook_damage_value = tk.DoubleVar(value=0)
        self.quicklook_results_damage_label2 = ttk.Label(quicklook_results_frame, text=f"{self.quicklook_damage_value.get():.0f}", anchor="e", font="courier", width=6)
        self.quicklook_results_damage_label2.grid(row=0, column=1)

        self.quicklook_results_tp_label1 = ttk.Label(quicklook_results_frame, text="Average TP =", anchor="e", font="courier", width=17)
        self.quicklook_results_tp_label1.grid(row=1, column=0)

        self.quicklook_tp_value = tk.DoubleVar(value=0)
        self.quicklook_results_tp_label2 = ttk.Label(quicklook_results_frame, text=f"{self.quicklook_tp_value.get():.1f}", anchor="e", font="courier", width=6)
        self.quicklook_results_tp_label2.grid(row=1, column=1)


        quicklook_subframe_right = ttk.Frame(quicklook_frame, height=200, width=410) # Right-hand side of the quicklook subframe. Holds the radio button frames.
        quicklook_subframe_right.grid(row=0, column=1, sticky="news")
        quicklook_subframe_right.rowconfigure(0, weight=1)
        quicklook_subframe_right.columnconfigure(0, weight=1)

        self.quicklook_scrollframes = {}
        for slot in self.all_equipment_dict:
            equipment_list = sorted([k["Name2" if "Name2" in k else "Name"] for k in self.all_equipment_dict[slot]])
            self.quicklook_scrollframes[slot] = VirtualRadioFrame(quicklook_subframe_right, text=f"  Select {slot.capitalize()}  ", labelanchor="nw", equipment_slot=slot, selection_type="quicklook", command=self.update_quicklook_equipment, master_data=equipment_list, N=16)
            self.quicklook_scrollframes[slot].place(relx=0, rely=0, relwidth=1.0, relheight=1.0)


        '''
        ==============================================================================================
            Build the frame containing gear selection checkboxes.
        ==============================================================================================
        '''
        optimize_tab.rowconfigure(0, weight=1)
        optimize_tab.columnconfigure(0, weight=1)

        optimize_frame_top = ttk.Frame(optimize_tab, width=630, height=480)
        optimize_frame_top.grid(row=0, column=0, sticky="new", columnspan=2)
        optimize_frame_top.grid_propagate(False)
        optimize_frame_top.rowconfigure(0, weight=1)
        optimize_frame_top.columnconfigure(0, weight=1) 

        '''
        ===============================================
        Build the frame holding the 4x4 grid of buttons
            and the "select all" buttons
            and the conditional select buttons.
        ===============================================
        '''

        optimize_frame_topleft = ttk.Frame(optimize_frame_top, padding=5, width=300, height=400)
        optimize_frame_topleft.grid(row=0, column=0, padx=2, sticky="news")
        optimize_frame_topleft.grid_propagate(False)
        optimize_frame_topleft.rowconfigure((0,1), weight=1)
        optimize_frame_topleft.columnconfigure(0, weight=1) 

        '''
        ===============================================
                Build the 4x4 grid of buttons
        ===============================================
        '''
        buttons_grid_frame1 = ttk.Frame(optimize_frame_topleft, width=400, height=400)
        buttons_grid_frame1.grid_propagate(True) # Take the dimensions of the stuff inside (this one holds only the 4x4 buttons grid)
        buttons_grid_frame1.grid(row=0, column=0, sticky="")

        self.pixel_image = tk.PhotoImage(width=1, height=1) # 1x1 pixel image to allow easy tk.Button dimensions. Must use "self." or Python will GarbageCollect it before making the buttons.
        button_size = 48
        select_buttons_dict = {slot: {} for slot in self.equipment_button_positions}
        for slot in self.equipment_button_positions:
            select_buttons_dict[slot]["button"] = tk.Button(buttons_grid_frame1, image=self.pixel_image, text=slot, compound=tk.CENTER, height=button_size, width=button_size, command=lambda event=slot: self.update_visible_optimize_frame(event))
            select_buttons_dict[slot]["button"].grid(row=self.equipment_button_positions[slot][0], column=self.equipment_button_positions[slot][1], padx=1, pady=1)

        '''
        ===============================================
                Add in the Select all buttons.
        ===============================================
        '''
        buttons_grid_frame2 = ttk.Frame(optimize_frame_topleft, width=100, height=100)
        buttons_grid_frame2.grid_propagate(True)
        buttons_grid_frame2.grid(row=1, column=0, sticky="n")
        
        select_all_visible_button = tk.Button(buttons_grid_frame2, text="Select all", image=self.pixel_image, compound=tk.CENTER, width=100, height=30, command=lambda: self.select_gear_opt("select all slot"))
        select_all_visible_button_tip = Hovertip(select_all_visible_button,"Select all items in the currently displayed list.", hover_delay=500)
        select_all_visible_button.grid(row=0, column=0, padx=1, pady=1)

        select_all_button = tk.Button(buttons_grid_frame2, text="Select ALL", image=self.pixel_image, compound=tk.CENTER, width=100, height=30, command=lambda: self.select_gear_opt("select all"))
        select_all_button_tip = Hovertip(select_all_button,"Select all items in all equipment lists.", hover_delay=500)
        select_all_button.grid(row=1, column=0, padx=1, pady=1)

        unselect_all_visible_button = tk.Button(buttons_grid_frame2, text="Unselect all", image=self.pixel_image, compound=tk.CENTER, width=100, height=30, command=lambda: self.select_gear_opt("unselect all slot"))
        unselect_all_visible_button_tip = Hovertip(unselect_all_visible_button, "Unselect all items in the currently displayed list.", hover_delay=500)
        unselect_all_visible_button.grid(row=0, column=1, padx=1, pady=1)

        select_all_file_button = tk.Button(buttons_grid_frame2, text="Select all File", image=self.pixel_image, compound=tk.CENTER, width=100, height=30, command=lambda: self.select_gear_opt("select all file"))
        select_all_file_button_tip = Hovertip(select_all_file_button,"Select all items in all equipment lists if the item appears in an input file.\nInput file must use format from Windower command \"//gs export all\"", hover_delay=500)
        select_all_file_button.grid(row=1, column=1, padx=1, pady=1)

        '''
        ===============================================
                Add in the conditional entries.
        ===============================================
        '''

        select_conditionals_frame = ttk.Frame(optimize_frame_topleft, width=400, height=125)
        select_conditionals_frame.grid_propagate(False) # Take the dimensions of the stuff inside (it holds only the 4x4 buttons grid.)
        select_conditionals_frame.grid(row=2, column=0, sticky="n")
        select_conditionals_frame.columnconfigure((0, 1, 2), weight=1)

        self.ody_rank_value = tk.StringVar(value="30")
        self.mastery_rank_value = tk.StringVar(value="MR07")
        self.tvr_selection_value = tk.StringVar(value="Lehko Habhoka's")
        self.soa_selection_value = tk.StringVar(value="Weatherspoon")
        self.nyame25_checkbox_value = tk.BooleanVar(value=True)

        self.tvr_rings = ["Cornelia's", "Ephramad's", "Fickblix's", "Gurebu-Ogurebu's", "Lehko Habhoka's", "Medada's", "Ragelise's", "None"]
        self.soa_rings = ["Weatherspoon", "Karieyh", "Vocane", "None"]
        ody_selections = ["30", "25", "20", "15", "0", "None"]
        mastery_rank_selections = ["MR10", "MR09", "MR08", "MR07", "MR06", "MR05"]

        ody_rank_label = ttk.Label(select_conditionals_frame, text="Odyssey Rank:", width=20, anchor="w")
        ody_rank_label.grid(row=0, column=0, sticky="w", pady=2)
        self.ody_rank_combobox = ttk.Combobox(select_conditionals_frame, values=ody_selections, textvariable=self.ody_rank_value, state="readonly", width=18, name="defaults_ody_rank_combobox")
        ody_rank_label_tip = Hovertip(self.ody_rank_combobox, "Only select Odyssey equipment with this rank when using the Select buttons.", hover_delay=500)
        self.ody_rank_combobox.grid(row=0, column=1, sticky="e", pady=2)

        soa_ring_label = ttk.Label(select_conditionals_frame, text="SoA Ring:", width=20, anchor="w")
        soa_ring_label.grid(row=1, column=0, sticky="w", pady=2)
        self.soa_ring_combobox = ttk.Combobox(select_conditionals_frame, values=self.soa_rings, textvariable=self.soa_selection_value, state="readonly", width=18, name="defaults_soa_ring_combobox")
        soa_ring_label_tip = Hovertip(self.soa_ring_combobox, "Only select this SoA ring when using the Select buttons.", hover_delay=500)
        self.soa_ring_combobox.grid(row=1, column=1, sticky="e", pady=2)

        tvr_ring_label = ttk.Label(select_conditionals_frame, text="TVR Ring:", width=20, anchor="w")
        tvr_ring_label.grid(row=2, column=0, sticky="w", pady=2)
        self.tvr_ring_combobox = ttk.Combobox(select_conditionals_frame, values=self.tvr_rings, textvariable=self.tvr_selection_value, state="readonly", width=18, name="defaults_tvr_ring_combobox")
        tvr_ring_label_tip = Hovertip(self.tvr_ring_combobox, "Only select this TVR ring when using the Select buttons.", hover_delay=500)
        self.tvr_ring_combobox.grid(row=2, column=1, sticky="e", pady=2)

        mastery_rank_label = ttk.Label(select_conditionals_frame, text="Mastery Rank:", width=20, anchor="w")
        mastery_rank_label.grid(row=3, column=0, sticky="w", pady=2)
        self.mastery_rank_combobox = ttk.Combobox(select_conditionals_frame, values=mastery_rank_selections, textvariable=self.mastery_rank_value, state="readonly", width=18, name="defaults_mastery_rank_combobox")
        mastery_rank_label_tip = Hovertip(self.mastery_rank_combobox, "Only select the Hoxne Earring with this mastery rank when using the Select buttons.", hover_delay=500)
        self.mastery_rank_combobox.grid(row=3, column=1, sticky="e", pady=2)

        self.nyame25_checkbox = ttk.Checkbutton(select_conditionals_frame, text="Max R25 Nyame?", variable=self.nyame25_checkbox_value, name="defaults_nyame25_checkbox")
        nyame25_label_tip = Hovertip(self.nyame25_checkbox, "Select R25B Nyame when Odyssey Rank selection is 30.", hover_delay=500)
        self.nyame25_checkbox.grid(row=4, column=1, sticky="e", pady=2)


        '''
        ===============================================
          Build the 16 scrollframes of gear checkboxes
        ===============================================
        '''
        opt_scrollframe_relative_frame = ttk.Frame(optimize_frame_top, height=300, width=370)
        opt_scrollframe_relative_frame.grid(row=0, column=1, sticky="news")

        self.optimize_scrollframes = {}
        for slot in self.all_equipment_dict:
            equipment_list = sorted([k["Name2" if "Name2" in k else "Name"] for k in self.all_equipment_dict[slot]])
            self.optimize_scrollframes[slot] = VirtualCheckboxFrame(opt_scrollframe_relative_frame,
                                                                    text=f"  Select {slot.capitalize()}  ",
                                                                    labelanchor="nw",
                                                                    master_data=equipment_list,
                                                                    N=22,
                                                                    )
            self.optimize_scrollframes[slot].place(relx=0, rely=0, relwidth=1.0, relheight=1.0)

        '''
        ==============================================================================================
          Build the bottom part of the optimize tab.
          Contains PDT/MDT requirements, metrics, and the buttons to run optimizations
        ==============================================================================================
        '''
        optimize_frame_bottom = ttk.Frame(optimize_tab, width=630, height=320)
        optimize_frame_bottom.grid(row=1, column=0, sticky="new")
        optimize_frame_bottom.grid_propagate(False)
        optimize_frame_bottom.rowconfigure(0, weight=1)
        optimize_frame_bottom.columnconfigure((0,1), weight=1) 


        '''
        ===============================================
        Build the frame containing PDT, MDT, and metrics
        ===============================================
        '''
        optimize_frame_bottomleft = ttk.Frame(optimize_frame_bottom, width=250, height=230)
        optimize_frame_bottomleft.grid(row=0, column=0, sticky="n")
        optimize_frame_bottomleft.grid_propagate(False)
        optimize_frame_bottomleft.columnconfigure(0, weight=1)

        def optimizer_help(row, message):
            help_button = ttk.Button(optimize_frame_bottomleft, text="?", width=2)
            help_button.grid(row=row, column=2, padx=(3, 0), pady=2)
            Hovertip(help_button, message, hover_delay=300)
            return help_button

        self.pdt_requirements_value = tk.IntVar(value=0)
        self.mdt_requirements_value = tk.IntVar(value=0)
        self.tp_metric_value = tk.StringVar(value="Time to WS")
        self.ws_metric_value = tk.StringVar(value="Damage Dealt")
        self.spell_metric_value = tk.StringVar(value="Damage Dealt")
        self.optimizer_restarts_value = tk.IntVar(value=3)
        self.optimizer_workers_value = tk.IntVar(value=0)
        self.optimizer_seed_value = tk.StringVar(value="")
        self.optimizer_action_buttons = []

        tp_metrics = ["Time to WS", "Damage dealt", "TP return", "DPS"]
        spell_metrics = ["Damage dealt", "TP return"]
        ws_metrics = ["Damage dealt", "TP return", "Magic Accuracy"]

        pdt_requirements_text = ttk.Label(optimize_frame_bottomleft, text="Required PDT %", width=20, anchor="w")
        pdt_requirements_text.grid(row=0, column=0, pady=2, sticky="w")
        pdt_requirements_entry = ttk.Entry(optimize_frame_bottomleft, textvariable=self.pdt_requirements_value, width=10, justify="center")
        pdt_requirements_entry.grid(row=0, column=1, pady=2, sticky="ew")
        pdt_requirements_entry.bind("<MouseWheel>", lambda event:
                                                self.pdt_requirements_value.set( 100 if (self.pdt_requirements_value.get() + (event.delta/120)*1) > 100
                                                                        else -50 if (self.pdt_requirements_value.get() + (event.delta/120)*1) < -50
                                                                        else int(self.pdt_requirements_value.get() + (event.delta/120)*1)
                                                                        )
                                )

        mdt_requirements_text = ttk.Label(optimize_frame_bottomleft, text="Required MDT %", width=20, anchor="w")
        mdt_requirements_text.grid(row=1, column=0, pady=2, sticky="w")
        mdt_requirements_entry = ttk.Entry(optimize_frame_bottomleft, textvariable=self.mdt_requirements_value, width=10, justify="center")
        mdt_requirements_entry.grid(row=1, column=1, pady=2, sticky="ew")
        mdt_requirements_entry.bind("<MouseWheel>", lambda event:
                                                self.mdt_requirements_value.set( 100 if (self.mdt_requirements_value.get() + (event.delta/120)*1) > 100
                                                                        else -50 if (self.mdt_requirements_value.get() + (event.delta/120)*1) < -50
                                                                        else int(self.mdt_requirements_value.get() + (event.delta/120)*1)
                                                                        )
                                )

        tp_metric_text = ttk.Label(optimize_frame_bottomleft, text="TP set metric", width=20, anchor="w")
        tp_metric_text.grid(row=2, column=0, pady=2, sticky="w")

        self.tp_metric_combobox = ttk.Combobox(optimize_frame_bottomleft, textvariable=self.tp_metric_value, values=tp_metrics, state="readonly", width=15)
        self.tp_metric_combobox.grid(row=2, column=1, pady=2, sticky="ew")

        ws_metric_text = ttk.Label(optimize_frame_bottomleft, text="WS set metric", width=20, anchor="w")
        ws_metric_text.grid(row=3, column=0, pady=2, sticky="w")
        self.ws_metric_combobox = ttk.Combobox(optimize_frame_bottomleft, textvariable=self.ws_metric_value, values=ws_metrics, state="readonly", width=15)
        self.ws_metric_combobox.grid(row=3, column=1, pady=2, sticky="ew")

        spell_metric_text = ttk.Label(optimize_frame_bottomleft, text="Spell set metric", width=20, anchor="w")
        spell_metric_text.grid(row=4, column=0, pady=2, sticky="w")
        self.spell_metric_combobox = ttk.Combobox(optimize_frame_bottomleft, textvariable=self.spell_metric_value, values=spell_metrics, state="readonly", width=15)
        self.spell_metric_combobox.grid(row=4, column=1, pady=2, sticky="ew")

        optimizer_restarts_text = ttk.Label(optimize_frame_bottomleft, text="Optimizer restarts", width=20, anchor="w")
        optimizer_restarts_text.grid(row=5, column=0, pady=2, sticky="w")
        optimizer_restarts_entry = ttk.Entry(optimize_frame_bottomleft, textvariable=self.optimizer_restarts_value, width=10, justify="center")
        optimizer_restarts_entry.grid(row=5, column=1, pady=2, sticky="ew")
        Hovertip(optimizer_restarts_entry, "Run independent seeded searches and keep the strongest result. 1 preserves a single search.", hover_delay=500)
        optimizer_help(5, "Restarts run independent randomized searches. Higher values explore more starting paths and can find better sets, but increase total work.")

        optimizer_workers_text = ttk.Label(optimize_frame_bottomleft, text="Parallel workers", width=20, anchor="w")
        optimizer_workers_text.grid(row=6, column=0, pady=2, sticky="w")
        optimizer_workers_entry = ttk.Entry(optimize_frame_bottomleft, textvariable=self.optimizer_workers_value, width=10, justify="center")
        optimizer_workers_entry.grid(row=6, column=1, pady=2, sticky="ew")
        Hovertip(optimizer_workers_entry, "0 = Auto. Workers run restarts in separate processes; use 1 for serial execution.", hover_delay=500)
        optimizer_help(6, "Number of restart processes. 0 chooses automatically while leaving one CPU core available. Use 1 to disable parallel execution.")

        optimizer_seed_text = ttk.Label(optimize_frame_bottomleft, text="Optimizer seed", width=20, anchor="w")
        optimizer_seed_text.grid(row=7, column=0, pady=2, sticky="w")
        optimizer_seed_entry = ttk.Entry(optimize_frame_bottomleft, textvariable=self.optimizer_seed_value, width=10, justify="center")
        optimizer_seed_entry.grid(row=7, column=1, pady=2, sticky="ew")
        Hovertip(optimizer_seed_entry, "Optional. Reuse a printed winning seed to reproduce a single optimizer restart.", hover_delay=500)
        optimizer_help(7, "Optional whole-number seed. Leave blank for a fresh search. Reusing a winning seed reproduces that restart's randomized search order.")


        '''
        ===============================================
          Build the frame containing optimize buttons
        ===============================================
        '''
        optimize_frame_bottomright = ttk.Frame(optimize_frame_bottom, width=300, height=230)
        optimize_frame_bottomright.grid(row=0, column=1, sticky="n")
        optimize_frame_bottomright.grid_propagate(False)
        optimize_frame_bottomright.columnconfigure(0, weight=1) 

        self.optimizer_status_text = None

        self.show_similar_results_checkbox_value = tk.BooleanVar(value=False)
        self.show_similar_results_entry_value = tk.IntVar(value=2)
        
        show_similar_results_frame = ttk.Frame(optimize_frame_bottomright)
        show_similar_results_frame.grid(row=0, column=0)

        show_similar_results_checkbox = ttk.Checkbutton(show_similar_results_frame, text="Print equipment with similar results?", variable=self.show_similar_results_checkbox_value)
        show_similar_results_checkbox_tip = Hovertip(show_similar_results_checkbox, "Print equipment that is within x% of the best set, using the Entry box to enter x.", hover_delay=500)
        show_similar_results_checkbox.grid(row=0, column=0)
        
        show_similar_results_entry = ttk.Entry(show_similar_results_frame, textvariable=self.show_similar_results_entry_value, width=3, justify="center",)
        show_similar_results_entry_tip = Hovertip(show_similar_results_entry, "Print equipment that is within x% of the best set. Enter x here.", hover_delay=500)
        show_similar_results_entry.grid(row=0, column=1)


        status_frame = ttk.LabelFrame(optimize_frame_bottomright, text="Optimizer status", padding=3)
        status_frame.grid(row=1, column=0, padx=2, pady=(8, 2), sticky="ew")
        status_frame.columnconfigure(0, weight=1)
        self.optimizer_status_text = tk.Text(
            status_frame, height=5, width=38, wrap="word", state="disabled",
            relief="sunken", borderwidth=1,
        )
        self.optimizer_status_text.grid(row=0, column=0, sticky="nsew")
        status_scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=self.optimizer_status_text.yview)
        status_scrollbar.grid(row=0, column=1, sticky="ns")
        self.optimizer_status_text.configure(yscrollcommand=status_scrollbar.set)
        self._append_optimizer_status("Ready.")

        optimize_buttons_frame = ttk.Frame(optimize_frame_bottomright)
        optimize_buttons_frame.grid(row=2, column=0, pady=5)




        # buttons_grid_frame2 = ttk.Frame(optimize_frame_topleft, borderwidth=2, relief="solid", width=100, height=100)
        # buttons_grid_frame2.grid_propagate(True)
        # buttons_grid_frame2.grid(row=1, column=0, sticky="n")
        
        optimize_ws_button = tk.Button(optimize_buttons_frame, text="Optimize WS", image=self.pixel_image, compound=tk.CENTER, width=100, height=30, command=lambda event="optimize ws": self.quicklook(event))
        optimize_ws_button.grid(row=0, column=0, padx=1, pady=1)

        optimize_tp_button = tk.Button(optimize_buttons_frame, text="Optimize TP", image=self.pixel_image, compound=tk.CENTER, width=100, height=30, command=lambda event="optimize tp": self.quicklook(event))
        optimize_tp_button.grid(row=1, column=0, padx=1, pady=1)

        optimize_spell_button = tk.Button(optimize_buttons_frame, text="Optimize Spell", image=self.pixel_image, compound=tk.CENTER, width=100, height=30, command=lambda event="optimize spell": self.quicklook(event))
        optimize_spell_button.grid(row=0, column=1, padx=1, pady=1)
        self.optimizer_action_buttons = [optimize_ws_button, optimize_tp_button, optimize_spell_button]

        self.equip_best_set_button = tk.Button(optimize_buttons_frame, text="Equip best set", image=self.pixel_image, compound=tk.CENTER, width=100, height=30, command=lambda: self.equip_best_set(), state="disabled")
        self.equip_best_set_button.grid(row=1, column=1, padx=1, pady=1)





        '''
        ==============================================================================================
            Build the simulations tab.
        ==============================================================================================
        '''
        simulate_tab = ttk.Frame(self.notebook)
        self.notebook.add(simulate_tab, text="Simulate")
        self.bind_all("<Control-Key-3>", lambda e: self.notebook.select(simulate_tab)) 
        simulate_tab.rowconfigure((0,1,2), weight=0)
        simulate_tab.columnconfigure(0, weight=1)

        '''
        ===============================================
          Build the top frame (holding TP set stuff)
        ===============================================
        '''
        simulations_tp_frame = ttk.Frame(simulate_tab, height=300, width=650)
        simulations_tp_frame.grid(row=0, column=0, sticky="", pady=5)
        simulations_tp_frame.grid_propagate(False)
        simulations_tp_frame.rowconfigure(0, weight=1)
        simulations_tp_frame.columnconfigure((0), weight=1)

        simulations_tp_frame_left = ttk.LabelFrame(simulations_tp_frame, text="  Equipped TP set  ", labelanchor="nw")
        simulations_tp_frame_left.grid(row=0, column=0, sticky="news")
        simulations_tp_frame_left.grid_propagate(False)

        necessary_for_centering_tp_frame = ttk.Frame(simulations_tp_frame_left)
        necessary_for_centering_tp_frame.pack(expand=True, anchor="center")

        simulations_tp_copy_button_frame = ttk.Frame(necessary_for_centering_tp_frame,)
        simulations_tp_copy_button_frame.grid(row=0, column=0, pady=5)

        tp_copy2inputs_button = ttk.Button(simulations_tp_copy_button_frame, text="Copy to Quicklook", width=25, command=lambda event="tp to quicklook": self.copy_gearset_dict(event))
        tp_copy2inputs_button.grid(row=0, column=0, pady=1)
        tp_copy2clipboard_button = ttk.Button(simulations_tp_copy_button_frame, text="Copy to Clipboard", width=25, command=lambda event="tp": self.copy_to_clipboard(event))
        tp_copy2clipboard_button.grid(row=1, column=0, pady=1)

        simulations_tp_gear_frame = ttk.Frame(necessary_for_centering_tp_frame,)
        simulations_tp_gear_frame.grid(row=1, column=0, sticky="")
        simulations_tp_gear_frame.grid_propagate(True)

        self.tp_quicklook_equipped_dict = {slot: {"radio_variable":tk.StringVar(value="Empty"), "icon":self.get_equipment_icon(), "item":gear_pyfile.Empty} for slot in self.all_equipment_dict}
        for slot in self.tp_quicklook_equipped_dict:
            self.tp_quicklook_equipped_dict[slot]["button"] = ttk.Button(simulations_tp_gear_frame, image=self.tp_quicklook_equipped_dict[slot]["icon"], command=lambda event=slot: self.update_visible_quicklook_frame_tp(event))
            self.tp_quicklook_equipped_dict[slot]["button tooltip"] = Hovertip(self.tp_quicklook_equipped_dict[slot]["button"], self.format_tooltip_stats(self.tp_quicklook_equipped_dict[slot]["item"]), hover_delay=500)
            self.tp_quicklook_equipped_dict[slot]["button"].grid(row=self.equipment_button_positions[slot][0], column=self.equipment_button_positions[slot][1])

        simulations_tp_radio_frame = ttk.Frame(simulations_tp_frame, width=400, height=350)
        simulations_tp_radio_frame.grid(row=0, column=1, sticky="e")

        self.tp_quicklook_scrollframes = {}
        for slot in self.all_equipment_dict:
            equipment_list = sorted([k["Name2" if "Name2" in k else "Name"] for k in self.all_equipment_dict[slot]])
            self.tp_quicklook_scrollframes[slot] = VirtualRadioFrame(simulations_tp_radio_frame, text=f"  Select {slot.capitalize()}  ", labelanchor="nw", equipment_slot=slot, selection_type="tp", command=self.update_quicklook_equipment, master_data=equipment_list, N=13)
            self.tp_quicklook_scrollframes[slot].place(relx=0, rely=0, relwidth=1.0, relheight=1.0)


        '''
        ===============================================
         Build the bottom frame (holding WS set stuff)
         Literally just copy/paste the TP stuff and
            find+replace "tp" to "ws"
        ===============================================
        '''
        simulations_ws_frame = ttk.Frame(simulate_tab, height=300, width=650)
        simulations_ws_frame.grid(row=1, column=0, sticky="", pady=5)
        simulations_ws_frame.grid_propagate(False)
        simulations_ws_frame.rowconfigure(0, weight=1)
        simulations_ws_frame.columnconfigure((0), weight=1)

        simulations_ws_frame_left = ttk.LabelFrame(simulations_ws_frame, text="  Equipped WS set  ", labelanchor="nw")
        simulations_ws_frame_left.grid(row=0, column=0, sticky="news")
        simulations_ws_frame_left.grid_propagate(False)

        necessary_for_centering_ws_frame = ttk.Frame(simulations_ws_frame_left)
        necessary_for_centering_ws_frame.pack(expand=True, anchor="center")

        simulations_ws_copy_button_frame = ttk.Frame(necessary_for_centering_ws_frame,)
        simulations_ws_copy_button_frame.grid(row=0, column=0, pady=5)

        ws_copy2inputs_button = ttk.Button(simulations_ws_copy_button_frame, text="Copy to Quicklook", width=25, command=lambda event="ws to quicklook": self.copy_gearset_dict(event))
        ws_copy2inputs_button.grid(row=0, column=0, pady=1)
        ws_copy2clipboard_button = ttk.Button(simulations_ws_copy_button_frame, text="Copy to Clipboard", width=25, command=lambda event="ws": self.copy_to_clipboard(event))
        ws_copy2clipboard_button.grid(row=1, column=0, pady=1)

        simulations_ws_gear_frame = ttk.Frame(necessary_for_centering_ws_frame,)
        simulations_ws_gear_frame.grid(row=1, column=0, sticky="")
        simulations_ws_gear_frame.grid_propagate(True)

        self.ws_quicklook_equipped_dict = {slot: {"radio_variable":tk.StringVar(value="Empty"), "icon":self.get_equipment_icon(), "item":gear_pyfile.Empty} for slot in self.all_equipment_dict}
        for slot in self.ws_quicklook_equipped_dict:
            self.ws_quicklook_equipped_dict[slot]["button"] = ttk.Button(simulations_ws_gear_frame, image=self.ws_quicklook_equipped_dict[slot]["icon"], command=lambda event=slot: self.update_visible_quicklook_frame_ws(event))
            self.ws_quicklook_equipped_dict[slot]["button tooltip"] = Hovertip(self.ws_quicklook_equipped_dict[slot]["button"], self.format_tooltip_stats(self.ws_quicklook_equipped_dict[slot]["item"]), hover_delay=500)
            self.ws_quicklook_equipped_dict[slot]["button"].grid(row=self.equipment_button_positions[slot][0], column=self.equipment_button_positions[slot][1])

        simulations_ws_radio_frame = ttk.Frame(simulations_ws_frame, width=400, height=350)
        simulations_ws_radio_frame.grid(row=0, column=1, sticky="e")

        self.ws_quicklook_scrollframes = {}
        for slot in self.all_equipment_dict:
            equipment_list = sorted([k["Name2" if "Name2" in k else "Name"] for k in self.all_equipment_dict[slot]])
            self.ws_quicklook_scrollframes[slot] = VirtualRadioFrame(simulations_ws_radio_frame, text=f"  Select {slot.capitalize()}  ", labelanchor="nw", equipment_slot=slot, selection_type="ws", command=self.update_quicklook_equipment, master_data=equipment_list, N=13)
            self.ws_quicklook_scrollframes[slot].place(relx=0, rely=0, relwidth=1.0, relheight=1.0)


        '''
        ===============================================
                 Build the simulation buttons
        ===============================================
        '''
        simulation_button_frame = ttk.Frame(simulate_tab,)
        simulation_button_frame.grid(row=2, column=0, columnspan=2, pady=20)
        simulation_button_frame.columnconfigure((0,1,2), weight=1)
        simulation_button_frame.rowconfigure((0,1), weight=1)

        dps_simulation_button = tk.Button(simulation_button_frame, text="Run DPS simulations", image=self.pixel_image, compound=tk.CENTER, width=150, height=30, command=lambda event="run dps simulations": self.quicklook(event))
        dps_simulation_button.grid(row=0, column=0, padx=5,)

        self.plot_dps_checkbox_value = tk.BooleanVar(value=False)
        plot_dps_checkbox = ttk.Checkbutton(simulation_button_frame, text="Plot DPS", variable=self.plot_dps_checkbox_value, width=15)
        plot_dps_checkbox.grid(row=1, column=0, pady=5)

        ws_distribution_button = tk.Button(simulation_button_frame, text="Create WS damage\ndistribution plot", image=self.pixel_image, compound=tk.CENTER, width=150, height=30, command=lambda event="build distribution": self.quicklook(event))
        ws_distribution_button.grid(row=0, column=1, padx=5,)

        compare_sets = tk.Button(simulation_button_frame, text="Compare TP & WS stats", image=self.pixel_image, compound=tk.CENTER, width=150, height=30, command=lambda event="compare tp ws stats": self.quicklook(event))
        compare_sets.grid(row=0, column=2, padx=5,)



        '''
        ==============================================================================================
         Build the "show stats" frame
        ==============================================================================================
        '''

        stats_tab = ttk.Frame(self.notebook)
        self.notebook.add(stats_tab, text="Player Stats")
        self.bind_all("<Control-Key-4>", lambda e: self.notebook.select(stats_tab))
        stats_tab.rowconfigure(0, weight=0)
        stats_tab.columnconfigure(0, weight=1)

        stats_buttons_frame = ttk.Frame(stats_tab, width=600, height=50)
        stats_buttons_frame.grid_propagate(False)
        stats_buttons_frame.grid(row=0, column=0, pady=10)
        stats_buttons_frame.rowconfigure(0, weight=1)
        stats_buttons_frame.columnconfigure((0,1,2), weight=1)

        quicklook_stats_button = tk.Button(stats_buttons_frame, text="Quicklook Gear Stats", image=self.pixel_image, compound=tk.CENTER, width=150, height=30, command=lambda event="show stats quicklook": self.quicklook(event))
        quicklook_stats_button.grid(row=0, column=0)

        tp_stats_button = tk.Button(stats_buttons_frame, text="TP Gear Stats", image=self.pixel_image, compound=tk.CENTER, width=150, height=30, command=lambda event="show stats tp": self.quicklook(event))
        tp_stats_button.grid(row=0, column=1)

        ws_stats_button = tk.Button(stats_buttons_frame, text="WS Gear Stats", image=self.pixel_image, compound=tk.CENTER, width=150, height=30, command=lambda event="show stats ws": self.quicklook(event))
        ws_stats_button.grid(row=0, column=2)


        stats_frame = ttk.Frame(stats_tab, borderwidth=2, relief="solid", width=675, height=750)
        stats_frame.grid_propagate(False)
        stats_frame.grid(row=1, column=0)

        stats_frame1 = ttk.Frame(stats_frame)
        stats_frame1.grid(row=0, column=0, sticky="w", pady=5)

        stats_frame2 = ttk.Frame(stats_frame)
        stats_frame2.grid(row=1, column=0, sticky="w", pady=5)

        stats_frame3 = ttk.Frame(stats_frame)
        stats_frame3.grid(row=2, column=0, sticky="w", pady=5)

        useful_stats =  [
                        ["STR", "DEX", "VIT", "AGI", "INT", "MND", "CHR"],
                        ["Accuracy1", "Accuracy2", "Attack1", "Attack2", "Ranged Accuracy", "Ranged Attack",],
                        ["Magic Accuracy", "Magic Attack", "Magic Damage", "Magic Burst Damage", "Magic Burst Damage II", "Magic Burst Damage Trait",],
                        ["Daken", "Zanshin", "Kick Attacks", "DA", "TA", "QA", "Double Shot", "Triple Shot", "Quad Shot",],
                        ["Dual Wield", "Martial Arts", "Gear Haste", "JA Haste", "Magic Haste", "Delay Reduction",],
                        ["PDT", "MDT", "DT", "Evasion", "Magic Evasion", "Magic Defense", "Subtle Blow", "Subtle Blow II", ],
                        ["Regain", "Store TP", "Crit Rate", "Crit Damage", "Ranged Crit Damage", "Weapon Skill Damage", "Weapon Skill Damage Trait", "Skillchain Bonus", "PDL", "PDL Trait", "TP Bonus", ]
                        ]
        self.stats_dict = {stat:{} for k in useful_stats for stat in k }
        
        base_parameters_frame = ttk.LabelFrame(stats_frame1, borderwidth=2, relief="solid", text="  Base Parameters  ", labelanchor="n", width=120, height=170)
        base_parameters_frame.grid_propagate(False)
        base_parameters_frame.grid(row=0, column=0, padx=3)
        base_parameters_frame.columnconfigure(1, weight=1)
        for i,stat in enumerate(useful_stats[0]):
            self.stats_dict[stat]["stringvar"] = tk.StringVar(value="999")
            self.stats_dict[stat]["label1"] = ttk.Label(base_parameters_frame, text=f"{stat}", font="courier 10", justify="left",)
            self.stats_dict[stat]["label1"].grid(row=i, column=0, sticky="w", padx=5)
            self.stats_dict[stat]["label2"] = ttk.Label(base_parameters_frame, textvariable=self.stats_dict[stat]["stringvar"], font="courier 10", justify="right",)
            self.stats_dict[stat]["label2"].grid(row=i, column=2, sticky="e", padx=5)


        acc_atk_frame = ttk.LabelFrame(stats_frame1, borderwidth=2, relief="solid", text="  Physical  ", labelanchor="n", width=260, height=170)
        acc_atk_frame.grid_propagate(False)
        acc_atk_frame.grid(row=0, column=1, sticky="n", padx=3)
        acc_atk_frame.columnconfigure(1, weight=1)
        for i,stat in enumerate(useful_stats[1]):
            self.stats_dict[stat]["stringvar"] = tk.StringVar(value="9999")
            self.stats_dict[stat]["label1"] = ttk.Label(acc_atk_frame, text=f"{stat}", font="courier 10", justify="left")
            self.stats_dict[stat]["label1"].grid(row=i, column=0, sticky="w", padx=5, pady=2)
            self.stats_dict[stat]["label2"] = ttk.Label(acc_atk_frame, textvariable=self.stats_dict[stat]["stringvar"], font="courier 10", justify="right")
            self.stats_dict[stat]["label2"].grid(row=i, column=2, sticky="e", padx=5, pady=2)

        magic_frame = ttk.LabelFrame(stats_frame1, borderwidth=2, relief="solid", text="  Magical  ", labelanchor="n", width=260, height=170)
        magic_frame.grid_propagate(False)
        magic_frame.grid(row=0, column=2, sticky="n", padx=3)
        magic_frame.columnconfigure(1, weight=1)
        for i,stat in enumerate(useful_stats[2]):
            self.stats_dict[stat]["stringvar"] = tk.StringVar(value="999")
            self.stats_dict[stat]["label1"] = ttk.Label(magic_frame, text=f"{stat}", font="courier 10", justify="left")
            self.stats_dict[stat]["label1"].grid(row=i, column=0, sticky="w", pady=2, padx=5)
            self.stats_dict[stat]["label2"] = ttk.Label(magic_frame, textvariable=self.stats_dict[stat]["stringvar"], font="courier 10", justify="right")
            self.stats_dict[stat]["label2"].grid(row=i, column=2, sticky="e", pady=2, padx=5)

        multi_attack_frame = ttk.LabelFrame(stats_frame2, borderwidth=2, relief="solid", text="  Multi-Attack  ", labelanchor="n", width=200, height=240)
        multi_attack_frame.grid_propagate(False)
        multi_attack_frame.grid(row=1, column=0, padx=3)
        multi_attack_frame.columnconfigure(1, weight=1)
        for i,stat in enumerate(useful_stats[3]):
            self.stats_dict[stat]["stringvar"] = tk.StringVar(value="999")
            self.stats_dict[stat]["label1"] = ttk.Label(multi_attack_frame, text=f"{stat}", font="courier 10", justify="left")
            self.stats_dict[stat]["label1"].grid(row=i, column=0, sticky="w", pady=2, padx=5)
            self.stats_dict[stat]["label2"] = ttk.Label(multi_attack_frame, textvariable=self.stats_dict[stat]["stringvar"], font="courier 10", justify="right")
            self.stats_dict[stat]["label2"].grid(row=i, column=2, sticky="e", pady=2, padx=5)

        attack_speed_frame = ttk.LabelFrame(stats_frame2, borderwidth=2, relief="solid", text="  Attack Speed  ", labelanchor="n", width=250, height=240)
        attack_speed_frame.grid_propagate(False)
        attack_speed_frame.grid(row=1, column=1, padx=3, sticky="n")
        attack_speed_frame.columnconfigure(1, weight=1)
        for i,stat in enumerate(useful_stats[4]):
            self.stats_dict[stat]["stringvar"] = tk.StringVar(value="999")
            self.stats_dict[stat]["label1"] = ttk.Label(attack_speed_frame, text=f"{stat}", font="courier 10", justify="left")
            self.stats_dict[stat]["label1"].grid(row=i, column=0, sticky="w", pady=2, padx=5)
            self.stats_dict[stat]["label2"] = ttk.Label(attack_speed_frame, textvariable=self.stats_dict[stat]["stringvar"], font="courier 10", justify="right")
            self.stats_dict[stat]["label2"].grid(row=i, column=2, sticky="e", pady=2, padx=5)

        defensive_frame = ttk.LabelFrame(stats_frame3, borderwidth=2, relief="solid", text="  Defensive  ", labelanchor="nw", width=210, height=290)
        defensive_frame.grid(row=2, column=0, padx=3, sticky="n")
        defensive_frame.grid_propagate(False)
        defensive_frame.columnconfigure(1, weight=1)
        for i,stat in enumerate(useful_stats[5]):
            self.stats_dict[stat]["stringvar"] = tk.StringVar(value="999")
            self.stats_dict[stat]["label1"] = ttk.Label(defensive_frame, text=f"{stat}", font="courier 10", justify="left")
            self.stats_dict[stat]["label1"].grid(row=i, column=0, sticky="w", pady=2, padx=5)
            self.stats_dict[stat]["label2"] = ttk.Label(defensive_frame, textvariable=self.stats_dict[stat]["stringvar"], font="courier 10", justify="right")
            self.stats_dict[stat]["label2"].grid(row=i, column=2, sticky="e", pady=2, padx=5)

        misc_stat_frame = ttk.LabelFrame(stats_frame3, borderwidth=2, relief="solid", text="  Other Stats  ", labelanchor="nw", width=270, height=290)
        misc_stat_frame.grid(row=2, column=1, padx=3, sticky="n")
        misc_stat_frame.grid_propagate(False)
        misc_stat_frame.columnconfigure(1, weight=1)
        for i,stat in enumerate(useful_stats[6]):
            self.stats_dict[stat]["stringvar"] = tk.StringVar(value="999")
            self.stats_dict[stat]["label1"] = ttk.Label(misc_stat_frame, text=f"{stat}", font="courier 10", justify="left")
            self.stats_dict[stat]["label1"].grid(row=i, column=0, sticky="w", pady=2, padx=5)
            self.stats_dict[stat]["label2"] = ttk.Label(misc_stat_frame, textvariable=self.stats_dict[stat]["stringvar"], font="courier 10", justify="right")
            self.stats_dict[stat]["label2"].grid(row=i, column=2, sticky="e", pady=2, padx=5)


        '''
        ==============================================================================================
            Build the Automaton tab.
            Work in progress. Still need details on automaton stats anyway.
        ==============================================================================================
        '''
        if False:
            automaton_tab = ttk.Frame(self.notebook)
            self.notebook.add(automaton_tab, text="Automaton")
            self.bind_all("<Control-Key-5>", lambda e: self.notebook.select(automaton_tab)) 
            automaton_tab.rowconfigure((0,1), weight=1)
            automaton_tab.columnconfigure(0, weight=1)

            self.automaton_equipped_dict = {f"slot{i}":{} for i in range(21)} # 16 attachments, 3 maneuvers, head, frame

            container = ttk.Frame(automaton_tab, borderwidth=2, relief="solid", width=350, height=350)
            container.grid_propagate(False)
            container.grid(row=0, column=0)

            head_frame_frame = ttk.Frame(container, borderwidth=2, relief="solid", width=120, height=50)
            head_frame_frame.grid(row=0, column=1, sticky="w")
            for i in range(2):
                random_pet = [
                            np.random.choice(["Harlequin Head", "Valoredge Head", "Stormwaker Head", "Soulsoother Head", "Spiritreaver Head"]),
                            np.random.choice(["Harlequin Frame", "Valoredge Frame", "Stormwaker Frame"]),
                            ]
                self.automaton_equipped_dict[f"slot{i}"]["icon"] = self.get_equipment_icon(random_pet[i])
                self.automaton_equipped_dict[f"slot{i}"]["button"] = ttk.Button(head_frame_frame, image=self.automaton_equipped_dict[f"slot{i}"]["icon"], command=lambda event=f"slot{i}": print(event))
                self.automaton_equipped_dict[f"slot{i}"]["button"].grid(row=0, column=i)


            capacity_frame = ttk.Frame(container, borderwidth=2, relief="solid", width=120, height=170)
            capacity_frame.grid_propagate(False)
            capacity_frame.grid(row=1, column=0, sticky="ns")

            gear_frame = ttk.Frame(container, borderwidth=2, relief="solid",)
            gear_frame.grid_propagate(True)
            gear_frame.grid(row=1, column=1)

            for i,slot in enumerate(self.equipment_button_positions):
                random_attachment = np.random.choice(["Fire Attachment", "Ice Attachment", "Thunder Attachment", "Earth Attachment", "Light Attachment", "Dark Attachment", "Water Attachment", "Wind Attachment"])
                self.automaton_equipped_dict[f"slot{i+2}"]["icon"] = self.get_equipment_icon(random_attachment)
                self.automaton_equipped_dict[f"slot{i+2}"]["button"] = ttk.Button(gear_frame, image=self.automaton_equipped_dict[f"slot{i+2}"]["icon"], command=lambda event=f"slot{i+2}": print(event))
                self.automaton_equipped_dict[f"slot{i+2}"]["button"].grid(row=self.equipment_button_positions[slot][0], column=self.equipment_button_positions[slot][1])

            maneuver_frame = ttk.Frame(container, borderwidth=2, relief="solid")
            maneuver_frame.grid_propagate(True)
            maneuver_frame.grid(row=1, column=2, sticky="n")

            for i in range(3):
                random_maneuvers = [
                            np.random.choice([element + " Maneuver" for element in ["Fire", "Earth", "Water", "Wind", "Ice", "Thunder", "Light", "Dark"]]),
                            np.random.choice([element + " Maneuver" for element in ["Fire", "Earth", "Water", "Wind", "Ice", "Thunder", "Light", "Dark"]]),
                            np.random.choice([element + " Maneuver" for element in ["Fire", "Earth", "Water", "Wind", "Ice", "Thunder", "Light", "Dark"]]),
                            ]
                self.automaton_equipped_dict[f"slot{i+18}"]["icon"] = self.get_equipment_icon(random_maneuvers[i])
                self.automaton_equipped_dict[f"slot{i+18}"]["button"] = ttk.Button(maneuver_frame, image=self.automaton_equipped_dict[f"slot{i+18}"]["icon"], command=lambda event=f"slot{i+18}": print(event))
                self.automaton_equipped_dict[f"slot{i+18}"]["button"].grid(row=i, column=0)


            self.notebook.select(automaton_tab)
        '''
        ==============================================================================================
         The GUI has been built at this point.
         Call the update functions to set good values in all entries.
        ==============================================================================================
        '''
        if os.path.isfile("defaults.pkl"):
            self.load_defaults("default")
        else:
            # Dictionary containing the job profiles for saving/loading defaults by main job selection.
            # Saved to defaults.pkl when using "Save Defaults" button.
            self.states = {"default":{}, **{job:{} for job in self.jobs_dict.keys()}}

            self.update_job("main")
            self.update_job("sub")
            for slot in self.quicklook_equipped_dict:
                self.update_quicklook_equipment((slot, self.quicklook_equipped_dict[slot]["item"]["Name2"], self.quicklook_equipped_dict, "quicklook"))
                self.update_quicklook_equipment((slot, self.tp_quicklook_equipped_dict[slot]["item"]["Name2"], self.tp_quicklook_equipped_dict, "tp"))
                self.update_quicklook_equipment((slot, self.ws_quicklook_equipped_dict[slot]["item"]["Name2"], self.ws_quicklook_equipped_dict, "ws"))

        self.update_visible_quicklook_frame("main")
        self.update_visible_quicklook_frame_tp("main")
        self.update_visible_quicklook_frame_ws("main")
        self.update_visible_optimize_frame("main")
        self.after(1500, self.poll_wsdist_bridge)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = application()
    app.wait_visibility()

    app.mainloop()
