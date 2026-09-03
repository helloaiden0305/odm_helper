import hashlib
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def message_signature(role: str, content: str) -> str:
    raw = f"{role}:{content}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def estimate_token_count(content: str) -> int:
    return math.ceil(len(content or "") / 1.6)


@dataclass
class ContextTurn:
    turn_id: str
    session_id: str
    role: str
    content: str
    created_at: str
    token_estimate: int
    signature: str


@dataclass
class ConversationTurn:
    """Lifecycle metadata for one user question and its eventual assistant answer."""

    turn_id: str
    session_id: str
    channel: str
    question: str
    status: str
    created_at: str
    completed_at: str | None = None
    assistant_turn_id: str | None = None


@dataclass
class ArchiveRecord:
    archive_id: str
    session_id: str
    turn_ids: list[str]
    content: str
    keywords: list[str]
    created_at: str
    token_estimate: int


@dataclass
class SummaryRecord:
    summary_id: str
    session_id: str
    source_archive_ids: list[str]
    source_turn_ids: list[str]
    summary_type: str
    summary: str
    token_before: int
    token_after: int
    created_at: str


@dataclass
class FactRecord:
    fact_id: str
    session_id: str
    source_turn_id: str
    key: str
    value: str
    confidence: float
    created_at: str


@dataclass
class InMemoryContextStore:
    active_turns: dict[str, list[ContextTurn]] = field(default_factory=dict)
    conversation_turns: dict[str, dict[str, ConversationTurn]] = field(default_factory=dict)
    archived_messages: dict[str, list[ArchiveRecord]] = field(default_factory=dict)
    collapsed_summaries: dict[str, list[SummaryRecord]] = field(default_factory=dict)
    session_summary: dict[str, SummaryRecord] = field(default_factory=dict)
    session_facts: dict[str, list[FactRecord]] = field(default_factory=dict)
    summary_by_id: dict[str, SummaryRecord] = field(default_factory=dict)
    active_epochs: dict[str, str] = field(default_factory=dict)
    archived_signatures: dict[str, set[str]] = field(default_factory=dict)
    fact_signatures: dict[str, set[str]] = field(default_factory=dict)
    turn_seq: dict[str, int] = field(default_factory=dict)

    def _next_turn_id(self, session_id: str) -> str:
        next_value = self.turn_seq.get(session_id, 0) + 1
        self.turn_seq[session_id] = next_value
        return f"turn_{next_value:04d}"

    def build_turn(self, session_id: str, message: dict[str, Any], token_estimate: int) -> ContextTurn:
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        return ContextTurn(
            turn_id=self._next_turn_id(session_id),
            session_id=session_id,
            role=role,
            content=content,
            created_at=now_iso(),
            token_estimate=token_estimate,
            signature=message_signature(role, content),
        )

    def set_active_turns(self, session_id: str, turns: list[ContextTurn]) -> None:
        self.active_turns[session_id] = turns

    def get_active_turns(self, session_id: str) -> list[ContextTurn]:
        return list(self.active_turns.get(session_id, []))

    def get_active_epoch(self, session_id: str) -> str:
        return self.active_epochs.setdefault(session_id, "epoch_001")

    def append_active_turns(self, session_id: str, turns: list[ContextTurn]) -> int:
        """Append completed turns to the canonical history for one conversation."""
        if not turns:
            return 0

        active_turns = self.active_turns.setdefault(session_id, [])
        active_turns.extend(turns)
        return len(turns)

    def begin_turn(self, session_id: str, question: str, channel: str) -> str:
        user_turn = self.build_turn(
            session_id=session_id,
            message={"role": "user", "content": question},
            token_estimate=estimate_token_count(question),
        )
        self.active_turns.setdefault(session_id, []).append(user_turn)
        self.conversation_turns.setdefault(session_id, {})[user_turn.turn_id] = ConversationTurn(
            turn_id=user_turn.turn_id,
            session_id=session_id,
            channel=channel,
            question=question,
            status="pending",
            created_at=user_turn.created_at,
        )
        return user_turn.turn_id

    def get_conversation_turn(self, session_id: str, turn_id: str) -> ConversationTurn | None:
        return self.conversation_turns.get(session_id, {}).get(turn_id)

    def complete_turn(self, session_id: str, turn_id: str, answer: str) -> list[ContextTurn]:
        record = self.get_conversation_turn(session_id, turn_id)
        if not record or record.status != "pending" or not answer:
            return []

        active_turns = self.active_turns.get(session_id, [])
        user_turn = next((turn for turn in active_turns if turn.turn_id == turn_id), None)
        if not user_turn:
            return []

        assistant_turn = self.build_turn(
            session_id=session_id,
            message={"role": "assistant", "content": answer},
            token_estimate=estimate_token_count(answer),
        )
        active_turns.append(assistant_turn)
        record.status = "completed"
        record.completed_at = now_iso()
        record.assistant_turn_id = assistant_turn.turn_id
        return [user_turn, assistant_turn]

    def close_turn(self, session_id: str, turn_id: str, status: str) -> bool:
        if status not in {"failed", "interrupted"}:
            raise ValueError(f"Unsupported turn status: {status}")

        record = self.get_conversation_turn(session_id, turn_id)
        if not record or record.status != "pending":
            return False

        record.status = status
        record.completed_at = now_iso()
        self.active_turns[session_id] = [
            turn for turn in self.active_turns.get(session_id, []) if turn.turn_id != turn_id
        ]
        return True

    def archive_turns(self, session_id: str, turns: list[ContextTurn], keywords: list[str]) -> ArchiveRecord | None:
        if not turns:
            return None

        seen = self.archived_signatures.setdefault(session_id, set())
        new_turns = [turn for turn in turns if turn.signature not in seen]
        if not new_turns:
            return None

        for turn in new_turns:
            seen.add(turn.signature)

        archive = ArchiveRecord(
            archive_id=f"arch_{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            turn_ids=[turn.turn_id for turn in new_turns],
            content="\n".join(f"{turn.role}: {turn.content}" for turn in new_turns),
            keywords=keywords,
            created_at=now_iso(),
            token_estimate=sum(turn.token_estimate for turn in new_turns),
        )
        self.archived_messages.setdefault(session_id, []).append(archive)
        return archive

    def get_archives(self, session_id: str) -> list[ArchiveRecord]:
        return list(self.archived_messages.get(session_id, []))

    def add_collapse_summary(self, session_id: str, summary: SummaryRecord) -> None:
        self.collapsed_summaries.setdefault(session_id, []).append(summary)
        self.summary_by_id[summary.summary_id] = summary

    def get_collapse_summaries(self, session_id: str) -> list[SummaryRecord]:
        return list(self.collapsed_summaries.get(session_id, []))

    def set_session_summary(self, session_id: str, summary: SummaryRecord) -> None:
        self.session_summary[session_id] = summary
        self.summary_by_id[summary.summary_id] = summary

    def get_session_summary(self, session_id: str) -> SummaryRecord | None:
        return self.session_summary.get(session_id)

    def get_summary(self, summary_id: str) -> SummaryRecord | None:
        return self.summary_by_id.get(summary_id)

    def replace_turns_with_summary_marker(
        self,
        session_id: str,
        turn_ids: list[str],
        summary_id: str,
    ) -> bool:
        """Replace one old working-context segment with a summary marker.

        The source turns remain available in conversation and archive records; only
        the active prompt representation is replaced.
        """
        target_ids = set(turn_ids)
        if not target_ids:
            return False

        turns = self.active_turns.get(session_id, [])
        first_index = next((index for index, turn in enumerate(turns) if turn.turn_id in target_ids), None)
        if first_index is None:
            return False

        marker = ContextTurn(
            turn_id=f"marker_{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            role="system",
            content=f"[[summary_id:{summary_id}]]",
            created_at=now_iso(),
            token_estimate=estimate_token_count(summary_id),
            signature=message_signature("system", summary_id),
        )
        retained = [turn for turn in turns if turn.turn_id not in target_ids]
        retained.insert(first_index, marker)
        self.active_turns[session_id] = retained
        return True

    def rollover_epoch(self, session_id: str, summary_id: str) -> tuple[str, str]:
        current_epoch = self.get_active_epoch(session_id)
        try:
            next_number = int(current_epoch.rsplit("_", 1)[-1]) + 1
        except ValueError:
            next_number = 2
        next_epoch = f"epoch_{next_number:03d}"
        self.active_epochs[session_id] = next_epoch
        self.active_turns[session_id] = [
            ContextTurn(
                turn_id=f"marker_{uuid.uuid4().hex[:8]}",
                session_id=session_id,
                role="system",
                content=f"[[summary_id:{summary_id}]]",
                created_at=now_iso(),
                token_estimate=estimate_token_count(summary_id),
                signature=message_signature("system", summary_id),
            )
        ]
        return current_epoch, next_epoch

    def upsert_facts(self, session_id: str, facts: list[FactRecord]) -> int:
        if not facts:
            return 0

        seen = self.fact_signatures.setdefault(session_id, set())
        stored = self.session_facts.setdefault(session_id, [])
        added = 0
        for fact in facts:
            signature = f"{fact.key}:{fact.value}".lower()
            if signature in seen:
                continue
            seen.add(signature)
            stored.append(fact)
            added += 1
        return added

    def get_facts(self, session_id: str, limit: int = 12) -> list[FactRecord]:
        return list(self.session_facts.get(session_id, []))[-limit:]


context_store = InMemoryContextStore()
