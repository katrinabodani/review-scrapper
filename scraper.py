#!/usr/bin/env python3
import argparse
import os
import re
from datetime import datetime

from tqdm import tqdm

from fetcher import fetch_balanced_reviews_filtered
from writer import append_combined_csv, update_log, write_per_app_csv, write_per_app_json


def extract_app_id(raw: str) -> str:
    raw = raw.strip()
    match = re.search(r"[?&]id=([a-zA-Z0-9._]+)", raw)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+", raw):
        return raw
    return ""


def load_app_inputs(path: str) -> list[str]:
    app_inputs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                app_inputs.append(line)
    return app_inputs


def scrape_app(
    raw_app: str,
    app_name: str | None,
    output_dir: str,
    total_reviews: int,
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, int, str]:
    app_id = extract_app_id(raw_app)
    if not app_id:
        return raw_app, 0, "failed: invalid app ID or Play Store URL"

    display_name = app_name or app_id
    try:
        reviews = fetch_balanced_reviews_filtered(
            app_id,
            display_name,
            total_reviews=total_reviews,
            start_date=start_date,
            end_date=end_date,
        )
        if reviews:
            write_per_app_csv(app_id, reviews, output_dir)
            write_per_app_json(app_id, reviews, output_dir)
            append_combined_csv(reviews, output_dir)
            update_log(app_id, "success", len(reviews), output_dir)
            return app_id, len(reviews), "success"
        else:
            update_log(app_id, "empty", 0, output_dir)
            return app_id, 0, "empty"
    except Exception as exc:
        update_log(app_id, "failed", 0, output_dir)
        return app_id, 0, f"failed: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape balanced Google Play Store reviews")
    parser.add_argument("--apps", default="apps_list.txt", help="Path to app IDs file")
    parser.add_argument("--app-name", default=None, help="Display name to write into the app_name column")
    parser.add_argument("--total-reviews", type=int, default=200, help="Total reviews per app; must be even")
    parser.add_argument("--start-date", default=None, help="Optional lower date bound in YYYY-MM-DD format")
    parser.add_argument("--end-date", default=None, help="Optional upper date bound in YYYY-MM-DD format")
    parser.add_argument("--output", default="./output", help="Output directory")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    app_inputs = load_app_inputs(args.apps)
    if not app_inputs:
        print("No app IDs or Play Store URLs found in", args.apps)
        return

    if args.app_name and len(app_inputs) > 1:
        print("--app-name can only be used when the apps file contains one app.")
        return

    validation_error = validate_args(args.total_reviews, args.start_date, args.end_date)
    if validation_error:
        print(f"Invalid arguments: {validation_error}")
        return

    per_group = args.total_reviews // 2 if args.total_reviews % 2 == 0 else "invalid"
    date_range = f"{args.start_date or 'any'} to {args.end_date or 'any'}"
    print(
        f"Scraping {len(app_inputs)} apps | target {args.total_reviews} reviews each "
        f"({per_group} positive/{per_group} negative) | dates {date_range}"
    )
    print(f"Output -> {os.path.abspath(args.output)}\n")

    total_reviews = 0
    success_count = 0
    fail_count = 0

    with tqdm(total=len(app_inputs), unit="app") as bar:
        for raw_app in app_inputs:
            app_id, count, status = scrape_app(
                raw_app,
                args.app_name,
                args.output,
                args.total_reviews,
                args.start_date,
                args.end_date,
            )
            if status == "success":
                success_count += 1
                total_reviews += count
                bar.set_postfix({"last": app_id, "reviews": count})
            else:
                fail_count += 1
                bar.set_postfix({"last": app_id, "status": status})
            bar.update(1)

    print(f"\n--- Done ---")
    print(f"Apps succeeded : {success_count}")
    print(f"Apps failed    : {fail_count}")
    print(f"Total reviews  : {total_reviews}")
    print(f"Log            : {os.path.join(args.output, 'scrape_log.json')}")


def validate_args(total_reviews: int, start_date: str | None, end_date: str | None) -> str:
    if total_reviews < 2:
        return "--total-reviews must be at least 2"
    if total_reviews % 2 != 0:
        return "--total-reviews must be even"

    parsed_start = parse_cli_date(start_date, "--start-date")
    if isinstance(parsed_start, str):
        return parsed_start

    parsed_end = parse_cli_date(end_date, "--end-date")
    if isinstance(parsed_end, str):
        return parsed_end

    if parsed_start and parsed_end and parsed_start > parsed_end:
        return "--start-date cannot be after --end-date"

    return ""


def parse_cli_date(value: str | None, label: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return f"{label} must use YYYY-MM-DD format"


if __name__ == "__main__":
    main()
