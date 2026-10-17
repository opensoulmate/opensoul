import threading
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path.home() / ".openmate"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

# ── All known organs ────────────────────────────────────────
ALL_ORGANS: list[str] = [
    "cortex",
    "nerve",
    "vein",
    "sense",
    "will",
    "immune",
    "vital",
    "marrow",
    "gland",
    "gene",
    "echo",
    "mirror",
    "link",
    "hippo",
    "reflex",
    "heredity",
    "pulse",
    "nest",
    "limb",
    "voice",
    "vision",
    "mind",
    "trajectory",
    "mcp",
    "learn",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "version": "2.0",
    "models": {
        "default": "mimo-v2.5-pro",
    },
    "plugins": [],
    "organs": {organ: {"enabled": True} for organ in ALL_ORGANS},
}


class ConfigManager:
    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._mtime: float = 0.0
        self._ensure_dir()
        self._load()

    @staticmethod
    def _ensure_dir() -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
            self._mtime = CONFIG_PATH.stat().st_mtime
        else:
            self._config = DEFAULT_CONFIG.copy()
            self._save()

    def _save(self) -> None:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(self._config, f, default_flow_style=False, allow_unicode=True)
        self._mtime = CONFIG_PATH.stat().st_mtime

    def _check_reload(self) -> None:
        if CONFIG_PATH.exists():
            mtime = CONFIG_PATH.stat().st_mtime
            if mtime > self._mtime:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    self._config = yaml.safe_load(f) or {}
                self._mtime = mtime

    def get(self, section: str | None = None, key: str | None = None) -> Any:
        with self._lock:
            self._check_reload()
            if section is None:
                return self._config
            sec = self._config.get(section)
            if sec is None:
                return None
            if key is None:
                return sec
            return sec.get(key) if isinstance(sec, dict) else None

    def update(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._config.update(data)
            self._save()
            return self._config


config_manager = ConfigManager()
