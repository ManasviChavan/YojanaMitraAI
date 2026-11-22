from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
from pathlib import Path
from typing import Any

from config.settings import VECTOR_STORE_PATH
from services.documents import chunk_text
from services.embedding_service import embed_texts, provider_signature

INDEX_FILE = "official_sources.faiss"
METADATA_FILE = "chunks.json"
FALLBACK_FILE = "official_sources.vectors.bin"


class RetrievalError(RuntimeError):
    pass


def _load_faiss():
    """Return FAISS when its native extension loads; some managed Windows hosts block the DLL."""
    try:
        import faiss
        return faiss
    except Exception:
        return None


def active_backend() -> str:
    faiss = _load_faiss()
    index_path, metadata_path = _paths()
    fallback_path = VECTOR_STORE_PATH / FALLBACK_FILE
    try:
        metadata = _read_metadata(metadata_path)
        _faiss, backend = _select_backend(metadata, index_path, fallback_path, faiss)
    except RetrievalError:
        backend = "faiss" if faiss is not None else "numpy-fallback"
    return "faiss" if backend == "faiss" else "pure-Python vector fallback"


def _paths() -> tuple[Path, Path]:
    VECTOR_STORE_PATH.mkdir(parents=True, exist_ok=True)
    return VECTOR_STORE_PATH / INDEX_FILE, VECTOR_STORE_PATH / METADATA_FILE


def _read_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "embedding_signature": provider_signature(), "chunks": []}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetrievalError("The local vector metadata could not be read. Clear the index and re-index sources.") from exc
    if not isinstance(result, dict):
        raise RetrievalError("The local vector metadata has a legacy or invalid format. Reset the index and search again.")
    if result.get("embedding_signature") != provider_signature():
        raise RetrievalError("The embedding provider/model changed. Clear the local index and re-index sources before searching.")
    if not isinstance(result.get("chunks"), list):
        raise RetrievalError("The local vector metadata has an invalid format.")
    return result


def _select_backend(metadata: dict[str, Any], index_path: Path, fallback_path: Path, available_faiss: Any) -> tuple[Any, str]:
    """Keep using the backend that owns the persisted index when its data is present."""
    stored = metadata.get("vector_backend")
    has_chunks = bool(metadata.get("chunks"))
    if has_chunks and stored == "numpy-fallback" and fallback_path.exists():
        return None, "numpy-fallback"
    if has_chunks and stored == "faiss" and available_faiss is not None and index_path.exists():
        return available_faiss, "faiss"
    if available_faiss is not None and (not has_chunks or index_path.exists()):
        return available_faiss, "faiss"
    if fallback_path.exists():
        return None, "numpy-fallback"
    if has_chunks:
        raise RetrievalError("The stored vector index files are missing. Restore the index files or clear the index and search again.")
    return (available_faiss, "faiss") if available_faiss is not None else (None, "numpy-fallback")


def _chunk_id(url: str, page: int | None, text: str) -> int:
    digest = hashlib.sha256(f"{url}\0{page}\0{text}".encode("utf-8")).hexdigest()
    return int(digest[:15], 16)


