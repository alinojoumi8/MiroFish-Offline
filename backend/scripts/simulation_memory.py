"""
Structured simulation memory for OASIS runs.

This file-backed store complements actions.jsonl and graph memory:
- actions.jsonl remains the monitoring log
- Neo4j graph memory remains the long-term RAG layer
- this store keeps deterministic per-agent, cross-platform memory records
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


MEMORY_CONTEXT_HEADER = "### Recent simulation memory"
MEMORY_CONTEXT_FOOTER = "### End recent simulation memory"


def record_round_memories(memory_store, platform, round_num, db_path, last_rowid, agent_names):
    """Persist newly completed OASIS actions and return the consumed trace cursor."""
    try:
        from .simulation_actions import fetch_new_actions_from_db
    except ImportError:
        from simulation_actions import fetch_new_actions_from_db
    actions, cursor = fetch_new_actions_from_db(db_path, last_rowid, agent_names)
    for action in actions:
        record_action_memory(memory_store, platform, round_num, action)
    return cursor


def record_action_memory(
    memory_store: Optional["SimulationMemoryStore"],
    platform: str,
    round_num: int,
    action_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Persist one logged action into a structured memory store."""
    if not memory_store:
        return None

    return memory_store.add_action(
        platform=platform,
        round_num=round_num,
        agent_id=action_data.get("agent_id", 0),
        agent_name=action_data.get("agent_name", f"Agent_{action_data.get('agent_id', 0)}"),
        action_type=action_data.get("action_type", ""),
        action_args=action_data.get("action_args", {}),
        timestamp=action_data.get("timestamp"),
        result=action_data.get("result"),
        success=action_data.get("success", True),
    )


def replace_memory_context(value: str, context: str) -> str:
    """Replace the prior memory block in a text field with the latest context."""
    if MEMORY_CONTEXT_HEADER in value and MEMORY_CONTEXT_FOOTER in value:
        start = value.index(MEMORY_CONTEXT_HEADER)
        end = value.index(MEMORY_CONTEXT_FOOTER) + len(MEMORY_CONTEXT_FOOTER)
        value = (value[:start] + value[end:]).strip()

    if not context:
        return value

    memory_block = f"{MEMORY_CONTEXT_HEADER}\n{context}\n{MEMORY_CONTEXT_FOOTER}"
    return f"{value}\n\n{memory_block}".strip()


def attach_memory_context_to_agent(
    agent,
    memory_store: Optional["SimulationMemoryStore"],
    agent_id: int,
    limit: int = 8,
) -> bool:
    """Attach current per-agent memory context to a mutable agent profile field."""
    if not memory_store:
        return False

    context = memory_store.build_context_for_agent(agent_id=agent_id, limit=limit)
    if not context:
        return False

    # OASIS uses CAMEL BaseMessage objects and serializes messages into memory.
    # Updating a profile attribute alone does not change the model's context.
    from camel.memories import ChatHistoryMemory
    from camel.messages import BaseMessage
    from camel.types import OpenAIBackendRole

    message = getattr(agent, "system_message", None)
    memory = getattr(agent, "memory", None)
    if isinstance(message, BaseMessage) and isinstance(memory, ChatHistoryMemory):
        records = [item.memory_record for item in memory.retrieve()]
        system_records = [
            record for record in records
            if record.role_at_backend == OpenAIBackendRole.SYSTEM
        ]
        if not system_records:
            return False
        content = replace_memory_context(message.content, context)
        message.content = content
        system_records[0].message = message
        memory.clear()
        memory.write_records(records)
        return True

    for attr in ("persona", "profile", "bio", "description", "user_char", "system_message"):
        value = getattr(agent, attr, None)
        if isinstance(value, str):
            setattr(agent, attr, replace_memory_context(value, context))
            return True
        if isinstance(value, dict):
            for key in ("persona", "bio", "user_char", "description"):
                nested = value.get(key)
                if isinstance(nested, str):
                    value[key] = replace_memory_context(nested, context)
                    return True

    return False


