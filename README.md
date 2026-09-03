# XZY ODM 研发测试助手

面向 ODM 研发测试场景的智能助手 Demo，支持文字问答和 RTC 语音交互，帮助工程师查询 SOP、测试规范、常见软硬件问题、历史缺陷与日志分析方法。

项目使用脱敏 mock 数据演示完整交互链路，适合本地运行和技术方案展示。

## 业务背景

ODM 研发测试过程中会沉淀大量规范、问题单、调试记录和历史缺陷。

新人排查蓝牙、Wi-Fi、ANR、刷机失败、相机黑屏等问题时，常常需要在多份文档和日志中来回检索。

## 使用模式

当前项目支持文字模式和语音模式两种入口。

文字模式适用于办公桌面，以及输入项目、型号、错误码或短日志片段等结构化信息的场景，回答以流式文字持续展示。

语音模式适用于接线、刷机、操作设备或查看仪器等不便打字的现场场景，支持语音提问、打断和确认发送，减少短暂停顿造成的误提交。

## 上下文与压缩治理

文字和语音共用同一会话标识，切换输入方式后仍可延续本次排障上下文。

会话优先保留原文，仅在旧片段触发治理后通过摘要标记替代对应历史。

RAG 检索与上下文读取并行完成后再组装 Prompt。

调用模型前会执行输入长度与 Prompt 总预算保护。

超限时仅临时按时间顺序缩减最早历史片段，不改写会话原始数据。

回答结束后异步按水位执行 T1-T5 治理：轻量清洗、旧轮次归档、局部折叠、九段摘要与会话 Epoch 滚动。

当前展示版使用内存态上下文和 Mock 存储接口，不要求部署 Redis 或数据库；超长 Android Log 会提示转入独立日志分析模式处理。

## 架构流程

```text
文字模式：用户输入 -> Python 后端 -> RAG 检索 -> LLM 流式回答

语音模式：用户语音 -> RTC / ASR -> Python 后端 -> RAG + LLM -> TTS 语音回复
```

## 实现说明

前端负责聊天展示、文字输入和 RTC 语音交互；Python 后端负责场景配置、工程知识检索、LLM 流式生成与语音回调处理。

当前版本面向本地单实例演示，RTC 会话使用独立身份标识，便于多窗口调试。

## RAG 知识库说明

当前 GitHub 展示版为了方便本地运行，默认使用 `rag_llm_server/data/odm_knowledge.json` 中的脱敏 mock ODM 知识数据和轻量关键词检索逻辑模拟 RAG 流程。

mock 数据覆盖：

```text
X100 蓝牙连接失败、Wi-Fi 断连日志抓取、ANR 日志排查方法、刷机失败、相机预览黑屏、音频无声、测试报告提交规范、Bug 回归验证等。
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

## 当前范围

当前版本面向本地单实例演示；真实项目可根据企业规范接入内部知识库、日志平台、缺陷管理系统和权限体系。