def _atomic_write_index(index: Any, destination: Path, faiss: Any) -> None:
    handle, temp_name = tempfile.mkstemp(prefix="index-", suffix=".faiss", dir=destination.parent)
    os.close(handle)
    try:
        faiss.write_index(index, temp_name)
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _atomic_write_metadata(metadata: dict[str, Any], destination: Path) -> None:
    handle, temp_name = tempfile.mkstemp(prefix="chunks-", suffix=".json", dir=destination.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(metadata, output, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _read_fallback_vectors(path: Path, expected_rows: int, dimensions: int) -> list[list[float]]:
    if not path.exists():
        if expected_rows:
            raise RetrievalError("The local vector matrix is missing. Reset the local index and search again.")
        return []
    if dimensions < 1:
        raise RetrievalError("The local vector dimension is missing. Reset the local index and search again.")
    try:
        data = path.read_bytes()
        row_bytes = dimensions * 4
        if len(data) != expected_rows * row_bytes:
            raise RetrievalError("The local vector matrix and source metadata do not match. Reset the local index.")
        vectors = [
            list(struct.unpack_from(f"<{dimensions}f", data, offset * row_bytes))
            for offset in range(expected_rows)
        ]
    except RetrievalError:
        raise
    except Exception as exc:
        raise RetrievalError("The local vector matrix is unreadable. Reset the local index and re-index.") from exc
    return vectors


def _atomic_write_fallback_vectors(vectors: list[list[float]], destination: Path) -> None:
    handle, temp_name = tempfile.mkstemp(prefix="vectors-", suffix=".bin", dir=destination.parent)
    try:
        with os.fdopen(handle, "wb") as output:
            for vector in vectors:
                output.write(struct.pack(f"<{len(vector)}f", *vector))
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def index_documents(documents: list[dict[str, Any]], chunk_size: int = 1100, overlap: int = 160) -> int:
    """Index extracted documents; updated URLs replace old chunks and identical input is deduplicated."""
    available_faiss = _load_faiss()
    index_path, metadata_path = _paths()
    fallback_path = VECTOR_STORE_PATH / FALLBACK_FILE
    metadata = _read_metadata(metadata_path)
    faiss, backend = _select_backend(metadata, index_path, fallback_path, available_faiss)
    old_chunks = metadata["chunks"]
    by_url: dict[str, list[dict[str, Any]]] = {}
    for document in documents:
        url = document.get("url")
        text = document.get("text", "").strip()
        if not url or not text:
            continue
        if not document.get("retrieved_at") or not document.get("source_name"):
            raise RetrievalError("Every document must include its actual source name and retrieval timestamp.")
        for piece in chunk_text(text, chunk_size=chunk_size, overlap=overlap):
            item = {key: document.get(key) for key in (
                "title", "url", "source_name", "document_type", "page", "publication_date", "retrieved_at",
                "file_creation_date", "document_truncated", "pages_total"
            )}
            item["text"] = piece
            item["chunk_id"] = _chunk_id(url, item.get("page"), piece)
            by_url.setdefault(url, []).append(item)

    for url, items in by_url.items():
        unique = {item["chunk_id"]: item for item in items}
        by_url[url] = list(unique.values())

    if not by_url:
        return 0

    replacements = {url: chunks for url, chunks in by_url.items()}
    old_by_url: dict[str, list[dict[str, Any]]] = {}
    for chunk in old_chunks:
        old_by_url.setdefault(chunk["url"], []).append(chunk)

    changed_urls = set()
    metadata_only_urls = set()
    for url, new_chunks in replacements.items():
        old_ids = {c["chunk_id"] for c in old_by_url.get(url, [])}
        new_ids = {c["chunk_id"] for c in new_chunks}
        if old_ids != new_ids:
            changed_urls.add(url)
        elif old_by_url.get(url):
            metadata_only_urls.add(url)

    if not changed_urls and not metadata_only_urls:
        return 0

    retained = []
    for chunk in old_chunks:
        if chunk["url"] in changed_urls:
            continue
        if chunk["url"] in metadata_only_urls:
            updated = next((item for item in replacements[chunk["url"]] if item["chunk_id"] == chunk["chunk_id"]), chunk)
            retained.append(updated)
        else:
            retained.append(chunk)
    additions = [c for url in changed_urls for c in replacements[url]]
    new_metadata = {
        "version": 1,
        "embedding_signature": provider_signature(),
        "vector_backend": backend,
        "vector_dimension": metadata.get("vector_dimension"),
        "chunks": retained + additions,
    }

    if not changed_urls:
        _atomic_write_metadata(new_metadata, metadata_path)
        return 0

    if faiss is None:
        old_dimension = metadata.get("vector_dimension") or 0
        old_vectors = _read_fallback_vectors(fallback_path, len(old_chunks), old_dimension) if old_chunks else []
        if old_chunks:
            retained_positions = [i for i, chunk in enumerate(old_chunks) if chunk["url"] not in changed_urls]
            # The fallback stores vectors as Python lists, which do not support
            # NumPy-style list-of-indices selection.
            retained_vectors = [old_vectors[i] for i in retained_positions]
        else:
            retained_vectors = []
        additions_vectors = embed_texts([f"title: {c.get('title') or 'none'} | text: {c['text']}" for c in additions], "document") if additions else []
        if retained_vectors and additions_vectors and len(retained_vectors[0]) != len(additions_vectors[0]):
            raise RetrievalError("Embedding dimensions changed. Reset the local index and re-index sources.")
        vectors = retained_vectors + additions_vectors
        new_metadata["vector_dimension"] = len(vectors[0]) if vectors else old_dimension
        _atomic_write_fallback_vectors(vectors, fallback_path)
        _atomic_write_metadata(new_metadata, metadata_path)
        return len(additions)

    index = None
    if index_path.exists():
        try:
            index = faiss.read_index(str(index_path))
        except Exception as exc:
            raise RetrievalError("The FAISS index file is unreadable. Clear the index and re-index.") from exc
        if index.ntotal != len(old_chunks):
            raise RetrievalError("FAISS index and metadata row counts do not match. Clear the index and re-index.")
        if not isinstance(index, faiss.IndexIDMap2):
            raise RetrievalError("Unexpected FAISS index type. Clear the index and re-index.")
    elif old_chunks:
        raise RetrievalError("FAISS index is missing while metadata exists. Clear the index and re-index.")

    if index is None:
        additions_vectors = embed_texts([f"title: {c.get('title') or 'none'} | text: {c['text']}" for c in additions], "document")
        np = __import__("numpy")
        faiss_vectors = np.asarray(additions_vectors, dtype="float32")
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(faiss_vectors.shape[1]))
        index.add_with_ids(faiss_vectors, np.asarray([c["chunk_id"] for c in additions], dtype="int64"))
        new_metadata["vector_dimension"] = int(faiss_vectors.shape[1])
    else:
        np = __import__("numpy")
        removed_ids = [c["chunk_id"] for c in old_chunks if c["url"] in changed_urls]
        if removed_ids:
            index.remove_ids(np.asarray(removed_ids, dtype="int64"))
        if additions:
            vectors = np.asarray(embed_texts([f"title: {c.get('title') or 'none'} | text: {c['text']}" for c in additions], "document"), dtype="float32")
            if vectors.shape[1] != index.d:
                raise RetrievalError("Embedding dimensions changed. Clear the local index and re-index sources.")
            index.add_with_ids(vectors, np.asarray([c["chunk_id"] for c in additions], dtype="int64"))
        new_metadata["vector_dimension"] = int(index.d)

    _atomic_write_index(index, index_path, faiss)
    _atomic_write_metadata(new_metadata, metadata_path)
    return len(additions)


def retrieve_chunks(query: str, k: int = 6, min_score: float | None = None) -> list[dict[str, Any]]:
    available_faiss = _load_faiss()
    index_path, metadata_path = _paths()
    fallback_path = VECTOR_STORE_PATH / FALLBACK_FILE
    metadata = _read_metadata(metadata_path)
    faiss, _backend = _select_backend(metadata, index_path, fallback_path, available_faiss)
    chunks = metadata["chunks"]
    if not chunks:
        return []
    query_vector = embed_texts([query], "query")[0]
    if faiss is None:
        vectors = _read_fallback_vectors(fallback_path, len(chunks), int(metadata.get("vector_dimension") or 0))
        if len(query_vector) != int(metadata.get("vector_dimension") or 0):
            raise RetrievalError("Query embedding dimensions do not match the index. Re-index with the same provider.")
        scores = [sum(a * b for a, b in zip(query_vector, vector)) for vector in vectors]
        ranked_ids = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)[:min(max(1, k), len(chunks))]
        threshold = min_score if min_score is not None else (0.10 if provider_signature().startswith("local:") else 0.30)
        return [{**chunks[i], "score": float(scores[i])} for i in ranked_ids if scores[i] >= threshold]
    if not index_path.exists():
        raise RetrievalError("FAISS index is missing while source metadata exists. Reset the local index and search again.")
    index = faiss.read_index(str(index_path))
    if index.ntotal != len(chunks) or not isinstance(index, faiss.IndexIDMap2):
        raise RetrievalError("FAISS index and metadata are inconsistent. Clear the index and re-index.")
    np = __import__("numpy")
    query_vector = np.asarray([query_vector], dtype="float32")
    if query_vector.shape[1] != index.d:
        raise RetrievalError("Query embedding dimensions do not match the index. Re-index with the same embedding provider.")
    scores, ids = index.search(query_vector, min(max(1, k), index.ntotal))
    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    threshold = min_score if min_score is not None else (0.10 if provider_signature().startswith("local:") else 0.30)
    results = []
    for score, chunk_id in zip(scores[0], ids[0]):
        if chunk_id < 0 or score < threshold:
            continue
        chunk = by_id.get(int(chunk_id))
        if chunk:
            results.append({**chunk, "score": float(score)})
    return results


def clear_index() -> None:
    index_path, metadata_path = _paths()
    index_path.unlink(missing_ok=True)
    metadata_path.unlink(missing_ok=True)
    (VECTOR_STORE_PATH / FALLBACK_FILE).unlink(missing_ok=True)
