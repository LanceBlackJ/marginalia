"""数据目录布局（对应说明书“推荐目录结构”）。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    data_dir: Path

    @property
    def database_dir(self) -> Path:
        return self.data_dir / "database"

    @property
    def db_path(self) -> Path:
        return self.database_dir / "paperdesk.db"

    @property
    def papers_dir(self) -> Path:
        return self.data_dir / "papers"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def settings_dir(self) -> Path:
        return self.data_dir / "settings"

    def paper_dir(self, paper_id: str) -> Path:
        return self.papers_dir / paper_id

    def ensure(self) -> None:
        for d in (self.database_dir, self.papers_dir, self.cache_dir, self.logs_dir, self.settings_dir):
            d.mkdir(parents=True, exist_ok=True)
