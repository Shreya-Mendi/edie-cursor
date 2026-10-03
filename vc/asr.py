"""Live speech to text: Whisper large-v3-turbo on Apple MLX, local only.

The browser sends one utterance at a time (cut at silence), as recorded
audio. ffmpeg decodes it, Whisper transcribes it. Audio never leaves this Mac.
"""
import tempfile
import threading
import time

from huggingface_hub import snapshot_download

MODEL = "mlx-community/whisper-large-v3-turbo"


class WhisperASR:
    name = "whisper-large-v3-turbo (MLX, local)"

    def __init__(self, path):
        import mlx_whisper

        self._transcribe = mlx_whisper.transcribe
        self.path = path
        self._lock = threading.Lock()

    @classmethod
    def load_if_present(cls, repo=MODEL):
        try:
            return cls(snapshot_download(repo, local_files_only=True))
        except Exception:
            return None

    def transcribe(self, audio: bytes, suffix=".webm"):
        with tempfile.NamedTemporaryFile(suffix=suffix) as f, self._lock:
            f.write(audio)
            f.flush()
            t0 = time.perf_counter()
            r = self._transcribe(f.name, path_or_hf_repo=self.path, condition_on_previous_text=False)
            ms = round((time.perf_counter() - t0) * 1000)
        return {"text": r["text"].strip(), "language": r.get("language"), "ms": ms}
