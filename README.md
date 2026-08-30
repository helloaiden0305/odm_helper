# XZY ODM 研发测试助手

本项目演示一个面向 ODM 研发测试场景的智能助手架构，包含 RTC 语音交互、RAG 知识检索、LLM 流式生成和 TTS 语音回复能力。

页面展示名为 **XZY 研发测试助手**，用于模拟内部工程师查询 SOP、测试规范、软硬件常见问题、历史缺陷和日志分析方法。

## 业务背景

ODM 研发测试过程中会沉淀大量规范、问题单、调试记录和历史缺陷。

新人排查蓝牙、Wi-Fi、ANR、刷机失败、相机黑屏等问题时，常常需要在多份文档和日志中来回检索。

本项目用脱敏 mock 数据演示一个轻量研发测试助手：先检索工程知识，再生成简洁、可执行的处理建议。

## 使用模式

当前项目支持文字模式和语音模式两种入口。

文字模式面向办公桌面、日志分析和型号/错误码较多的场景。
用户可以直接输入项目、设备型号、错误码或日志片段，
前端调用 Python 后端文字流式接口，
后端复用同一套 RAG 检索和 Ark / Doubao LLM 生成能力，回答以文字形式持续展示。

语音模式面向硬件开发、测试执行和现场排障场景。
用户手上在接线、刷机、操作设备或查看仪器时，可以通过麦克风提问；
底层由 RTC 云端 Agent 完成语音链路编排，ASR 将语音转成文本，
后端接收 CustomLLM callback 后执行 RAG + LLM，再通过 TTS 播放语音回复。

为减少短暂停顿导致的半句话误提交，语音模式支持确认发送机制：
ASR 识别出用户文本后，后端先推送一条待确认问题给前端；
用户确认后，前端通过 RTC Agent 的 `ExternalTextToLLM` 指令把文本交回语音链路，
继续触发 RAG、LLM 和 TTS。
该机制可通过 `VOICE_CONFIRM_MODE` 配置开关控制。

## 上下文记忆处理

文字问答链路加入了轻量级上下文管理，用于长对话场景下保留关键信息、控制上下文长度。当前采用内存存储，便于本地演示，后续可替换为 Redis 或数据库。

处理流程分为五层：T1 清洗监控、T2 Snip 归档、T3 旧片段折叠、T4 九段结构化压缩、T5 超限兜底裁剪。

九段模板会保留用户意图、工程环境、问题模块、知识库依据、已尝试动作、确认结论、待补充信息、回复策略和下一步计划，确保多轮追问时设备型号、软件版本、错误码和日志类型等关键信息不易丢失。

## 架构流程

```text
用户语音
  ↓
前端 RTC SDK
  ↓
火山 RTC 云端 Agent
  ↓
ASR 语音转文字
  ↓
CustomLLM Callback
  ↓
Python FastAPI /api/chat_callback
  ↓
RAG 检索 ODM 工程知识
  ↓
Ark / Doubao LLM 流式生成
  ↓
SSE 返回 RTC 云端
  ↓
TTS 合成语音
  ↓
前端播放 AI 回复
```

## 前端职责

前端展示 RTC 进房、设备控制、字幕展示、打断和通话状态展示能力。
当前改造只使用场景展示和少量样式，使页面呈现为内部工程工作台风格。

默认前端代理地址保持为：

```text
http://localhost:3001
```

## 后端职责

Python 后端：`rag_llm_server`。

Python 后端负责返回场景配置、代理 RTC OpenAPI 请求，并接收 CustomLLM callback 执行 RAG 检索与 LLM 流式生成。

当前 Demo 使用内存版 session store 保存 `RoomId/UserId` 与 `TaskId` 的映射，满足本地单实例演示；

生产多实例部署时可将该存储替换为 Redis 或数据库，以支持跨进程共享会话状态。

需要兼容的接口：

```text
/getScenes
/proxy
/api/chat_callback
/api/text_chat
/api/text_chat_stream
/api/voice_text_stream
/api/confirm_voice_question
/debug/chat
/debug/rag
```

## 并发与生产化预留

当前版本面向本地单实例演示，默认使用内存版 session store 维护会话状态。

