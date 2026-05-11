import time
import logging
from datetime import date, datetime
from google_play_scraper import reviews, Sort

try:
    from langdetect import DetectorFactory, LangDetectException, detect

    DetectorFactory.seed = 0
except ImportError:  # pragma: no cover - fallback for older local environments
    detect = None
    LangDetectException = Exception

logger = logging.getLogger(__name__)

# ── original generic fetcher (kept for CLI backward compat) ──────────────────

SORT_MAP = {
    "newest": Sort.NEWEST,
    "rating": Sort.RATING,
    "helpfulness": Sort.MOST_RELEVANT,
}


def fetch_reviews(app_id: str, count: int, lang: str = "en", sort: str = "newest") -> list[dict]:
    sort_order = SORT_MAP.get(sort, Sort.NEWEST)
    collected = []
    continuation_token = None
    max_attempts = 3

    while len(collected) < count:
        batch_size = min(200, count - len(collected))
        for attempt in range(1, max_attempts + 1):
            try:
                result, continuation_token = reviews(
                    app_id,
                    lang=lang,
                    country="us",
                    sort=sort_order,
                    count=batch_size,
                    continuation_token=continuation_token,
                )
                break
            except Exception as exc:
                delay = 2 ** attempt
                logger.warning("App %s attempt %d failed: %s — retrying in %ds", app_id, attempt, exc, delay)
                if attempt == max_attempts:
                    logger.error("App %s permanently failed after %d attempts", app_id, max_attempts)
                    return _normalize_generic(app_id, collected)
                time.sleep(delay)

        if not result:
            break
        collected.extend(result)
        if continuation_token is None:
            break

    return _normalize_generic(app_id, collected[:count])


def _normalize_generic(app_id: str, raw: list) -> list[dict]:
    out = []
    for r in raw:
        out.append({
            "app_id": app_id,
            "review_id": r.get("reviewId", ""),
            "user_name": r.get("userName", ""),
            "rating": r.get("score", 0),
            "content": r.get("content", ""),
            "thumbs_up": r.get("thumbsUpCount", 0),
            "review_at": r.get("at", ""),
            "reply_content": r.get("replyContent", ""),
            "replied_at": r.get("repliedAt", ""),
        })
    return out


# ── research-grade balanced fetcher ─────────────────────────────────────────

TARGET_PER_GROUP = 100
MIN_WORDS = 50
MAX_RAW_PER_SCORE = 5000
RESEARCH_FIELDS = [
    "review_id",
    "app_name",
    "score",
    "content",
    "word_count",
    "date",
    "sentiment_group",
]


def fetch_balanced_reviews(app_id: str, app_name: str) -> list[dict]:
    """
    Returns up to 200 reviews: 100 positive (4-5★) + 100 negative (1-2★).
    Each review is English, ≥50 words, deduplicated, sorted newest-first.
    """
    return fetch_balanced_reviews_filtered(app_id, app_name)


