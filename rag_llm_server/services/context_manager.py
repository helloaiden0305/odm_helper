import asyncio
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any

from config import settings
from services.context_store import (
    ContextTurn,
    FactRecord,
    SummaryRecord,
    context_store,
    now_iso,
)
from services.storage_mock import log_mock_storage


LOADING_TEXTS = {
    "XZY 研发测试助手正在分析",
    "助手正在分析",
    "分析中",
}

PROBLEM_MODULES = [
    "蓝牙",
    "Wi-Fi",
    "wifi",
    "WIFI",
    "OTA",
    "升级",
    "相机",
    "音频",
    "通话",
    "定位",
    "网络",
    "功耗",
    "崩溃",
]

LOG_TYPES = [
    "logcat",
    "bt_stack",
    "dumpsys",
    "tombstone",
    "crash",
    "ANR",
    "kernel",
    "main log",
    "radio log",
]

PROBLEM_KEYWORDS = [
    "失败",
    "异常",
    "无声",
    "搜不到",
    "连接不上",
    "断连",
    "卡顿",
    "崩溃",
    "重启",
    "超时",
]

DEVICE_MODEL_PATTERN = re.compile(r"\bX\d{2,4}\b", re.IGNORECASE)
VERSION_PATTERN = re.compile(r"\b(?:V|v)?\d+(?:\.\d+){1,3}\b")
ERROR_CODE_PATTERN = re.compile(r"\b(?:ERR|ERROR|E|0x)[A-Za-z0-9_-]{2,}\b", re.IGNORECASE)


@dataclass
class ContextBuildResult:
    messages: list[dict[str, str]]
    rag_context: str
    stats: dict[str, Any]


@dataclass
class ContextReadResult:
    messages: list[dict[str, str]]
    stats: dict[str, Any]


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / 1.6)


def _safe_ratio(tokens: int) -> float:
    budget = max(settings.CONTEXT_BUDGET_TOKENS, 1)
    return tokens / budget


