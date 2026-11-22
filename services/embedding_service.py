from __future__ import annotations

import hashlib
import math

from config.settings import EMBEDDING_BATCH_SIZE, EMBEDDING_PROVIDER, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL

Vector = list[float]


class EmbeddingError(RuntimeError):
    pass


def provider_signature() -> str:
    if EMBEDDING_PROVIDER == "gemini":
        return f"gemini:{GEMINI_EMBEDDING_MODEL}:retrieval-v1"
    if EMBEDDING_PROVIDER == "local":
        return "local:hashing-char-2-5:2048-v1"
    raise EmbeddingError("EMBEDDING_PROVIDER must be either 'gemini' or 'local'.")


def _local_embedding(text: str, dimensions: int = 2048) -> Vector:
    """Deterministic character n-gram hashing in pure Python, including for multilingual text."""
    normalized = text.lower()
    counts: dict[int, float] = {}
    for start in range(len(normalized)):
        for width in range(2, 6):
            end = start + width
            if end > len(normalized):
                break
            gram = normalized[start:end].encode("utf-8")
            bucket = int.from_bytes(hashlib.blake2b(gram, digest_size=8).digest(), "little") % dimensions
            counts[bucket] = counts.get(bucket, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
    vector = [0.0] * dimensions
    for bucket, count in counts.items():
        vector[bucket] = count / norm
    return vector


def embed_texts(texts: list[str], purpose: str = "document") -> list[Vector]:
    if not texts:
        return []
    if purpose not in {"document", "query"}:
        raise EmbeddingError("Embedding purpose must be 'document' or 'query'.")
    provider_signature()
    if EMBEDDING_PROVIDER == "local":
        return [_local_embedding(text) for text in texts]

    if not GEMINI_API_KEY:
        raise EmbeddingError("GEMINI_API_KEY is required when EMBEDDING_PROVIDER=gemini.")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        task_type = "RETRIEVAL_QUERY" if purpose == "query" else "RETRIEVAL_DOCUMENT"
        batches = []
        try:
            batch_size = max(1, EMBEDDING_BATCH_SIZE)
            for start in range(0, len(texts), batch_size):
                response = client.models.embed_content(
                    model=GEMINI_EMBEDDING_MODEL,
                    contents=texts[start:start + batch_size],
                    config=types.EmbedContentConfig(task_type=task_type),
                )
                batches.extend([float(value) for value in embedding.values] for embedding in response.embeddings)
        finally:
            client.close()
        if len(batches) != len(texts):
            raise EmbeddingError("Gemini returned an unexpected number of embeddings.")
        normalized = []
        for vector in batches:
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            normalized.append([value / norm for value in vector])
        return normalized
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError("Gemini could not generate embeddings. Check your API key, model and connection.") from exc
