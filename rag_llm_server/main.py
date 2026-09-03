import asyncio
import uuid
import time
import base64
import hashlib
import httpx
import struct
import uvicorn
from dataclasses import dataclass
from urllib.parse import urlencode
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any

from config import settings
from services.llm_service import llm_service
from services.session_store import session_store
from services.token_build import AccessToken, PRIVILEGES
from services.utils import Signer  # 确保 utils.py 已移动到 services 目录
from services.context_manager import context_manager

from fastapi.responses import JSONResponse

from fastapi import Request
from fastapi.responses import StreamingResponse  # <--- 必须导入这个
import json
from services.rag_service import rag_service  # <--- 新增这行

# 在你的 settings.py 或 main.py 顶部
from dotenv import load_dotenv

load_dotenv()  # 必须先执行这一行，后面的 settings 才能读到值

app = FastAPI()

WELCOME_MESSAGE = "你好，我是 XZY 研发测试助手，可以查 SOP、排查软硬件问题、检索历史缺陷并给出处理建议。"
SYSTEM_PROMPT = "你是 XZY 研发测试助手，面向 ODM 内部工程问题处理场景。你需要根据知识库内容回答测试规范、SOP、软硬件常见问题、历史缺陷和日志分析相关问题。回答要简洁、可执行；如果信息不足，需要先追问项目名、设备型号、软件版本或问题模块，不要编造不存在的处理结论。"
DEFAULT_ROOM_ID = "XzyDemoRoom"
DEFAULT_USER_ID = "XzyTester"
DEFAULT_AGENT_ID = "XzyAgent"
DEFAULT_TASK_ID = "XzyDemoTask"
CONFIRMED_VOICE_QUESTION_TTL_SECONDS = 30

voice_text_clients: Dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}


@dataclass
class ConfirmedVoiceQuestion:
    expires_at: float
    turn_id: str


confirmed_voice_questions: Dict[str, dict[str, ConfirmedVoiceQuestion]] = {}


def build_session_key(room_id: str, user_id: str) -> str:
    return f"{room_id}:{user_id}"


async def publish_voice_text_event(session_key: str, event: dict[str, Any]):
    clients = list(voice_text_clients.get(session_key, set()))
    for queue in clients:
        await queue.put(event)


def normalize_voice_question(question: str) -> str:
    return " ".join((question or "").split())


def voice_question_key(question: str) -> str:
    return hashlib.sha256(normalize_voice_question(question).encode("utf-8")).hexdigest()


def consume_confirmed_voice_question(session_key: str, question: str) -> str:
    if not session_key or not question:
        return ""

    question_hash = voice_question_key(question)
    session_questions = confirmed_voice_questions.get(session_key)
    if not session_questions:
        return ""

    now = time.time()
    for key, confirmed_question in list(session_questions.items()):
        if confirmed_question.expires_at < now:
            session_questions.pop(key, None)

    if question_hash not in session_questions:
        if not session_questions:
            confirmed_voice_questions.pop(session_key, None)
        return ""

    confirmed_question = session_questions.pop(question_hash)
    if not session_questions:
        confirmed_voice_questions.pop(session_key, None)
    return confirmed_question.turn_id


