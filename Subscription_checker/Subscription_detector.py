from difflib import SequenceMatcher
from pathlib import Path
import argparse
import json
import re

import pandas as pd

# -----------------------------
# CONFIGURATION
# -----------------------------
# Expected columns (case-insensitive matching handled later):
# Date, Description, Amount

DATE_COL_CANDIDATES = [
    "date",
    "transaction date",
    "posted date",
    "post date",
    "posting date",
    "activity date",
    "effective date",
]
DESC_COL_CANDIDATES = [
    "description",
    "merchant",
    "name",
    "details",
    "transaction",
    "transaction description",
    "memo",
]
AMOUNT_COL_CANDIDATES = [
    "amount",
    "debit",
    "credit",
    "transaction amount",
    "posted amount",
    "value",
]

FREQUENCY_TOLERANCE_DAYS = 3
YEARLY_TOLERANCE_DAYS = 30
AMOUNT_TOLERANCE = 1.00
FUZZY_MATCH_THRESHOLD = 0.82
MIN_RECURRING_OCCURRENCES = 2

FREQUENCY_TARGETS = {
    "Weekly": 7,
    "Biweekly": 14,
    "Monthly": 30,
    "Yearly": 365,
}

# Ordered from more specific to more general.
MERCHANT_ALIAS_PATTERNS = [
    (r"\bamzn\b.*\bmktp\b|\bamazon\b.*\bmarketplace\b", "Amazon Marketplace"),
    (r"\bnetflix\b", "Netflix"),
    (r"\bspotify\b", "Spotify"),
    (r"\bapple\.?com\/?bill\b|\bapple\b.*\bbill\b|\bitunes\b", "Apple Services"),
    (r"\bgoogle\b.*\b(storage|one|youtube)\b|\byoutube\b", "Google Services"),
    (r"\bmicrosoft\b|\bxbox\b", "Microsoft"),
    (r"\buber\s*one\b", "Uber One"),
    (r"\bdoordash\b|\bdashpass\b", "DoorDash"),
    (r"\binstacart\b", "Instacart"),
    (r"\bhulu\b", "Hulu"),
    (r"\bdisney\b", "Disney+"),
    (r"\bprime video\b|\bamazon prime\b", "Amazon Prime"),
    (r"\badobe\b", "Adobe"),
    (r"\bdropbox\b", "Dropbox"),
    (r"\bnotion\b", "Notion"),
    (r"\bslack\b", "Slack"),
    (r"\bzoom\b", "Zoom"),
    (r"\bopenai\b|\bchatgpt\b", "OpenAI"),
]

DEFAULT_CATEGORY_KEYWORDS = {
    "Shopping": ["amazon marketplace", "amazon prime", "instacart"],
    "Streaming": ["netflix", "spotify", "hulu", "disney+", "youtube", "prime video"],
    "Technology": ["apple services", "google services", "microsoft", "openai", "adobe", "dropbox", "notion", "slack", "zoom"],
    "Food Delivery": ["doordash", "uber one"],
}


# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def load_transactions(file_path):
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".xlsx":
        try:
            return pd.read_excel(path)
        except ImportError as exc:
            raise ImportError(
                "Reading .xlsx files requires the 'openpyxl' package. "
                "Install it with: pip install openpyxl"
            ) from exc

    raise ValueError("Unsupported input type. Please provide a .csv or .xlsx file.")


def load_category_keywords(config_path=None):
    if config_path is None:
        default_config = Path(__file__).with_name("category_rules.json")
        if default_config.exists():
            config_path = default_config

    if config_path is None:
        return normalize_category_keywords(DEFAULT_CATEGORY_KEYWORDS)

    path = Path(config_path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        with path.open("r", encoding="utf-8") as config_file:
            raw_config = json.load(config_file)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "Reading YAML category files requires the 'PyYAML' package. "
                "Install it with: pip install pyyaml"
            ) from exc

        with path.open("r", encoding="utf-8") as config_file:
            raw_config = yaml.safe_load(config_file)
    else:
        raise ValueError("Category config must be a .json, .yaml, or .yml file.")

    return normalize_category_keywords(raw_config)


def normalize_category_keywords(raw_config):
    if not raw_config:
        return {}

    if isinstance(raw_config, dict) and "categories" in raw_config:
        raw_config = raw_config["categories"]

    if isinstance(raw_config, dict):
        category_map = raw_config
    elif isinstance(raw_config, list):
        category_map = {}
        for entry in raw_config:
            if not isinstance(entry, dict) or "name" not in entry:
                raise ValueError(
                    "Each category entry must be a dictionary with 'name' and 'keywords'."
                )
            category_map[entry["name"]] = entry.get("keywords", [])
    else:
        raise ValueError(
            "Category config must be either a mapping of category names to keywords "
            "or a list of {name, keywords} objects."
        )

    normalized = {}
    for category, keywords in category_map.items():
        normalized[str(category)] = [str(keyword).lower().strip() for keyword in keywords if str(keyword).strip()]

    return normalized


