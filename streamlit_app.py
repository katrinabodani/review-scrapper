import csv
import io
import re
import sys
import os
from datetime import date

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
from fetcher import RESEARCH_FIELDS, fetch_balanced_reviews_filtered

# ── helpers ──────────────────────────────────────────────────────────────────

def extract_app_id(raw: str) -> str:
    raw = raw.strip()
    # Full Play Store URL — pull the id= param
    match = re.search(r"[?&]id=([a-zA-Z0-9._]+)", raw)
    if match:
        return match.group(1)
    # Bare package name (e.g. com.king.candycrushsaga)
    if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+", raw):
        return raw
    return ""


def reviews_to_csv_bytes(reviews: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=RESEARCH_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(reviews)
    return buf.getvalue().encode("utf-8")


# ── page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Play Store Review Scraper",
    page_icon="🎮",
    layout="centered",
)

st.title("🎮 Play Store Review Scraper")
st.caption("Paste an app ID or full Play Store URL to download balanced research-ready reviews as CSV.")

# ── inputs ───────────────────────────────────────────────────────────────────

raw_input = st.text_input(
    "App ID or Play Store URL",
    placeholder="e.g. com.king.candycrushsaga  or  https://play.google.com/store/apps/details?id=...",
)

app_name = st.text_input(
    "App name",
    placeholder="e.g. Candy Crush Saga",
)

total_reviews = st.number_input(
    "Total reviews",
    min_value=2,
    max_value=5000,
    value=200,
    step=2,
)

date_col_1, date_col_2 = st.columns(2)
with date_col_1:
    start_date = st.date_input("Start date", value=None)
with date_col_2:
    end_date = st.date_input("End date", value=None)

target_per_group = int(total_reviews) // 2
st.caption(f"Target split: {target_per_group} positive and {target_per_group} negative reviews.")

scrape_btn = st.button("Scrape Balanced Reviews", type="primary", use_container_width=True)

# ── scrape ───────────────────────────────────────────────────────────────────

if scrape_btn:
    if not raw_input.strip():
        st.error("Please enter an app ID or Play Store URL.")
    else:
        app_id = extract_app_id(raw_input)
        if not app_id:
            st.error(
                "Could not recognise a valid app ID or Play Store URL. "
                "Example: `com.spotify.music` or the full store URL."
            )
        else:
            display_name = app_name.strip() or app_id
            if int(total_reviews) % 2 != 0:
                st.error("Total reviews must be an even number so positive and negative reviews split equally.")
                st.stop()
            if start_date and end_date and start_date > end_date:
                st.error("Start date cannot be after end date.")
                st.stop()

            st.info(f"Scraping balanced reviews for `{app_id}` …")
            with st.spinner("Fetching reviews from Play Store …"):
                try:
                    reviews = fetch_balanced_reviews_filtered(
                        app_id,
                        display_name,
                        total_reviews=int(total_reviews),
                        start_date=start_date.isoformat() if isinstance(start_date, date) else None,
                        end_date=end_date.isoformat() if isinstance(end_date, date) else None,
                    )
                except Exception as exc:
                    reviews = []
                    st.error(f"Scrape failed: {exc}")

            if reviews:
                positive_count = sum(1 for r in reviews if r["sentiment_group"] == "positive")
                negative_count = sum(1 for r in reviews if r["sentiment_group"] == "negative")
                st.success(
                    f"Fetched **{len(reviews)}** reviews "
                    f"({positive_count} positive, {negative_count} negative)."
                )

                # Preview table
                import pandas as pd
                df = pd.DataFrame(reviews)
                st.subheader("Preview (first 10 rows)")
                st.dataframe(
                    df[["review_id", "app_name", "score", "word_count", "date", "sentiment_group", "content"]].head(10),
                    use_container_width=True,
                )

                # Download button
                csv_bytes = reviews_to_csv_bytes(reviews)
                st.download_button(
                    label="⬇️ Download CSV",
                    data=csv_bytes,
                    file_name=f"reviews_{app_id}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            elif not st.session_state.get("scrape_error"):
                st.warning(
                    f"No reviews found for `{app_id}`. "
                    "Check that the app ID is correct and the app has reviews."
                )
