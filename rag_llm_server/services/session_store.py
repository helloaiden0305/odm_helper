from dataclasses import dataclass, field


@dataclass
class InMemorySessionStore:
    """Demo session store. Replace with Redis/DB for multi-instance deployment."""

    task_ids: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def build_key(room_id: str, user_id: str) -> str:
        return f"{room_id}:{user_id}"

    def set_task_id(self, room_id: str, user_id: str, task_id: str) -> str:
        session_key = self.build_key(room_id, user_id)
        self.task_ids[session_key] = task_id
        return session_key

    def pop_task_id(self, room_id: str, user_id: str, default_task_id: str) -> str:
        session_key = self.build_key(room_id, user_id)
        return self.task_ids.pop(session_key, default_task_id)


session_store = InMemorySessionStore()
