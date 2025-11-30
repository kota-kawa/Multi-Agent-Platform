import json
import os
import tempfile
import unittest
from typing import Any, Dict
from multi_agent_app.memory_manager import MemoryManager

class TestMemoryManager(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.file_path = self.temp_file.name
        self.temp_file.close()

    def tearDown(self):
        if os.path.exists(self.file_path):
            os.remove(self.file_path)

    def test_load_non_existent_file(self):
        # Delete the temp file first to simulate non-existence
        os.remove(self.file_path)
        
        manager = MemoryManager(self.file_path)
        memory = manager.load_memory()
        
        self.assertEqual(memory["type"], "chat_memory")
        self.assertEqual(memory["summary_text"], "")
        self.assertEqual(memory["slots"], [])

    def test_migrate_legacy_memory(self):
        legacy_data = {"memory": "Old summary content"}
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f)
            
        manager = MemoryManager(self.file_path)
        memory = manager.load_memory()
        
        self.assertEqual(memory["type"], "chat_memory")
        self.assertEqual(memory["summary_text"], "Old summary content")
        self.assertEqual(memory["slots"], [])

    def test_apply_diff_update_summary(self):
        manager = MemoryManager(self.file_path)
        diff = {
            "summary_text": "New summary",
            "operations": []
        }
        memory = manager.apply_diff(diff)
        
        self.assertEqual(memory["summary_text"], "New summary")
        
        # Verify persistence
        loaded_memory = manager.load_memory()
        self.assertEqual(loaded_memory["summary_text"], "New summary")

    def test_apply_diff_add_new_slot(self):
        manager = MemoryManager(self.file_path)
        diff = {
            "summary_text": "User likes cats",
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "pet_preference",
                    "value": "Cat",
                    "label": "好きなペット",
                    "category": "preference",
                    "confidence": 0.9
                }
            ]
        }
        memory = manager.apply_diff(diff)
        
        self.assertEqual(len(memory["slots"]), 1)
        slot = memory["slots"][0]
        self.assertEqual(slot["id"], "pet_preference")
        self.assertEqual(slot["current_value"], "Cat")
        self.assertEqual(slot["label"], "好きなペット")
        self.assertEqual(slot["category"], "preference")
        self.assertEqual(slot["confidence"], 0.9)

    def test_apply_diff_update_slot(self):
        # Initial setup
        manager = MemoryManager(self.file_path)
        initial_diff = {
            "summary_text": "",
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "location",
                    "value": "Tokyo"
                }
            ]
        }
        manager.apply_diff(initial_diff)
        
        # Update
        update_diff = {
            "summary_text": "",
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "location",
                    "value": "Osaka",
                    "log_change": True,
                    "reason": "Moved"
                }
            ]
        }
        memory = manager.apply_diff(update_diff)
        
        slot = memory["slots"][0]
        self.assertEqual(slot["current_value"], "Osaka")
        self.assertEqual(len(slot["history"]), 1)
        self.assertEqual(slot["history"][0]["from_value"], "Tokyo")
        self.assertEqual(slot["history"][0]["to_value"], "Osaka")
        self.assertEqual(slot["history"][0]["reason"], "Moved")
        
        self.assertEqual(len(memory["important_changes"]), 1)
        self.assertEqual(memory["important_changes"][0]["from_value"], "Tokyo")
        self.assertEqual(memory["important_changes"][0]["to_value"], "Osaka")

    def test_apply_diff_no_change(self):
        # Initial setup
        manager = MemoryManager(self.file_path)
        initial_diff = {
            "summary_text": "",
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "status",
                    "value": "active"
                }
            ]
        }
        manager.apply_diff(initial_diff)
        
        # Update with same value
        update_diff = {
            "summary_text": "",
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "status",
                    "value": "active",
                    "log_change": True, # Should be ignored
                    "reason": "Still active"
                }
            ]
        }
        memory = manager.apply_diff(update_diff)
        
        slot = memory["slots"][0]
        self.assertEqual(len(slot["history"]), 0) # Should not log history for no change

if __name__ == "__main__":
    unittest.main()
