import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from typing import Any, Dict
from unittest.mock import patch

from multi_agent_app.memory_manager import MemoryManager


def _memory_settings(**overrides):
    base = {
        "enabled": True,
        "history_sync_enabled": True,
        "short_term_ttl_minutes": 5,
        "short_term_grace_minutes": 0,
        "short_term_active_task_hold_minutes": 0,
        "short_term_promote_score": 1,
        "short_term_promote_importance": 0.5,
    }
    base.update(overrides)
    return base

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

    def test_apply_diff_preserves_unknown_category(self):
        manager = MemoryManager(self.file_path)
        diff = {
            "category_summaries": {
                "music": "ピアノを10年続けている",
                "general": "既存メモ",
            },
            "operations": []
        }
        memory = manager.apply_diff(diff)

        self.assertIn("music", memory["category_summaries"])
        self.assertEqual(memory["category_summaries"]["music"], "ピアノを10年続けている")

    def test_get_formatted_memory_includes_custom_category(self):
        manager = MemoryManager(self.file_path)
        diff = {
            "category_summaries": {"music": "ギターが得意"},
            "operations": [
                {
                    "op": "set_slot",
                    "slot_id": "fav_music",
                    "value": "ジャズ",
                    "label": "好きなジャンル",
                    "category": "music",
                    "confidence": 0.8,
                }
            ],
        }
        manager.apply_diff(diff)

        formatted = manager.get_formatted_memory()
        self.assertIn("【Music】", formatted)
        self.assertIn("概要: ギターが得意", formatted)
        self.assertIn("好きなジャンル", formatted)

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

    def test_replace_with_user_payload_updates_summary_when_log_exists(self):
        manager = MemoryManager(self.file_path)
        seeded = manager.load_memory()
        seeded["summary_text"] = "old"
        seeded["summary_log"] = ["2025-12-01: old"]
        seeded["category_summaries"] = {"general": "old"}
        manager.save_memory(seeded)

        manager.replace_with_user_payload({"profile": "新しいプロフィール"})
        updated = manager.load_memory()

        self.assertEqual(updated["category_summaries"], {"profile": "新しいプロフィール"})
        self.assertEqual(updated["summary_text"], "新しいプロフィール")

    def test_replace_with_user_payload_handles_strings_and_clears_entries(self):
        manager = MemoryManager(self.file_path)

        manager.replace_with_user_payload("単純なメモ")
        memory = manager.load_memory()
        self.assertEqual(memory["category_summaries"], {"general": "単純なメモ"})
        self.assertEqual(memory["summary_text"], "単純なメモ")

        manager.replace_with_user_payload({})
        cleared = manager.load_memory()
        self.assertEqual(cleared["category_summaries"], {})
        self.assertEqual(cleared["summary_text"], "")

    def test_replace_with_user_payload_persists_category_titles(self):
        manager = MemoryManager(self.file_path)
        payload = {
            "categories": {
                "daily_memo": "天気と日報をまとめたメモ",
            },
            "titles": {
                "daily_memo": "日報メモ",
            },
        }

        manager.replace_with_user_payload(payload)
        memory = manager.load_memory()

        self.assertEqual(memory["category_summaries"]["daily_memo"], "天気と日報をまとめたメモ")
        self.assertEqual(memory["category_titles"]["daily_memo"], "日報メモ")

    def test_category_titles_fallback_to_pretty_label(self):
        manager = MemoryManager(self.file_path)
        manager.replace_with_user_payload({"meal_planning": "献立メモ"})
        memory = manager.load_memory()

        self.assertEqual(memory["category_summaries"]["meal_planning"], "献立メモ")
        self.assertEqual(memory["category_titles"]["meal_planning"], "Meal Planning")

    def test_load_memory_normalizes_category_keys(self):
        payload = {
            "type": "chat_memory",
            "summary_text": "",
            "summary_log": [],
            "category_summaries": {"Music": "ギター経験あり", "PLAN": "来週の計画"},
        }
        with open(self.file_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp)

        manager = MemoryManager(self.file_path)
        memory = manager.load_memory()

        self.assertIn("music", memory["category_summaries"])
        self.assertNotIn("plan", memory["category_summaries"])
        self.assertIn("general", memory["category_summaries"])
        self.assertIn("来週の計画", memory["category_summaries"]["general"])

    def test_replace_with_user_payload_structures_profile_and_preferences(self):
        manager = MemoryManager(self.file_path)
        payload = {
            "profile": "名前: 山田太郎\n居住地: 東京\n職業: エンジニア",
            "preference": "・コーヒーが好き\n・辛い料理は苦手",
        }

        manager.replace_with_user_payload(payload)
        memory = manager.load_memory()

        manual_slots = [slot for slot in memory["slots"] if slot.get("source") == "manual_editor"]
        self.assertGreaterEqual(len(manual_slots), 3)
        labels = {slot["label"] for slot in manual_slots}
        self.assertIn("名前", labels)
        self.assertIn("居住地", labels)
        self.assertIn("職業", labels)

        profile = memory.get("user_profile") or {}
        self.assertEqual(profile.get("name"), "山田太郎")
        self.assertEqual(profile.get("location"), "東京")
        self.assertEqual(profile.get("occupation"), "エンジニア")

        preferences = memory.get("preferences") or {}
        self.assertIn("コーヒーが好き", preferences.get("likes", []))
        self.assertIn("辛い料理は苦手", preferences.get("dislikes", []))

    def test_replace_with_user_payload_clears_manual_slots_when_empty(self):
        manager = MemoryManager(self.file_path)
        manager.replace_with_user_payload({"general": "・最初の項目\n・次の項目"})
        seeded = manager.load_memory()
        manual_slots = [slot for slot in seeded["slots"] if slot.get("source") == "manual_editor"]
        self.assertGreater(len(manual_slots), 0)

        manager.replace_with_user_payload({})
        cleared = manager.load_memory()
        manual_slots_after = [slot for slot in cleared["slots"] if slot.get("source") == "manual_editor"]
        self.assertEqual(manual_slots_after, [])

    @patch("multi_agent_app.memory_manager.load_memory_settings")
    def test_short_term_expiry_resets_store(self, mock_settings):
        mock_settings.return_value = _memory_settings(
            short_term_ttl_minutes=1,
            short_term_grace_minutes=0,
            short_term_active_task_hold_minutes=0,
            short_term_promote_score=5,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            short_path = os.path.join(tmpdir, "short_term_memory.json")
            manager = MemoryManager(short_path)
            memory = manager.load_memory()
            memory["slots"].append(
                {
                    "id": "ephemeral_fact",
                    "label": "一時的な事実",
                    "category": "general",
                    "current_value": "短期メモ",
                    "confidence": 0.5,
                    "last_updated": datetime.now().isoformat(),
                    "history": [],
                    "source": "test",
                    "verified": False,
                    "access_count": 0,
                    "last_accessed": datetime.now().isoformat(),
                    "priority": "medium",
                    "score": 0,
                }
            )
            memory["expires_at"] = "2000-01-01T00:00:00"
            manager.save_memory(memory)

            refreshed = manager.load_memory()
            self.assertEqual(refreshed["slots"], [])
            self.assertIsNotNone(refreshed.get("expires_at"))

    @patch("multi_agent_app.memory_manager.load_memory_settings")
    def test_short_term_active_task_hold_extends_expiry(self, mock_settings):
        mock_settings.return_value = _memory_settings(
            short_term_ttl_minutes=1,
            short_term_grace_minutes=0,
            short_term_active_task_hold_minutes=30,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            short_path = os.path.join(tmpdir, "short_term_memory.json")
            manager = MemoryManager(short_path)
            memory = manager.load_memory()
            memory["active_task"] = {"id": "task-1", "goal": "demo"}
            expired_time = datetime.now() - timedelta(minutes=1)
            memory["expires_at"] = expired_time.isoformat()
            manager.save_memory(memory)

            refreshed = manager.load_memory()
            new_expiry = datetime.fromisoformat(refreshed["expires_at"])
            self.assertGreater(new_expiry, expired_time)
            self.assertEqual(refreshed["active_task"]["id"], "task-1")

    @patch("multi_agent_app.memory_manager.load_memory_settings")
    def test_short_term_expiry_promotes_high_score_slots(self, mock_settings):
        mock_settings.return_value = _memory_settings(
            short_term_ttl_minutes=1,
            short_term_grace_minutes=0,
            short_term_promote_score=1,
            short_term_promote_importance=0.1,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            prev_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                short_path = os.path.join(tmpdir, "short_term_memory.json")
                manager = MemoryManager(short_path)
                memory = manager.load_memory()
                memory["slots"].append(
                    {
                        "id": "important_fact",
                        "label": "重要な事実",
                        "category": "general",
                        "current_value": "プロジェクトXが進行中",
                        "confidence": 0.8,
                        "last_updated": datetime.now().isoformat(),
                        "history": [],
                        "source": "test",
                        "verified": True,
                        "access_count": 5,
                        "last_accessed": datetime.now().isoformat(),
                        "priority": "high",
                        "score": 3,
                    }
                )
                memory["expires_at"] = (datetime.now() - timedelta(minutes=10)).isoformat()
                manager.save_memory(memory)

                manager.load_memory()  # Trigger expiry + promotion

                long_manager = MemoryManager(os.path.join(tmpdir, "long_term_memory.json"))
                promoted = long_manager.load_memory()
                self.assertTrue(any(slot["id"] == "important_fact" for slot in promoted["slots"]))
            finally:
                os.chdir(prev_cwd)

if __name__ == "__main__":
    unittest.main()
