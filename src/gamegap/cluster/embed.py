"""Embedding + UMAP + HDBSCAN. Heavy deps are imported lazily (install the `ml` extra)."""

from __future__ import annotations


def cluster_wants(texts: list[str], min_cluster_size: int = 8) -> list[int]:
    """Returns a cluster label per text (-1 = noise)."""
    import hdbscan
    import umap
    from sentence_transformers import SentenceTransformer

    emb = SentenceTransformer("all-MiniLM-L6-v2").encode(texts, show_progress_bar=False)
    reduced = umap.UMAP(n_components=5, metric="cosine", random_state=42).fit_transform(emb)
    return hdbscan.HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(reduced).tolist()
