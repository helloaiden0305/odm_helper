import json
import re
from pathlib import Path



class RagService:
    def __init__(self):
        self.data_path = Path(__file__).resolve().parents[1] / "data" / "odm_knowledge.json"
        self.documents = self._load_documents()

    def _load_documents(self):
        try:
            with open(self.data_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception as exc:
            print(f"[RagService] 本地 mock 知识库加载失败: {exc}")
            return []

    def _tokenize(self, text: str):
        normalized = text.lower()
        raw_tokens = re.findall(r"[a-z0-9][a-z0-9_-]*|[\u4e00-\u9fff]+", normalized)
        tokens = set()
        for token in raw_tokens:
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", token):
                if len(token) >= 2:
                    tokens.add(token)
                continue

            if len(token) >= 2:
                tokens.add(token)

        domain_terms = {
            "蓝牙", "连接", "配对", "通话", "无声", "日志", "排查", "故障", "缺陷",
            "wifi", "wi-fi", "断连", "刷机", "升级", "fastboot", "anr", "卡顿",
            "相机", "黑屏", "音频", "测试", "报告", "bug", "回归", "问题单",
            "x100", "bt_stack", "hfp", "ota", "recovery", "perfetto", "systrace",
        }
        compact = re.sub(r"\s+", "", normalized)
        for term in domain_terms:
            if term in compact:
                tokens.add(term)

        return tokens

    def _score(self, query_tokens, document):
        metadata = document.get("metadata", {})
        searchable = " ".join(
            [
                document.get("title", ""),
                document.get("content", ""),
                " ".join(str(value) for value in metadata.values()),
            ]
        ).lower()
        score = 0
        for token in query_tokens:
            if token and token in searchable:
                score += 3 if token in document.get("title", "").lower() else 1
        return score

    async def retrieve(self, query: str) -> str:
        """
        根据用户问题检索本地 mock ODM 知识库
        :param query: 用户查询语句
        :return: 整合后的上下文文本
        """
        query = (query or "").strip()
        if not query or not self.documents:
            return ""

        query_tokens = self._tokenize(query)
        ranked = sorted(
            (
                (self._score(query_tokens, document), document)
                for document in self.documents
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        hits = [document for score, document in ranked if score > 0][:5]
        if not hits:
            print("[RagService] 本地 mock 知识库未命中")
            return ""

        context_blocks = []
        for document in hits:
            metadata = document.get("metadata", {})
            context_blocks.append(
                f"[{document.get('id')}] {document.get('title')}\n"
                f"类型: {metadata.get('doc_type', '-')}; 项目: {metadata.get('project', '-')}; 模块: {metadata.get('module', '-')}\n"
                f"内容: {document.get('content', '')}"
            )

        context_text = "\n\n".join(context_blocks)
        print(f"[RagService] 本地 mock 知识库命中 {len(hits)} 条")
        return context_text

# 实例化单例
rag_service = RagService()