def _read_uint16(buffer: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<H", buffer, offset)[0], offset + 2


def _read_uint32(buffer: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<I", buffer, offset)[0], offset + 4


def _read_bytes(buffer: bytes, offset: int) -> tuple[bytes, int]:
    size, offset = _read_uint16(buffer, offset)
    return buffer[offset : offset + size], offset + size


def _read_string(buffer: bytes, offset: int) -> tuple[str, int]:
    value, offset = _read_bytes(buffer, offset)
    return value.decode("utf-8"), offset


def _read_privileges(buffer: bytes, offset: int) -> tuple[dict[int, int], int]:
    size, offset = _read_uint16(buffer, offset)
    privileges = {}
    for _ in range(size):
        key, offset = _read_uint16(buffer, offset)
        value, offset = _read_uint32(buffer, offset)
        privileges[key] = value
    return privileges, offset


def summarize_rtc_token(token: str) -> dict[str, Any]:
    summary = {
        "fingerprint": hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:12],
        "length": len(token or ""),
        "parse_ok": False,
    }
    try:
        if not token.startswith("001") or len(token) <= 27:
            return summary
        summary["app_id"] = token[3:27]
        content = base64.b64decode(token[27:])
        message, offset = _read_bytes(content, 0)
        signature, _ = _read_bytes(content, offset)

        msg_offset = 0
        nonce, msg_offset = _read_uint32(message, msg_offset)
        issued_at, msg_offset = _read_uint32(message, msg_offset)
        expire_at, msg_offset = _read_uint32(message, msg_offset)
        room_id, msg_offset = _read_string(message, msg_offset)
        user_id, msg_offset = _read_string(message, msg_offset)
        privileges, msg_offset = _read_privileges(message, msg_offset)

        summary.update(
            {
                "parse_ok": True,
                "nonce": nonce,
                "issued_at": issued_at,
                "expire_at": expire_at,
                "room_id": room_id,
                "user_id": user_id,
                "privileges": privileges,
                "signature_length": len(signature),
            }
        )
    except Exception as exc:
        summary["parse_error"] = str(exc)
    return summary


def log_rtc_token_summary(source: str, token: str):
    summary = summarize_rtc_token(token)
    app_key = (settings.RTC_APP_KEY or "").strip()
    print(
        "DEBUG: RTC Token summary "
        f"source={source}, "
        f"fingerprint={summary.get('fingerprint')}, "
        f"length={summary.get('length')}, "
        f"parse_ok={summary.get('parse_ok')}, "
        f"app_id={summary.get('app_id')}, "
        f"room_id={summary.get('room_id')}, "
        f"user_id={summary.get('user_id')}, "
        f"expire_at={summary.get('expire_at')}, "
        f"privileges={summary.get('privileges')}, "
        f"signature_length={summary.get('signature_length')}, "
        f"app_key_length={len(app_key)}, "
        f"app_key_fingerprint={hashlib.sha256(app_key.encode('utf-8')).hexdigest()[:12] if app_key else None}"
    )


def build_rtc_token(room_id: str, user_id: str) -> tuple[str, str]:
    token_mode = (settings.RTC_TOKEN_MODE or "auto").lower()
    use_temp_token = token_mode == "temp"
    app_id = (settings.RTC_APP_ID or "").strip()
    app_key = (settings.RTC_APP_KEY or "").strip()

    if not use_temp_token and app_id and app_key:
        expire_at = int(time.time()) + 3600 * 24
        token_builder = AccessToken(
            app_id, app_key, room_id, user_id
        )
        token_builder.add_privilege(PRIVILEGES["PrivSubscribeStream"], expire_at)
        token_builder.add_privilege(PRIVILEGES["PrivPublishStream"], expire_at)
        token_builder.expire_time(expire_at)
        return token_builder.serialize(), "app_key"

    if settings.RTC_TOKEN:
        return settings.RTC_TOKEN, "temp_token"

    return "your_rtc_temp_token_or_key", "placeholder"


def resolve_rtc_identity() -> tuple[str, str, str]:
    token_mode = (settings.RTC_TOKEN_MODE or "auto").lower()
    if settings.RTC_DYNAMIC_SESSION and token_mode != "temp":
        suffix = uuid.uuid4().hex[:8]
        return f"XzyRoom_{suffix}", f"XzyUser_{suffix}", "dynamic"

    return (
        settings.RTC_ROOM_ID or DEFAULT_ROOM_ID,
        settings.RTC_USER_ID or DEFAULT_USER_ID,
        "fixed",
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- 1. 获取场景 (前端展示用) ---
@app.post("/getScenes")
async def get_scenes(request: Request):
    room_id, user_id, identity_source = resolve_rtc_identity()

    token, token_source = build_rtc_token(room_id, user_id)
    print(
        f"DEBUG: RTC identity source={identity_source}, "
        f"Token source={token_source}, RoomId={room_id}, UserId={user_id}"
    )
    log_rtc_token_summary(token_source, token)
    if settings.RTC_DEBUG_PRINT_TOKEN:
        print("DEBUG: RTC Token validation payload")
        print(f"DEBUG: RTC_TOKEN={token}")
        print(f"DEBUG: RTC_ROOM_ID={room_id}")
        print(f"DEBUG: RTC_USER_ID={user_id}")

    # 构造返回结构
    return {
        "ResponseMetadata": {"Action": "getScenes"},
        "Result": {
            "scenes": [
                {
                    "scene": {
                        # --- 补全的核心字段 ---
                        "id": "Custom",  # 建议改为 Custom，通常前端会根据这个 ID 做特殊处理
                        "name": "XZY ODM研发测试助手",
                        "botName": DEFAULT_AGENT_ID,
                        "icon": "/favicon.png",
                        # --- 功能开关 ---
                        "isInterruptMode": True,  # 是否支持打断
                        "isVision": False,  # 补全：是否开启视觉（摄像头）
                        "isScreenMode": False,  # 补全：是否开启屏幕共享
                        # --- 数字人相关 (无数字人时设为 None/null) ---
                        "isAvatarScene": None,
                        "avatarBgUrl": None,
                    },
                    "rtc": {
                        "AppId": settings.RTC_APP_ID or "your_rtc_app_id",
                        "RoomId": room_id,
                        "UserId": user_id,
                        "Token": token,
                    },
                    # 这里的配置主要是为了兼容前端透传，实际生效主要看 proxy
                    "VoiceChat": {},
                }
            ]
        },
    }


# --- 2. 拦截前端的 StartVoiceChat 请求 (核心配置下发) ---
# main.py 核心修改
# rag_llm_server/main.py


@app.post("/proxy")
async def proxy(request: Request):
    """
    完全硬编码的代理接口，用于测试链路是否畅通
    """
    action = request.query_params.get("Action")
    version = request.query_params.get("Version", "2024-12-01")
    trace_id = uuid.uuid4().hex[:8]
    incoming_body = {}

    # 打印前端实际传过来的数据，方便观察
    try:
        incoming_body = await request.json()
        print(f"DEBUG: [{trace_id}] 收到前端请求 {action}, Body: {incoming_body}")
    except:
        pass

    # --- 开始硬编码数据 ---
    target_app_id = settings.RTC_APP_ID or "your_rtc_app_id"
    target_room_id = incoming_body.get("RoomId") or settings.RTC_ROOM_ID or DEFAULT_ROOM_ID
    target_user_id = incoming_body.get("UserId") or settings.RTC_USER_ID or DEFAULT_USER_ID
    conversation_id = str(incoming_body.get("ConversationId") or "").strip()

    request_body = {}

    callback_query = urlencode({"room_id": target_room_id, "user_id": target_user_id})
    callback_url = f"{settings.SERVER_URL}/api/chat_callback?{callback_query}"
    print(f"DEBUG: [{trace_id}] RTC callback {callback_url}")

    if action == "StartVoiceChat":
        active_task_id = f"{DEFAULT_TASK_ID}-{uuid.uuid4().hex[:8]}"
        session_key = session_store.set_task_id(target_room_id, target_user_id, active_task_id)
        session_store.set_conversation_id(target_room_id, target_user_id, conversation_id)
        print(f"DEBUG: [{trace_id}] Session task stored key={session_key}, task_id={active_task_id}")
        print(
            f"DEBUG: [{trace_id}] Context conversation bound "
            f"key={session_key}, conversation_id={conversation_id or 'voice_fallback'}"
        )
        request_body = {
            "AppId": target_app_id,
            "RoomId": target_room_id,
            "TaskId": active_task_id,
            "AgentConfig": {
                "TargetUserId": [target_user_id],
                "WelcomeMessage": WELCOME_MESSAGE,
                "UserId": DEFAULT_AGENT_ID,
                "EnableConversationStateCallback": True, 
            },
            "Config": {
                "ASRConfig": {
                    "Provider": "volcano",
                    "ProviderParams": {
                        "Mode": "smallmodel",
                        "AppId": settings.ASR_APP_ID or "your_asr_app_id",
                        "Cluster": "volcengine_streaming_common",
                    },
                },
                "TTSConfig": {
                    "Provider": "volcano",
                    "ProviderParams": {
                        "app": {"appid": settings.TTS_APP_ID or "your_tts_app_id", "cluster": "volcano_tts"},
                        "audio": {
                            "voice_type": "BV001_streaming",
                            "speed_ratio": settings.TTS_SPEED_RATIO,
                            "pitch_ratio": 1,
                            "volume_ratio": 1,
                        },
                    },
                },
                "LLMConfig": {
                    # 先用 Custom 模式测试你的回调地址
                    "Mode": "CustomLLM",
                    "Url": callback_url,
                    "Method": "POST",
                    "ApiType": "https"
                    if str(settings.SERVER_URL).startswith("https")
                    else "http",
                },
                "InterruptMode": 0,
            },
        }
    elif action == "StopVoiceChat":
        active_task_id = session_store.pop_task_id(
            target_room_id,
            target_user_id,
            DEFAULT_TASK_ID,
        )
        print(f"DEBUG: [{trace_id}] Session task popped task_id={active_task_id}")
        request_body = {
            "AppId": target_app_id,
            "RoomId": target_room_id,
            "TaskId": active_task_id,
        }
    else:
        # 其他 Action 直接返回前端传的内容
        request_body = incoming_body

    # --- 签名与发送 ---
    host = "rtc.volcengineapi.com"
    open_api_request_data = {
        "method": "POST",
        "path": "/",
        "params": {"Action": action, "Version": version},
        "headers": {"Host": host, "Content-Type": "application/json"},
        "body": request_body,
    }

    # 这里的 AK/SK 必须拥有调用 RTC OpenAPI 的权限
    account_config = {"accessKeyId": settings.VOLC_AK, "secretKey": settings.VOLC_SK}

    signer = Signer(open_api_request_data, "rtc")
    signer.add_authorization(account_config)
    print(
        f"DEBUG: [{trace_id}] OpenAPI signature prepared "
        f"ak_prefix={(settings.VOLC_AK or '')[:4] or 'missing'}, "
        f"body_bytes={len(signer.body_bytes)}, "
        f"body_sha={open_api_request_data['headers'].get('X-Content-Sha256')}"
    )

    url = f"https://{host}?Action={action}&Version={version}"

    # print(f"DEBUG: 发送请求到 {url} callback rtc")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            headers=open_api_request_data["headers"],
            content=signer.body_bytes,
            timeout=settings.RTC_OPENAPI_TIMEOUT,
        )
        result = resp.json()
        print(f"DEBUG: [{trace_id}] 火山引擎返回结果: {result}")
        return result


# --- 3. 业务回调接口 (RTC -> 这里) ---


# ... 其他代码 ...


@app.get("/api/voice_text_stream")
async def voice_text_stream(room_id: str, user_id: str):
    session_key = build_session_key(room_id, user_id)

    async def generate_voice_text_events():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        clients = voice_text_clients.setdefault(session_key, set())
        clients.add(queue)
        try:
            yield f"data: {json.dumps({'type': 'ready'}, ensure_ascii=False)}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            clients.discard(queue)
            if not clients:
                voice_text_clients.pop(session_key, None)

    return StreamingResponse(
        generate_voice_text_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.post("/api/chat_callback")
async def chat_callback(request: Request):
    try:
        data = await request.json()
    except:
        return {"text": ""}

    print(f"======================== 流式请求", data)

    messages = data.get("messages", [])
    room_id = request.query_params.get("room_id", "")
    user_id = request.query_params.get("user_id", "")
    session_key = build_session_key(room_id, user_id) if room_id and user_id else ""
    conversation_id = session_store.get_conversation_id(room_id, user_id) if session_key else ""
    context_session_id = conversation_id or (f"voice:{session_key}" if session_key else "voice_default")
    preview_id = uuid.uuid4().hex[:8]

    # 校验逻辑 (保持不变)
    if not messages or messages[-1].get("role") != "user":
        print("⚠️ 忽略：非用户主动发言")
        return {"text": ""}

    latest_user_text = (messages[-1].get("content") or "").strip()
    is_welcome_message = latest_user_text == "欢迎语"
    input_limit_message = context_manager.input_limit_message(latest_user_text)
    if input_limit_message and not is_welcome_message:
        print(f"WARN: [{preview_id}] 语音输入预检拦截 text_len={len(latest_user_text)}")
        return JSONResponse(status_code=413, content={"text": input_limit_message})

    voice_turn_id = consume_confirmed_voice_question(session_key, latest_user_text)
    if settings.VOICE_CONFIRM_MODE and session_key and not is_welcome_message and not voice_turn_id:
        print(
            f"DEBUG: 语音待确认 preview_id={preview_id}, "
            f"session={session_key}, text_len={len(latest_user_text)}"
        )
        await publish_voice_text_event(
            session_key,
            {
                "type": "pending",
                "id": preview_id,
                "content": latest_user_text,
                "createdAt": int(time.time() * 1000),
            },
        )

        return StreamingResponse(
            iter(["data: [DONE]\n\n"]),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )

    if not voice_turn_id and not is_welcome_message:
        voice_turn_id = context_manager.begin_turn(
            session_id=context_session_id,
            question=latest_user_text,
            channel="voice",
            trace_id=preview_id,
        )

    # --- 定义 SSE 生成器 ---
    async def generate_sse():
        preview_started = False
        answer_parts: list[str] = []
        stream_succeeded = False

        try:
            rag_started_at = time.time()
            rag_task = asyncio.create_task(rag_service.retrieve(latest_user_text))
            context_task = asyncio.create_task(
                asyncio.to_thread(
                    context_manager.read_context,
                    session_id=context_session_id,
                    history=messages[:-1],
                    turn_id=voice_turn_id,
                    trace_id=preview_id,
                )
            )
            rag_content, context_read = await asyncio.gather(rag_task, context_task)
            print(f"DEBUG: [{preview_id}] 语音 RAG 与 Context Read 并行耗时: {time.time() - rag_started_at:.2f}s")
            context_result = context_manager.build_prompt(
                context_read=context_read,
                question=latest_user_text,
                rag_context=rag_content,
                trace_id=preview_id,
                system_prompt=SYSTEM_PROMPT,
            )

            stream_iterator = llm_service.chat_stream(
                context_result.messages,
                context_result.rag_context,
            )
            if session_key:
                await publish_voice_text_event(
                    session_key,
                    {"type": "start", "id": preview_id, "createdAt": int(time.time() * 1000)},
                )
                preview_started = True

            for chunk in stream_iterator:
                if chunk:
                    # Ark SDK 的 chunk 是一个对象 (ChatCompletionChunk)
                    # 直接转成 JSON 字符串后按 OpenAI 兼容 SSE 格式返回给 RTC。
                    chunk_json = chunk.model_dump_json()
                    choices = getattr(chunk, "choices", None)
                    if session_key and choices:
                        delta = choices[0].delta
                        content = getattr(delta, "content", None)
                        if content:
                            answer_parts.append(content)
                            await publish_voice_text_event(
                                session_key,
                                {"type": "delta", "id": preview_id, "content": content},
                            )

                    yield f"data: {chunk_json}\n\n"
            stream_succeeded = bool(answer_parts)
        except asyncio.CancelledError:
            if voice_turn_id:
                context_manager.close_turn(
                    session_id=context_session_id,
                    turn_id=voice_turn_id,
                    status="interrupted",
                    trace_id=preview_id,
                )
            raise
        except Exception as exc:
            print(f"ERROR: 语音回调流式输出失败 preview_id={preview_id}: {exc}")
            if session_key and preview_started:
                await publish_voice_text_event(
                    session_key,
                    {"type": "error", "id": preview_id, "content": "助手暂时没有返回，请稍后重试。"},
                )
        finally:
            try:
                if voice_turn_id and stream_succeeded:
                    asyncio.create_task(
                        context_manager.complete_turn_async(
                            session_id=context_session_id,
                            turn_id=voice_turn_id,
                            answer="".join(answer_parts),
                            trace_id=preview_id,
                        )
                    )
                elif voice_turn_id:
                    context_manager.close_turn(
                        session_id=context_session_id,
                        turn_id=voice_turn_id,
                        status="failed",
                        trace_id=preview_id,
                    )
            except Exception as exc:
                print(f"WARN: 语音上下文状态回填失败 preview_id={preview_id}: {exc}")
            if session_key and preview_started:
                await publish_voice_text_event(session_key, {"type": "end", "id": preview_id})
        yield "data: [DONE]\n\n"

    # --- 返回流式响应 ---
    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",  # <--- 必须是这个 Header
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 如果存在跨域问题，可以加上 Access-Control-Allow-Origin
            "Access-Control-Allow-Origin": "*",
        },
    )