def _normalize_content(content: str) -> str:
    text = str(content or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _dedupe_key(message: dict[str, str]) -> str:
    content = re.sub(r"\s+", " ", message["content"]).strip().lower()
    return f"{message['role']}:{content}"


def _truncate_content(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content

    marker = "\n...[中间内容已按上下文预算省略，保留首尾关键信息]...\n"
    available = max(max_chars - len(marker), 80)
    head_chars = max(int(available * 0.6), 40)
    tail_chars = max(available - head_chars, 40)
    return f"{content[:head_chars]}{marker}{content[-tail_chars:]}"


def _clean_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    previous_key = ""
    for raw_message in messages:
        role = str(raw_message.get("role") or "").strip()
        if role not in {"user", "assistant", "system"}:
            continue

        content = _normalize_content(str(raw_message.get("content") or ""))
        if not content or content in LOADING_TEXTS:
            continue

        content = _truncate_content(content, settings.CONTEXT_MAX_MESSAGE_CHARS)
        message = {"role": role, "content": content}
        message_key = _dedupe_key(message)
        if message_key == previous_key:
            continue

        cleaned.append(message)
        previous_key = message_key
    return cleaned


def _extract_keywords(text: str, limit: int = 12) -> list[str]:
    keywords: list[str] = []

    for model in DEVICE_MODEL_PATTERN.findall(text):
        keywords.append(model.upper())

    for module in PROBLEM_MODULES:
        if module in text:
            keywords.append("Wi-Fi" if module.lower() == "wifi" else module)

    for log_type in LOG_TYPES:
        if log_type in text:
            keywords.append(log_type)

    for problem in PROBLEM_KEYWORDS:
        if problem in text:
            keywords.append(problem)

    deduped: list[str] = []
    for keyword in keywords:
        if keyword not in deduped:
            deduped.append(keyword)
        if len(deduped) >= limit:
            break
    return deduped


def _to_turns(session_id: str, messages: list[dict[str, str]]) -> list[ContextTurn]:
    turns: list[ContextTurn] = []
    for message in messages:
        turns.append(
            context_store.build_turn(
                session_id=session_id,
                message=message,
                token_estimate=estimate_tokens(message["content"]),
            )
        )
    return turns


def _turns_to_messages(turns: list[ContextTurn]) -> list[dict[str, str]]:
    return [{"role": turn.role, "content": turn.content} for turn in turns]


def _build_fact(session_id: str, source_turn_id: str, key: str, value: str) -> FactRecord:
    return FactRecord(
        fact_id=f"fact_{uuid.uuid4().hex[:8]}",
        session_id=session_id,
        source_turn_id=source_turn_id,
        key=key,
        value=value,
        confidence=0.8,
        created_at=now_iso(),
    )


def _extract_facts(session_id: str, turns: list[ContextTurn]) -> list[FactRecord]:
    facts: list[FactRecord] = []
    for turn in turns:
        content = turn.content

        for model in DEVICE_MODEL_PATTERN.findall(content):
            facts.append(_build_fact(session_id, turn.turn_id, "device_model", model.upper()))

        for version in VERSION_PATTERN.findall(content):
            facts.append(_build_fact(session_id, turn.turn_id, "software_version", version))

        for error_code in ERROR_CODE_PATTERN.findall(content):
            facts.append(_build_fact(session_id, turn.turn_id, "error_code", error_code))

        for module in PROBLEM_MODULES:
            if module in content:
                value = "Wi-Fi" if module.lower() == "wifi" else module
                facts.append(_build_fact(session_id, turn.turn_id, "problem_module", value))

        for log_type in LOG_TYPES:
            if log_type in content:
                facts.append(_build_fact(session_id, turn.turn_id, "log_type", log_type))

        for keyword in PROBLEM_KEYWORDS:
            if keyword in content:
                facts.append(_build_fact(session_id, turn.turn_id, "problem_keyword", keyword))

    return facts


def _first_user_quotes(messages: list[dict[str, str]], limit: int = 3) -> list[str]:
    quotes = [message["content"] for message in messages if message["role"] == "user"]
    return quotes[:limit]


def _last_assistant_notes(messages: list[dict[str, str]], limit: int = 2) -> list[str]:
    notes = [message["content"] for message in messages if message["role"] == "assistant"]
    return notes[-limit:]


def _join_snippets(items: list[str], max_chars: int = 360) -> str:
    if not items:
        return "暂无明确记录"
    text = "；".join(item.replace("\n", " ") for item in items)
    return _truncate_content(text, max_chars)


def _fact_lines(facts: list[FactRecord]) -> list[str]:
    return [f"- {fact.key}: {fact.value}" for fact in facts]


def _build_old_segment_summary(
    session_id: str,
    archives: list,
    token_before: int,
) -> SummaryRecord:
    archive_text = "\n".join(archive.content for archive in archives)
    keywords = _extract_keywords(archive_text, limit=8)
    source_archive_ids = [archive.archive_id for archive in archives]
    source_turn_ids = [turn_id for archive in archives for turn_id in archive.turn_ids]

    summary = "\n".join(
        [
            f"1. 旧历史主题：{', '.join(keywords) if keywords else '暂无明确关键词'}",
            f"2. 用户关键信息：{_truncate_content(archive_text, 220)}",
            "3. 助手建议或结论：保留旧片段中的排查建议，下一轮需要结合最新问题判断是否仍适用。",
            "4. 可能过时内容：若后续用户提供了新机型、版本或故障现象，以后续信息为准。",
            f"5. source_turn_ids / source_archive_ids：{source_turn_ids} / {source_archive_ids}",
        ]
    )

    return SummaryRecord(
        summary_id=f"sum_collapse_{uuid.uuid4().hex[:8]}",
        session_id=session_id,
        source_archive_ids=source_archive_ids,
        source_turn_ids=source_turn_ids,
        summary_type="old_segment_summary",
        summary=summary,
        token_before=token_before,
        token_after=estimate_tokens(summary),
        created_at=now_iso(),
    )


def _build_nine_section_summary(
    session_id: str,
    messages: list[dict[str, str]],
    collapse_summaries: list[SummaryRecord],
    facts: list[FactRecord],
    rag_context: str,
    token_before: int,
) -> SummaryRecord:
    all_text = "\n".join(message["content"] for message in messages)
    keywords = _extract_keywords(all_text + "\n" + rag_context, limit=12)
    user_quotes = _first_user_quotes(messages)
    assistant_notes = _last_assistant_notes(messages)
    fact_text = "\n".join(_fact_lines(facts[-12:])) if facts else "暂无明确结构化事实"
    collapse_text = "\n".join(summary.summary for summary in collapse_summaries[-2:])
    rag_snippet = _truncate_content(rag_context, 360) if rag_context else "本轮未命中明确 RAG 内容"

    summary = "\n".join(
        [
            f"1. 用户原始问题与真实意图：{_join_snippets(user_quotes)}",
            f"2. 已确认的工程环境：{fact_text}",
            f"3. 当前问题模块与关键词：{', '.join(keywords) if keywords else '暂无明确关键词'}",
            f"4. 已召回的知识库依据：{rag_snippet}",
            f"5. 已尝试的排查动作：{_join_snippets(assistant_notes)}",
            "6. 已确认的结论与排除项：仅保留对话和知识库中明确出现的结论，未确认内容不做推断。",
            "7. 待补充信息：如项目名、设备型号、软件版本、日志类型或复现步骤缺失，下一轮优先追问。",
            "8. 当前回复策略：优先围绕 ODM 研发测试场景输出可执行排查步骤。",
            f"9. 下一步计划：结合最新问题、RAG 命中和已记录事实继续排查。T3 摘要参考：{_truncate_content(collapse_text, 240) if collapse_text else '暂无'}",
        ]
    )

    source_turn_ids = []
    source_archive_ids = []
    for collapse_summary in collapse_summaries:
        source_turn_ids.extend(collapse_summary.source_turn_ids)
        source_archive_ids.extend(collapse_summary.source_archive_ids)

    return SummaryRecord(
        summary_id=f"sum_compact_{uuid.uuid4().hex[:8]}",
        session_id=session_id,
        source_archive_ids=list(dict.fromkeys(source_archive_ids)),
        source_turn_ids=list(dict.fromkeys(source_turn_ids)),
        summary_type="odm_9_sections",
        summary=summary,
        token_before=token_before,
        token_after=estimate_tokens(summary),
        created_at=now_iso(),
    )


SUMMARY_MARKER_PATTERN = re.compile(r"^\[\[summary_id:([^\]]+)\]\]$")


def _summary_marker_id(content: str) -> str:
    matched = SUMMARY_MARKER_PATTERN.match((content or "").strip())
    return matched.group(1) if matched else ""


def _summary_message(summary: SummaryRecord) -> dict[str, str]:
    label = "T4 九段结构化摘要" if summary.summary_type == "odm_9_sections" else "T3 旧片段折叠摘要"
    return {"role": "system", "content": f"### {label}\n{summary.summary}"}


class ContextManager:
    def input_limit_message(self, question: str) -> str:
        tokens = estimate_tokens(_normalize_content(question))
        if tokens <= settings.CONTEXT_MAX_INPUT_TOKENS:
            return ""
        return "当前输入内容过长；如需分析 Android Log，请切换至 Log 分析模式上传或粘贴日志。"

    def begin_turn(self, *, session_id: str, question: str, channel: str, trace_id: str) -> str:
        if not settings.CONTEXT_MANAGER_ENABLED:
            return ""

        cleaned_question = _truncate_content(
            _normalize_content(question),
            settings.CONTEXT_MAX_MESSAGE_CHARS,
        )
        if not cleaned_question:
            return ""

        turn_id = context_store.begin_turn(
            session_id=session_id,
            question=cleaned_question,
            channel=channel,
        )
        print(
            f"DEBUG: [{trace_id}] context_turn pending "
            f"session_id={session_id} turn_id={turn_id} channel={channel} "
            f"question_len={len(cleaned_question)}"
        )
        return turn_id

    async def complete_turn_async(self, *, session_id: str, turn_id: str, answer: str, trace_id: str) -> None:
        """Run persistence and governance after the streaming response has completed."""
        await asyncio.sleep(0)
        try:
            await asyncio.to_thread(
                self.complete_turn,
                session_id=session_id,
                turn_id=turn_id,
                answer=answer,
                trace_id=trace_id,
            )
        except Exception as exc:
            print(f"WARN: [{trace_id}] context_background_governance_failed: {exc}")

    def complete_turn(self, *, session_id: str, turn_id: str, answer: str, trace_id: str) -> None:
        if not settings.CONTEXT_MANAGER_ENABLED or not turn_id:
            return

        cleaned_answer = _normalize_content(answer)
        completed_turns = context_store.complete_turn(session_id, turn_id, cleaned_answer)
        if not completed_turns:
            return

        facts_added = context_store.upsert_facts(session_id, _extract_facts(session_id, completed_turns))
        output_tokens = estimate_tokens(cleaned_answer)
        print(
            f"DEBUG: [{trace_id}] context_turn completed "
            f"session_id={session_id} turn_id={turn_id} answer_chars={len(cleaned_answer)} "
            f"answer_tokens={output_tokens} facts_added={facts_added}"
        )
        log_mock_storage(
            target="postgresql",
            operation="persist_conversation_turns",
            session_id=session_id,
            trace_id=trace_id,
            detail=f"turn_id={turn_id}, answer_chars={len(cleaned_answer)}",
        )
        print(f"DEBUG: [{trace_id}] 已落入 Mock PostgreSQL 会话表 session_id={session_id}")
        self._govern_completed_history(session_id=session_id, trace_id=trace_id)

    def close_turn(self, *, session_id: str, turn_id: str, status: str, trace_id: str) -> None:
        if not settings.CONTEXT_MANAGER_ENABLED or not turn_id:
            return

        if context_store.close_turn(session_id, turn_id, status):
            print(
                f"DEBUG: [{trace_id}] context_turn {status} "
                f"session_id={session_id} turn_id={turn_id}"
            )

    def read_context(
        self,
        *,
        session_id: str,
        history: list[dict[str, Any]],
        turn_id: str = "",
        trace_id: str,
    ) -> ContextReadResult:
        if not settings.CONTEXT_MANAGER_ENABLED:
            return ContextReadResult(messages=_clean_messages(history), stats={"enabled": False})

        incoming_history = _clean_messages(history)
        stored_current_turn = next(
            (
                turn
                for turn in context_store.get_active_turns(session_id)
                if turn.turn_id == turn_id and turn.role == "user"
            ),
            None,
        )
        active_history_turns = [
            turn
            for turn in context_store.get_active_turns(session_id)
            if not stored_current_turn or turn.turn_id != stored_current_turn.turn_id
        ]
        seeded_turns = 0
        history_source = "store"
        if not active_history_turns and incoming_history:
            seeded_turns = len(incoming_history)
            seeded_history_turns = _to_turns(session_id, incoming_history)
            if stored_current_turn:
                seeded_history_turns.append(stored_current_turn)
            context_store.set_active_turns(
                session_id,
                seeded_history_turns,
            )
            active_history_turns = [
                turn
                for turn in context_store.get_active_turns(session_id)
                if not stored_current_turn or turn.turn_id != stored_current_turn.turn_id
            ]
            history_source = "input_seed"
            print(
                f"DEBUG: [{trace_id}] context_history seeded "
                f"session_id={session_id} turns={seeded_turns}"
            )
        elif incoming_history:
            print(
                f"DEBUG: [{trace_id}] context_history use_store "
                f"session_id={session_id} ignored_carrier_messages={len(incoming_history)}"
            )

        resolved_messages: list[dict[str, str]] = []
        marker_ids: list[str] = []
        for turn in active_history_turns:
            summary_id = _summary_marker_id(turn.content)
            if not summary_id:
                resolved_messages.append({"role": turn.role, "content": turn.content})
                continue

            marker_ids.append(summary_id)
            print(
                f"DEBUG: [{trace_id}] Context 原文扫描命中 summary_id "
                f"session_id={session_id}, active_epoch={context_store.get_active_epoch(session_id)}, "
                f"summary_id={summary_id}"
            )
            summary = context_store.get_summary(summary_id)
            if summary:
                log_mock_storage(
                    target="redis",
                    operation="read_summary",
                    session_id=session_id,
                    trace_id=trace_id,
                    summary_id=summary_id,
                    detail=f"summary_type={summary.summary_type}",
                )
                resolved_messages.append(_summary_message(summary))
            else:
                log_mock_storage(
                    target="redis",
                    operation="read_summary_fallback",
                    session_id=session_id,
                    trace_id=trace_id,
                    summary_id=summary_id,
                    detail="summary_not_found_in_mock_store",
                )

        print(
            f"DEBUG: [{trace_id}] Context Read 完成 session_id={session_id}, "
            f"active_epoch={context_store.get_active_epoch(session_id)}, "
            f"raw_or_summary_messages={len(resolved_messages)}, marker_count={len(marker_ids)}"
        )
        return ContextReadResult(
            messages=resolved_messages,
            stats={
                "enabled": True,
                "session_id": session_id,
                "active_epoch": context_store.get_active_epoch(session_id),
                "history_source": history_source,
                "seeded_turns": seeded_turns,
                "marker_ids": marker_ids,
            },
        )

    def build_prompt(
        self,
        *,
        context_read: ContextReadResult,
        question: str,
        rag_context: str,
        system_prompt: str,
        trace_id: str,
    ) -> ContextBuildResult:
        cleaned_question = _normalize_content(question)
        messages = list(context_read.messages) + [{"role": "user", "content": cleaned_question}]
        safe_budget = min(
            int(settings.CONTEXT_BUDGET_TOKENS * settings.CONTEXT_PROMPT_SAFE_THRESHOLD),
            max(settings.CONTEXT_BUDGET_TOKENS - settings.CONTEXT_OUTPUT_RESERVE_TOKENS, 1),
        )

        def prompt_tokens() -> int:
            return estimate_tokens("\n".join([system_prompt, rag_context, *[message["content"] for message in messages]]))

        tokens_before = prompt_tokens()
        removed_history_messages = 0
        while prompt_tokens() > safe_budget:
            # Context Read preserves chronological order. Summary messages are compact
            # substitutes for earlier history, so they are trimmed in that same order.
            earliest_history_index = 0 if len(messages) > 1 else None
            if earliest_history_index is None:
                break
            removed = messages.pop(earliest_history_index)
            removed_history_messages += 1
            fragment_type = "摘要片段" if removed["role"] == "system" else "原文片段"
            print(
                f"WARN: [{trace_id}] Context Prompt 临时裁剪最早历史{fragment_type} "
                f"session_id={context_read.stats.get('session_id')}, role={removed['role']}, "
                f"content_chars={len(removed['content'])}"
            )

        tokens_after = prompt_tokens()
        print(
            f"DEBUG: [{trace_id}] Prompt 总预算校验 session_id={context_read.stats.get('session_id')}, "
            f"tokens_before={tokens_before}, tokens_after={tokens_after}, safe_budget={safe_budget}, "
            f"removed_history_messages={removed_history_messages}"
        )
        return ContextBuildResult(
            messages=messages,
            rag_context=rag_context,
            stats={
                **context_read.stats,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "safe_budget": safe_budget,
                "removed_history_messages": removed_history_messages,
            },
        )

    def _govern_completed_history(self, *, session_id: str, trace_id: str) -> None:
        active_turns = context_store.get_active_turns(session_id)
        if not active_turns:
            return

        resolved_messages = self._resolve_governance_messages(session_id, active_turns)
        tokens_before = estimate_tokens("\n".join(message["content"] for message in resolved_messages))
        ratio_before = _safe_ratio(tokens_before)
        layers: list[str] = []
        active_epoch = context_store.get_active_epoch(session_id)

        if ratio_before >= settings.CONTEXT_T1_THRESHOLD:
            layers.append("T1")

        raw_turns = [turn for turn in active_turns if not _summary_marker_id(turn.content)]
        archive = None
        max_recent_messages = max(settings.CONTEXT_RECENT_ROUNDS * 2, 2)
        if ratio_before >= settings.CONTEXT_T2_THRESHOLD and len(raw_turns) > max_recent_messages:
            old_turns = raw_turns[:-max_recent_messages]
            archive = context_store.archive_turns(
                session_id=session_id,
                turns=old_turns,
                keywords=_extract_keywords("\n".join(turn.content for turn in old_turns)),
            )
            if archive:
                layers.append("T2")

        if archive and ratio_before >= settings.CONTEXT_T3_THRESHOLD:
            collapse_summary = _build_old_segment_summary(
                session_id=session_id,
                archives=[archive],
                token_before=archive.token_estimate,
            )
            context_store.add_collapse_summary(session_id, collapse_summary)
            context_store.replace_turns_with_summary_marker(
                session_id=session_id,
                turn_ids=archive.turn_ids,
                summary_id=collapse_summary.summary_id,
            )
            layers.append("T3")
            self._log_summary_persistence(
                session_id=session_id,
                trace_id=trace_id,
                summary=collapse_summary,
                layer="T3",
            )

        active_turns_after_t3 = context_store.get_active_turns(session_id)
        resolved_after_t3 = self._resolve_governance_messages(session_id, active_turns_after_t3)
        tokens_after_t3 = estimate_tokens("\n".join(message["content"] for message in resolved_after_t3))
        ratio_after_t3 = _safe_ratio(tokens_after_t3)
        should_t4 = ratio_after_t3 >= settings.CONTEXT_T4_THRESHOLD or (
            settings.CONTEXT_DEMO_MODE and bool(context_store.get_archives(session_id))
        )
        compact_summary = None
        if should_t4:
            compact_summary = _build_nine_section_summary(
                session_id=session_id,
                messages=resolved_after_t3,
                collapse_summaries=context_store.get_collapse_summaries(session_id),
                facts=context_store.get_facts(session_id),
                rag_context="",
                token_before=tokens_after_t3,
            )
            context_store.set_session_summary(session_id, compact_summary)
            context_store.replace_turns_with_summary_marker(
                session_id=session_id,
                turn_ids=[turn.turn_id for turn in active_turns_after_t3],
                summary_id=compact_summary.summary_id,
            )
            layers.append("T4")
            self._log_summary_persistence(
                session_id=session_id,
                trace_id=trace_id,
                summary=compact_summary,
                layer="T4",
            )

        active_turns_after_t4 = context_store.get_active_turns(session_id)
        resolved_after_t4 = self._resolve_governance_messages(session_id, active_turns_after_t4)
        ratio_after_t4 = _safe_ratio(
            estimate_tokens("\n".join(message["content"] for message in resolved_after_t4))
        )
        if compact_summary and ratio_after_t4 >= settings.CONTEXT_T5_THRESHOLD:
            old_epoch, new_epoch = context_store.rollover_epoch(session_id, compact_summary.summary_id)
            layers.append("T5")
            print(
                f"DEBUG: [{trace_id}] Mock active_epoch 已切换 session_id={session_id}, "
                f"from={old_epoch}, to={new_epoch}"
            )

        print(
            f"DEBUG: [{trace_id}] Context T1/T2/T3/T4/T5 触发结果 "
            f"session_id={session_id}, active_epoch={active_epoch}, layers={','.join(layers) or 'none'}, "
            f"tokens_before={tokens_before}, ratio_before={ratio_before:.2f}"
        )

    def _resolve_governance_messages(
        self,
        session_id: str,
        turns: list[ContextTurn],
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for turn in turns:
            summary_id = _summary_marker_id(turn.content)
            if summary_id:
                summary = context_store.get_summary(summary_id)
                if summary:
                    messages.append(_summary_message(summary))
                continue
            messages.append({"role": turn.role, "content": turn.content})
        return messages

    def _log_summary_persistence(
        self,
        *,
        session_id: str,
        trace_id: str,
        summary: SummaryRecord,
        layer: str,
    ) -> None:
        log_mock_storage(
            target="postgresql",
            operation="persist_context_summary",
            session_id=session_id,
            trace_id=trace_id,
            summary_id=summary.summary_id,
            detail=f"summary_type={layer}, summary_chars={len(summary.summary)}",
        )
        print(
            f"DEBUG: [{trace_id}] 已落入 Mock PostgreSQL 摘要表 "
            f"summary_id={summary.summary_id}, layer={layer}"
        )
        log_mock_storage(
            target="redis",
            operation="cache_context_summary",
            session_id=session_id,
            trace_id=trace_id,
            summary_id=summary.summary_id,
            detail=f"summary_type={layer}, summary_chars={len(summary.summary)}",
        )

context_manager = ContextManager()
