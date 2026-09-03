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


def _build_context_note(session_id: str) -> str:
    parts: list[str] = []
    session_summary = context_store.get_session_summary(session_id)
    collapse_summaries = context_store.get_collapse_summaries(session_id)
    facts = context_store.get_facts(session_id)

    if session_summary:
        parts.append(f"### T4 全量九段结构化摘要\n{session_summary.summary}")
    elif collapse_summaries:
        latest_collapse = collapse_summaries[-1]
        parts.append(f"### T3 旧片段折叠摘要\n{latest_collapse.summary}")

    if facts:
        parts.append("### 关键事实\n" + "\n".join(_fact_lines(facts)))

    if not parts:
        return ""

    return "以下是后端上下文压缩层提供的历史摘要和关键事实，请结合本轮问题与 RAG 内容回答，不要编造未出现的信息。\n\n" + "\n\n".join(parts)


class ContextManager:
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

    def complete_turn(self, *, session_id: str, turn_id: str, answer: str, trace_id: str) -> None:
        if not settings.CONTEXT_MANAGER_ENABLED or not turn_id:
            return

        cleaned_answer = _truncate_content(
            _normalize_content(answer),
            settings.CONTEXT_MAX_MESSAGE_CHARS,
        )
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

    def close_turn(self, *, session_id: str, turn_id: str, status: str, trace_id: str) -> None:
        if not settings.CONTEXT_MANAGER_ENABLED or not turn_id:
            return

        if context_store.close_turn(session_id, turn_id, status):
            print(
                f"DEBUG: [{trace_id}] context_turn {status} "
                f"session_id={session_id} turn_id={turn_id}"
            )

    def build_context(
        self,
        *,
        session_id: str,
        history: list[dict[str, Any]],
        question: str,
        turn_id: str = "",
        rag_context: str,
        trace_id: str,
        system_prompt: str = "",
    ) -> ContextBuildResult:
        if not settings.CONTEXT_MANAGER_ENABLED:
            messages = _clean_messages(history) + [{"role": "user", "content": question}]
            return ContextBuildResult(messages=messages, rag_context=rag_context, stats={"enabled": False})

        incoming_history = _clean_messages(history)
        cleaned_question = _truncate_content(_normalize_content(question), settings.CONTEXT_MAX_MESSAGE_CHARS)
        stored_current_turn = next(
            (
                turn
                for turn in context_store.get_active_turns(session_id)
                if turn.turn_id == turn_id and turn.role == "user"
            ),
            None,
        )
        if stored_current_turn and stored_current_turn.content != cleaned_question:
            stored_current_turn = None

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

        cleaned_history = _turns_to_messages(active_history_turns)
        question_message = (
            {"role": stored_current_turn.role, "content": stored_current_turn.content}
            if stored_current_turn
            else {"role": "user", "content": cleaned_question}
        )
        all_messages = cleaned_history + [question_message]
        tokens_before = estimate_tokens(
            "\n".join([system_prompt, rag_context, *[message["content"] for message in all_messages]])
        )
        ratio_before = _safe_ratio(tokens_before)
        print(
            f"DEBUG: [{trace_id}] context_budget start "
            f"session_id={session_id} history_in={len(cleaned_history)} "
            f"tokens_before={tokens_before} budget={settings.CONTEXT_BUDGET_TOKENS} "
            f"ratio={ratio_before:.2f}"
        )

        layers = ["T1"]
        reason = "under_budget"
        max_recent_messages = max(settings.CONTEXT_RECENT_ROUNDS * 2, 2)
        should_t2 = len(cleaned_history) > max_recent_messages or ratio_before >= settings.CONTEXT_T2_THRESHOLD
        old_messages: list[dict[str, str]] = []
        recent_messages = cleaned_history
        archived_turns = 0

        recent_history_turns = active_history_turns
        if should_t2 and len(cleaned_history) > max_recent_messages:
            old_messages = cleaned_history[:-max_recent_messages]
            recent_messages = cleaned_history[-max_recent_messages:]
            old_turns = active_history_turns[:-max_recent_messages]
            recent_history_turns = active_history_turns[-max_recent_messages:]
            archive = context_store.archive_turns(
                session_id=session_id,
                turns=old_turns,
                keywords=_extract_keywords("\n".join(message["content"] for message in old_messages)),
            )
            archived_turns = len(archive.turn_ids) if archive else 0
            context_store.set_active_turns(
                session_id,
                recent_history_turns + ([stored_current_turn] if stored_current_turn else []),
            )
            layers.append("T2")
            reason = "recent_rounds_exceeded"

        facts_added = context_store.upsert_facts(
            session_id,
            _extract_facts(session_id, recent_history_turns),
        )
        if facts_added:
            print(f"DEBUG: [{trace_id}] context_facts upsert session_id={session_id} added={facts_added}")

        archives = context_store.get_archives(session_id)
        if archives and ratio_before >= settings.CONTEXT_T3_THRESHOLD:
            collapse_source = archives[: min(len(archives), 2)]
            collapse_summary = _build_old_segment_summary(
                session_id=session_id,
                archives=collapse_source,
                token_before=sum(archive.token_estimate for archive in collapse_source),
            )
            context_store.add_collapse_summary(session_id, collapse_summary)
            layers.append("T3")
            reason = "collapse_old_segments"
            print(
                f"DEBUG: [{trace_id}] context_collapse generated "
                f"session_id={session_id} template=old_segment_summary "
                f"source_turns={len(collapse_summary.source_turn_ids)} "
                f"summary_chars={len(collapse_summary.summary)}"
            )

        should_t4 = ratio_before >= settings.CONTEXT_T4_THRESHOLD or (
            settings.CONTEXT_DEMO_MODE and bool(archives)
        )
        if should_t4:
            all_facts = context_store.get_facts(session_id)
            collapse_summaries = context_store.get_collapse_summaries(session_id)
            compact_summary = _build_nine_section_summary(
                session_id=session_id,
                messages=cleaned_history,
                collapse_summaries=collapse_summaries,
                facts=all_facts,
                rag_context=rag_context,
                token_before=tokens_before,
            )
            context_store.set_session_summary(session_id, compact_summary)
            layers.append("T4")
            reason = "compact_high_watermark" if ratio_before >= settings.CONTEXT_T4_THRESHOLD else "compact_demo_mode"
            print(
                f"DEBUG: [{trace_id}] context_compact generated "
                f"session_id={session_id} template=odm_9_sections "
                f"source_turns={len(compact_summary.source_turn_ids)} "
                f"facts={len(all_facts)} summary_chars={len(compact_summary.summary)}"
            )

        context_note = _build_context_note(session_id)
        compacted_messages = []
        if context_note:
            compacted_messages.append({"role": "system", "content": context_note})
        compacted_messages.extend(recent_messages)
        compacted_messages.append(question_message)

        rag_context_out = rag_context
        tokens_after = estimate_tokens(
            "\n".join([system_prompt, rag_context_out, *[message["content"] for message in compacted_messages]])
        )

        if tokens_after >= settings.CONTEXT_BUDGET_TOKENS * settings.CONTEXT_T5_THRESHOLD:
            layers.append("T5")
            reason = "budget_exceeded"
            target_chars = max(int(settings.CONTEXT_BUDGET_TOKENS * settings.CONTEXT_T5_THRESHOLD * 1.2), 480)
            context_chars = max(int(target_chars * 0.35), 180)
            rag_chars = max(int(target_chars * 0.25), 120)
            message_chars = max(int(target_chars * 0.40), 180)

            recent_messages = recent_messages[-4:]
            per_message_chars = max(int(message_chars / max(len(recent_messages) + 1, 1)), 48)
            recent_messages = [
                {"role": message["role"], "content": _truncate_content(message["content"], per_message_chars)}
                for message in recent_messages
            ]
            question_message = {
                "role": "user",
                "content": _truncate_content(question_message["content"], per_message_chars),
            }
            rag_context_out = _truncate_content(rag_context_out, rag_chars)
            if context_note:
                context_note = _truncate_content(context_note, context_chars)
            compacted_messages = []
            if context_note:
                compacted_messages.append({"role": "system", "content": context_note})
            compacted_messages.extend(recent_messages)
            compacted_messages.append(question_message)
            tokens_after = estimate_tokens(
                "\n".join([system_prompt, rag_context_out, *[message["content"] for message in compacted_messages]])
            )
            if tokens_after >= settings.CONTEXT_BUDGET_TOKENS * settings.CONTEXT_T5_THRESHOLD:
                recent_messages = recent_messages[-2:]
                compacted_messages = [{"role": "user", "content": question_message["content"]}]
                if context_note:
                    compacted_messages.insert(0, {"role": "system", "content": _truncate_content(context_note, 180)})
                rag_context_out = _truncate_content(rag_context_out, 120)
                tokens_after = estimate_tokens(
                    "\n".join([system_prompt, rag_context_out, *[message["content"] for message in compacted_messages]])
                )
            print(
                f"WARN: [{trace_id}] context_fallback applied "
                f"session_id={session_id} layer=T5 reason=budget_exceeded "
                f"history_out={len(recent_messages)} tokens_after={tokens_after}"
            )

        if len(layers) == 1 and tokens_before < settings.CONTEXT_BUDGET_TOKENS * settings.CONTEXT_T2_THRESHOLD:
            print(
                f"DEBUG: [{trace_id}] context_compact skip "
                f"session_id={session_id} reason=under_budget "
                f"history_in={len(cleaned_history)} history_out={len(compacted_messages)} "
                f"tokens_before={tokens_before} tokens_after={tokens_after} "
                f"ratio={ratio_before:.2f}"
            )
        else:
            print(
                f"DEBUG: [{trace_id}] context_compact applied "
                f"session_id={session_id} layers={','.join(layers)} reason={reason} "
                f"history_in={len(cleaned_history)} history_out={len(compacted_messages)} "
                f"archived_turns={archived_turns} tokens_before={tokens_before} tokens_after={tokens_after}"
            )

        return ContextBuildResult(
            messages=compacted_messages,
            rag_context=rag_context_out,
            stats={
                "enabled": True,
                "session_id": session_id,
                "layers": layers,
                "reason": reason,
                "history_in": len(cleaned_history),
                "history_out": len(compacted_messages),
                "history_source": history_source,
                "seeded_turns": seeded_turns,
                "archived_turns": archived_turns,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "ratio": ratio_before,
            },
        )

context_manager = ContextManager()
