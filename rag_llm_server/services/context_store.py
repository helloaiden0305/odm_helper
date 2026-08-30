import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def message_signature(role: str, content: str) -> str:
    raw = f"{role}:{content}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


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
    archived_messages: dict[str, list[ArchiveRecord]] = field(default_factory=dict)
    collapsed_summaries: dict[str, list[SummaryRecord]] = field(default_factory=dict)
    session_summary: dict[str, SummaryRecord] = field(default_factory=dict)
    session_facts: dict[str, list[FactRecord]] = field(default_factory=dict)
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

    def get_collapse_summaries(self, session_id: str) -> list[SummaryRecord]:
        return list(self.collapsed_summaries.get(session_id, []))

    def set_session_summary(self, session_id: str, summary: SummaryRecord) -> None:
        self.session_summary[session_id] = summary

    def get_session_summary(self, session_id: str) -> SummaryRecord | None:
        return self.session_summary.get(session_id)

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
