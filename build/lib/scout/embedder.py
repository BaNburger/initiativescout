"""Dense embedding generation and similarity search using model2vec.

Lightweight (~15MB model, numpy-only) semantic embeddings for initiative
similarity search. Vectors are stored as .npy sidecar files next to the DB.

Install: pip install model2vec
"""
from __future__ import annotations

import logging
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

from sqlalchemy import select
from sqlalchemy.orm import Session

from scout.db import DATA_DIR, current_db_name
from scout.models import Enrichment, Initiative

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy model loader (singleton)
# ---------------------------------------------------------------------------

_model = None


def _check_deps() -> None:
    """Raise ImportError with a clear message if optional deps are missing."""
    if np is None:
        raise ImportError("Embeddings require numpy and model2vec. Install: pip install 'scout[embeddings]'")


def _get_model():
    global _model
    if _model is None:
        _check_deps()
        try:
            from model2vec import StaticModel
        except ImportError:
            raise ImportError("model2vec not installed. Install: pip install 'scout[embeddings]'")
        _model = StaticModel.from_pretrained("minishlab/potion-base-32M")
    return _model


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
    _check_deps()
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

    # Save sidecar files
    emb_path, ids_path = _sidecar_paths()
    np.save(emb_path, vectors)
    np.save(ids_path, ids)

    log.info("Embedded %d initiatives → %s", len(inits), emb_path)
    return len(inits)


# ---------------------------------------------------------------------------
# Re-embed one
# ---------------------------------------------------------------------------


def re_embed_one(session: Session, init: Initiative) -> None:
    """Re-embed a single initiative, updating the sidecar files in-place.

    No-op if sidecar files don't exist yet (user must run embed_all first).
    """
    _check_deps()
    emb_path, ids_path = _sidecar_paths()
    if not emb_path.exists() or not ids_path.exists():
        return

    model = _get_model()
    vectors = np.load(emb_path)
    ids = np.load(ids_path)

    # Build text
    enrichments = session.execute(
        select(Enrichment).where(Enrichment.initiative_id == init.id)
    ).scalars().all()
    summaries = [e.summary or "" for e in enrichments]
    text = _build_text(init, summaries)

    # Encode and normalize
    vec = model.encode([text], show_progress_bar=False)[0]
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm

    # Find existing position or append
    idx = np.where(ids == init.id)[0]
    if len(idx) > 0:
        vectors[idx[0]] = vec
    else:
        vectors = np.vstack([vectors, vec.reshape(1, -1)])
        ids = np.append(ids, init.id)

    np.save(emb_path, vectors)
    np.save(ids_path, ids)


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
    _check_deps()
    emb_path, ids_path = _sidecar_paths()
    if not emb_path.exists() or not ids_path.exists():
        return []

    vectors = np.load(emb_path)
    ids = np.load(ids_path)

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

    # Apply mask and get top-k
    masked_scores = np.where(mask, scores, -np.inf)
    top_indices = np.argsort(masked_scores)[::-1][:top_k]

    results = []
    for i in top_indices:
        if not np.isfinite(masked_scores[i]):
            break
        results.append((int(ids[i]), round(float(scores[i]), 4)))

    return results