from typing import List, Optional


# 1. 定义消息模型
class ChatMessage(BaseModel):
    role: str  # "user" 或 "assistant"
    content: str


class DebugRequest(BaseModel):
    history: Optional[List[ChatMessage]] = []
    question: str
    session_id: Optional[str] = None


class ConfirmVoiceQuestionRequest(BaseModel):
    room_id: str
    user_id: str
    question: str


@app.post("/api/confirm_voice_question")
async def confirm_voice_question(request: ConfirmVoiceQuestionRequest):
    question = normalize_voice_question(request.question)
    if not request.room_id or not request.user_id or not question:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "room_id、user_id 和 question 不能为空"},
        )

    input_limit_message = context_manager.input_limit_message(question)
    if input_limit_message:
        return JSONResponse(status_code=413, content={"ok": False, "message": input_limit_message})

    session_key = build_session_key(request.room_id, request.user_id)
    conversation_id = session_store.get_conversation_id(request.room_id, request.user_id)
    context_session_id = conversation_id or f"voice:{session_key}"
    trace_id = uuid.uuid4().hex[:8]
    turn_id = context_manager.begin_turn(
        session_id=context_session_id,
        question=question,
        channel="voice",
        trace_id=trace_id,
    )
    confirmation_turn_id = turn_id or f"confirm_{uuid.uuid4().hex[:8]}"
    session_questions = confirmed_voice_questions.setdefault(session_key, {})
    session_questions[voice_question_key(question)] = ConfirmedVoiceQuestion(
        expires_at=time.time() + CONFIRMED_VOICE_QUESTION_TTL_SECONDS,
        turn_id=confirmation_turn_id,
    )
    print(
        f"DEBUG: 语音问题确认登记 session={session_key}, "
        f"context_session_id={context_session_id}, turn_id={turn_id}, "
        f"text_len={len(question)}, pending_count={len(session_questions)}"
    )
    return {"ok": True}


