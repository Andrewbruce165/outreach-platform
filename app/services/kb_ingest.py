"""Phase 16 — KB ingest pipeline (KB-02/KB-03, D-06).

Pure pipeline pieces that turn an uploaded/pasted document blob into searchable
chunks + embeddings. The background ``KnowledgeIngestWorker``
(``app/services/kb_ingest_worker.py``) orchestrates these off the HTTP request:

    extract_text_async(blob, kind) → chunk_text(text) → embed_texts(chunks)

Design notes:
- **Off the event loop (Pitfall 3):** ``pypdf`` / ``python-docx`` / ``tiktoken``
  are sync + CPU-bound. ``extract_text_async`` wraps the dispatch in
  ``asyncio.to_thread``; the worker also runs ``chunk_text`` via ``to_thread``.
  The embedding HTTP call (``embed_texts``) is natively async (AsyncOpenAI).
- **Token-accurate chunking (Pitfall 7):** ``tiktoken`` ``cl100k_base`` is the
  encoding for the ``text-embedding-3`` family; ~800/120 keeps every chunk far
  below the 8191-token embed limit by construction.
- **Batched embeds (Pitfall 6):** ``embed_texts`` batches chunks into groups of
  ≤256 (≈205k tokens at 800 tok/chunk) so one request never exceeds the OpenAI
  2048-item / 300k-token ceiling. ``resp.data`` is order-preserving; we
  concatenate batches in order.
- **Monkeypatch contract:** the worker calls ``kb_ingest.embed_texts`` by module
  reference, so worker tests patch ``app.services.kb_ingest.embed_texts`` to a
  deterministic stub (no OpenAI network). Keep ``embed_texts`` a module-level
  function (not a bound method) so the patch target stays stable.

Empty/whitespace-only documents → zero chunks (the caller marks the doc
``indexed`` with ``chunk_count=0``, NOT ``failed``).
"""

import asyncio
import io
import logging
from typing import Optional

import tiktoken

# Reuse the existing module-level AsyncOpenAI client (ai_engine.py:39) — do NOT
# create a second client (one connection pool, one place to configure the key).
from app.services.ai_engine import client

logger = logging.getLogger(__name__)

# Lazy tiktoken init: ``get_encoding`` fetches the BPE vocab on first call
# (the api container has outbound network — Pitfall/cold-start note in RESEARCH).
# Deferring it avoids paying that fetch at import time for processes that never
# chunk (e.g. the listener, which shares requirements.txt but never ingests).
_enc: Optional["tiktoken.Encoding"] = None

# Per-request embedding batch size. ≤256 chunks ≈ 205k tokens at ~800 tok/chunk,
# comfortably under the OpenAI 2048-item / 300k-token per-request ceiling (Pitfall 6).
_EMBED_BATCH_SIZE = 256


def _get_encoding() -> "tiktoken.Encoding":
    """Lazily initialise the cl100k_base encoding (text-embedding-3 family)."""
    global _enc
    if _enc is None:
        _enc = tiktoken.get_encoding("cl100k_base")
    return _enc


def chunk_text(text: str, max_tokens: int = 800, overlap: int = 120) -> list[str]:
    """Split ``text`` into token-bounded chunks with a sliding overlap window.

    Each chunk holds ≤ ``max_tokens`` tokens; adjacent chunks overlap by
    ``overlap`` tokens so an answer is never split across a hard boundary.
    Short text → a single chunk. Empty / whitespace-only → ``[]`` (the caller
    marks the doc indexed with chunk_count=0, not failed).
    """
    if not text or not text.strip():
        return []

    enc = _get_encoding()
    toks = enc.encode(text)
    chunks: list[str] = []
    start = 0
    while start < len(toks):
        end = min(start + max_tokens, len(toks))
        chunks.append(enc.decode(toks[start:end]))
        if end == len(toks):
            break
        start = end - overlap  # sliding window with overlap
    return chunks


def _extract_pdf(blob: bytes) -> str:
    """PDF → text via pypdf, joining each page's extracted text."""
    import pypdf  # local import — keeps the dep out of the import path for non-ingest processes

    reader = pypdf.PdfReader(io.BytesIO(blob))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _extract_docx(blob: bytes) -> str:
    """DOCX → text via python-docx, joining each paragraph."""
    import docx  # local import (python-docx)

    document = docx.Document(io.BytesIO(blob))
    return "\n".join(p.text for p in document.paragraphs)


def _extract_plaintext(blob: bytes) -> str:
    """TXT / MD / CSV / pasted-text → utf-8 with a latin-1 fallback.

    Mirrors the contacts CSV encoding sniff: try strict utf-8, fall back to
    latin-1 (never raises on arbitrary bytes) so a non-utf-8 upload doesn't fail
    the whole ingest.
    """
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return blob.decode("latin-1")


def extract_text(blob: bytes, source_kind: str) -> str:
    """Dispatch raw bytes → text by ``source_kind``.

    Supported kinds: ``pdf`` | ``docx`` | ``txt`` | ``md`` | ``csv`` | ``text``
    (``text`` = pasted text whose utf-8 bytes live in ``kb_documents.raw_content``).
    Unknown kind → ``ValueError``.

    CPU-bound — call ``extract_text_async`` from async code (Pitfall 3).
    """
    if source_kind == "pdf":
        return _extract_pdf(blob)
    if source_kind == "docx":
        return _extract_docx(blob)
    if source_kind in ("txt", "md", "csv", "text"):
        return _extract_plaintext(blob)
    raise ValueError(f"unsupported source_kind: {source_kind}")


async def extract_text_async(blob: bytes, source_kind: str) -> str:
    """Run the CPU-bound ``extract_text`` in a thread (Pitfall 3 — never block the loop)."""
    return await asyncio.to_thread(extract_text, blob, source_kind)


async def embed_texts(texts: list[str], model: str) -> list[list[float]]:
    """Embed ``texts`` via the existing AsyncOpenAI client, order-preserving.

    Batches the input into groups of ≤256 (Pitfall 6) so one request never
    exceeds the OpenAI 2048-item / 300k-token ceiling. Each returned vector is
    1536-dim for ``text-embedding-3-small``. ``resp.data`` is ordered to match
    the batch input; batches are concatenated in order, so the output index lines
    up with the input index.

    Lets ``RateLimitError`` / ``APIError`` propagate — the worker's ``_tick``
    catches and flips the doc to ``failed`` so it can be re-indexed. ``[]`` in →
    ``[]`` out (no API call).

    NB: the worker references this by module path so tests patch
    ``app.services.kb_ingest.embed_texts`` to a deterministic stub.
    """
    if not texts:
        return []

    vectors: list[list[float]] = []
    for i in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch = texts[i:i + _EMBED_BATCH_SIZE]
        resp = await client.embeddings.create(model=model, input=batch)
        vectors.extend(d.embedding for d in resp.data)
    return vectors
