"""Final ranking of search results. Pure functions, unit-tested.

final score = SIMILARITY_WEIGHT × similarity (rescaled) + TASTE_WEIGHT × taste score

Why rescale similarity? Gemini's cosine similarities for one query sit in a narrow band
(measured on our catalog: ~0.60-0.72), while the taste score spans 0-1. Mixed raw, taste
would decide almost every ranking. Rescaling to 0-1 *within one result set* (best = 1,
worst = 0) lets both signals count as intended.

Why 0.6 / 0.4? The request is what the customer asked for *now*, so it leads; the taste
profile is a long-term preference that should break ties and nudge, not override the
request. (README: justify these weights.)
"""

from collections.abc import Mapping

SIMILARITY_WEIGHT = 0.6
TASTE_WEIGHT = 0.4
MIN_SPREAD = 0.02   # below this the similarities are effectively equal (no meaningful order)


def rescale(similarities: Mapping[int, float]) -> dict[int, float]:
    """Min-max scale to 0-1. All (nearly) equal → all 1.0, so only taste decides."""
    if not similarities:
        return {}
    low, high = min(similarities.values()), max(similarities.values())
    if high - low < MIN_SPREAD:
        return {k: 1.0 for k in similarities}
    return {k: (v - low) / (high - low) for k, v in similarities.items()}


def final_score(similarity: float, taste: float) -> float:
    return round(SIMILARITY_WEIGHT * similarity + TASTE_WEIGHT * taste, 4)


def rank(candidates: list[dict], similarity_by_record: Mapping[int, float], taste_by_record: Mapping[int, float]) -> list[dict]:
    """Attach scores and sort best first. Ties: cheaper copy first, then better condition order kept.

    Each candidate is a copy; similarity and taste belong to its release (record_id).
    A release the vector search didn't return gets similarity 0.
    """
    scaled = rescale(similarity_by_record)
    ranked = []
    for c in candidates:
        rid = c['record_id']
        sim, taste = scaled.get(rid, 0.0), taste_by_record.get(rid, 0.0)
        ranked.append(c | {
            'similarity': round(similarity_by_record.get(rid, 0.0), 4),
            'taste': round(taste, 4),
            'score': final_score(sim, taste),
        })
    return sorted(ranked, key=lambda c: (-c['score'], c['price'], c['item_id']))