class SimulationMemoryStore:
    """Append-only structured memory store for one simulation directory."""

    MEMORY_DIR = "memory"
    MEMORY_FILE = "agent_memories.jsonl"

    def __init__(self, simulation_dir: str | os.PathLike, agent_names: Optional[Dict[int, str]] = None):
        self.simulation_dir = Path(simulation_dir)
        self.memory_dir = self.simulation_dir / self.MEMORY_DIR
        self.memory_path = self.memory_dir / self.MEMORY_FILE
        self.agent_names = agent_names or {}
        self.agent_name_to_id = {
            name: agent_id
            for agent_id, name in self.agent_names.items()
            if name
        }
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._records = []
        self._read_offset = 0
        self._file_identity = None

    def add_action(
        self,
        platform: str,
        round_num: int,
        agent_id: int,
        agent_name: str,
        action_type: str,
        action_args: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
        result: Optional[str] = None,
        success: bool = True,
    ) -> Dict[str, Any]:
        """Normalize and persist one action as structured memory."""
        action_args = action_args or {}
        timestamp = timestamp or datetime.now().isoformat()
        content = self._extract_content(action_type, action_args)
        target_agent_name = self._extract_target_name(action_type, action_args)
        target_agent_id = self._extract_target_id(action_args, target_agent_name)
        participants = [agent_id]
        if target_agent_id is not None and target_agent_id not in participants:
            participants.append(target_agent_id)

        record = {
            "memory_id": str(uuid.uuid4()),
            "platform": platform,
            "round": round_num,
            "timestamp": timestamp,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "target_agent_id": target_agent_id,
            "target_agent_name": target_agent_name,
            "participants": participants,
            "action_type": action_type,
            "content": content,
            "action_args": action_args,
            "result": result,
            "success": success,
            "text": self._describe_action(
                platform=platform,
                agent_name=agent_name,
                action_type=action_type,
                content=content,
                target_agent_name=target_agent_name,
                action_args=action_args,
            ),
        }

        with self.memory_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return record

    def get_agent_memories(
        self,
        agent_id: int,
        limit: int = 8,
        platform: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return newest memories where the agent was actor or target."""
        records = []
        for record in self._read_records():
            if platform and record.get("platform") != platform:
                continue
            participants = record.get("participants") or []
            if agent_id in participants:
                records.append(record)

        records.sort(
            key=lambda r: (
                int(r.get("round") or 0),
                str(r.get("timestamp") or ""),
            ),
            reverse=True,
        )
        return records[:limit]

    def get_public_context(self, agent_id: int, limit: int = 4) -> List[Dict[str, Any]]:
        """Return newest public records not already tied to the agent."""
        records = [
            record
            for record in self._read_records()
            if agent_id not in (record.get("participants") or [])
        ]
        records.sort(
            key=lambda r: (
                int(r.get("round") or 0),
                str(r.get("timestamp") or ""),
            ),
            reverse=True,
        )
        return records[:limit]

    def build_context_for_agent(self, agent_id: int, limit: int = 8) -> str:
        """Build concise prompt context for an agent's next action."""
        direct = self.get_agent_memories(agent_id=agent_id, limit=limit)
        public = self.get_public_context(agent_id=agent_id, limit=max(0, limit - len(direct)))
        lines = [self._format_context_line(record) for record in direct + public]
        return "\n".join(line for line in lines if line)

    def _read_records(self) -> Iterable[Dict[str, Any]]:
        # Tail the file so separate platform stores see each other's appends
        # without reparsing the complete history for every agent and round.
        try:
            f = self.memory_path.open("rb")
        except FileNotFoundError:
            self._records = []
            self._read_offset = 0
            self._file_identity = None
            return self._records
        with f:
            stat = os.fstat(f.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if identity != self._file_identity or stat.st_size < self._read_offset:
                self._records = []
                self._read_offset = 0
                self._file_identity = identity
            f.seek(self._read_offset)
            while line := f.readline():
                if not line.endswith(b"\n"):
                    break  # A concurrent writer has not completed this record.
                self._read_offset = f.tell()
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        self._records.append(record)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
        return self._records

    def _extract_content(self, action_type: str, action_args: Dict[str, Any]) -> str:
        keys_by_action = {
            "CREATE_POST": ("content",),
            "CREATE_COMMENT": ("content", "post_content"),
            "QUOTE_POST": ("quote_content", "content", "original_content"),
            "REPOST": ("original_content",),
            "LIKE_POST": ("post_content",),
            "DISLIKE_POST": ("post_content",),
            "LIKE_COMMENT": ("comment_content",),
            "DISLIKE_COMMENT": ("comment_content",),
            "SEARCH_POSTS": ("query", "keyword"),
            "SEARCH_USER": ("query", "username"),
        }
        for key in keys_by_action.get(action_type, ("content", "query")):
            value = action_args.get(key)
            if value:
                return str(value)
        return ""

    def _extract_target_name(self, action_type: str, action_args: Dict[str, Any]) -> Optional[str]:
        keys_by_action = {
            "FOLLOW": ("target_user_name",),
            "MUTE": ("target_user_name",),
            "LIKE_POST": ("post_author_name",),
            "DISLIKE_POST": ("post_author_name",),
            "CREATE_COMMENT": ("post_author_name",),
            "REPOST": ("original_author_name",),
            "QUOTE_POST": ("original_author_name",),
            "LIKE_COMMENT": ("comment_author_name",),
            "DISLIKE_COMMENT": ("comment_author_name",),
        }
        for key in keys_by_action.get(action_type, ("target_agent_name", "target_user_name")):
            value = action_args.get(key)
            if value:
                return str(value)
        return None

    def _extract_target_id(
        self,
        action_args: Dict[str, Any],
        target_agent_name: Optional[str],
    ) -> Optional[int]:
        for key in ("target_agent_id", "post_author_id", "comment_author_id"):
            value = action_args.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass
        if target_agent_name:
            return self.agent_name_to_id.get(target_agent_name)
        return None

    def _describe_action(
        self,
        platform: str,
        agent_name: str,
        action_type: str,
        content: str,
        target_agent_name: Optional[str],
        action_args: Dict[str, Any],
    ) -> str:
        quoted = f': "{content}"' if content else ""
        if action_type == "CREATE_POST":
            return f"{agent_name} posted on {platform}{quoted}"
        if action_type == "CREATE_COMMENT":
            target = f" on {target_agent_name}'s post" if target_agent_name else ""
            return f"{agent_name} commented{target}{quoted}"
        if action_type == "LIKE_POST":
            target = f"{target_agent_name}'s post" if target_agent_name else "a post"
            return f"{agent_name} liked {target}{quoted}"
        if action_type == "DISLIKE_POST":
            target = f"{target_agent_name}'s post" if target_agent_name else "a post"
            return f"{agent_name} disliked {target}{quoted}"
        if action_type == "REPOST":
            target = f"{target_agent_name}'s post" if target_agent_name else "a post"
            return f"{agent_name} reposted {target}{quoted}"
        if action_type == "QUOTE_POST":
            target = f"{target_agent_name}'s post" if target_agent_name else "a post"
            quote = action_args.get("quote_content") or action_args.get("content")
            suffix = f' with comment: "{quote}"' if quote else quoted
            return f"{agent_name} quoted {target}{suffix}"
        if action_type == "FOLLOW":
            target = target_agent_name or "another user"
            return f"{agent_name} followed {target}"
        if action_type == "MUTE":
            target = target_agent_name or "another user"
            return f"{agent_name} muted {target}"
        if action_type == "LIKE_COMMENT":
            target = f"{target_agent_name}'s comment" if target_agent_name else "a comment"
            return f"{agent_name} liked {target}{quoted}"
        if action_type == "DISLIKE_COMMENT":
            target = f"{target_agent_name}'s comment" if target_agent_name else "a comment"
            return f"{agent_name} disliked {target}{quoted}"
        if action_type in {"SEARCH_POSTS", "SEARCH_USER"}:
            return f"{agent_name} searched on {platform}{quoted}"
        return f"{agent_name} performed {action_type} on {platform}{quoted}"

    def _format_context_line(self, record: Dict[str, Any]) -> str:
        platform = record.get("platform", "unknown")
        round_num = record.get("round", "?")
        text = record.get("text", "").strip()
        if not text:
            return ""
        return f"- [{platform}] round {round_num}: {text}"
