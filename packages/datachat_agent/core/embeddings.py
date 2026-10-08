"""Text embeddings: local and free by default (fastembed, CPU), or any OpenAI-compatible API."""

import hashlib
import math
import re
import threading
from functools import lru_cache
from typing import Protocol

from datachat_agent.config import Settings, get_settings


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingConfigError(RuntimeError):
    pass


class LocalEmbedder:
    """Runs BAAI/bge-small-en-v1.5 (or another fastembed model) on the CPU. No key, no cost."""

    def __init__(self, model: str, dim: int, cache_dir: str):
        from fastembed import TextEmbedding

        self.dim = dim
        self._model = TextEmbedding(model_name=model, cache_dir=cache_dir)
        self._lock = threading.Lock()  # the ONNX session is not safe to share across threads

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            return [v.tolist() for v in self._model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        with self._lock:
            return next(iter(self._model.query_embed(text))).tolist()


class OpenAICompatibleEmbedder:
    def __init__(self, model: str, dim: int, base_url: str | None, api_key: str):
        from langchain_openai import OpenAIEmbeddings

        self.dim = dim
        self._client = OpenAIEmbeddings(
            model=model,
            base_url=base_url,
            api_key=api_key or "not-needed",
            check_embedding_ctx_length=False,  # other providers do not use OpenAI's tokenizer
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)


class HashEmbedder:
    """Tests only: deterministic word-hash vectors, so texts sharing words land close together."""

    def __init__(self, dim: int):
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            for token in {word, word.rstrip("s")}:
                h = int(hashlib.md5(token.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class _Checked:
    """Wraps an embedder and fails clearly if its vectors do not match EMBED_DIM."""

    def __init__(self, inner: Embedder):
        self._inner = inner
        self.dim = inner.dim

    def _check(self, vectors: list[list[float]]) -> list[list[float]]:
        if vectors and len(vectors[0]) != self.dim:
            raise EmbeddingConfigError(
                f"The embedding model returns {len(vectors[0])} numbers per text but EMBED_DIM "
                f"is {self.dim}. Set EMBED_DIM to {len(vectors[0])} before creating the database."
            )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._check(self._inner.embed_documents(texts)) if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._check([self._inner.embed_query(text)])[0]


def build_embedder(settings: Settings) -> Embedder:
    if settings.embed_provider == "hash":
        inner: Embedder = HashEmbedder(settings.embed_dim)
    elif settings.embed_provider == "openai_compatible":
        inner = OpenAICompatibleEmbedder(
            settings.embed_model,
            settings.embed_dim,
            settings.embed_base_url or settings.llm_base_url,
            settings.embed_api_key or settings.llm_api_key,
        )
    else:
        settings.models_cache_dir.mkdir(parents=True, exist_ok=True)
        inner = LocalEmbedder(
            settings.embed_model, settings.embed_dim, str(settings.models_cache_dir)
        )
    return _Checked(inner)


@lru_cache
def get_embedder() -> Embedder:
    return build_embedder(get_settings())
