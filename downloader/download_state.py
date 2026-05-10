import json
import os
import time
from dataclasses import dataclass, field, asdict


@dataclass
class ChunkState:
    index: int
    start: int
    end: int
    done: bool = False
    retries: int = 0
    bytes: int = 0
    error: str = ""


@dataclass
class DownloadState:
    id: str
    url: str
    filename: str
    path: str
    part_path: str
    state_path: str

    total_size: int = 0
    downloaded: int = 0
    status: str = "queued"
    error: str = ""

    relay_requests: int = 0
    relay_errors: int = 0
    relay_sent_bytes: int = 0
    relay_received_bytes: int = 0
    quota_used: int = 0

    chunk_size: int = 0
    parallel: int = 0
    retries: int = 0

    started_at: float = 0.0
    finished_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    logs: list = field(default_factory=list)
    chunks: list = field(default_factory=list)

    def log(self, level, message):
        self.logs.append({
            "t": time.strftime("%H:%M:%S"),
            "level": str(level).upper(),
            "message": str(message),
        })
        self.logs = self.logs[-300:]

    def save(self):
        self.updated_at = time.time()
        tmp = self.state_path + ".tmp"
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.state_path)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["chunks"] = [
            ChunkState(**c) if isinstance(c, dict) else c
            for c in data.get("chunks", [])
        ]
        return cls(**data)
