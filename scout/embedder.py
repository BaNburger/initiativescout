"""Dense embedding generation and similarity search using model2vec.

Lightweight (~15MB model, numpy-only) semantic embeddings for initiative
similarity search. Vectors are stored as .npy sidecar files next to the DB.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

try:
    import numpy as np
    from model2vec import StaticModel
    _MODEL2VEC_AVAILABLE = True
except ImportError:
    np = None  # type: ignore[assignment]
    StaticModel = None  # type: ignore[assignment,misc]
    _MODEL2VEC_AVAILABLE = False
from sqlalchemy.orm import Session

from scout.db import DATA_DIR, current_db_name
from scout.models import Enrichment, Initiative

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy model loader (singleton)
# ---------------------------------------------------------------------------

_model = None
# In-memory cache for sidecar arrays — avoids re-reading .npy files on every find_similar() call.
# Keyed by db stem name; invalidated on embed_all() and re_embed_one().
_vec_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}


def _get_model():  # type: ignore[return]
    global _model
    if _model is None:
        if not _MODEL2VEC_AVAILABLE:
            raise ImportError(
                "model2vec is required for semantic search. "
                "Install it with: pip install 'scout[embeddings]'"
            )
        _model = StaticModel.from_pretrained("minishlab/potion-base-32M")
    return _model


def _cache_vectors(vectors: np.ndarray, ids: np.ndarray) -> None:
    """Store vectors in the in-memory cache for the current database."""
    _vec_cache[current_db_name()] = (vectors, ids)


def _load_vectors() -> tuple[np.ndarray, np.ndarray] | None:
    """Load vectors from cache or disk. Returns None if no sidecar files exist."""
    stem = current_db_name()
    if stem in _vec_cache:
        return _vec_cache[stem]
    emb_path, ids_path = _sidecar_paths()
    if not emb_path.exists() or not ids_path.exists():
        return None
    vectors = np.load(emb_path)
    ids = np.load(ids_path)
    _vec_cache[stem] = (vectors, ids)
    return vectors, ids


# ---------------------------------------------------------------------------
# Sidecar file paths
# ---------------------------------------------------------------------------


def _sidecar_paths() -> tuple[Path, Path]:
    """Return (embeddings.npy, ids.npy) paths for the current database."""
    stem = current_db_name()
    return DATA_DIR / f"{stem}_embeddings.npy", DATA_DIR / f"{stem}_embed_ids.npy"


# ---------------------------------------------------------------------------
# Text builder
# ---------------------------------------------------------------------------


def _build_text(init: Initiative, enrichment_summaries: list[str] | None = None) -> str:
    """Concatenate initiative fields into a single text for embedding."""
    parts = [
        init.name or "",
        init.uni or "",
        init.faculty or "",
        init.description or "",
        init.sector or "",
        init.technology_domains or "",
        init.market_domains or "",
        init.categories or "",
    ]
    if enrichment_summaries:
        parts.extend(enrichment_summaries)
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Batch embed
# ---------------------------------------------------------------------------


def embed_all(session: Session) -> int:
    """Embed all initiatives and save to .npy sidecar files. Returns count."""
    model = _get_model()

    # Load initiatives with their enrichment summaries
    inits = session.execute(
        select(Initiative).order_by(Initiative.id)
    ).scalars().all()

    if not inits:
        return 0

    # Preload enrichment summaries
    enrichments = session.execute(select(Enrichment)).scalars().all()
    enrich_map: dict[int, list[str]] = {}
    for e in enrichments:
        enrich_map.setdefault(e.initiative_id, []).append(e.summary or "")

    texts = [_build_text(init, enrich_map.get(init.id)) for init in inits]
    ids = np.array([init.id for init in inits], dtype=np.int64)

    # Batch encode and L2-normalize
    vectors = model.encode(texts, show_progress_bar=False)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1  # avoid division by zero
    vectors = vectors / norms

    # Save sidecar files and update cache
    emb_path, ids_path = _sidecar_paths()
    np.save(emb_path, vectors)
    np.save(ids_path, ids)
    _cache_vectors(vectors, ids)

    log.info("Embedded %d initiatives → %s", len(inits), emb_path)
    return len(inits)


# ---------------------------------------------------------------------------
# Re-embed one
# ---------------------------------------------------------------------------


def re_embed_one(session: Session, init: Initiative) -> None:
    """Re-embed a single initiative, updating the sidecar files in-place.

    Auto-initializes sidecar files if they don't exist yet.
    """
    emb_path, ids_path = _sidecar_paths()

    # Build text and encode first (also determines vector dimension for auto-init)
    enrichments = session.execute(
        select(Enrichment).where(Enrichment.initiative_id == init.id)
    ).scalars().all()
    summaries = [e.summary or "" for e in enrichments]
    txt = _build_text(init, summaries)

    model = _get_model()
    vec = model.encode([txt], show_progress_bar=False)[0]
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm

    # Load existing sidecar files or auto-initialize empty arrays
    cached = _load_vectors()
    if cached is not None:
        vectors, ids = cached
    else:
        vectors = np.empty((0, vec.shape[0]), dtype=np.float32)
        ids = np.empty(0, dtype=np.int64)

    # Find existing position or append
    idx = np.where(ids == init.id)[0]
    if len(idx) > 0:
        # In-place update — no copy needed
        vectors[idx[0]] = vec
    else:
        # Must copy to extend
        vectors = np.vstack([vectors, vec.reshape(1, -1)])
        ids = np.append(ids, init.id)

    np.save(emb_path, vectors)
    np.save(ids_path, ids)
    _cache_vectors(vectors, ids)


# ---------------------------------------------------------------------------
# Similarity search
# ---------------------------------------------------------------------------


def find_similar(
    *,
    query_text: str | None = None,
    initiative_id: int | None = None,
    top_k: int = 10,
    exclude_id: int | None = None,
    id_mask: set[int] | None = None,
) -> list[tuple[int, float]]:
    """Find similar initiatives by cosine similarity.

    Args:
        query_text: Free-text query to embed and search against.
        initiative_id: Find initiatives similar to this one (uses stored vector).
        top_k: Number of results to return.
        exclude_id: Exclude this initiative ID from results (e.g. the query itself).
        id_mask: If provided, only return results with IDs in this set.

    Returns:
        List of (initiative_id, similarity_score) tuples, sorted by similarity descending.
    """
    cached = _load_vectors()
    if cached is None:
        return []

    vectors, ids = cached

    if len(vectors) == 0:
        return []

    # Get query vector
    if initiative_id is not None:
        idx = np.where(ids == initiative_id)[0]
        if len(idx) == 0:
            return []
        query_vec = vectors[idx[0]]
        if exclude_id is None:
            exclude_id = initiative_id
    elif query_text is not None:
        model = _get_model()
        query_vec = model.encode([query_text], show_progress_bar=False)[0]
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm
    else:
        return []

    # Cosine similarity (dot product on pre-normalized vectors)
    scores = vectors @ query_vec

    # Build mask
    mask = np.ones(len(ids), dtype=bool)
    if exclude_id is not None:
        mask &= ids != exclude_id
    if id_mask is not None:
        id_set = np.array(list(id_mask), dtype=np.int64)
        mask &= np.isin(ids, id_set)

    # Apply mask and get top-k (argpartition is O(n) vs argsort O(n log n))
    masked_scores = np.where(mask, scores, -np.inf)
    n_valid = int(np.sum(np.isfinite(masked_scores)))
    k = min(top_k, n_valid)
    if k == 0:
        return []
    # argpartition gives the top-k in arbitrary order, then we sort just those
    part_idx = np.argpartition(masked_scores, -k)[-k:]
    top_indices = part_idx[np.argsort(masked_scores[part_idx])[::-1]]

    return [
        (int(ids[i]), round(float(scores[i]), 4))
        for i in top_indices
        if np.isfinite(masked_scores[i])
    ]
