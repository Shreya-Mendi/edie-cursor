"""Local LLM for filling values: Qwen3-4B (4-bit) on Apple MLX.

Loads only from the local Hugging Face cache, so it never touches the
network. Greedy decoding and Qwen3's thinking mode off, for speed and
repeatable output.

Prompt cache: the prompt puts the conversation first and the field last, so
consecutive calls share a long prefix. The KV cache keeps the longest common
prefix with the previous prompt and only the new tokens are processed (new
turns plus the field question). Generated tokens are trimmed back off after
every call.
"""
import threading
import time

from huggingface_hub import snapshot_download

MODEL = "mlx-community/Qwen3-4B-4bit"


def _common_prefix(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class MLXBackend:
    def __init__(self, path, name):
        from mlx_lm import load
        from mlx_lm.sample_utils import make_sampler

        self.model, self.tokenizer = load(path)
        self.sampler = make_sampler(temp=0.0)
        self.name = name
        self._lock = threading.Lock()  # one generation at a time on the GPU
        self._cache, self._cached = None, []
        self.last_stats = {}

    @classmethod
    def load_if_present(cls, repo=MODEL):
        try:
            path = snapshot_download(repo, local_files_only=True)
        except Exception:
            return None
        return cls(path, f"{repo.split('/')[-1]} (MLX, local)")

    def generate(self, messages, max_tokens=120):
        from mlx_lm import stream_generate
        from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache

        prompt = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False, enable_thinking=False
        )
        tokens = self.tokenizer.encode(prompt, add_special_tokens=self.tokenizer.bos_token is None)
        with self._lock:
            t0 = time.perf_counter()
            if self._cache is None:
                self._cache, self._cached = make_prompt_cache(self.model), []
            keep = min(_common_prefix(self._cached, tokens), len(tokens) - 1)  # feed at least one token
            drop = self._cache[0].offset - keep
            if drop > 0 and trim_prompt_cache(self._cache, drop) != drop:
                self._cache, keep = make_prompt_cache(self.model), 0  # could not trim: start fresh
            text, last = "", None
            for resp in stream_generate(self.model, self.tokenizer, tokens[keep:], max_tokens=max_tokens,
                                        sampler=self.sampler, prompt_cache=self._cache):
                text += resp.text
                last = resp
            extra = self._cache[0].offset - len(tokens)  # generated tokens now in the cache
            if extra > 0:
                trim_prompt_cache(self._cache, extra)
            self._cached = tokens
            ms = round((time.perf_counter() - t0) * 1000)
        self.last_stats = {"prompt_tokens": len(tokens), "new_tokens": len(tokens) - keep,
                           "output_tokens": last.generation_tokens if last else 0, "ms": ms}
        return text, ms
