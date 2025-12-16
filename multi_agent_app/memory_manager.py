"""Memory manager for structured JSON memory with semantic diff application."""

from __future__ import annotations

import json
import logging
import os
import difflib
import re
import hashlib
import threading
import textwrap
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict, cast, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic

from .settings import resolve_llm_config
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
    # New metadata
    source: str  # user_explicit, inferred, agent_observed
    verified: bool
    access_count: int
    last_accessed: str
    priority: str  # high, medium, low
    score: int  # Relevance score based on usage (+1 for used, -1 for ignored/rejected)

class EpisodicItem(TypedDict):
    id: str
    timestamp: str
    content: str
    type: str  # conversation, action, insight, event
    importance: float
    tags: List[str]
    related_slots: List[str]

class ProjectMemory(TypedDict):
    id: str
    name: str
    description: str
    created_at: str
    last_updated: str
    status: str  # active, archived, suspended
    semantic_memory: List[MemorySlot]  # Project-specific knowledge
    episodic_memory: List[EpisodicItem]  # Project-specific events

class ImportantChange(TypedDict):
    id: int
    slot_id: str
    changed_at: str
    label: str
    from_value: Any
    to_value: Any
    note: str

# Category summaries store per-category text summaries
CategorySummaries = Dict[str, str]

class MemoryStore(TypedDict):
    type: str
    version: int
    last_updated: str
    summary_text: str  # Legacy field, kept for backwards compatibility
    category_summaries: CategorySummaries  # New: per-category summaries
    slots: List[MemorySlot]  # Global Semantic Memory
    important_changes: List[ImportantChange]
    
    # New: Global Episodic Memory & Projects
    episodic_memory: List[EpisodicItem]
    projects: Dict[str, ProjectMemory]

    # Maintenance fields
    last_decay_processed: Optional[str]

    # Short-term memory fields
    expires_at: Optional[str]
    active_task: Optional[Dict[str, Any]]
    pending_questions: Optional[List[str]]
    recent_entities: Optional[List[Dict[str, Any]]]
    emotional_context: Optional[str]
    
    # Long-term memory fields
    user_profile: Optional[Dict[str, Any]]
    preferences: Optional[Dict[str, Any]]
    recurring_patterns: Optional[List[Dict[str, Any]]]
    learned_corrections: Optional[List[Dict[str, Any]]]
    relationship_graph: Optional[List[Dict[str, Any]]]
    topics_of_interest: Optional[List[str]]
    do_not_mention: Optional[List[str]]
    created_at: Optional[str]
    source: Optional[str]

class MemoryOperation(TypedDict, total=False):
    op: str  # "set_slot", "set_category_summary", "add_episode", "update_project", "record_usage"
    slot_id: str
    value: Any
    log_change: bool
    reason: str
    # For new slots
    label: str
    category: str
    confidence: float
    # New slot metadata
    source: str
    verified: bool
    priority: str
    # For episodes / projects
    project_id: str
    content: str
    importance: float
    tags: List[str]
    # For project update
    project_name: str
    project_description: str
    project_status: str
    # For usage recording
    used: bool

class MemoryDiff(TypedDict, total=False):
    summary_text: str  # Legacy field
    category_summaries: CategorySummaries  # New: partial updates to category summaries
    operations: List[MemoryOperation]
    # Top-level field updates
    new_data: Dict[str, Any]


# Standard categories for memory organization
MEMORY_CATEGORIES = [
    "profile",       # 基本情報（年齢、職業、居住地など）
    "preference",    # 好み（食べ物、趣味など）
    "health",        # 健康情報
    "work",          # 仕事・学業
    "hobby",         # 趣味
    "relationship",  # 人間関係
    "life",          # 生活習慣・エリア
    "travel",        # 旅行
    "food",          # 食事
    "general",       # その他
]

_memory_llm_instance = None
_memory_llm_lock = threading.Lock()
_memory_llm_signature: tuple[str, str, str, str] | None = None


