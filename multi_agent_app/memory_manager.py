"""Memory manager for structured JSON memory with semantic diff application."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict, cast

from .config import _current_datetime_line

# Type Definitions

class MemorySlotHistory(TypedDict):
    changed_at: str
    from_value: Any
    to_value: Any
    reason: str

class MemorySlot(TypedDict):
    id: str
    label: str
    category: str
    current_value: Any
    confidence: float
    last_updated: str
    history: List[MemorySlotHistory]

class ImportantChange(TypedDict):
    id: int
    slot_id: str
    changed_at: str
    label: str
    from_value: Any
    to_value: Any
    note: str

class MemoryStore(TypedDict):
    type: str
    version: int
    last_updated: str
    summary_text: str
    slots: List[MemorySlot]
    important_changes: List[ImportantChange]

class MemoryOperation(TypedDict, total=False):
    op: str  # "set_slot"
    slot_id: str
    value: Any
    log_change: bool
    reason: str
    # For new slots
    label: str
    category: str
    confidence: float

class MemoryDiff(TypedDict):
    summary_text: str
    operations: List[MemoryOperation]


class MemoryManager:
    """Manages reading, writing, and updating structured memory files."""

    VERSION = 1
    TYPE = "chat_memory"

    def __init__(self, file_path: str):
        self.file_path = file_path

    def load_memory(self) -> MemoryStore:
        """Load memory from file, initializing or migrating if necessary."""
        if not os.path.exists(self.file_path):
            return self._create_empty_memory()

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            logging.warning(f"Failed to load memory from {self.file_path}, resetting.")
            return self._create_empty_memory()

        # Migration from legacy text-only memory
        if "memory" in data and "slots" not in data:
            return self._migrate_legacy_memory(data.get("memory", ""))
        
        # Basic validation/fill missing keys
        if data.get("type") != self.TYPE:
             data["type"] = self.TYPE
        if "slots" not in data:
            data["slots"] = []
        if "important_changes" not in data:
            data["important_changes"] = []
        if "summary_text" not in data:
            data["summary_text"] = ""
            
        return cast(MemoryStore, data)

    def save_memory(self, memory: MemoryStore) -> None:
        """Save memory to file."""
        memory["last_updated"] = datetime.now().isoformat()
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(memory, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logging.error(f"Failed to save memory to {self.file_path}: {e}")

    def apply_diff(self, diff: MemoryDiff) -> MemoryStore:
        """Apply a semantic diff (operations) to the current memory."""
        memory = self.load_memory()
        
        # 1. Update summary
        new_summary = diff.get("summary_text")
        if new_summary:
            memory["summary_text"] = new_summary

        # 2. Apply operations
        operations = diff.get("operations") or []
        for op in operations:
            op_type = op.get("op")
            if op_type == "set_slot":
                self._apply_set_slot(memory, op)
            else:
                logging.warning(f"Unknown memory operation: {op_type}")

        self.save_memory(memory)
        return memory

    def _create_empty_memory(self) -> MemoryStore:
        return {
            "type": self.TYPE,
            "version": self.VERSION,
            "last_updated": datetime.now().isoformat(),
            "summary_text": "",
            "slots": [],
            "important_changes": []
        }

    def _migrate_legacy_memory(self, text: str) -> MemoryStore:
        """Convert old text-based memory to new structure."""
        return {
            "type": self.TYPE,
            "version": self.VERSION,
            "last_updated": datetime.now().isoformat(),
            "summary_text": text, # Use the old text as summary
            "slots": [],
            "important_changes": []
        }

    def _apply_set_slot(self, memory: MemoryStore, op: MemoryOperation) -> None:
        slot_id = op.get("slot_id")
        if not slot_id:
            return
            
        new_value = op.get("value")
        log_change = op.get("log_change", False)
        reason = op.get("reason", "")
        
        # Find existing slot
        target_slot: Optional[MemorySlot] = None
        for slot in memory["slots"]:
            if slot["id"] == slot_id:
                target_slot = slot
                break
        
        current_time = datetime.now().isoformat()

        if target_slot:
            # Update existing
            old_value = target_slot["current_value"]
            
            # Skip if value hasn't changed (unless forced? no, strictly check value)
            # Simple equality check. For complex types, might need more.
            if old_value == new_value:
                return

            target_slot["current_value"] = new_value
            target_slot["last_updated"] = current_time
            if op.get("confidence"):
                 target_slot["confidence"] = op["confidence"]

            if log_change:
                # Add to history
                history_entry: MemorySlotHistory = {
                    "changed_at": current_time,
                    "from_value": old_value,
                    "to_value": new_value,
                    "reason": reason
                }
                if "history" not in target_slot:
                    target_slot["history"] = []
                target_slot["history"].append(history_entry)

                # Add to important_changes
                change_entry: ImportantChange = {
                    "id": len(memory["important_changes"]) + 1,
                    "slot_id": slot_id,
                    "changed_at": current_time,
                    "label": target_slot["label"],
                    "from_value": old_value,
                    "to_value": new_value,
                    "note": reason
                }
                memory["important_changes"].append(change_entry)
                
                # Keep important_changes bounded? (Optional, maybe last 50)
                if len(memory["important_changes"]) > 50:
                     memory["important_changes"] = memory["important_changes"][-50:]

        else:
            # Create new slot
            # User said "Fraudulent slot_id is ignored or error", but here we allow creation
            # if sufficient info is provided, or we create a generic one.
            # For now, let's be permissive to allow learning new things.
            new_slot: MemorySlot = {
                "id": slot_id,
                "label": op.get("label") or slot_id,
                "category": op.get("category") or "general",
                "current_value": new_value,
                "confidence": op.get("confidence") or 1.0,
                "last_updated": current_time,
                "history": []
            }
            
            # Initial history entry? Maybe not needed for creation.
            memory["slots"].append(new_slot)
