"""Product reviews with moderation. Only verified buyers' approved reviews count toward ratings."""
import re
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.utils.dates import utc_now

BANNED_WORDS = {"scam", "fake"}
MIN_LENGTH = 10


@dataclass
class Review:
    sku: str
    customer_id: str
    rating: int                       # 1-5
    text: str
    verified_buyer: bool = False
    approved: bool = False
    created: datetime = field(default_factory=utc_now)


def needs_manual_review(review: Review) -> bool:
    words = set(re.findall(r"[a-z]+", review.text.lower()))
    return bool(words & BANNED_WORDS) or "http" in review.text.lower() or not review.verified_buyer


def moderate(review: Review) -> Review:
    """Auto-approve clean reviews from verified buyers; everything else waits for staff."""
    if not 1 <= review.rating <= 5:
        raise ValueError("rating must be between 1 and 5")
    if len(review.text.strip()) < MIN_LENGTH:
        raise ValueError(f"review must be at least {MIN_LENGTH} characters")
    review.approved = not needs_manual_review(review)
    return review


def average_rating(reviews: list[Review]) -> float | None:
    """Mean of approved, verified ratings rounded to one decimal; None if there are none."""
    counted = [r.rating for r in reviews if r.approved and r.verified_buyer]
    return round(sum(counted) / len(counted), 1) if counted else None


def rating_histogram(reviews: list[Review]) -> dict[int, int]:
    hist = {star: 0 for star in range(1, 6)}
    for r in reviews:
        if r.approved:
            hist[r.rating] += 1
    return hist