def normalize_columns(df):
    df = df.copy()
    original_columns = list(df.columns)
    df.columns = [normalize_column_name(c) for c in df.columns]

    def find_col(candidates, label):
        for candidate in candidates:
            if candidate in df.columns:
                return candidate
        raise ValueError(
            f"Missing required {label} column. "
            f"Tried aliases: {candidates}. "
            f"Found columns: {original_columns}"
        )

    date_col = find_col(DATE_COL_CANDIDATES, "date")
    desc_col = find_col(DESC_COL_CANDIDATES, "description")
    amount_col = find_col(AMOUNT_COL_CANDIDATES, "amount")

    df = df[[date_col, desc_col, amount_col]].copy()
    df.columns = ["date", "description", "amount"]

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["description"] = df["description"].astype(str).str.strip()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

    df = df.dropna(subset=["date", "description", "amount"]).copy()
    df["amount"] = df["amount"].abs()
    df["merchant_key"] = df["description"].apply(normalize_merchant_key)

    return df


def normalize_column_name(column_name):
    cleaned = str(column_name).strip().lower()
    cleaned = cleaned.replace("_", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def normalize_merchant_key(description):
    cleaned = description.lower()
    cleaned = re.sub(r"\b(?:pos|debit|credit|purchase|payment|card|online|pending|recurring)\b", " ", cleaned)
    cleaned = re.sub(r"\d+", " ", cleaned)
    cleaned = re.sub(r"[^a-z+& ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    for pattern, canonical in MERCHANT_ALIAS_PATTERNS:
        if re.search(pattern, cleaned):
            return canonical.lower()

    return cleaned


def similarity_score(left, right):
    if not left or not right:
        return 0.0

    if left == right:
        return 1.0

    left_tokens = set(left.split())
    right_tokens = set(right.split())

    if not left_tokens or not right_tokens:
        token_overlap = 0.0
    else:
        token_overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    sequence_ratio = SequenceMatcher(None, left, right).ratio()
    return max(sequence_ratio, token_overlap)


def assign_canonical_merchants(df, category_keywords):
    canonical_keys = []
    canonical_map = {}

    for merchant_key in sorted(df["merchant_key"].unique(), key=len):
        best_match = None
        best_score = 0.0

        for candidate in canonical_keys:
            score = similarity_score(merchant_key, candidate)
            if score > best_score:
                best_score = score
                best_match = candidate

        if best_match and best_score >= FUZZY_MATCH_THRESHOLD:
            canonical_map[merchant_key] = best_match
        else:
            canonical_map[merchant_key] = merchant_key
            canonical_keys.append(merchant_key)

    df = df.copy()
    df["canonical_merchant"] = df["merchant_key"].map(canonical_map)
    df["merchant_display"] = df["canonical_merchant"].apply(format_merchant_name)
    df["category"] = df["canonical_merchant"].apply(
        lambda canonical_name: tag_category(canonical_name, category_keywords)
    )
    return df


def format_merchant_name(canonical_name):
    if not canonical_name:
        return "Unknown"

    for _, display_name in MERCHANT_ALIAS_PATTERNS:
        if canonical_name == display_name.lower():
            return display_name

    return canonical_name.title()


def tag_category(canonical_name, category_keywords):
    for category, keywords in category_keywords.items():
        if any(keyword in canonical_name for keyword in keywords):
            return category
    return "Uncategorized"


def cluster_amounts(group):
    clusters = []

    for _, row in group.sort_values("amount").iterrows():
        matched_cluster = None

        for cluster in clusters:
            if abs(row["amount"] - cluster["mean_amount"]) <= AMOUNT_TOLERANCE:
                matched_cluster = cluster
                break

        if matched_cluster is None:
            matched_cluster = {
                "rows": [],
                "amounts": [],
                "mean_amount": row["amount"],
            }
            clusters.append(matched_cluster)

        matched_cluster["rows"].append(row.to_dict())
        matched_cluster["amounts"].append(row["amount"])
        matched_cluster["mean_amount"] = sum(matched_cluster["amounts"]) / len(matched_cluster["amounts"])

    return [pd.DataFrame(cluster["rows"]) for cluster in clusters]


def build_recurring_chain(dates, target_days, tolerance_days):
    if len(dates) < 2:
        return []

    best_chain = []
    sorted_dates = sorted(dates)

    for start_index, start_date in enumerate(sorted_dates):
        chain = [start_date]
        cursor = start_index + 1

        while cursor < len(sorted_dates):
            expected_date = chain[-1] + pd.Timedelta(days=target_days)
            match_index = None

            for candidate_index in range(cursor, len(sorted_dates)):
                delta_days = abs((sorted_dates[candidate_index] - expected_date).days)
                if delta_days <= tolerance_days:
                    match_index = candidate_index
                    break

            if match_index is None:
                break

            chain.append(sorted_dates[match_index])
            cursor = match_index + 1

        if len(chain) > len(best_chain):
            best_chain = chain

    return best_chain


def detect_recurring_series(dates):
    if len(dates) < MIN_RECURRING_OCCURRENCES:
        return None

    best_frequency = None
    best_chain = []

    for frequency, target_days in FREQUENCY_TARGETS.items():
        tolerance = YEARLY_TOLERANCE_DAYS if frequency == "Yearly" else FREQUENCY_TOLERANCE_DAYS
        chain = build_recurring_chain(dates, target_days, tolerance)

        if len(chain) > len(best_chain):
            best_chain = chain
            best_frequency = frequency

    if len(best_chain) < MIN_RECURRING_OCCURRENCES:
        return None

    return {
        "frequency": best_frequency,
        "dates": best_chain,
        "outliers_removed": len(dates) - len(best_chain),
    }


# -----------------------------
# CORE LOGIC
# -----------------------------
def find_subscriptions(df, category_keywords):
    df = assign_canonical_merchants(df, category_keywords)
    results = []

    for _, merchant_group in df.groupby("canonical_merchant"):
        amount_clusters = cluster_amounts(merchant_group)

        for amount_group in amount_clusters:
            recurring_series = detect_recurring_series(list(amount_group["date"]))
            if recurring_series is None:
                continue

            recurring_amounts = amount_group[amount_group["date"].isin(recurring_series["dates"])]["amount"]
            merchant_name = amount_group["merchant_display"].mode().iloc[0]
            category = amount_group["category"].mode().iloc[0]

            results.append(
                {
                    "Subscription": merchant_name,
                    "Category": category,
                    "Avg Cost": round(recurring_amounts.mean(), 2),
                    "Cost Range": f"${recurring_amounts.min():.2f} - ${recurring_amounts.max():.2f}",
                    "Frequency": recurring_series["frequency"],
                    "Occurrences": len(recurring_series["dates"]),
                    "Outliers Ignored": recurring_series["outliers_removed"],
                    "First Seen": min(recurring_series["dates"]).date().isoformat(),
                    "Last Seen": max(recurring_series["dates"]).date().isoformat(),
                }
            )

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results).sort_values(
        by=["Occurrences", "Subscription"],
        ascending=[False, True],
    )


def default_output_path(input_file_path):
    input_path = Path(input_file_path)
    return input_path.with_name(f"{input_path.stem}_subscriptions.xlsx")


def write_results_to_excel(results_df, output_file_path):
    output_path = Path(output_file_path)

    if output_path.suffix.lower() != ".xlsx":
        output_path = output_path.with_suffix(".xlsx")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        results_df.to_excel(output_path, index=False)
    except ImportError as exc:
        raise ImportError(
            "Writing .xlsx files requires the 'openpyxl' package. "
            "Install it with: pip install openpyxl"
        ) from exc

    return output_path


# -----------------------------
# MAIN EXECUTION
# -----------------------------
def main(input_file_path, category_config_path=None, output_file_path=None):
    df = load_transactions(input_file_path)
    category_keywords = load_category_keywords(category_config_path)
    df = normalize_columns(df)
    subscriptions = find_subscriptions(df, category_keywords)

    if subscriptions.empty:
        print("No recurring subscriptions found.")
    else:
        print("Detected Subscriptions:\n")
        print(subscriptions.to_string(index=False))

        output_path = write_results_to_excel(
            subscriptions,
            output_file_path or default_output_path(input_file_path),
        )
        print(f"\nSaved results to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Detect recurring subscription transactions from a CSV or XLSX file"
    )
    parser.add_argument("input_file", help="Path to your financial CSV or XLSX file")
    parser.add_argument(
        "--category-config",
        help="Optional path to a JSON or YAML file containing category rules",
    )
    parser.add_argument(
        "--output",
        help="Optional path for the generated Excel results file (.xlsx)",
    )
    args = parser.parse_args()
    main(args.input_file, args.category_config, args.output)