def fetch_balanced_reviews_filtered(
    app_id: str,
    app_name: str,
    total_reviews: int = 200,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> list[dict]:
    """
    Returns balanced reviews split evenly between positive (4-5★) and negative (1-2★).
    Reviews are English, ≥50 words, deduplicated, newest-first, and optionally date-filtered.
    """
    target_per_group = _validate_total_reviews(total_reviews) // 2
    parsed_start = _parse_date(start_date, "start_date")
    parsed_end = _parse_date(end_date, "end_date")

    if parsed_start and parsed_end and parsed_start > parsed_end:
        raise ValueError("start_date cannot be after end_date")

    positive = _fetch_by_scores(app_id, [5, 4], target=target_per_group, start_date=parsed_start, end_date=parsed_end)
    negative = _fetch_by_scores(app_id, [1, 2], target=target_per_group, start_date=parsed_start, end_date=parsed_end)

    return _build_output(positive, negative, app_name)


def _fetch_by_scores(
    app_id: str,
    scores: list[int],
    target: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """Fetch until the score group has `target` qualifying reviews or pages run out."""
    qualifying = []
    seen_ids = set()
    seen_user_texts = set()
    max_attempts = 3

    state = {
        score: {
            "continuation_token": None,
            "fetched_raw": 0,
            "active": True,
        }
        for score in scores
    }

    while len(qualifying) < target and any(item["active"] for item in state.values()):
        round_added = 0

        for score in scores:
            if len(qualifying) >= target:
                break

            score_state = state[score]
            if not score_state["active"] or score_state["fetched_raw"] >= MAX_RAW_PER_SCORE:
                score_state["active"] = False
                continue

            batch = 200
            for attempt in range(1, max_attempts + 1):
                try:
                    result, continuation_token = reviews(
                        app_id,
                        lang="en",
                        country="us",
                        sort=Sort.NEWEST,
                        count=batch,
                        filter_score_with=score,
                        continuation_token=score_state["continuation_token"],
                    )
                    break
                except Exception as exc:
                    delay = 2 ** attempt
                    logger.warning(
                        "App %s score=%d attempt %d: %s — retry in %ds",
                        app_id, score, attempt, exc, delay,
                    )
                    if attempt == max_attempts:
                        result, continuation_token = [], None
                    else:
                        time.sleep(delay)

            score_state["continuation_token"] = continuation_token
            score_state["fetched_raw"] += len(result)

            for r in result:
                rid = r.get("reviewId", "")
                user = (r.get("userName") or "").strip().lower()
                text = (r.get("content") or "").strip()
                user_text_key = (user, text.lower())

                if rid and rid in seen_ids:
                    continue
                if user_text_key in seen_user_texts:
                    continue

                if _is_qualifying_review(r, start_date=start_date, end_date=end_date):
                    seen_ids.add(rid)
                    seen_user_texts.add(user_text_key)
                    qualifying.append(r)
                    round_added += 1

            if not result or continuation_token is None:
                score_state["active"] = False

        if round_added == 0 and not any(item["active"] for item in state.values()):
            break

    return sorted(
        qualifying,
        key=lambda r: r.get("at") or "",
        reverse=True,
    )[:target]


def _is_qualifying_review(review: dict, start_date: date | None = None, end_date: date | None = None) -> bool:
    text = (review.get("content") or "").strip()
    return (
        bool(text)
        and _word_count(text) >= MIN_WORDS
        and _is_english(text)
        and _is_within_date_range(review.get("at"), start_date, end_date)
    )


def _is_within_date_range(value: object, start_date: date | None, end_date: date | None) -> bool:
    review_date = _coerce_review_date(value)
    if review_date is None:
        return start_date is None and end_date is None
    if start_date and review_date < start_date:
        return False
    if end_date and review_date > end_date:
        return False
    return True


def _coerce_review_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _parse_date(value: str | date | None, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"{label} must use YYYY-MM-DD format") from exc
    raise ValueError(f"{label} must be a YYYY-MM-DD string or date")


def _validate_total_reviews(total_reviews: int) -> int:
    try:
        total = int(total_reviews)
    except (TypeError, ValueError) as exc:
        raise ValueError("total_reviews must be an even integer") from exc

    if total < 2:
        raise ValueError("total_reviews must be at least 2")
    if total % 2 != 0:
        raise ValueError("total_reviews must be even so positive and negative reviews split equally")
    return total


def _is_english(text: str) -> bool:
    if detect is None:
        return True

    try:
        return detect(text) == "en"
    except LangDetectException:
        return False


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def _build_output(positives: list[dict], negatives: list[dict], app_name: str) -> list[dict]:
    # assign sequential review IDs
    out = []
    for i, r in enumerate(positives + negatives, start=1):
        score = r.get("score", 0)
        date_raw = r.get("at")
        date_str = date_raw.strftime("%Y-%m-%d") if hasattr(date_raw, "strftime") else str(date_raw)[:10]
        content = (r.get("content") or "").strip()
        out.append({
            "review_id": f"R{i:04d}",
            "app_name": app_name,
            "score": score,
            "content": content,
            "word_count": _word_count(content),
            "date": date_str,
            "sentiment_group": "positive" if score >= 4 else "negative",
        })

    return out
