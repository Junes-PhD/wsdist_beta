import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import actions
from result_history import ResultHistory


class ResultHistoryTests(unittest.TestCase):
    def test_round_trip_and_pin_aware_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            history = ResultHistory(Path(directory), source_hash="source-a", limit=2)
            first = history.add("Malware", "cycle", "First", {"metrics": {"dps": 1}})
            pinned = history.add("Malware", "cycle", "Pinned", {"metrics": {"dps": 2}}, pinned=True)
            history.add("Malware", "cycle", "Third", {"metrics": {"dps": 3}})
            history.add("Malware", "cycle", "Fourth", {"metrics": {"dps": 4}})
            records = history.list("Malware")
            self.assertEqual(len(records), 3)
            self.assertIn(pinned, {record["id"] for record in records})
            self.assertNotIn(first, {record["id"] for record in records})
            self.assertEqual(history.get(pinned)["payload"]["metrics"]["dps"], 2)

    def test_source_mismatch_is_viewable_as_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = ResultHistory(Path(directory), source_hash="source-a")
            result_id = writer.add("A", "optimizer", "Old", {"seed": 4})
            reader = ResultHistory(Path(directory), source_hash="source-b")
            record = reader.get(result_id)
            self.assertTrue(record["stale"])
            self.assertEqual(record["payload"]["seed"], 4)

    def test_corrupt_payload_does_not_break_history_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            history = ResultHistory(Path(directory), source_hash="source")
            result_id = history.add("A", "cycle", "Corruptible", {"seed": 1})
            connection = sqlite3.connect(history.path)
            try:
                connection.execute(
                    "UPDATE simulation_history SET payload = ? WHERE result_id = ?",
                    ("{not-json", result_id),
                )
                connection.commit()
            finally:
                connection.close()
            record = history.get(result_id)
            self.assertEqual(record["payload"], {})

    def test_clear_unpinned_preserves_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            history = ResultHistory(Path(directory), source_hash="source")
            history.add("A", "cycle", "Saved", {})
            pinned = history.add("A", "cycle", "Keep", {}, pinned=True)
            removed = history.clear("A", pinned=True)
            self.assertEqual(removed, 1)
            self.assertEqual(history.get(pinned)["title"], "Keep")

    def test_seeded_structured_results_reproduce_without_global_rng_leak(self):
        player = SimpleNamespace(
            stats={"Delay1": 240, "Delay2": 0, "Dual Wield": 0, "Martial Arts": 0,
                   "Magic Haste": 0, "JA Haste": 0, "Gear Haste": 0, "Regain": 0},
            gearset={"main": {"Name": "Test Weapon", "Skill Type": "Sword"}, "sub": {}},
            abilities={},
        )
        enemy = SimpleNamespace(stats={})
        with (
            patch.object(actions, "average_attack_round", side_effect=lambda *args, **kwargs: (
                float(np.random.uniform(90, 110)), 100.0, 0.0,
            )),
            patch.object(actions, "average_ws", side_effect=lambda *args, **kwargs: (
                float(np.random.uniform(900, 1100)), 0.0,
            )),
        ):
            before = np.random.get_state()
            first = actions.run_simulation_structured(player, player, enemy, 1000, "Test WS", "melee", seed=7)
            after = np.random.get_state()
            second = actions.run_simulation_structured(player, player, enemy, 1000, "Test WS", "melee", seed=7)
        self.assertEqual(first, second)
        self.assertEqual(before[1].tolist(), after[1].tolist())
        self.assertLessEqual(len(first["dps_series"]["time"]), 600)


if __name__ == "__main__":
    unittest.main()
