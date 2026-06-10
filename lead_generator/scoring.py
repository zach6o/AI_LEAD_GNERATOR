from __future__ import annotations

from typing import Any

# Industries where a single closed deal tends to be worth more, so we bias
# the score upward when we find them with weak digital presence.
HIGH_VALUE_INDUSTRIES = {"dentist", "law_firm", "real_estate", "clinic", "hotel"}


def score_prospect(p: dict[str, Any]) -> int:
    """Return a 0-100 lead score based on signals available at hunt time.

    The score answers: "how badly does this business need what we sell, and
    how cheaply can we reach them?" Higher = better target.
    """
    score = 0

    has_website = bool(p.get("website"))
    has_phone = bool(p.get("phone"))
    has_email = bool(p.get("email"))
    has_whatsapp = bool(p.get("whatsapp"))
    rating = p.get("google_rating")
    reviews = p.get("google_reviews") or 0
    industry = p.get("industry") or "other"

    # Missing-website is the strongest signal — entire flagship service.
    if not has_website:
        score += 35
    elif rating is not None and rating < 4.0:
        # Has a site but rating suggests bad experience -> redesign / chatbot pitch.
        score += 15

    # Reachability — cheaper outreach = higher score.
    if has_phone:
        score += 10
    if has_email:
        score += 10
    if has_whatsapp:
        score += 10

    # Activity signal — businesses with traction are more likely to spend.
    if reviews >= 200:
        score += 15
    elif reviews >= 50:
        score += 10
    elif reviews >= 10:
        score += 5

    # Industry bias.
    if industry in HIGH_VALUE_INDUSTRIES:
        score += 10

    # Quality floor — if rating is great AND has good website, less to sell.
    if has_website and rating is not None and rating >= 4.7 and reviews >= 100:
        score -= 15

    return max(0, min(100, score))
