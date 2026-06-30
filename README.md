# Shopify Cohort LTV Analysis Toolkit

Tools for analyzing customer Lifetime Value (LTV) by first product purchased and cohort month from Shopify order exports.

## Features

- **Interactive HTML Dashboard** - Full-featured dashboard with LTV heatmap, revenue gap analysis, monthly summaries, and top SKU rankings
- **Cohort LTV Heatmap** - Visualize average LTV by first product and cohort month
- **Revenue Gap Analysis** - Compare first order revenue vs. subsequent purchases
- **Cumulative LTV Tracking** - Track how LTV grows over time for specific SKU cohorts

## Installation

```bash
pip install pandas numpy seaborn matplotlib
```

## Tools

### 1. Interactive Dashboard Generator

```bash
python generate_dashboard.py orders_export*.csv --output dashboard.html
```

Creates an interactive HTML dashboard with:
- Summary statistics (total customers, revenue, avg LTV)
- LTV heatmap by first SKU and cohort month
- Revenue Gap Analysis table (first order vs. extra purchases)
- Monthly cohort summary
- Top SKUs ranked by average LTV

**Options:**
- `--min-volume N` - Minimum first purchases for SKU inclusion (default: 10)
- `--output FILE` - Output HTML file (default: cohort_ltv_dashboard.html)

### 2. Cohort LTV Heatmap (Static Image)

```bash
python shopify_cohort_ltv_heatmap.py orders_export*.csv --output heatmap.png
```

Generates a static PNG heatmap showing average LTV by first product and cohort month.

### 3. SKU Cumulative LTV Analysis

```bash
python sku_cumulative_ltv.py orders_export*.csv --sku "PRODUCT-SKU" --start-month 2025-01 --output ltv.png
```

Tracks cumulative LTV over time for customers whose first purchase was a specific SKU.

## Input Data Format

Uses standard Shopify order exports with these columns:
- `Name` - Order number
- `Email` - Customer email
- `Created at` - Order timestamp
- `Lineitem name` - Product name
- `Total` - Order total
- `Financial Status` - Payment status (paid, pending, etc.)

## Example Output

The dashboard includes:

| Metric | Description |
|--------|-------------|
| Avg LTV | Average lifetime value per customer |
| Gap % | Extra revenue as % of first order (higher = better retention) |
| Cohort Month | Month of customer's first purchase |

## License

MIT
