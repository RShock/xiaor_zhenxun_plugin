import hashlib
import time
from collections import deque

from zhenxun.services.log import logger


def _extract_text_from_dict(seg: dict) -> str:
    st = seg.get("type", "")
    if st == "text":
        return seg.get("data", {}).get("text", "")
    if st == "image":
        fid = seg.get("data", {}).get("file", "")
        return f"[image:{fid}]"
    return str(seg)


def _extract_text_from_obj(seg) -> str:
    st = getattr(seg, "type", "")
    data = getattr(seg, "data", {})
    if st == "text":
        return data.get("text", "") if isinstance(data, dict) else str(data)
    if st == "image":
        fid = data.get("file", "") if isinstance(data, dict) else ""
        return f"[image:{fid}]"
    return f"[{st}]"


def _extract_segment_text(seg) -> str:
    if isinstance(seg, str):
        return seg
    if isinstance(seg, dict):
        return _extract_text_from_dict(seg)
    if hasattr(seg, "type"):
        return _extract_text_from_obj(seg)
    return str(seg)


def extract_message_content(message) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, dict) or hasattr(message, "type"):
        return _extract_segment_text(message)
    if hasattr(message, "__iter__"):
        return "".join(_extract_segment_text(seg) for seg in message)
    return str(message)


def _count_images_in_dict(seg: dict) -> int:
    if seg.get("type") == "image":
        return 1
    data = seg.get("data")
    if isinstance(data, dict) and ("file" in data or "url" in data):
        return 1
    return 0


def _count_images_in_segment(seg) -> int:
    if isinstance(seg, str):
        return seg.count("[image:") + seg.count("[CQ:image")
    if isinstance(seg, dict):
        return _count_images_in_dict(seg)
    if hasattr(seg, "type") and getattr(seg, "type", None) == "image":
        return 1
    return 0


def count_images(message) -> int:
    if not message:
        return 0
    if isinstance(message, str):
        return message.count("[image:") + message.count("[CQ:image")
    if isinstance(message, dict):
        return _count_images_in_dict(message)
    if hasattr(message, "type") and getattr(message, "type", None) == "image":
        return 1
    if hasattr(message, "__iter__"):
        return sum(_count_images_in_segment(seg) for seg in message)
    return 0


class MessageDedup:
    def __init__(self, window_seconds: int = 20, max_content_bytes: int = 10240):
        self._window = window_seconds
        self._max_bytes = max_content_bytes
        self._entries: deque[tuple[str, str, float]] = deque()
        self._index: dict[str, tuple[str, float]] = {}
        self._blocked_count: int = 0

    def _hash_message(self, message) -> str:
        content = extract_message_content(message)
        raw = content[: self._max_bytes].encode("utf-8", errors="replace")
        return hashlib.md5(raw).hexdigest()

    def check_and_record(self, bot_id: str, message) -> bool:
        self._cleanup()
        h = self._hash_message(message)
        now = time.time()
        if h in self._index:
            existing_bot, existing_time = self._index[h]
            if existing_bot != bot_id and (now - existing_time) < self._window:
                self._blocked_count += 1
                logger.info(
                    f"[Route] 消息去重拦截: Bot {bot_id} 与 Bot {existing_bot} "
                    f"发送相同消息(hash={h[:8]})，已拦截(第{self._blocked_count}次)"
                )
                return True
        self._index[h] = (bot_id, now)
        self._entries.append((h, bot_id, now))
        return False

    def _cleanup(self):
        now = time.time()
        while self._entries:
            h, bid, ts = self._entries[0]
            if now - ts > self._window:
                self._entries.popleft()
                if h in self._index and self._index[h] == (bid, ts):
                    del self._index[h]
            else:
                break

    @property
    def blocked_count(self) -> int:
        return self._blocked_count

    @property
    def entry_count(self) -> int:
        return len(self._entries)
