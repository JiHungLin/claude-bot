import json
import threading
from pathlib import Path

_lock = threading.Lock()


def is_allowed(user_id: str, allowlist_path: str) -> bool:
    path = Path(allowlist_path)
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return user_id in data.get("allowed_user_ids", [])


def add_user(user_id: str, allowlist_path: str) -> None:
    path = Path(allowlist_path)
    with _lock:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        ids = data.get("allowed_user_ids", [])
        if user_id not in ids:
            ids.append(user_id)
        data["allowed_user_ids"] = ids
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
