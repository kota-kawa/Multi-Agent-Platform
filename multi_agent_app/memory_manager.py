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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TypedDict, cast, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic

from .settings import resolve_llm_config, load_memory_settings, DEFAULT_MEMORY_SETTINGS
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
    summary_log: List[str]  # Journal-style append-only summaries
    category_summaries: CategorySummaries  # New: per-category summaries
    category_titles: Dict[str, str]
    slots: List[MemorySlot]  # Global Semantic Memory
    important_changes: List[ImportantChange]
    
    # New: Global Episodic Memory & Projects
    episodic_memory: List[EpisodicItem]
    projects: Dict[str, ProjectMemory]

    # Maintenance fields
    last_decay_processed: Optional[str]
    last_consolidated_to_long: Optional[str]

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


class ManualEntry(TypedDict, total=False):
    category: str
    key: Optional[str]
    value: str


class ManualStructuredPayload(TypedDict):
    entries: List[ManualEntry]
    profile: Dict[str, Any]
    preferences: Dict[str, List[str]]


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

_CATEGORY_FRIENDLY_NAMES = {
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

_PROFILE_FIELD_ALIASES = {
    "name": ["名前", "氏名", "name", "my name is", "full name", "call me"],
    "nickname": ["ニックネーム", "呼び名", "nickname", "handle", "alias"],
    "occupation": ["職業", "仕事", "job", "role", "職種", "profession", "働いて", "勤務"],
    "company": ["会社", "勤務先", "所属", "company", "employer"],
    "location": ["居住地", "住まい", "在住", "住んで", "住む", "location", "city", "country", "住", "拠点", "based"],
    "age": ["年齢", "歳", "age"],
    "birthday": ["誕生日", "生年月日", "birthday", "birthdate", "dob"],
    "pronouns": ["代名詞", "pronoun", "呼び方"],
    "hometown": ["出身", "hometown", "故郷"],
}

_PROFILE_FIELD_LABELS = {
    "name": "名前",
    "nickname": "ニックネーム",
    "occupation": "職業",
    "company": "勤務先",
    "location": "居住地",
    "age": "年齢",
    "birthday": "誕生日",
    "pronouns": "代名詞",
    "hometown": "出身地",
}

_PREFERENCE_POSITIVE_KEYWORDS = ["好き", "love", "enjoy", "好む", "ハマって", "推し", "お気に入り"]
_PREFERENCE_NEGATIVE_KEYWORDS = ["嫌い", "苦手", "避け", "アレルギー", "NG", "no ", "控え", "avoid"]

_MANUAL_SLOT_SOURCE = "manual_editor"
_MANUAL_BULLET_STRIP = re.compile(r"^(?:[\s\-\*\u30fb\u2022\u25cf\u25cb\u25a0\u25a1\u25b6\u25ba>\u2023\u2219]+|\d+[\.\)]\s*)")
_MANUAL_KEY_VALUE_PATTERNS = [
    re.compile(r"^\s*(?P<key>[^:：=]+?)\s*[:：=]\s*(?P<value>.+)$"),
    re.compile(r"^\s*(?P<key>.+?)\s*は\s*(?P<value>.+)$"),
]

_SHORT_TERM_PROMOTION_LIMIT = 5

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


def _strip_manual_marker(line: str) -> str:
    """Remove bullet markers or numbering prefixes from manual input lines."""

    if not isinstance(line, str):
        return ""
    cleaned = line.replace("\u3000", " ").strip()
    cleaned = _MANUAL_BULLET_STRIP.sub("", cleaned).strip()
    cleaned = cleaned.lstrip("・・").strip("-— ").strip()
    return cleaned


def _split_manual_text_block(text: str) -> List[str]:
    """Split multiline free-form text into atomic statements."""

    if not isinstance(text, str):
        return []
    if not text.strip():
        return []

    normalized = (
        text.replace("\r\n", "\n")
        .replace("・", "\n")
        .replace("•", "\n")
        .replace("●", "\n")
        .replace("■", "\n")
        .replace("▶", "\n")
    )

    chunks: List[str] = []
    for raw_line in normalized.split("\n"):
        stripped = _strip_manual_marker(raw_line)
        if stripped:
            chunks.append(stripped)
    return chunks


def _parse_manual_key_value_line(line: str) -> tuple[Optional[str], str]:
    """Detect simple key-value statements like '名前: 山田'."""

    for pattern in _MANUAL_KEY_VALUE_PATTERNS:
        match = pattern.match(line)
        if match:
            key = (match.group("key") or "").strip()
            value = (match.group("value") or "").strip()
            if key and value:
                return key, value
    return None, line.strip()


def _matches_alias(text: str, alias: str) -> bool:
    """Return True if alias-like token appears in the text."""

    if not text or not alias:
        return False
    lowered_text = text.casefold()
    lowered_alias = alias.casefold()
    return lowered_alias in lowered_text or alias in text


def _identify_profile_field(key: Optional[str], text: str) -> Optional[str]:
    """Infer which user_profile field a manual line should update."""

    candidates = []
    if key:
        candidates.append(key)
    if text:
        candidates.append(text)

    for candidate in candidates:
        for field, aliases in _PROFILE_FIELD_ALIASES.items():
            for alias in aliases:
                if _matches_alias(candidate, alias):
                    return field
    return None


def _clean_manual_value(value: str) -> str:
    """Trim sentences down to a stable slot value."""

    cleaned = value.strip()
    cleaned = cleaned.strip("。！？!?.,;；")
    return cleaned.strip()


def _classify_preference_from_text(category: str, text: str) -> Optional[str]:
    """Heuristically classify text as like/dislike entry."""

    haystack_lower = text.casefold()
    for keyword in _PREFERENCE_NEGATIVE_KEYWORDS:
        needle = keyword.casefold()
        if needle and needle in haystack_lower:
            return "dislikes"

    for keyword in _PREFERENCE_POSITIVE_KEYWORDS:
        needle = keyword.casefold()
        if needle and needle in haystack_lower:
            return "likes"

    if category == "preference":
        return "likes"
    return None


def _coerce_profile_value(field: str, value: str) -> Any:
    """Convert well-known profile fields into structured values."""

    if field == "age":
        match = re.search(r"\d{1,3}", value)
        if match:
            try:
                return int(match.group(0))
            except ValueError:
                return value.strip()
    return value.strip()
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
    short_snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    """Construct a constrained prompt that asks the LLM for a MemoryDiff JSON."""

    datetime_line = _current_datetime_line()

    def _conversation_block() -> str:
        lines = []
        for idx, item in enumerate(recent_conversation, start=1):
            role = item.get("role")
            content = item.get("content")
            if isinstance(role, str) and isinstance(content, str):
                lines.append(f"{idx}. {role}: {content}")
        return "\n".join(lines) if lines else "会話ログはありません。"

    # Keep the provided memory context lean to avoid overwrite bias.
    if memory_kind == "long" and short_snapshot:
        short_focus = {
            "active_task": short_snapshot.get("active_task"),
            "pending_questions": short_snapshot.get("pending_questions"),
            "recent_entities": short_snapshot.get("recent_entities"),
            "emotional_context": short_snapshot.get("emotional_context"),
            "episodic_memory": short_snapshot.get("episodic_memory", [])[-5:],
            "slot_index": [
                {"id": s.get("id"), "label": s.get("label"), "category": s.get("category")}
                for s in short_snapshot.get("slots", [])[:15]
            ],
        }
        memory_block = json.dumps(short_focus, ensure_ascii=False, indent=2)
    else:
        # Provide only a light index of existing slots to avoid "rewrite everything" behaviour.
        memory_block = json.dumps(
            {
                "slot_index": [
                    {"id": s.get("id"), "label": s.get("label"), "category": s.get("category")}
                    for s in current_memory.get("slots", [])[:25]
                ],
                "category_summaries": current_memory.get("category_summaries", {}),
            },
            ensure_ascii=False,
            indent=2,
        )

    conversation_text = _conversation_block()

    if memory_kind == "short":
        new_data_block = """
#### 2. 短期記憶 new_data の厳格ガイド（上書き禁止）
- 目的: 直近の意図・保留事項のバッファ。長期保存はしない。
- `pending_questions`, `recent_entities`: {"add":[...], "remove":[...]} で差分のみ提示。全量列挙しない。
- `active_task`: 進行中タスクだけを簡潔に更新（task_id, goal, status）。欠落フィールドは保持する前提で書く。
- `emotional_context`: 単語1つ（例: "urgent", "calm"）。文章禁止。
- `expires_at`: 必要時のみ ISO 8601 で追加。不要なら出力しない。
"""
        role_note = "短期記憶は作業メモリ。未指定項目はそのまま保持し、過去情報を削除しない。"
    else:
        new_data_block = """
#### 2. 長期記憶 new_data の厳格ガイド（永続・非破壊）
- 目的: 永続的な事実の蓄積。削除は利用者が明示した場合のみ。
- `user_profile`, `preferences`: 既存キーを残しつつ追加/更新。欠落キーを空にしない。
- リスト型 (`recurring_patterns`, `learned_corrections`, `relationship_graph`, `topics_of_interest`, `do_not_mention` など):
    - 追加: {"add":[...]} / 削除・訂正: {"remove":[...]} を明示。全量上書き禁止。
- テキストの書き直し禁止。構造化スロットへの追加を最優先。
"""
        role_note = "長期記憶は蓄積専用。提供していない情報を『不存在』とみなさず、未指定の事実は保持すること。"

    return textwrap.dedent(
        f"""
        現在の日時: {datetime_line}

        あなたはユーザーの記憶を統合するエージェントです。以下の入力を基に、MemoryDiff 形式の JSON **のみ** を返してください。

        ### 直近の会話ログ
        {conversation_text}

        ### 参照用スナップショット（読み取り専用・既存情報の一覧）
        ```json
        {memory_block}
        ```

        ### 指示
        - {role_note}
        - 追加・削除を明示し、**差分操作のみ** を提案すること。未指定の情報は保持される前提で書く。
        - リスト型は必ず add/remove 形式で部分更新。全量提示や置換は禁止。
        - slot は「1 スロット = 1 事実（Key/Value）」の粒度で snake_case id を使用。まとめ書きしない。
        - category_summaries は該当カテゴリのみ、1文≤30語で要点だけ。summary_text は書き直さず省略してよい。
        - 既知情報を消す・空文字にする・None を入れる行為は禁止（明示の remove を除く）。
        - 出力は有効な JSON オブジェクトのみ。説明文・Markdown・前置き・後置きは禁止。
        - 何も新規が無い場合は {{}} を返す（空オブジェクト）。

        ### 禁止事項
        - 既存スロットやリストを全量再生成して置換すること
        - 未指定フィールドを空にする／削除すること
        - 複数の事実を1つの長文スロットにまとめること

        ### 簡潔な検証チェックリスト（あなたが回答前に内部確認すること）
        1) 追加・削除は add/remove で書いたか？
        2) 未指定データを消していないか？
        3) JSON が単一オブジェクトであるか？（配列や文字列のみは不可）

{new_data_block.strip()}

        ### 出力スキーマ
        {{
          "category_summaries": {{}},
          "operations": [
            {{"op": "set_slot", "slot_id": "new_hobby", "value": "ロードバイク", "category": "hobby", "reason": "会話から判明"}}
          ],
          "new_data": {{
            "topics_of_interest": {{"add": ["Rust-lang"]}},
            "pending_questions": {{"add": ["次回の締切は？"], "remove": []}}
          }}
        }}
        - Markdown や文章の説明は不要。**有効な JSON オブジェクトのみ** を返してください。
        """
    ).strip()



class MemoryManager:
    """Manages reading, writing, and updating structured memory files."""

    VERSION = 7  # Bumped for non-destructive diff & summary log
    TYPE = "chat_memory"

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._is_short_term = os.path.basename(file_path) == "short_term_memory.json"

    def load_memory(self) -> MemoryStore:
        """Load memory from file, initializing or migrating if necessary."""
        if not os.path.exists(self.file_path):
            return self._finalize_loaded_memory(self._create_empty_memory())

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            logging.warning(f"Failed to load memory from {self.file_path}, resetting.")
            return self._finalize_loaded_memory(self._create_empty_memory())

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
                          "topics_of_interest", "do_not_mention", "episodic_memory", "summary_log"]:
            if list_field not in data or data[list_field] is None:
                data[list_field] = []
        
        # Ensure dict fields are present
        for dict_field in ["category_summaries", "category_titles", "active_task", "user_profile", "preferences", "projects"]:
            if dict_field not in data or data[dict_field] is None:
                data[dict_field] = {}

        if "last_consolidated_to_long" not in data:
            data["last_consolidated_to_long"] = None

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
            normalized_summaries: Dict[str, str] = {}
            for cat, text in list(summaries.items()):
                if not isinstance(text, str):
                    continue
                cleaned = self._clean_human_summary(text, fallback="")
                if not cleaned:
                    continue
                normalized = self._normalize_category(cat)
                if normalized in normalized_summaries:
                    normalized_summaries[normalized] = f"{normalized_summaries[normalized]}\n{cleaned}".strip()
                else:
                    normalized_summaries[normalized] = cleaned
            data["category_summaries"] = normalized_summaries
        else:
            data["category_summaries"] = {}
        
        memory = cast(MemoryStore, data)
        self._ensure_category_titles(memory)
        return self._finalize_loaded_memory(memory)

    def _finalize_loaded_memory(self, memory: MemoryStore) -> MemoryStore:
        if not self._is_short_term:
            return memory
        return self._ensure_short_term_freshness(memory)

    def _short_term_config(self) -> Dict[str, Any]:
        settings = load_memory_settings()
        return {
            "ttl_minutes": int(settings.get("short_term_ttl_minutes") or DEFAULT_MEMORY_SETTINGS["short_term_ttl_minutes"]),
            "grace_minutes": int(settings.get("short_term_grace_minutes") or DEFAULT_MEMORY_SETTINGS["short_term_grace_minutes"]),
            "active_hold_minutes": int(
                settings.get("short_term_active_task_hold_minutes")
                or DEFAULT_MEMORY_SETTINGS["short_term_active_task_hold_minutes"]
            ),
            "promote_score": int(settings.get("short_term_promote_score") or DEFAULT_MEMORY_SETTINGS["short_term_promote_score"]),
            "promote_importance": float(
                settings.get("short_term_promote_importance")
                or DEFAULT_MEMORY_SETTINGS["short_term_promote_importance"]
            ),
        }

    def _refresh_short_term_expiry(self, memory: MemoryStore, *, now: datetime | None = None) -> None:
        if not self._is_short_term:
            return
        config = self._short_term_config()
        ttl_minutes = max(1, config["ttl_minutes"])
        bonus = config["active_hold_minutes"] if (memory.get("active_task") and config["active_hold_minutes"] > 0) else 0
        base_minutes = ttl_minutes + bonus
        expires_at = (now or datetime.now()) + timedelta(minutes=base_minutes)
        memory["expires_at"] = expires_at.isoformat()

    def _initialize_short_memory_base(
        self,
        current: Optional[MemoryStore] = None,
        *,
        preserve_active_task: bool = True,
        preserve_emotional_context: bool = True,
    ) -> MemoryStore:
        base = self._create_empty_memory()
        if preserve_active_task and current and current.get("active_task"):
            base["active_task"] = current.get("active_task")
        if preserve_emotional_context and current and current.get("emotional_context"):
            base["emotional_context"] = current.get("emotional_context")
        self._refresh_short_term_expiry(base)
        return base

    def _promote_short_term_highlights(self, memory: MemoryStore) -> None:
        if not self._is_short_term:
            return
        config = self._short_term_config()
        score_threshold = config.get("promote_score", 0)
        importance_threshold = config.get("promote_importance", 1.0)

        operations: List[MemoryOperation] = []

        slot_candidates = sorted(memory.get("slots", []), key=lambda s: s.get("score", 0), reverse=True)
        for slot in slot_candidates:
            try:
                slot_score = float(slot.get("score", 0) or 0)
            except (TypeError, ValueError):
                slot_score = 0.0
            if slot_score < score_threshold:
                continue
            slot_id = str(slot.get("id") or f"short_slot_{len(operations)}")
            operations.append(
                {
                    "op": "set_slot",
                    "slot_id": slot_id,
                    "value": slot.get("current_value"),
                    "label": slot.get("label") or slot_id,
                    "category": slot.get("category", "general"),
                    "confidence": min(0.95, float(slot.get("confidence", 0.8) or 0.8)),
                    "reason": "promoted_from_short_term",
                    "log_change": True,
                }
            )
            if len(operations) >= _SHORT_TERM_PROMOTION_LIMIT:
                break

        for episode in memory.get("episodic_memory", []):
            importance = episode.get("importance", 0) or 0
            try:
                importance_value = float(importance)
            except (TypeError, ValueError):
                importance_value = 0.0
            if importance_value < importance_threshold:
                continue
            operations.append(
                {
                    "op": "add_episode",
                    "content": episode.get("content"),
                    "importance": importance_value,
                    "tags": episode.get("tags") or [],
                    "reason": episode.get("type") or "event",
                }
            )

        if not operations:
            return

        try:
            MemoryManager("long_term_memory.json").apply_diff({"operations": operations})
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to promote short-term highlights: %s", exc)

    def _ensure_short_term_freshness(self, memory: MemoryStore) -> MemoryStore:
        config = self._short_term_config()
        now = datetime.now()
        expires_str = memory.get("expires_at")
        if not expires_str:
            self._refresh_short_term_expiry(memory, now=now)
            self.save_memory(memory)
            return memory
        try:
            expires_at = datetime.fromisoformat(expires_str)
        except ValueError:
            self._refresh_short_term_expiry(memory, now=now)
            self.save_memory(memory)
            return memory

        if now < expires_at:
            return memory

        if memory.get("active_task") and config["active_hold_minutes"] > 0:
            hold_deadline = expires_at + timedelta(minutes=config["active_hold_minutes"])
            if now < hold_deadline:
                memory["expires_at"] = hold_deadline.isoformat()
                self.save_memory(memory)
                return memory

        if config["grace_minutes"] > 0:
            grace_deadline = expires_at + timedelta(minutes=config["grace_minutes"])
            if now < grace_deadline:
                memory["expires_at"] = grace_deadline.isoformat()
                self.save_memory(memory)
                return memory

        self._promote_short_term_highlights(memory)
        refreshed = self._initialize_short_memory_base(
            memory,
            preserve_active_task=False,
            preserve_emotional_context=False,
        )
        refreshed["last_consolidated_to_long"] = datetime.now().isoformat()
        self.save_memory(refreshed)
        return refreshed

    def save_memory(self, memory: MemoryStore) -> None:
        """Save memory to file."""
        memory["last_updated"] = datetime.now().isoformat()
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(memory, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logging.error(f"Failed to save memory to {self.file_path}: {e}")

    def replace_with_user_payload(self, payload: Any) -> MemoryStore:
        """Replace the stored summaries based on user-provided payload."""
        memory = self.load_memory()
        categories, titles = self._coerce_user_category_payload(payload)
        memory["category_summaries"] = categories
        self._ensure_category_titles(memory, overrides=titles)
        structured = self._extract_manual_structure(categories)
        self._apply_manual_structure(memory, structured)
        self._sync_summary_text(memory, force=True)
        if self._is_short_term:
            self._refresh_short_term_expiry(memory)
        self.save_memory(memory)
        return memory

    def consolidate_memory(
        self,
        recent_conversation: List[Dict[str, Any]],
        memory_kind: Literal["short", "long"] = "long",
        llm: Any | None = None,
        short_snapshot: Optional[Dict[str, Any]] = None,
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
        prompt = _build_consolidation_prompt(
            memory_kind,
            current_memory,
            normalized_history,
            short_snapshot=short_snapshot,
        )

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
                "short": {"active_task", "pending_questions", "recent_entities", "emotional_context"},
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

        # Guardrail: normalise category keys but keep unexpected categories so
        # the orchestrator prompt can still reference them.
        if "category_summaries" in diff and isinstance(diff["category_summaries"], dict):
            cleaned_categories: Dict[str, str] = {}
            for cat, val in diff["category_summaries"].items():
                if not isinstance(val, str):
                    continue
                normalized = self._normalize_category(cat)
                value = val.strip()
                if not value:
                    continue
                if normalized in cleaned_categories:
                    existing = cleaned_categories[normalized]
                    cleaned_categories[normalized] = f"{existing}\n{value}".strip()
                    continue
                cleaned_categories[normalized] = value
            diff["category_summaries"] = cleaned_categories

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

    @staticmethod
    def _merge_list_unique(target: List[Any], additions: List[Any]) -> List[Any]:
        """Append only unseen items, preserving order."""
        for item in additions:
            if item not in target:
                target.append(item)
        return target

    @staticmethod
    def _apply_list_patch(existing: List[Any], patch: Dict[str, Any]) -> List[Any]:
        """Apply {{add, remove}} patch semantics to a list."""
        updated = list(existing)
        to_add = patch.get("add") if isinstance(patch, dict) else None
        to_remove = patch.get("remove") if isinstance(patch, dict) else None

        if isinstance(to_add, list):
            MemoryManager._merge_list_unique(updated, to_add)
        if isinstance(to_remove, list):
            updated = [item for item in updated if item not in to_remove]
        return updated

    def apply_diff(self, diff: MemoryDiff) -> MemoryStore:
        """Apply a semantic diff (operations) to the current memory."""
        memory = self.load_memory()
        
        # 1. Update top-level data fields (new behavior)
        new_data = diff.get("new_data")
        if isinstance(new_data, dict):
            for key, value in new_data.items():
                if value is None:
                    continue

                existing_value = memory.get(key)

                # List fields: prefer append/remove semantics to avoid destructive replacement
                if isinstance(existing_value, list):
                    if isinstance(value, dict) and (value.get("add") or value.get("remove")):
                        memory[key] = self._apply_list_patch(existing_value, value)
                    elif isinstance(value, list):
                        memory[key] = self._merge_list_unique(existing_value, value)
                    else:
                        # Fallback: avoid replacing the list unless explicitly instructed
                        continue
                # Dict fields: deep merge keys
                elif isinstance(existing_value, dict) and isinstance(value, dict):
                    self._deep_merge(existing_value, value)
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
        self._ensure_category_titles(memory)

        # 3. Update legacy summary_text
        new_summary = diff.get("summary_text")
        if new_summary and isinstance(new_summary, str):
            cleaned_summary = self._clean_human_summary(new_summary, fallback=memory.get("summary_text", ""))
            if cleaned_summary:
                stamped = f"{datetime.now().date().isoformat()}: {cleaned_summary}"
                log_list = memory.get("summary_log") if isinstance(memory.get("summary_log"), list) else []
                log_list.append(stamped)
                memory["summary_log"] = log_list[-50:]  # Keep recent entries
                memory["summary_text"] = cleaned_summary
                memory.setdefault("category_summaries", {})
                memory["category_summaries"]["general"] = cleaned_summary
            # Do not auto-overwrite category summaries; rely on explicit category_summaries

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

        if self._is_short_term:
            self._refresh_short_term_expiry(memory)

        self.save_memory(memory)
        return memory
    
    def apply_decay(self) -> MemoryStore:
        """
        4. 減衰（Decay: 忘却曲線）
        apply_decay を定期実行し、参照されない情報の信頼度を指数的に低下させる。
          - 安定性: S ≈ ln(access_count + 1) + 1 （参照回数が多いほど忘れにくい）
          - 信頼度: Confidence(t) = exp(-0.05 * t / S) （最終アクセスからの日数 t に応じて減衰）
          - 長期未参照の情報はスコアが下がり「低優先度」へ分類するが、完全削除はせず痕跡を保持
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

    def reset_short_memory(self, preserve_active_task: bool = True) -> MemoryStore:
        """Reset short-term memory after long-term consolidation."""

        current = self.load_memory()
        base = self._initialize_short_memory_base(
            current,
            preserve_active_task=preserve_active_task,
            preserve_emotional_context=True,
        )

        base["last_consolidated_to_long"] = datetime.now().isoformat()
        self.save_memory(base)
        return base

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
            
            days_inactive = max(
                (current_time - last_accessed).total_seconds() / 86400.0,
                0.0,
            )
            
            if days_inactive < 1:
                continue
                
            access_count_raw = slot.get("access_count", 0) or 0
            try:
                access_count = max(int(access_count_raw), 0)
            except (TypeError, ValueError):
                access_count = 0
            stability = math.log(access_count + 1) + 1.0
            
            # Confidence(t) = exp(-0.05 * t / S)
            decay_target = math.exp(-0.05 * (days_inactive / stability))
            current_confidence = slot.get("confidence", 1.0) or 1.0
            try:
                current_confidence = float(current_confidence)
            except (TypeError, ValueError):
                current_confidence = 1.0
            new_confidence = max(0.05, min(current_confidence, decay_target))
            slot["confidence"] = round(new_confidence, 3)
            
            # Decay score proportional to inactivity horizon but keep trace.
            current_score = slot.get("score", 0) or 0
            try:
                current_score = int(current_score)
            except (TypeError, ValueError):
                current_score = 0
            drop_units = math.floor(days_inactive / max(7.0, stability * 5.0))
            if drop_units > 0:
                slot["score"] = current_score - drop_units
            else:
                slot["score"] = current_score
                
            score_for_priority = slot.get("score", 0) or 0
            if (
                days_inactive > stability * 14
                or score_for_priority <= -3
                or slot["confidence"] < 0.2
            ):
                slot["priority"] = "low"
            elif score_for_priority > 5 and slot["confidence"] > 0.8:
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

    def _sync_summary_text(self, memory: MemoryStore, *, force: bool = False) -> None:
        """Regenerate summary_text from category_summaries for legacy compatibility."""

        categories = memory.get("category_summaries") or {}
        if not categories:
            if force:
                memory["summary_text"] = ""
            return

        # If journal log exists, avoid overwriting it; keep legacy summary only as fallback.
        if memory.get("summary_log") and not force:
            return

        parts: List[str] = []
        for category in MEMORY_CATEGORIES:
            summary = categories.get(category)
            if summary:
                parts.append(summary)

        for category, summary in categories.items():
            if category in MEMORY_CATEGORIES:
                continue
            if summary:
                parts.append(summary)

        memory["summary_text"] = " ".join(parts) if parts else ""

    def _purge_tasks(self, memory: Dict[str, Any]) -> None:
        """Strip ongoing/completed task tracking from memory store."""

        task_keys = ["active_task", "completed_tasks", "tasks", "task_history"]
        for key in task_keys:
            if key == "active_task" and self._is_short_term:
                continue
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

    def _extract_manual_structure(self, categories: CategorySummaries) -> ManualStructuredPayload:
        """Derive structured slots/profile/preference data from manual text payload."""

        entries: List[ManualEntry] = []
        profile_updates: Dict[str, Any] = {}
        preference_updates: Dict[str, List[str]] = {"likes": [], "dislikes": []}

        for raw_category, text in categories.items():
            normalized_category = self._normalize_category(raw_category)
            for chunk in _split_manual_text_block(text):
                key, value = _parse_manual_key_value_line(chunk)
                cleaned_value = _clean_manual_value(value)
                if not cleaned_value:
                    continue
                entry: ManualEntry = {
                    "category": normalized_category,
                    "key": key,
                    "value": cleaned_value,
                }
                entries.append(entry)

                profile_field = _identify_profile_field(key, chunk)
                if profile_field:
                    profile_updates[profile_field] = _coerce_profile_value(profile_field, cleaned_value)

                pref_bucket = _classify_preference_from_text(normalized_category, chunk)
                safe_chunk = chunk.strip()
                if pref_bucket == "likes":
                    preference_updates["likes"].append(safe_chunk or cleaned_value)
                elif pref_bucket == "dislikes":
                    preference_updates["dislikes"].append(safe_chunk or cleaned_value)

        return {
            "entries": entries,
            "profile": profile_updates,
            "preferences": preference_updates,
        }

    def _apply_manual_structure(self, memory: MemoryStore, structured: ManualStructuredPayload) -> None:
        """Apply structured manual updates to slots/profile/preferences."""

        entries = structured.get("entries") if structured else []
        self._apply_manual_slots(memory, entries or [])
        self._apply_manual_profile_updates(memory, structured.get("profile") if structured else {})
        self._apply_manual_preference_updates(memory, structured.get("preferences") if structured else {})

    def _apply_manual_slots(self, memory: MemoryStore, entries: List[ManualEntry]) -> None:
        """Replace manual-editor slots with freshly parsed entries."""

        slots = memory.get("slots") or []
        preserved_slots = [slot for slot in slots if slot.get("source") != _MANUAL_SLOT_SOURCE]
        memory["slots"] = preserved_slots

        if not entries:
            return

        manual_slots: List[MemorySlot] = []
        existing_ids = {slot.get("id") for slot in preserved_slots if isinstance(slot.get("id"), str)}

        for idx, entry in enumerate(entries, start=1):
            value = entry.get("value")
            if not isinstance(value, str) or not value.strip():
                continue
            category = entry.get("category") or "general"
            label = entry.get("key") or value[:40]
            slot_id_raw = f"manual_{category}_{entry.get('key') or value[:32]}"
            slot_id = self._normalize_id(slot_id_raw) or f"manual_{category}_{idx}"
            slot_id = self._ensure_unique_manual_slot_id(slot_id, existing_ids)
            existing_ids.add(slot_id)

            current_time = datetime.now().isoformat()
            manual_slot: MemorySlot = {
                "id": slot_id,
                "label": label[:80],
                "category": category,
                "current_value": value,
                "confidence": 0.85,
                "last_updated": current_time,
                "history": [],
                "source": _MANUAL_SLOT_SOURCE,
                "verified": True,
                "access_count": 1,
                "last_accessed": current_time,
                "priority": "medium",
                "score": 0,
            }
            manual_slots.append(manual_slot)

        memory["slots"].extend(manual_slots)

    def _ensure_unique_manual_slot_id(self, base_id: str, existing_ids: set[str]) -> str:
        """Ensure manual slot IDs do not collide with other slots."""

        candidate = base_id or "manual_entry"
        counter = 2
        while candidate in existing_ids:
            candidate = f"{base_id}_{counter}"
            counter += 1
        return candidate

    def _apply_manual_profile_updates(self, memory: MemoryStore, updates: Dict[str, Any] | None) -> None:
        """Merge profile fields derived from manual text."""

        if not updates:
            return
        profile = memory.get("user_profile")
        if not isinstance(profile, dict):
            profile = {}
        for field, value in updates.items():
            if value is None:
                continue
            profile[field] = value
        memory["user_profile"] = profile

    def _apply_manual_preference_updates(self, memory: MemoryStore, updates: Dict[str, List[str]] | None) -> None:
        """Merge like/dislike lists influenced by manual text."""

        if not updates:
            return
        prefs = memory.get("preferences")
        if not isinstance(prefs, dict):
            prefs = {}

        for key in ("likes", "dislikes"):
            values = updates.get(key) if updates else None
            if not values:
                continue
            clean_values = [v for v in values if isinstance(v, str) and v.strip()]
            if not clean_values:
                continue
            existing = prefs.get(key)
            if isinstance(existing, list):
                prefs[key] = self._merge_list_unique(existing, clean_values)
            else:
                prefs[key] = clean_values

        memory["preferences"] = prefs


    def _coerce_user_category_payload(self, payload: Any) -> tuple[CategorySummaries, Dict[str, str]]:
        """Normalise user-provided summaries (string or dict) into categories and optional titles."""

        categories: CategorySummaries = {}
        titles: Dict[str, str] = {}

        if isinstance(payload, str):
            cleaned = self._clean_human_summary(payload, fallback="")
            if cleaned:
                categories["general"] = cleaned
            return categories, titles

        raw_map: Dict[str, Any] | None = None
        provided_titles: Dict[str, Any] = {}

        if isinstance(payload, dict):
            if "categories" in payload and isinstance(payload.get("categories"), dict):
                raw_map = payload.get("categories")
                meta_titles = payload.get("titles")
                if isinstance(meta_titles, dict):
                    provided_titles = meta_titles
            else:
                raw_map = payload

        if isinstance(raw_map, dict):
            for raw_category, summary in raw_map.items():
                normalized = self._normalize_category(str(raw_category))
                value = summary
                explicit_title = None

                if isinstance(summary, dict):
                    if "value" in summary:
                        value = summary.get("value")
                    explicit_title = summary.get("title")
                if not isinstance(value, str):
                    continue

                cleaned = self._clean_human_summary(value, fallback="")
                if not cleaned:
                    continue
                categories[normalized] = cleaned

                title_value = explicit_title or provided_titles.get(raw_category) or provided_titles.get(normalized)
                if isinstance(title_value, str):
                    stripped_title = title_value.strip()
                    if stripped_title:
                        titles[normalized] = stripped_title

        return categories, titles

    def _default_category_title(self, category: str) -> str:
        if category in _CATEGORY_FRIENDLY_NAMES:
            return _CATEGORY_FRIENDLY_NAMES[category]
        fallback = (category or "").replace("_", " ").strip()
        if not fallback:
            return "メモ"
        return fallback.title()

    def _ensure_category_titles(
        self,
        memory: MemoryStore,
        overrides: Optional[Dict[str, str]] = None,
    ) -> None:
        titles = memory.get("category_titles")
        if not isinstance(titles, dict):
            titles = {}

        summaries = memory.get("category_summaries") or {}
        updated: Dict[str, str] = {}

        for key in summaries.keys():
            source = None
            if overrides:
                source = overrides.get(key)
            if not source:
                source = titles.get(key)
            label = source.strip() if isinstance(source, str) else ""
            if not label:
                label = self._default_category_title(key)
            updated[key] = label

        memory["category_titles"] = updated

    def _resolve_category_title(self, category: str, titles: Optional[Dict[str, str]] = None) -> str:
        if titles and isinstance(titles.get(category), str):
            label = titles[category].strip()
            if label:
                return label
        return self._default_category_title(category)

    def _create_empty_memory(self) -> MemoryStore:
        return {
            "type": self.TYPE,
            "version": self.VERSION,
            "last_updated": datetime.now().isoformat(),
            "summary_text": "",
            "summary_log": [],
            "category_summaries": {},
            "category_titles": {},
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
            "last_decay_processed": datetime.now().isoformat(),
            "last_consolidated_to_long": None,
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
        titles = memory.get("category_titles") if isinstance(memory.get("category_titles"), dict) else {}

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
        
        ordered_categories = list(MEMORY_CATEGORIES)
        extra_categories = sorted(
            (set(slots_by_category.keys()) | set(memory.get("category_summaries", {}).keys()))
            - set(ordered_categories)
        )

        for category in ordered_categories + extra_categories:
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
                category_label = self._get_category_label(category, titles)
                section = f"【{category_label}】\n" + "\n".join(category_parts)
                sections.append(section)
        
        return "\n\n".join(sections) if sections else ""
    
    def _get_category_label(self, category: str, titles: Optional[Dict[str, str]] = None) -> str:
        """Resolve a user-friendly label for a category."""
        return self._resolve_category_title(category, titles)

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

    def _merge_slot_value(self, old_value: Any, new_value: Any) -> Any:
        """Non-destructive merge for slot values."""

        if isinstance(old_value, list):
            if isinstance(new_value, dict) and (new_value.get("add") or new_value.get("remove")):
                return self._apply_list_patch(list(old_value), new_value)
            if isinstance(new_value, list):
                return self._merge_list_unique(list(old_value), new_value)
            # Avoid replacing lists with scalars unless explicitly requested
            if new_value is None:
                return list(old_value)
            if new_value in old_value:
                return list(old_value)
            return list(old_value) + [new_value]

        if isinstance(old_value, dict) and isinstance(new_value, dict):
            merged = dict(old_value)
            self._deep_merge(merged, new_value)
            return merged

        return new_value

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

            merged_value = self._merge_slot_value(old_value, new_value)
            if merged_value == old_value:
                return

            target_slot["current_value"] = merged_value
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
                    "to_value": merged_value,
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
                    "to_value": merged_value,
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
                "category": self._normalize_category(op.get("category") or "general"),
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
