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
        self.assertEqual(memory["category_summaries"], {})
        self.assertEqual(memory["slots"], [])

    def test_migrate_legacy_memory(self):
        legacy_data = {"memory": "Old summary content"}
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f)
            
        manager = MemoryManager(self.file_path)
        memory = manager.load_memory()
        
        self.assertEqual(memory["type"], "chat_memory")
        self.assertEqual(memory["summary_text"], "Old summary content")
        self.assertEqual(memory["category_summaries"], {"general": "Old summary content"})
        self.assertEqual(memory["slots"], [])

    def test_apply_diff_update_summary(self):
        manager = MemoryManager(self.file_path)
        diff = {
            "summary_text": "New summary",
            "operations": []
        }
        memory = manager.apply_diff(diff)
        
        self.assertEqual(memory["summary_text"], "New summary")
        self.assertEqual(memory["category_summaries"]["general"], "New summary")
        
        # Verify persistence
        loaded_memory = manager.load_memory()
        self.assertEqual(loaded_memory["summary_text"], "New summary")

    def test_apply_diff_category_summaries(self):
        manager = MemoryManager(self.file_path)
        diff = {
            "category_summaries": {
                "preference": "醤油ラーメンが好き",
                "travel": "来週京都旅行を計画中"
            },
            "operations": []
        }
        memory = manager.apply_diff(diff)
        
        self.assertEqual(memory["category_summaries"]["preference"], "醤油ラーメンが好き")
        self.assertEqual(memory["category_summaries"]["travel"], "来週京都旅行を計画中")
        
        # Verify persistence
        loaded_memory = manager.load_memory()
        self.assertEqual(loaded_memory["category_summaries"]["preference"], "醤油ラーメンが好き")

    def test_apply_diff_add_new_slot(self):
        manager = MemoryManager(self.file_path)
        diff = {
            "category_summaries": {"preference": "User likes cats"},
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
            "category_summaries": {},
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
            "category_summaries": {},
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
            "category_summaries": {},
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
            "category_summaries": {},
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

    def test_get_formatted_memory(self):
        manager = MemoryManager(self.file_path)
        diff = {
            "category_summaries": {
                "preference": "醤油ラーメンが好き",
                "travel": "来週京都旅行を計画中"
            },
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "favorite_food",
                    "value": "ラーメン",
                    "label": "好きな食べ物",
                    "category": "preference"
                }
            ]
        }
        manager.apply_diff(diff)
    
        formatted = manager.get_formatted_memory()
    
        self.assertIn("【好み・嗜好】", formatted)
        self.assertIn("醤油ラーメンが好き", formatted)
        # Updated assertion to handle metadata string (e.g. "好きな食べ物 (更新:2025-12-07): ラーメン")
        self.assertIn("好きな食べ物", formatted)
        self.assertIn("ラーメン", formatted)
        self.assertIn("【旅行】", formatted)
        self.assertIn("来週京都旅行を計画中", formatted)
    def test_migrate_memory_without_category_summaries(self):
        # Existing memory format without category_summaries
        old_format = {
            "type": "chat_memory",
            "version": 1,
            "last_updated": "2025-01-01T00:00:00",
            "summary_text": "User likes coffee",
            "slots": [],
            "important_changes": []
        }
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(old_format, f)
        
        manager = MemoryManager(self.file_path)
        memory = manager.load_memory()
        
        # Should migrate summary_text to category_summaries.general
        self.assertEqual(memory["category_summaries"]["general"], "User likes coffee")
        self.assertEqual(memory["summary_text"], "User likes coffee")

    def test_apply_diff_deep_merge(self):
        """Test that dictionary fields are deep merged, not overwritten."""
        manager = MemoryManager(self.file_path)
        
        # Initial state
        initial_data = {
            "user_profile": {
                "name": "Taro",
                "age": 30,
                "location": {
                    "city": "Tokyo",
                    "country": "Japan"
                }
            }
        }
        
        # Manually inject initial state
        memory = manager.load_memory()
        memory["user_profile"] = initial_data["user_profile"]
        manager.save_memory(memory)
        
        # Apply diff with partial update
        diff = {
            "new_data": {
                "user_profile": {
                    "age": 31,  # Update existing field
                    "occupation": "Engineer",  # Add new field
                    "location": {
                        "city": "Yokohama"  # Update nested field, country should remain
                    }
                }
            }
        }
        
        updated_memory = manager.apply_diff(diff)
        profile = updated_memory["user_profile"]
        
        # Verify updates
        self.assertEqual(profile["name"], "Taro")  # Should persist
        self.assertEqual(profile["age"], 31)  # Should update
        self.assertEqual(profile["occupation"], "Engineer")  # Should add
        
        # Verify nested merge
        self.assertEqual(profile["location"]["city"], "Yokohama")  # Should update
        self.assertEqual(profile["location"]["country"], "Japan")  # Should persist

    def test_apply_diff_fuzzy_match_slot(self):
        """Test that similar slot IDs are merged."""
        manager = MemoryManager(self.file_path)
        
        # 1. Create initial slot
        diff1 = {
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "user_hobby",
                    "value": "Soccer"
                }
            ]
        }
        manager.apply_diff(diff1)
        
        # 2. Try to add similar slot "user_hobbies"
        diff2 = {
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "user_hobbies", # Similar ID
                    "value": "Soccer, Tennis",
                    "log_change": True
                }
            ]
        }
        memory = manager.apply_diff(diff2)
        
        # Should have merged into "user_hobby"
        self.assertEqual(len(memory["slots"]), 1)
        slot = memory["slots"][0]
        self.assertEqual(slot["id"], "user_hobby")
        self.assertEqual(slot["current_value"], "Soccer, Tennis")

    def test_apply_diff_id_normalization(self):
        """Test that slot IDs are normalized."""
        manager = MemoryManager(self.file_path)
        
        diff = {
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "User Name", # Should become user_name
                    "value": "Taro"
                },
                 {
                    "op": "set_slot",
                    "slot_id": "my___VARIABLE", # Should become my_variable
                    "value": "X"
                }
            ]
        }
        memory = manager.apply_diff(diff)
        
        ids = [s["id"] for s in memory["slots"]]
        self.assertIn("user_name", ids)
        self.assertIn("my_variable", ids)
        self.assertNotIn("User Name", ids)

if __name__ == "__main__":
    unittest.main()
