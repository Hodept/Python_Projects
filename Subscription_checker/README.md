# Subscription Detector

`Subscription_detector.py` scans a bank or card activity export and identifies likely recurring subscriptions.

It supports:
- CSV and XLSX input files
- Fuzzy merchant matching
- Amount tolerance of `+/- $1.00`
- Outlier filtering for one-off anomalies
- Category tagging from a built-in mapping or an external JSON/YAML file
- Excel export of the detected subscriptions to a new `.xlsx` file

## Requirements

Install the Python packages used by the script:

```bash
pip install pandas openpyxl
```

If you want to use YAML category files, also install:

```bash
pip install pyyaml
```

## Input File Requirements

The script accepts:
- `.csv`
- `.xlsx`

It looks for three required logical fields:
- date
- description
- amount

The header names do not need to be exact. The script currently recognizes common variants such as:

- Date: `date`, `transaction date`, `posted date`, `post date`, `posting date`, `activity date`, `effective date`
- Description: `description`, `merchant`, `name`, `details`, `transaction`, `transaction description`, `memo`
- Amount: `amount`, `debit`, `credit`, `transaction amount`, `posted amount`, `value`

If a required column cannot be found, the script will print the headers it detected so you can troubleshoot the export format.

## Basic Usage

Run the script with an input file:

```bash
python3 Subscription_detector.py activity.xlsx
```

When subscriptions are found, the script:
- prints the results to the terminal
- creates a new Excel file in the same folder as the input

Default output filename:

```text
<input_filename>_subscriptions.xlsx
```

Example:

```text
activity.xlsx -> activity_subscriptions.xlsx
```

## Command-Line Options

### Required Argument

```bash
python3 Subscription_detector.py <input_file>
```

### Optional Switches

Use a custom category config file:

```bash
python3 Subscription_detector.py activity.xlsx --category-config category_rules.json
```

Write the results to a custom Excel file:

```bash
python3 Subscription_detector.py activity.xlsx --output april_results.xlsx
```

Use both options together:

```bash
python3 Subscription_detector.py activity.xlsx --category-config custom_categories.yaml --output reports/april_subscriptions.xlsx
```

Available switches:
- `--category-config` path to a `.json`, `.yaml`, or `.yml` category rules file
- `--output` path to the generated `.xlsx` output file

## Category Rules

By default, the script looks for a file named `category_rules.json` in the same folder as `Subscription_detector.py`.

If that file exists, it is used automatically.

If no category file is found, the script falls back to an internal default mapping.

### Simple JSON Format

The easiest format is a JSON object where each key is a category name and each value is a list of match keywords:

```json
{
  "Shopping": ["amazon marketplace", "amazon prime", "instacart"],
  "Streaming": ["netflix", "spotify", "hulu"],
  "Technology": ["apple services", "google services", "microsoft"],
  "Food Delivery": ["doordash", "uber one"]
}
```

### YAML Format

You can also use YAML:

```yaml
Shopping:
  - amazon marketplace
  - amazon prime
  - instacart
Streaming:
  - netflix
  - spotify
  - hulu
Technology:
  - apple services
  - google services
  - microsoft
```

### Alternate Structured Format

The script also accepts a structured `categories` layout in JSON or YAML:

```yaml
categories:
  - name: Shopping
    keywords:
      - amazon marketplace
      - amazon prime
  - name: Streaming
    keywords:
      - netflix
      - spotify
```

### How Category Matching Works

- Keywords are normalized to lowercase
- Matching is based on whether a keyword appears inside the canonical merchant name
- If no category rule matches, the subscription is labeled `Uncategorized`

## Output Columns

The generated results currently include:
- `Subscription`
- `Category`
- `Avg Cost`
- `Cost Range`
- `Frequency`
- `Occurrences`
- `Outliers Ignored`
- `First Seen`
- `Last Seen`

## Notes

- Reading and writing `.xlsx` files requires `openpyxl`
- Some Excel exports may show an `openpyxl` warning about workbook styles; that warning is usually harmless
- The script normalizes merchant descriptions before matching, so merchant variants like `AMZN Mktp` and `Amazon Marketplace` can be grouped together

## Files

- `Subscription_detector.py` main script
- `category_rules.json` editable default category mapping
