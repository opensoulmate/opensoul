"""TTS Engine — 文字转语音引擎，支持多后端。"""

import hashlib
import os
import tempfile
import time
from dataclasses import dataclass, field


@dataclass
class TTSResult:
    """Result of a TTS synthesis."""

    audio_data: bytes
    format: str = "mp3"
    duration_seconds: float = 0.0
    engine: str = "edge-tts"
    voice_id: str = ""
    sample_rate: int = 24000
    size_bytes: int = 0
    cached: bool = False
    elapsed_seconds: float = 0.0

    def __post_init__(self):
        if not self.size_bytes:
            self.size_bytes = len(self.audio_data)


@dataclass
class TTSJob:
    """A queued TTS job."""

    job_id: str
    text: str
    voice_id: str
    status: str = "pending"  # pending, processing, completed, failed
    result_path: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0


# Check available backends
HAS_EDGE_TTS = False
HAS_PYTTSX3 = False

try:
    import edge_tts

    HAS_EDGE_TTS = True
except ImportError:
    pass

try:
    import pyttsx3  # noqa: F401

    HAS_PYTTSX3 = True
except ImportError:
    pass


class TTSEngine:
    """Multi-backend TTS engine with caching and queue."""

    def __init__(self, cache_dir: str = "", max_cache_mb: int = 256):
        self._cache_dir = cache_dir or os.path.expanduser("~/.opensoul/tts_cache")
        self._output_dir = os.path.expanduser("~/.opensoul/tts_output")
        os.makedirs(self._cache_dir, exist_ok=True)
        os.makedirs(self._output_dir, exist_ok=True)
        self._max_cache_bytes = max_cache_mb * 1024 * 1024
        self._jobs: dict[str, TTSJob] = {}
        # Stats
        self._total_synthesized = 0
        self._total_characters = 0
        self._total_cache_hits = 0
        self._errors = 0

    @property
    def backends(self) -> dict[str, bool]:
        return {
            "edge-tts": HAS_EDGE_TTS,
            "pyttsx3": HAS_PYTTSX3,
        }

    @property
    def preferred_backend(self) -> str:
        if HAS_EDGE_TTS:
            return "edge-tts"
        if HAS_PYTTSX3:
            return "pyttsx3"
        return "none"

    def _cache_key(self, text: str, voice_id: str, rate: str, pitch: str, volume: str) -> str:
        content = f"{text}|{voice_id}|{rate}|{pitch}|{volume}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _cache_path(self, key: str, fmt: str = "mp3") -> str:
        return os.path.join(self._cache_dir, f"{key}.{fmt}")

    def _check_cache(self, key: str, fmt: str = "mp3") -> bytes | None:
        path = self._cache_path(key, fmt)
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            if data:
                self._total_cache_hits += 1
                return data
        return None

    def _save_cache(self, key: str, data: bytes, fmt: str = "mp3"):
        path = self._cache_path(key, fmt)
        with open(path, "wb") as f:
            f.write(data)
        self._cleanup_cache()

    def _cleanup_cache(self):
        """Remove oldest cache files if over budget."""
        files = []
        total = 0
        for name in os.listdir(self._cache_dir):
            fp = os.path.join(self._cache_dir, name)
            if os.path.isfile(fp):
                stat = os.stat(fp)
                files.append((fp, stat.st_mtime, stat.st_size))
                total += stat.st_size

        if total <= self._max_cache_bytes:
            return

        files.sort(key=lambda x: x[1])  # oldest first
        for fp, _, size in files:
            if total <= self._max_cache_bytes * 0.8:
                break
            os.unlink(fp)
            total -= size

    def clear_cache(self) -> dict:
        """Remove ALL cached TTS audio files (space/load relief)."""
        removed = 0
        freed = 0
        for name in os.listdir(self._cache_dir):
            fp = os.path.join(self._cache_dir, name)
            if not os.path.isfile(fp):
                continue
            try:
                freed += os.path.getsize(fp)
                os.unlink(fp)
                removed += 1
            except OSError:
                continue
        return {"removed": removed, "freed_bytes": freed}

    async def synthesize(
        self,
        text: str,
        voice_id: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
        engine: str = "",
    ) -> TTSResult:
        """Synthesize text to speech audio."""
        start = time.time()
        if not engine:
            engine = self.preferred_backend

        if engine == "none" or (engine == "edge-tts" and not HAS_EDGE_TTS and not HAS_PYTTSX3):
            # Generate a simple WAV with silence as fallback
            return self._generate_placeholder(text, voice_id, start)

        # Check cache
        cache_key = self._cache_key(text, voice_id, rate, pitch, volume)
        cached = self._check_cache(cache_key)
        if cached:
            self._total_characters += len(text)
            return TTSResult(
                audio_data=cached,
                format="mp3",
                engine=engine,
                voice_id=voice_id,
                cached=True,
                elapsed_seconds=time.time() - start,
            )

        try:
            if engine == "edge-tts" and HAS_EDGE_TTS:
                result = await self._edge_tts_synthesize(text, voice_id, rate, pitch, volume)
            elif engine == "pyttsx3" and HAS_PYTTSX3:
                result = self._pyttsx3_synthesize(text, voice_id)
            else:
                return self._generate_placeholder(text, voice_id, start)

            self._save_cache(cache_key, result.audio_data, result.format)
            result.elapsed_seconds = time.time() - start
            self._total_synthesized += 1
            self._total_characters += len(text)
            return result

        except Exception:
            self._errors += 1
            # Return placeholder on error
            result = self._generate_placeholder(text, voice_id, start)
            result.engine = f"{engine}(fallback)"
            return result

    async def _edge_tts_synthesize(
        self, text: str, voice_id: str, rate: str, pitch: str, volume: str
    ) -> TTSResult:
        """Use edge-tts (Microsoft Edge online TTS, free)."""
        import edge_tts

        communicate = edge_tts.Communicate(
            text=text,
            voice=voice_id,
            rate=rate,
            pitch=pitch,
            volume=volume,
        )

        audio_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])

        audio_data = b"".join(audio_chunks)
        return TTSResult(
            audio_data=audio_data,
            format="mp3",
            engine="edge-tts",
            voice_id=voice_id,
        )

    def _pyttsx3_synthesize(self, text: str, voice_id: str) -> TTSResult:
        """Use pyttsx3 (offline TTS)."""
        import pyttsx3

        engine = pyttsx3.init()
        # Try to set voice by id
        for v in engine.getProperty("voices"):
            if voice_id.lower() in v.id.lower() or voice_id.lower() in v.name.lower():
                engine.setProperty("voice", v.id)
                break

        # Save to temp file
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        engine.save_to_file(text, tmp.name)
        engine.runAndWait()

        with open(tmp.name, "rb") as f:
            data = f.read()
        os.unlink(tmp.name)

        return TTSResult(
            audio_data=data,
            format="wav",
            engine="pyttsx3",
            voice_id=voice_id,
            sample_rate=22050,
        )

    def _generate_placeholder(self, text: str, voice_id: str, start: float) -> TTSResult:
        """Generate a minimal WAV file with silence (placeholder when no TTS backend)."""
        import struct

        duration = max(0.5, len(text) * 0.08)  # ~80ms per character
        sample_rate = 16000
        num_samples = int(duration * sample_rate)
        # Generate a very quiet tone so it's not complete silence
        samples = bytearray()
        for i in range(num_samples):
            # Very quiet 440Hz tone
            import math

            val = int(100 * math.sin(2 * math.pi * 440 * i / sample_rate))
            samples.extend(struct.pack("<h", val))

        # WAV header
        data_size = len(samples)
        wav = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + data_size,
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            sample_rate,
            sample_rate * 2,
            2,
            16,
            b"data",
            data_size,
        ) + bytes(samples)

        return TTSResult(
            audio_data=wav,
            format="wav",
            engine="placeholder",
            voice_id=voice_id,
            sample_rate=sample_rate,
            duration_seconds=duration,
            elapsed_seconds=time.time() - start,
        )

    async def list_edge_voices(self, language: str = "") -> list[dict]:
        """List available edge-tts voices."""
        if not HAS_EDGE_TTS:
            return []
        voices = await edge_tts.list_voices()
        if language:
            voices = [v for v in voices if v["Locale"].startswith(language)]
        return [
            {
                "id": v["ShortName"],
                "name": v["FriendlyName"],
                "language": v["Locale"],
                "gender": v["Gender"],
            }
            for v in voices
        ]

    def save_output(self, audio_data: bytes, filename: str, fmt: str = "mp3") -> str:
        """Save audio to output directory, return path."""
        path = os.path.join(self._output_dir, f"{filename}.{fmt}")
        with open(path, "wb") as f:
            f.write(audio_data)
        return path

    def list_outputs(self) -> list[dict]:
        """List saved TTS output files."""
        results = []
        for name in sorted(os.listdir(self._output_dir), reverse=True):
            fp = os.path.join(self._output_dir, name)
            if os.path.isfile(fp):
                stat = os.stat(fp)
                results.append(
                    {
                        "filename": name,
                        "size_bytes": stat.st_size,
                        "created_at": stat.st_mtime,
                    }
                )
        return results[:100]

    def delete_output(self, filename: str) -> bool:
        path = os.path.join(self._output_dir, filename)
        if os.path.exists(path):
            os.unlink(path)
            return True
        return False

    def stats(self) -> dict:
        cache_size = 0
        cache_entries = 0
        if os.path.exists(self._cache_dir):
            for name in os.listdir(self._cache_dir):
                fp = os.path.join(self._cache_dir, name)
                if os.path.isfile(fp):
                    cache_entries += 1
                    cache_size += os.path.getsize(fp)

        return {
            "backends": self.backends,
            "preferred_backend": self.preferred_backend,
            "total_synthesized": self._total_synthesized,
            "total_characters": self._total_characters,
            "cache_hits": self._total_cache_hits,
            "errors": self._errors,
            "cache": {
                "entries": cache_entries,
                "size_bytes": cache_size,
                "max_size_bytes": self._max_cache_bytes,
            },
            "output_dir": self._output_dir,
        }
