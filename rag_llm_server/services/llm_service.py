import os
from volcenginesdkarkruntime import Ark 
from config import settings

class LLMService:
    def __init__(self):
        api_key = settings.ARK_API_KEY 
        self.client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",    
            api_key=api_key, 
            timeout=1800, 

        )

    def chat_stream(self, history_messages: list, rag_context: str = ""):
        """
        流式对话
        :param history_messages: 对话历史
        :param rag_context: 从 rag_service 检索出来的背景知识
        """
        if not self.client:
            yield "服务配置错误"
            return

        system_content = """
        你是 XZY 研发测试助手，面向 ODM 内部工程问题处理场景。你需要根据知识库内容回答测试规范、SOP、软硬件常见问题、历史缺陷和日志分析相关问题。回答要简洁、可执行；如果信息不足，需要先追问项目名、设备型号、软件版本或问题模块，不要编造不存在的处理结论。
        """.strip()

        # --- 2. 构造最终发送给模型的消息序列 ---
        # messages = [{"role": "system", "content": system_content}]

        system_blocks = [system_content]

        if rag_context:
            # 使用明确的定界符，帮助模型在毫秒内定位知识
            system_blocks.append(f"### 参考知识库\n{rag_context.strip()}")

        # 合并为一条
        final_system_prompt = "\n\n".join(system_blocks)

        # 最终的消息序列
        messages = [{"role": "system", "content": final_system_prompt}]

        # 加入历史对话（确保包含用户最新的问题）
        messages.extend(history_messages)

        try:
            print(f"🚀 发起流式调用 (Endpoint: {settings.ARK_ENDPOINT_ID})")
            
            stream = self.client.chat.completions.create(
                model=settings.ARK_ENDPOINT_ID,
                messages=messages,
                temperature=0.3, # 降低随机性，确保回答更严谨地贴合 RAG
                stream=True,
                stream_options={"include_usage": True},
            )

            for chunk in stream:
                yield chunk

        except Exception as e:
            print(f"❌ LLM 调用失败: {e}")
            yield None

llm_service = LLMService()