def _normalise_history(conversation: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Return a sanitised list of {role, content} lines for prompts."""

    cleaned: List[Dict[str, str]] = []
    for entry in conversation:
        role = entry.get("role") if isinstance(entry, dict) else None
        content = entry.get("content") if isinstance(entry, dict) else None
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        trimmed = content.strip()
        if not trimmed:
            continue
        cleaned.append({"role": role.strip(), "content": trimmed})
    return cleaned


def _extract_text(content: Any) -> str:
    """Normalise LangChain response content to plain text."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    pieces.append(text)
        return "".join(pieces)
    if isinstance(content, dict) and isinstance(content.get("content"), str):
        return content["content"]
    return str(content)


def _extract_json_payload(response_text: str) -> str:
    """Pull the JSON object out of a response, tolerating code fences."""

    match = re.search(r"```(?:json)?\\s*(.*?)\\s*```", response_text, re.DOTALL)
    if match:
        return match.group(1)
    return response_text


def _coerce_memory_diff(response_text: str) -> MemoryDiff:
    """Best-effort coercion of the LLM output into MemoryDiff."""

    json_str = _extract_json_payload(response_text)
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        logging.warning("Memory consolidation returned non-JSON; wrapping raw text.")
        return {"category_summaries": {"general": response_text}, "operations": []}

    if not isinstance(parsed, dict):
        summary_text = (
            parsed if isinstance(parsed, (str, int, float, bool)) else json.dumps(parsed, ensure_ascii=False)
        )
        return {"category_summaries": {"general": str(summary_text)}, "operations": []}

    category_summaries = parsed.get("category_summaries")
    if not isinstance(category_summaries, dict):
        category_summaries = {}

    summary_text = parsed.get("summary_text")
    if isinstance(summary_text, str) and summary_text.strip() and not category_summaries:
        category_summaries["general"] = summary_text.strip()

    operations = parsed.get("operations")
    if not isinstance(operations, list):
        operations = []

    new_data = parsed.get("new_data")
    if not isinstance(new_data, dict):
        new_data = {}

    return {
        "summary_text": parsed.get("summary_text") if isinstance(parsed.get("summary_text"), str) else None,  # type: ignore[dict-item]
        "category_summaries": category_summaries,
        "operations": operations,
        "new_data": new_data,
    }


def get_memory_llm():
    """Initialise or reuse an LLM client dedicated to memory consolidation."""

    global _memory_llm_instance, _memory_llm_signature

    with _memory_llm_lock:
        try:
            config = resolve_llm_config("memory")
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to resolve memory LLM config: %s", exc)
            _memory_llm_instance = None
            _memory_llm_signature = None
            return None

        signature = (
            str(config.get("provider") or ""),
            str(config.get("model") or ""),
            str(config.get("base_url") or ""),
            hashlib.sha256(str(config.get("api_key") or "").encode("utf-8")).hexdigest()[:12],
        )

        if _memory_llm_instance is not None and _memory_llm_signature == signature:
            return _memory_llm_instance

        try:
            provider = config.get("provider", "openai")
            api_key = config.get("api_key")
            model_name = config.get("model")
            base_url = config.get("base_url") or None
            temperature = 0.15

            if provider == "gemini":
                client = ChatGoogleGenerativeAI(
                    model=model_name,
                    temperature=temperature,
                    google_api_key=api_key,
                )
            elif provider == "claude":
                client = ChatAnthropic(
                    model=model_name,
                    temperature=temperature,
                    api_key=api_key,
                    base_url=base_url,
                )
            else:
                client = ChatOpenAI(
                    model=model_name,
                    temperature=temperature,
                    api_key=api_key,
                    base_url=base_url,
                )

            _memory_llm_instance = client
            _memory_llm_signature = signature
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to initialise memory LLM: %s", exc)
            _memory_llm_instance = None
            _memory_llm_signature = None

    return _memory_llm_instance


def _build_consolidation_prompt(
    memory_kind: Literal["short", "long"],
    current_memory: Dict[str, Any],
    recent_conversation: List[Dict[str, str]],
) -> str:
    """Construct a constrained prompt that asks the LLM for a MemoryDiff JSON."""

    datetime_line = _current_datetime_line()
    memory_json = json.dumps(current_memory, ensure_ascii=False, indent=2)

    conversation_lines = []
    for idx, item in enumerate(recent_conversation, start=1):
        role = item.get("role")
        content = item.get("content")
        if isinstance(role, str) and isinstance(content, str):
            conversation_lines.append(f"{idx}. {role}: {content}")
    conversation_text = "\n".join(conversation_lines) if conversation_lines else "会話ログはありません。"

    if memory_kind == "short":
        new_data_block = """
#### 2. 短期記憶 new_data のガイド
- `active_task` (Dict): task_id, goal, status, started_at。
- `pending_questions` (List[str]): ユーザーへの未回答の確認事項。
- `recent_entities` (List[Dict]): 直近で出た固有名詞（name, type, context, when）。
- `emotional_context` (str): 現在のムードや緊急度（例: normal, urgent, happy）。
- `expires_at` (str): コンテキストの失効予定（ISO 8601）。
"""
    else:
        new_data_block = """
#### 2. 長期記憶 new_data のガイド
- `user_profile` (Dict): name, timezone, language, occupation, birthday など永続情報。
- `preferences` (Dict): communication_style, response_length, formality_level など。
- `recurring_patterns` (List[Dict]): 繰り返しの行動・習慣。
- `learned_corrections` (List[Dict]): ユーザーが訂正した内容（original, corrected, learned_at）。
- `relationship_graph` (List[Dict]): 人物と関係性。
- `topics_of_interest` (List[str]): 関心の強いトピック。
- `do_not_mention` (List[str]): 触れない方がよい話題。
"""

    return textwrap.dedent(
        f"""
        現在の日時: {datetime_line}

        あなたはユーザーの記憶を統合するエージェントです。以下の入力を基に、MemoryDiff 形式の JSON **のみ** を返してください。

        ### 直近の会話ログ
        {conversation_text}

        ### 現在の記憶データ
        ```json
        {memory_json}
        ```

        ### 指示
        1. 会話全体を反映して、各カテゴリの要約 (category_summaries) を更新してください。
           - 対応カテゴリ: profile, preference, health, work, hobby, relationship, life, travel, food, general
        2. ユーザーの新しい事実（趣味、計画、ガジェット、住所変更など）があれば `set_slot` オペレーションとして抽出してください。
           - 既存スロットと重複しないか確認し、あれば同じ slot_id を再利用してください。
           - slot_id は snake_case で、意味が分かる短い名前にしてください。
{new_data_block.strip()}
        3. 必要に応じて `add_episode`, `update_project`, `record_usage` も利用できます。
        4. 出力スキーマは MemoryDiff TypedDict と一致させてください:
           {{
             "category_summaries": {{}},
             "operations": [
               {{"op": "set_slot", "slot_id": "...", "value": "...", "category": "hobby", "reason": "会話から推定"}}
             ],
             "new_data": {{}}
           }}
        5. Markdown や文章の説明は書かず、**有効な JSON オブジェクトのみ** を返してください。
        """
    ).strip()



class MemoryManager:
    """Manages reading, writing, and updating structured memory files."""

    VERSION = 6  # Bumped for decay support
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
        
        if "summary_text" not in data:
            data["summary_text"] = ""
        
        if "last_decay_processed" not in data:
            data["last_decay_processed"] = datetime.now().isoformat()

        # Migration: add category_summaries if missing (BEFORE default initialization)
        if "category_summaries" not in data:
            data["category_summaries"] = {}
            if data.get("summary_text"):
                data["category_summaries"]["general"] = data["summary_text"]

        # Ensure list fields are present
        for list_field in ["slots", "important_changes", "pending_questions", "recent_entities", 
                          "recurring_patterns", "learned_corrections", "relationship_graph", 
                          "topics_of_interest", "do_not_mention", "episodic_memory"]:
            if list_field not in data or data[list_field] is None:
                data[list_field] = []
        
        # Ensure dict fields are present
        for dict_field in ["category_summaries", "active_task", "user_profile", "preferences", "projects"]:
            if dict_field not in data or data[dict_field] is None:
                data[dict_field] = {}

        # Normalize deprecated categories (e.g., schedule/plan -> general)
        schedule_summary = data["category_summaries"].pop("schedule", None)
        if schedule_summary:
            existing_general = data["category_summaries"].get("general")
            merged = f"{existing_general} {schedule_summary}".strip() if existing_general else schedule_summary
            data["category_summaries"]["general"] = merged

        plan_summary = data["category_summaries"].pop("plan", None)
        if plan_summary:
            existing_general = data["category_summaries"].get("general")
            merged = f"{existing_general} {plan_summary}".strip() if existing_general else plan_summary
            data["category_summaries"]["general"] = merged

        # Remove task-tracking fields and artifacts
        self._purge_tasks(data)

        # Backfill new slot fields if missing
        for slot in data.get("slots", []):
            slot["category"] = self._normalize_category(slot.get("category", ""))
            if "source" not in slot: slot["source"] = "unknown"
            if "verified" not in slot: slot["verified"] = False
            if "access_count" not in slot: slot["access_count"] = 0
            if "last_accessed" not in slot: slot["last_accessed"] = slot.get("last_updated", "")
            if "priority" not in slot: slot["priority"] = "medium"
            if "score" not in slot: slot["score"] = 0

        # Normalize categories within project memories as well
        for project in data.get("projects", {}).values():
            for slot in project.get("semantic_memory", []):
                slot["category"] = self._normalize_category(slot.get("category", ""))

        # Normalize operation categories
        for op in data.get("operations", []):
            if isinstance(op, dict) and "category" in op:
                op["category"] = self._normalize_category(op.get("category", ""))

        # Clean existing summaries to keep them human-readable
        summary = data.get("summary_text")
        if isinstance(summary, str):
            data["summary_text"] = self._clean_human_summary(summary, fallback="")

        summaries = data.get("category_summaries", {})
        if isinstance(summaries, dict):
            for cat, text in list(summaries.items()):
                if not isinstance(text, str):
                    summaries.pop(cat, None)
                    continue
                cleaned = self._clean_human_summary(text, fallback="")
                if cleaned:
                    summaries[cat] = cleaned
                else:
                    summaries.pop(cat, None)
            
        return cast(MemoryStore, data)

    def save_memory(self, memory: MemoryStore) -> None:
        """Save memory to file."""
        memory["last_updated"] = datetime.now().isoformat()
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(memory, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logging.error(f"Failed to save memory to {self.file_path}: {e}")

    def consolidate_memory(
        self,
        recent_conversation: List[Dict[str, Any]],
        memory_kind: Literal["short", "long"] = "long",
        llm: Any | None = None,
    ) -> MemoryStore:
        """Use an LLM to produce and apply a MemoryDiff based on recent conversation."""

        normalized_history = _normalise_history(recent_conversation)
        if not normalized_history:
            logging.info("Memory consolidation skipped: no recent conversation provided.")
            return self.load_memory()

        client = llm or get_memory_llm()
        if client is None:
            logging.warning("Memory consolidation skipped: memory LLM is not configured.")
            return self.load_memory()

        # Apply decay for long-term memory before reasoning so confidence values stay fresh.
        if memory_kind != "short":
            try:
                self.apply_decay()
            except Exception as exc:  # noqa: BLE001
                logging.debug("Memory decay skipped during consolidation: %s", exc)

        current_memory = self.load_memory()
        prompt = _build_consolidation_prompt(memory_kind, current_memory, normalized_history)

        try:
            response = client.invoke(
                [
                    SystemMessage(content=prompt),
                    HumanMessage(content="上記の指示に従い、MemoryDiff JSON だけを返してください。"),
                ]
            )
            response_text = _extract_text(response.content).strip()
            diff = _coerce_memory_diff(response_text)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Memory consolidation failed to parse LLM output: %s", exc)
            return current_memory

        # Guardrail: keep only recognised new_data fields per memory kind
        if "new_data" in diff and isinstance(diff["new_data"], dict):
            allowed_fields = {
                "short": {"active_task", "pending_questions", "recent_entities", "emotional_context", "expires_at"},
                "long": {
                    "user_profile",
                    "preferences",
                    "recurring_patterns",
                    "learned_corrections",
                    "relationship_graph",
                    "topics_of_interest",
                    "do_not_mention",
                },
            }.get(memory_kind, set())
            diff["new_data"] = {k: v for k, v in diff["new_data"].items() if k in allowed_fields}

        # Guardrail: drop category summaries outside the known set
        if "category_summaries" in diff and isinstance(diff["category_summaries"], dict):
            diff["category_summaries"] = {
                self._normalize_category(cat): val
                for cat, val in diff["category_summaries"].items()
                if isinstance(val, str) and self._normalize_category(cat) in MEMORY_CATEGORIES
            }

        return self.apply_diff(diff)

    @staticmethod
    def _deep_merge(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        """Recursive merge for dictionaries."""
        for key, value in source.items():
            if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                MemoryManager._deep_merge(target[key], value)
            else:
                target[key] = value
        return target

    def apply_diff(self, diff: MemoryDiff) -> MemoryStore:
        """Apply a semantic diff (operations) to the current memory."""
        memory = self.load_memory()
        
        # 1. Update top-level data fields (new behavior)
        new_data = diff.get("new_data")
        if isinstance(new_data, dict):
            for key, value in new_data.items():
                if value is not None:
                    # Deep merge if both are dicts
                    if key in memory and isinstance(memory[key], dict) and isinstance(value, dict):
                        self._deep_merge(memory[key], value)
                    else:
                        memory[key] = value

        # 2. Update category summaries
        new_category_summaries = diff.get("category_summaries")
        if isinstance(new_category_summaries, dict):
            for category, summary in new_category_summaries.items():
                if isinstance(summary, str):
                    normalized_category = self._normalize_category(category)
                    fallback = memory["category_summaries"].get(normalized_category, "")
                    cleaned = self._clean_human_summary(summary, fallback=fallback)
                    if cleaned:
                        memory["category_summaries"][normalized_category] = cleaned

        # 3. Update legacy summary_text
        new_summary = diff.get("summary_text")
        if new_summary and isinstance(new_summary, str):
            cleaned_summary = self._clean_human_summary(new_summary, fallback=memory.get("summary_text", ""))
            if cleaned_summary:
                memory["summary_text"] = cleaned_summary
            if not new_category_summaries:
                memory["category_summaries"]["general"] = memory["summary_text"]

        # 4. Apply operations
        operations = diff.get("operations") or []
        for op in operations:
            op_type = op.get("op")
            if op_type == "set_slot":
                self._apply_set_slot(memory, op)
            elif op_type == "set_category_summary":
                self._apply_set_category_summary(memory, op)
            elif op_type == "add_episode":
                self._apply_add_episode(memory, op)
            elif op_type == "update_project":
                self._apply_update_project(memory, op)
            elif op_type == "record_usage":
                self._apply_record_usage(memory, op)
            else:
                logging.warning(f"Unknown memory operation: {op_type}")

        # 5. Regenerate summary_text from category summaries
        self._sync_summary_text(memory)

        # 6. Final guard: drop any task artifacts before persisting
        self._purge_tasks(memory)

        self.save_memory(memory)
        return memory
    
    def apply_decay(self) -> MemoryStore:
        """
        Apply time-based decay to memory slots based on Ebbinghaus forgetting curve principles.
        Should be called periodically (e.g., daily).
        """
        memory = self.load_memory()
        
        current_time = datetime.now()
        last_processed_str = memory.get("last_decay_processed")
        
        # Only process if at least 24 hours have passed since last decay, or if never processed
        if last_processed_str:
            last_processed = datetime.fromisoformat(last_processed_str)
            if (current_time - last_processed).total_seconds() < 86400:
                return memory

        # Process decay for Global Slots
        self._decay_slots(memory["slots"], current_time)
        
        # Process decay for Project Slots
        for project in memory.get("projects", {}).values():
            self._decay_slots(project.get("semantic_memory", []), current_time)

        memory["last_decay_processed"] = current_time.isoformat()
        self.save_memory(memory)
        logging.info("Memory decay processing completed.")
        return memory

    def _decay_slots(self, slots: List[MemorySlot], current_time: datetime) -> None:
        """Apply decay logic to a list of slots."""
        import math
        
        for slot in slots:
            last_accessed_str = slot.get("last_accessed") or slot.get("last_updated")
            if not last_accessed_str:
                continue
            
            try:
                last_accessed = datetime.fromisoformat(last_accessed_str)
            except ValueError:
                continue
            
            days_inactive = (current_time - last_accessed).days
            
            if days_inactive < 1:
                continue
                
            # Ebbinghaus-inspired logic:
            # Strength (S) increases with repetitions (access_count).
            # Retention (R) = e^(-t/S)
            
            access_count = slot.get("access_count", 1)
            # Logarithmic stability growth: 1 access -> S=1, 10 accesses -> S~3.3, 100 accesses -> S~5.6
            stability = math.log(access_count + 1) + 1.0
            
            # Calculate decay factor based on inactive time and stability
            # If stability is high, decay is slow.
            # days_inactive is 't'.
            
            # Simple linear decay of confidence tailored by stability
            # We don't want to completely wipe memory, just reduce confidence/score.
            
            decay_factor = 0.05 * (days_inactive / stability)
            
            # 1. Decay Confidence
            current_confidence = slot.get("confidence", 1.0)
            new_confidence = max(0.1, current_confidence - (current_confidence * decay_factor))
            slot["confidence"] = round(new_confidence, 3)
            
            # 2. Decay Score (Priority)
            # If not accessed for a long time, score drops.
            # 1 point drop per (stability * 10) days?
            # Let's simplify: drop score if inactive for significant time relative to stability.
            if days_inactive > (stability * 7): # e.g., if stability=1 (new), >7 days inactive drops score
                slot["score"] = slot.get("score", 0) - 1
                
            # 3. Adjust Priority Label
            if slot.get("score", 0) < -5 or slot.get("confidence", 0) < 0.2:
                slot["priority"] = "low"
            elif slot.get("score", 0) > 5 and slot.get("confidence", 0) > 0.8:
                slot["priority"] = "high"

    def _apply_set_category_summary(self, memory: MemoryStore, op: MemoryOperation) -> None:
        """Apply a category summary update operation."""
        category = op.get("category")
        value = op.get("value")
        if category and isinstance(value, str):
            fallback = memory["category_summaries"].get(category, "")
            cleaned = self._clean_human_summary(value, fallback=fallback)
            if cleaned:
                memory["category_summaries"][category] = cleaned
    
    def _apply_add_episode(self, memory: MemoryStore, op: MemoryOperation) -> None:
        """Add an episodic memory item to global or project scope."""
        content = op.get("content")
        if not content:
            return
            
        project_id = op.get("project_id")
        current_time = datetime.now().isoformat()
        
        episode: EpisodicItem = {
            "id": f"ep_{int(datetime.now().timestamp() * 1000)}",
            "timestamp": current_time,
            "content": content,
            "type": op.get("reason") or "event",  # Re-using 'reason' field as type if convenient, or just generic
            "importance": op.get("importance") or 0.5,
            "tags": op.get("tags") or [],
            "related_slots": []
        }
        
        if project_id and project_id in memory["projects"]:
            memory["projects"][project_id]["episodic_memory"].append(episode)
            memory["projects"][project_id]["last_updated"] = current_time
        else:
            memory["episodic_memory"].append(episode)
            # Cap global episodic memory
            if len(memory["episodic_memory"]) > 100:
                memory["episodic_memory"] = memory["episodic_memory"][-100:]

    def _apply_update_project(self, memory: MemoryStore, op: MemoryOperation) -> None:
        """Create or update a project definition."""
        project_id = op.get("project_id")
        if not project_id:
            return

        current_time = datetime.now().isoformat()
        
        if project_id not in memory["projects"]:
            # Create new
            memory["projects"][project_id] = {
                "id": project_id,
                "name": op.get("project_name") or project_id,
                "description": op.get("project_description") or "",
                "created_at": current_time,
                "last_updated": current_time,
                "status": op.get("project_status") or "active",
                "semantic_memory": [],
                "episodic_memory": []
            }
        else:
            # Update existing
            proj = memory["projects"][project_id]
            proj["last_updated"] = current_time
            if op.get("project_name"): proj["name"] = op["project_name"]
            if op.get("project_description"): proj["description"] = op["project_description"]
            if op.get("project_status"): proj["status"] = op["project_status"]

    def _apply_record_usage(self, memory: MemoryStore, op: MemoryOperation) -> None:
        """Update usage statistics and score for a slot."""
        slot_id = op.get("slot_id")
        used = op.get("used", False)
        project_id = op.get("project_id")

        if not slot_id:
            return

        # Determine target slots list
        target_slots_list: List[MemorySlot] = memory["slots"]
        if project_id and project_id in memory["projects"]:
            target_slots_list = memory["projects"][project_id]["semantic_memory"]

        target_slot: Optional[MemorySlot] = None
        for slot in target_slots_list:
            if slot["id"] == slot_id:
                target_slot = slot
                break
        
        if target_slot:
            current_time = datetime.now().isoformat()
            target_slot["last_accessed"] = current_time
            
            if used:
                target_slot["access_count"] = target_slot.get("access_count", 0) + 1
                target_slot["score"] = target_slot.get("score", 0) + 1
                # Increase confidence slightly if verified by usage?
                # target_slot["confidence"] = min(1.0, target_slot.get("confidence", 0.5) + 0.05)
            else:
                # Penalize score for unused/rejected info
                target_slot["score"] = target_slot.get("score", 0) - 1

    def _sync_summary_text(self, memory: MemoryStore) -> None:
        """Regenerate summary_text from category_summaries for legacy compatibility."""
        if not memory.get("category_summaries"):
            return
        
        parts = []
        for category in MEMORY_CATEGORIES:
            summary = memory["category_summaries"].get(category)
            if summary:
                parts.append(summary)
        
        if parts:
            memory["summary_text"] = " ".join(parts)

    def _purge_tasks(self, memory: Dict[str, Any]) -> None:
        """Strip ongoing/completed task tracking from memory store."""

        task_keys = ["active_task", "completed_tasks", "tasks", "task_history"]
        for key in task_keys:
            if key in memory:
                memory[key] = {} if isinstance(memory[key], dict) else []

        # Remove task-like slots (id starting with task_)
        slots = memory.get("slots", [])
        if isinstance(slots, list):
            memory["slots"] = [s for s in slots if not (isinstance(s, dict) and str(s.get("id", "")).startswith("task_"))]

        # Remove task-like projects
        projects = memory.get("projects", {})
        if isinstance(projects, dict):
            for pid in list(projects.keys()):
                if str(pid).startswith("task_"):
                    projects.pop(pid, None)

        # Ensure category summaries don't include task-only categories (not used now)
        for legacy_key in ["task", "tasks"]:
            memory.get("category_summaries", {}).pop(legacy_key, None)

    def _clean_human_summary(self, text: str, fallback: str | None = None) -> str:
        """Keep summaries human-friendly: strip code fences/JSON-like payloads."""

        if not isinstance(text, str):
            return fallback or ""

        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json|text)?\\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\\s*```$", "", cleaned)
        cleaned = re.sub(r"\\s+", " ", cleaned).strip()

        if not cleaned:
            return fallback or ""

        # Reject obvious JSON/object payloads
        looks_like_json = cleaned.startswith("{") or cleaned.startswith("[") or cleaned.lower().startswith("json")
        if looks_like_json:
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, str):
                    cleaned = parsed.strip()
                else:
                    return fallback or ""
            except Exception:
                return fallback or ""

        return cleaned

    def _create_empty_memory(self) -> MemoryStore:
        return {
            "type": self.TYPE,
            "version": self.VERSION,
            "last_updated": datetime.now().isoformat(),
            "summary_text": "",
            "category_summaries": {},
            "slots": [],
            "important_changes": [],
            # Init empty containers
            "active_task": {},
            "pending_questions": [],
            "recent_entities": [],
            "user_profile": {},
            "preferences": {},
            "recurring_patterns": [],
            "learned_corrections": [],
            "relationship_graph": [],
            "topics_of_interest": [],
            "do_not_mention": [],
            "created_at": datetime.now().isoformat(),
            "source": "system_init",
            "expires_at": None,
            "emotional_context": "normal",
            # New containers
            "episodic_memory": [],
            "projects": {},
            "last_decay_processed": datetime.now().isoformat()
        }

    def _migrate_legacy_memory(self, text: str) -> MemoryStore:
        """Convert old text-based memory to new structure."""
        base = self._create_empty_memory()
        base.update({
            "summary_text": text,
            "category_summaries": {"general": text} if text else {},
        })
        return base

    def get_formatted_memory(self) -> str:
        """Get memory formatted for prompt injection, organized by category and structured data."""
        memory = self.load_memory()
        sections = []

        # 1. New Structured Data Fields
        structured_fields = {
            "【ユーザープロファイル】": memory.get("user_profile"),
            "【好み・設定】": memory.get("preferences"),
            "【現在のアクティブタスク】": memory.get("active_task"),
            "【未解決の質問】": memory.get("pending_questions"),
            "【直近のエンティティ】": memory.get("recent_entities"),
            "【感情コンテキスト】": memory.get("emotional_context"),
            "【繰り返しパターン】": memory.get("recurring_patterns"),
            "【学習済み訂正事項】": memory.get("learned_corrections"),
            "【人間関係グラフ】": memory.get("relationship_graph"),
            "【関心トピック】": memory.get("topics_of_interest"),
            "【避けるべき話題】": memory.get("do_not_mention"),
        }

        for label, data in structured_fields.items():
            if not data:
                continue
            # Pretty print JSON or simple string
            if isinstance(data, (dict, list)):
                if len(data) == 0: continue
                value_str = json.dumps(data, ensure_ascii=False, indent=2)
            else:
                value_str = str(data)
            sections.append(f"{label}\n{value_str}")

        # 2. Global Episodic Memory (Recent 5)
        episodic_memory = memory.get("episodic_memory", [])
        if episodic_memory:
            # Sort by timestamp just in case
            sorted_episodes = sorted(episodic_memory, key=lambda x: x.get("timestamp", ""), reverse=False)
            recent_episodes = sorted_episodes[-5:]
            ep_lines = []
            for ep in recent_episodes:
                ts = ep.get("timestamp", "").replace("T", " ")[:16] # YYYY-MM-DD HH:MM
                content = ep.get("content", "")
                ep_lines.append(f"- [{ts}] {content}")
            if ep_lines:
                sections.append("【最近の出来事(エピソード・時系列順)】\n" + "\n".join(ep_lines))

        # 3. Project Summaries
        projects = memory.get("projects", {})
        if projects:
            proj_lines = []
            for pid, proj in projects.items():
                status = proj.get("status", "active")
                if status != "active": continue
                desc = proj.get("description") or "No description"
                last_up = proj.get("last_updated", "").split("T")[0]
                proj_lines.append(f"- {proj.get('name')} (ID: {pid}): {desc} [Last: {last_up}]")
            if proj_lines:
                sections.append("【アクティブなプロジェクト】\n" + "\n".join(proj_lines))

        # 4. Global Category Summaries & Slots
        
        # Group slots by category
        slots_by_category: Dict[str, List[MemorySlot]] = {}
        for slot in memory.get("slots", []):
            category = slot.get("category", "general")
            if category not in slots_by_category:
                slots_by_category[category] = []
            slots_by_category[category].append(slot)
        
        for category in MEMORY_CATEGORIES:
            category_parts = []
            
            # Add category summary if exists
            category_summary = memory.get("category_summaries", {}).get(category)
            if category_summary:
                category_parts.append(f"概要: {category_summary}")
            
            # Add slots for this category
            category_slots = slots_by_category.get(category, [])
            for slot in category_slots:
                label = slot.get("label", slot.get("id", ""))
                value = slot.get("current_value")
                last_updated = slot.get("last_updated", "").split("T")[0]
                
                # Format with extra metadata if relevant
                extras = []
                if slot.get("verified"): extras.append("確認済")
                if slot.get("priority") == "high": extras.append("重要")
                
                # Add timestamp to extra info for time awareness
                extras.append(f"更新:{last_updated}")
                
                extra_str = f" ({', '.join(extras)})"

                if value is not None:
                    if isinstance(value, (dict, list)):
                        value_str = json.dumps(value, ensure_ascii=False)
                    else:
                        value_str = str(value)
                    category_parts.append(f"- {label}{extra_str}: {value_str}")
            
            if category_parts:
                category_label = self._get_category_label(category)
                section = f"【{category_label}】\n" + "\n".join(category_parts)
                sections.append(section)
        
        return "\n\n".join(sections) if sections else ""
    
    def _get_category_label(self, category: str) -> str:
        """Get Japanese label for category."""
        labels = {
            "profile": "基本情報",
            "preference": "好み・嗜好",
            "health": "健康",
            "work": "仕事・学業",
            "hobby": "趣味",
            "relationship": "人間関係",
            "life": "生活",
            "travel": "旅行",
            "food": "食事",
            "general": "その他",
        }
        return labels.get(category, category)

    def _normalize_category(self, category: str) -> str:
        """Normalize legacy category names.

        Currently folds the deprecated "schedule" and "plan" categories into
        "general" and defaults empty values to "general".
        """

        normalized = (category or "").strip().lower()
        if normalized in {"schedule", "plan"}:
            return "general"
        if not normalized:
            return "general"
        return normalized

    def _normalize_id(self, slot_id: str) -> str:
        """Normalize slot ID to snake_case and lower case to reduce ambiguity."""
        # Convert to lower case
        s = slot_id.lower()
        # Replace non-alphanumeric chars (except underscore) with underscore
        s = re.sub(r'[^a-z0-9_]+', '_', s)
        # Collapse multiple underscores
        s = re.sub(r'_+', '_', s)
        # Strip leading/trailing underscores
        s = s.strip('_')
        return s

    def _find_similar_slot(self, slot_id: str, label: str, slots: List[MemorySlot]) -> Optional[str]:
        """Find a similar existing slot ID using fuzzy matching."""
        # Check for ID similarity
        existing_ids = [slot["id"] for slot in slots]
        # Use cutoff 0.80 for high similarity requirement
        matches = difflib.get_close_matches(slot_id, existing_ids, n=1, cutoff=0.80)
        if matches:
            return matches[0]
            
        # Check for label similarity if label provided
        # This is riskier, so require higher cutoff or exact match logic if needed.
        # For now, let's stick to ID similarity primarily.
        
        return None

    def _apply_set_slot(self, memory: MemoryStore, op: MemoryOperation) -> None:
        raw_slot_id = op.get("slot_id")
        if not raw_slot_id:
            return
            
        # 1. Normalize ID
        slot_id = self._normalize_id(raw_slot_id)
            
        new_value = op.get("value")
        log_change = op.get("log_change", False)
        reason = op.get("reason", "")
        project_id = op.get("project_id")
        
        # New metadata fields
        source = op.get("source") or "inferred"
        verified = op.get("verified", False)
        priority = op.get("priority") or "medium"
        current_time = datetime.now().isoformat()
        
        # Determine target slots list
        target_slots_list: List[MemorySlot] = memory["slots"] # Default to global
        
        if project_id:
            if project_id not in memory["projects"]:
                 # Auto-create project if missing
                 memory["projects"][project_id] = {
                    "id": project_id,
                    "name": project_id,
                    "description": "",
                    "created_at": current_time,
                    "last_updated": current_time,
                    "status": "active",
                    "semantic_memory": [],
                    "episodic_memory": []
                }
            target_slots_list = memory["projects"][project_id]["semantic_memory"]
        
        # Find existing slot (Exact Match)
        target_slot: Optional[MemorySlot] = None
        for slot in target_slots_list:
            if slot["id"] == slot_id:
                target_slot = slot
                break
        
        # If not found, try fuzzy matching / duplicate detection
        if not target_slot:
            similar_id = self._find_similar_slot(slot_id, op.get("label", ""), target_slots_list)
            if similar_id:
                logging.info(f"Fuzzy match found for slot '{slot_id}' -> '{similar_id}'. Merging.")
                slot_id = similar_id
                for slot in target_slots_list:
                    if slot["id"] == slot_id:
                        target_slot = slot
                        break
        
        if target_slot:
            # Update existing
            old_value = target_slot["current_value"]
            
            # Update metadata
            target_slot["last_accessed"] = current_time
            target_slot["access_count"] = target_slot.get("access_count", 0) + 1
            if op.get("priority"): target_slot["priority"] = priority
            if op.get("verified") is not None: target_slot["verified"] = verified

            if old_value == new_value:
                return

            target_slot["current_value"] = new_value
            target_slot["last_updated"] = current_time
            if op.get("confidence"):
                 target_slot["confidence"] = op["confidence"]
            if op.get("source"):
                target_slot["source"] = source

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

                # Add to important_changes (only for global slots currently, or project specific log?)
                # For now, we log important changes to global list with project note if applicable
                note = reason
                if project_id:
                    note = f"[Project: {project_id}] {reason}"

                change_entry: ImportantChange = {
                    "id": len(memory["important_changes"]) + 1,
                    "slot_id": slot_id,
                    "changed_at": current_time,
                    "label": target_slot["label"],
                    "from_value": old_value,
                    "to_value": new_value,
                    "note": note
                }
                memory["important_changes"].append(change_entry)
                if len(memory["important_changes"]) > 50:
                     memory["important_changes"] = memory["important_changes"][-50:]

        else:
            # Create new slot
            new_slot: MemorySlot = {
                "id": slot_id,
                "label": op.get("label") or slot_id,
                "category": op.get("category") or "general",
                "current_value": new_value,
                "confidence": op.get("confidence") or 1.0,
                "last_updated": current_time,
                "history": [],
                # New fields
                "source": source,
                "verified": verified,
                "access_count": 1,
                "last_accessed": current_time,
                "priority": priority
            }
            
            target_slots_list.append(new_slot)
