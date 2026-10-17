from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Fixed key for XOR-based obfuscation (not production-grade crypto,
# but avoids a hard dependency on cryptography while still preventing
# accidental plaintext leakage in config files / logs).
_OBFUSCATION_KEY = hashlib.sha256(b"opensoul-gland-key-v1").digest()


def _obfuscate(plaintext: str) -> str:
    """XOR-encrypt then base64-encode an API key."""
    data = plaintext.encode()
    key = _OBFUSCATION_KEY
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(encrypted).decode()


def _deobfuscate(ciphertext: str) -> str:
    """Reverse of _obfuscate."""
    encrypted = base64.urlsafe_b64decode(ciphertext.encode())
    key = _OBFUSCATION_KEY
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted)).decode()


@dataclass
class KeySlot:
    provider: str
    key_encrypted: str
    is_valid: bool = True
    last_checked: float = 0.0
    fail_count: int = 0

    @property
    def key(self) -> str:
        return _deobfuscate(self.key_encrypted)


class KeyManager:
    """Manages API keys with encrypted storage, rotation, and health checks."""

    def __init__(self) -> None:
        self._slots: dict[str, list[KeySlot]] = {}
        self._indices: dict[str, int] = {}  # round-robin index per provider

    # ── key lifecycle ─────────────────────────────────────────────

    def add_key(self, provider: str, api_key: str) -> KeySlot:
        """Store a new key for *provider* (encrypted at rest)."""
        slot = KeySlot(provider=provider, key_encrypted=_obfuscate(api_key))
        self._slots.setdefault(provider, []).append(slot)
        logger.info("Added key for provider=%s (total=%d)", provider, len(self._slots[provider]))
        return slot

    def remove_key(self, provider: str, index: int = 0) -> bool:
        """Remove the key at *index* for *provider*."""
        slots = self._slots.get(provider, [])
        if 0 <= index < len(slots):
            slots.pop(index)
            return True
        return False

    # DEC-⑳-② 已删 get_keys（明文批量回吐便利包装——语义被活面 KeySlot.key
    # 100% 覆盖（逐槽解密原语，next_key/validate_key/status 真消费），全仓零生产
    # 消费；H1-②「回吐面不应存在」裁决贯彻到方法本体。测试内省改经 KeySlot.key
    # 原语直接表达（tests/test_key_remove_face.py _slot_keys），断言意图零丢失。

    # ── round-robin selection ─────────────────────────────────────

    def next_key(self, provider: str) -> str | None:
        """Return the next healthy key for *provider* (round-robin)."""
        healthy = [s for s in self._slots.get(provider, []) if s.is_valid]
        if not healthy:
            return None
        idx = self._indices.get(provider, 0) % len(healthy)
        self._indices[provider] = idx + 1
        return healthy[idx].key

    # ── health checking ──────────────────────────────────────────

    async def validate_key(self, provider: str, base_url: str, index: int = 0) -> bool:
        """Ping the provider's models endpoint to verify the key works.

        寻址收口（接线前定形，修前零消费方=无行为影响面）：未命中（未知 provider/
        越界/负 index）恒 False——修前负 index 越过 ``index >= len`` 检查后
        slots[-1] 静默误验末钥（与 remove_key 的负 index 未命中语义不对称）。
        """
        slots = self._slots.get(provider, [])
        if not (0 <= index < len(slots)):
            return False

        slot = slots[index]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {slot.key}"},
                )
                ok = resp.status_code == 200
                slot.is_valid = ok
                slot.fail_count = 0 if ok else slot.fail_count + 1
                slot.last_checked = time.time()
                return ok
        except Exception:
            slot.fail_count += 1
            slot.last_checked = time.time()
            if slot.fail_count >= 3:
                slot.is_valid = False
                logger.warning(
                    "Key for provider=%s marked invalid after %d failures",
                    provider,
                    slot.fail_count,
                )
            return False

    async def validate_all(self, provider: str, base_url: str) -> list[bool]:
        """Validate every key for *provider*."""
        results = []
        for i in range(len(self._slots.get(provider, []))):
            results.append(await self.validate_key(provider, base_url, i))
        return results

    # ── introspection ────────────────────────────────────────────

    def list_providers(self) -> list[str]:
        return list(self._slots.keys())

    def status(self) -> dict[str, list[dict]]:
        """Return a summary of all keys (secrets masked)."""
        out: dict[str, list[dict]] = {}
        for provider, slots in self._slots.items():
            out[provider] = [
                {
                    "masked_key": s.key[:4] + "..." + s.key[-4:] if len(s.key) > 8 else "****",
                    "is_valid": s.is_valid,
                    "fail_count": s.fail_count,
                    "last_checked": s.last_checked,
                }
                for s in slots
            ]
        return out

    def load_from_env(self) -> None:
        """Bootstrap keys from environment variables (LLM_API_KEY, EMBEDDING_API_KEY, etc.)."""
        env_map = {
            "openai": ["LLM_API_KEY", "EMBEDDING_API_KEY"],
            "ollama": [],  # Ollama typically needs no key
            "mimo": ["MIMO_API_KEY"],
        }
        for provider, env_vars in env_map.items():
            for var in env_vars:
                val = os.getenv(var, "")
                if val and not any(
                    hmac.compare_digest(s.key, val) for s in self._slots.get(provider, [])
                ):
                    self.add_key(provider, val)