@app.post("/api/text_chat")
async def text_chat(request: DebugRequest):
    question = request.question.strip()
    if not question:
        return {"answer": "", "references": []}

    current_messages = []
    for msg in request.history or []:
        current_messages.append({"role": msg.role, "content": msg.content})

    current_messages.append({"role": "user", "content": question})

    start_t = time.time()
    rag_content = await rag_service.retrieve(question)
    rag_duration = time.time() - start_t
    print(f"DEBUG: 文字问答知识库查询耗时: {rag_duration:.2f}s")

    answer = ""
    stream = llm_service.chat_stream(current_messages, rag_content)
    for chunk in stream:
        if chunk and chunk.choices:
            delta = chunk.choices[0].delta
            if delta.content:
                answer += delta.content

    return {"answer": answer, "references": []}


@app.post("/api/text_chat_stream")
async def text_chat_stream(request: DebugRequest):
    question = request.question.strip()
    trace_id = uuid.uuid4().hex[:8]
    if not question:
        return StreamingResponse(iter([""]), media_type="text/plain; charset=utf-8")

    input_limit_message = context_manager.input_limit_message(question)
    if input_limit_message:
        print(f"WARN: [{trace_id}] 文字输入预检拦截 question_len={len(question)}")
        return StreamingResponse(iter([input_limit_message]), media_type="text/plain; charset=utf-8")

    history_messages = []
    for msg in request.history or []:
        history_messages.append({"role": msg.role, "content": msg.content})

    request_start = time.time()
    session_id = request.session_id or settings.CONTEXT_SESSION_ID
    turn_id = context_manager.begin_turn(
        session_id=session_id,
        question=question,
        channel="text",
        trace_id=trace_id,
    )
    print(
        f"DEBUG: [{trace_id}] 文字流式请求开始 "
        f"session_id={session_id}, turn_id={turn_id}, question_len={len(question)}, "
        f"history_count={len(request.history or [])}"
    )

    async def generate_text_stream():
        output_chars = 0
        first_chunk_sent = False
        answer_parts: list[str] = []
        stream_succeeded = False

        try:
            parallel_started_at = time.time()
            rag_task = asyncio.create_task(rag_service.retrieve(question))
            context_task = asyncio.create_task(
                asyncio.to_thread(
                    context_manager.read_context,
                    session_id=session_id,
                    history=history_messages,
                    turn_id=turn_id,
                    trace_id=trace_id,
                )
            )
            rag_content, context_read = await asyncio.gather(rag_task, context_task)
            print(
                f"DEBUG: [{trace_id}] 文字 RAG 与 Context Read 并行耗时: "
                f"{time.time() - parallel_started_at:.2f}s"
            )
            context_result = context_manager.build_prompt(
                context_read=context_read,
                question=question,
                rag_context=rag_content,
                trace_id=trace_id,
                system_prompt=SYSTEM_PROMPT,
            )

            stream = llm_service.chat_stream(context_result.messages, context_result.rag_context)
            for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue

                delta = choices[0].delta
                content = getattr(delta, "content", None)
                if not content:
                    continue

                if not first_chunk_sent:
                    first_chunk_sent = True
                    first_chunk_duration = time.time() - request_start
                    print(f"DEBUG: [{trace_id}] 文字流式首 chunk 耗时: {first_chunk_duration:.2f}s")

                output_chars += len(content)
                answer_parts.append(content)
                yield content
            stream_succeeded = bool(answer_parts)
        except asyncio.CancelledError:
            if turn_id:
                context_manager.close_turn(
                    session_id=session_id,
                    turn_id=turn_id,
                    status="interrupted",
                    trace_id=trace_id,
                )
            raise
        except Exception as exc:
            print(f"ERROR: [{trace_id}] 文字流式输出失败: {exc}")
            yield "助手暂时没有返回，请稍后重试。"
        finally:
            if turn_id and stream_succeeded:
                asyncio.create_task(
                    context_manager.complete_turn_async(
                        session_id=session_id,
                        turn_id=turn_id,
                        answer="".join(answer_parts),
                        trace_id=trace_id,
                    )
                )
            elif turn_id:
                context_manager.close_turn(
                    session_id=session_id,
                    turn_id=turn_id,
                    status="failed",
                    trace_id=trace_id,
                )
            total_duration = time.time() - request_start
            print(
                f"DEBUG: [{trace_id}] 文字流式请求结束 "
                f"total={total_duration:.2f}s, output_chars={output_chars}"
            )

    return StreamingResponse(
        generate_text_stream(),
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# 2. 调试接口
@app.post("/debug/chat")
async def debug_chat(request: DebugRequest):


    # 构造当前发送给 LLM 的消息列表
    current_messages = []
    for msg in request.history:
        current_messages.append({"role": msg.role, "content": msg.content})

    # 放入用户最新问题
    current_messages.append({"role": "user", "content": request.question})

    async def generate_text():
        full_ai_response = ""
        total_usage = None

            # 1. 记录总开始时间
        start_t = time.time()
        # 查询知识库
        rag_content = await rag_service.retrieve(request.question)

        rag_duration = time.time() - start_t

        print(f"DEBUG: 知识库查询耗时: {rag_duration:.2f}s")
        # print(f"DEBUG: 知识库返回检索内容: {rag_content}")

        # 2. 记录 LLM 调用开始时间
        llm_start_t = time.time()

        # 调用 llm_service
        stream = llm_service.chat_stream(current_messages, rag_content)

        for chunk in stream:
            if chunk and chunk.choices:
                delta = chunk.choices[0].delta
                if delta.content:
                    content = delta.content
                    full_ai_response += content  # 累积 AI 的回答
                    yield content
            # 记录 Token 消耗
            if hasattr(chunk, "usage") and chunk.usage:
                total_usage = chunk.usage

        # 3. 记录 LLM 调用耗时
        llm_duration = time.time() - llm_start_t
        print(f"DEBUG: LLM 调用耗时: {llm_duration:.2f}s")

        if total_usage:
            print(
                f"🎫 Token 统计: Total={total_usage.total_tokens} (P:{total_usage.prompt_tokens}, C:{total_usage.completion_tokens})"
            )

        # --- 重点：在流结束后构造并打印 history 结构 ---
        # 构造完整的 history 列表
        new_history = []
        # 添加旧历史
        for m in request.history:
            new_history.append({"role": m.role, "content": m.content})
        # 添加最新的一轮对话
        new_history.append({"role": "user", "content": request.question})
        new_history.append({"role": "assistant", "content": full_ai_response})

        # 打印到控制台，方便你直接复制
        print("\n" + "=" * 50)
        print("🐞 调试完成！以下是可用于下次请求的 history 结构：")
        print(json.dumps({"history": new_history}, ensure_ascii=False, indent=2))
        print("=" * 50 + "\n")

    return StreamingResponse(generate_text(), media_type="text/plain")


# ... 其他导入保持不变 ...
from services.rag_service import rag_service  # 确保已导入 rag_service


# --- 新增：知识库调试接口 ---
@app.get("/debug/rag")
async def debug_rag(query: str):
    """
    调试接口：直接返回知识库检索到的原始文本内容
    用法：浏览器访问 http://127.0.0.1:8000/debug/rag?query=你的问题
    """
    if not query:
        return {"error": "请提供 query 参数"}

    print(f"🔍 [Debug] 正在检索知识库: {query}")

    # 调用我们在 rag_service.py 中实现的异步 retrieve 方法
    context = await rag_service.retrieve(query)

    return {
        "query": query,
        "retrieved_context": context,
        "length": len(context) if context else 0,
        "status": "success" if context else "no_results_or_error",
    }






if __name__ == "__main__":
    import uvicorn

    print(f"🚀 Python backend running at {settings.SERVER_URL}")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3001,
        reload=True,
        reload_dirs=[".", "services"],
        # 依然建议排除缓存文件，防止编译行为触发重启
        reload_excludes=[
            "*/__pycache__/*",
            "*.pyc",
            ".venv/*",  # 排除根目录下的虚拟环境
            "*/.venv/*",
        ],
    )
