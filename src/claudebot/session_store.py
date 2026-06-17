import json
import threading
from pathlib import Path
from typing import Optional


class SessionStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, user_id: str) -> Optional[str]:
        with self._lock:
            return self._read().get(user_id)

    def set(self, user_id: str, session_id: str) -> None:
        with self._lock:
            data = self._read()
            data[user_id] = session_id
            self._write(data)