每次 RTC 会话会动态生成 `RoomId/UserId/Token`，启动云端 Agent 时记录 `RoomId/UserId -> TaskId` 映射，

停止时再按同一组身份取回对应 `TaskId`，避免多用户共用固定房间和固定任务。

为了保留生产化扩展空间，后端已将 session 状态封装在 `rag_llm_server/services/session_store.py` 中。

后续如果部署为多进程或多实例服务，可以将该存储替换为 Redis 或数据库，实现跨实例共享会话状态；

同时 `/proxy` 请求日志带有短 `trace_id`，方便排查一次 Start/Stop 调用链路。

RTC OpenAPI 请求超时时间通过 `RTC_OPENAPI_TIMEOUT` 配置，便于压测和不同环境调整。

## RTC / ASR / LLM / TTS 协作关系

RTC 负责实时音频传输和 Agent 链路编排；

ASR 将用户语音转成文本；

CustomLLM callback 把文本请求转发到本地 FastAPI；

RAG 先检索 ODM 工程知识；

LLM 根据知识上下文生成回答；

TTS 将回答合成为语音并回传给前端播放。

## RAG 知识库说明

当前 GitHub 展示版为了方便本地运行，默认使用 `rag_llm_server/data/odm_knowledge.json` 中的脱敏 mock ODM 知识数据和轻量关键词检索逻辑模拟 RAG 流程。

真实企业环境可替换为火山知识库 / VikingDB 或企业内部 RAG 检索服务。

mock 数据覆盖：

```text
X100 蓝牙连接失败、Wi-Fi 断连日志抓取、ANR 日志分析、刷机失败、相机预览黑屏、音频无声、测试报告提交规范、Bug 回归验证等。
```

## 本地启动方式

### 1. 准备环境变量

首次运行时，先从模板创建自己的本地环境变量文件：

```shell
cp .env.example rag_llm_server/.env
```

然后编辑 `rag_llm_server/.env`，填入自己的火山引擎、RTC、Ark、ASR/TTS 和公网回调地址配置。不要把 `rag_llm_server/.env` 提交到 GitHub。

### 可选：一键启动调试链路

完成依赖安装和 `.env` 配置后，可以在项目根目录运行：

```shell
npm run dev:all
```

脚本会按原有方式同时启动 Python 后端、前端和 ngrok，本地调试日志写入 `.dev-logs/`。

如果 `rag_llm_server/.env` 中的 `SERVER_URL` 是 ngrok 域名，脚本会优先使用该固定域名启动 ngrok；退出时在终端按 `Control+C` 会一起停止三个进程。

### 2. 启动 Python 后端

```shell
cd rag_llm_server
python main.py
```

后端默认监听：

```text
http://localhost:3001
```

### 3. 启动公网回调映射

如果要测试完整 RTC 云端 Agent + CustomLLM callback，需要让火山云端能访问本地 Python 后端。可以使用 ngrok：

```shell
ngrok http 3001
```

把 ngrok 输出的 `Forwarding` HTTPS 地址填到 `rag_llm_server/.env`：

```env
SERVER_URL=https://your-ngrok-domain.ngrok-free.app
```

注意：`SERVER_URL` 只填写域名，不要带 `/api/chat_callback`，代码会自动拼接成：

```text
{SERVER_URL}/api/chat_callback
```

如果只是查看前端页面或调试本地 mock RAG，可以暂时不启动 ngrok。

### 4. 启动前端

```shell
npm install
npm run dev
```

前端默认访问：

```text
http://localhost:3000
```


## 安全说明

仓库只提交 `.env.example`，不提交任何真实 `.env`。`.gitignore` 已忽略 `.env` 和各目录下的 `.env` 文件。
上传 GitHub 前请再次确认：

```shell
git check-ignore -v rag_llm_server/.env
```

仓库中的示例配置只保留占位值，不应包含真实 AK/SK/API Key/Token。如果真实密钥已经进入 Git 历史，不要直接公开原仓库历史，应先轮换密钥并清理历史。

## 后续演进方向

后续可以接入真实企业知识库、日志系统、缺陷管理系统和权限体系；也可以将低敏知识放在云端知识库，将客户项目、完整日志和历史缺陷等敏感数据迁移到企业内部 RAG 检索服务。
