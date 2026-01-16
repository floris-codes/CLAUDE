#!/usr/bin/env python3
"""
SKU Cumulative LTV Analysis

Tracks cumulative LTV over time for customers whose first purchase was a specific SKU.

Usage:
    python3 sku_cumulative_ltv.py orders_export_*.csv --sku "BURN1-2141-2141" --start-month 2025-07 --output ltv_report.png
"""

import argparse
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt


def load_shopify_orders(filepaths: list) -> pd.DataFrame:
    """Load and combine multiple Shopify order export CSV files."""
    if isinstance(filepaths, str):
        filepaths = [filepaths]

    dfs = []
    for filepath in filepaths:
        print(f"  Loading: {filepath}")
        df_part = pd.read_csv(filepath, low_memory=False)
        dfs.append(df_part)

    df = pd.concat(dfs, ignore_index=True)
    print(f"  Combined {len(filepaths)} file(s) into {len(df):,} rows")

    # Deduplicate
    original_len = len(df)
    df = df.drop_duplicates(subset=['Name', 'Email', 'Lineitem name', 'Total'], keep='first')
    if len(df) < original_len:
        print(f"  Removed {original_len - len(df):,} duplicate rows")

    # Parse dates
    df['Created at'] = pd.to_datetime(df['Created at'], utc=True)

    # Clean Total column
    if df['Total'].dtype == 'object':
        df['Total'] = df['Total'].replace(r'[\$,]', '', regex=True).astype(float)

    # Filter to paid orders
    if 'Financial Status' in df.columns:
        valid_statuses = ['paid', 'partially_paid', 'authorized']
        df = df[df['Financial Status'].str.lower().isin(valid_statuses)]

    # Clean data
    df = df.dropna(subset=['Email', 'Created at', 'Lineitem name', 'Total'])
    df['Email'] = df['Email'].str.lower().str.strip()

    return df


def get_customers_by_first_sku(df: pd.DataFrame, target_sku: str) -> pd.DataFrame:
    """Find customers whose first order contained the target SKU."""

    # Sort by customer and date
    df_sorted = df.sort_values(['Email', 'Created at', 'Name'])

    # Get first order for each customer
    first_orders = df_sorted.groupby('Email').first().reset_index()
    first_order_names = first_orders[['Email', 'Name']].rename(columns={'Name': 'First_Order_Name'})

    # Get all line items from first orders
    first_order_items = df_sorted.merge(first_order_names, on='Email')
    first_order_items = first_order_items[first_order_items['Name'] == first_order_items['First_Order_Name']]

    # Find customers whose first order contained the target SKU
    customers_with_sku = first_order_items[
        first_order_items['Lineitem name'].str.contains(target_sku, case=False, na=False)
    ]['Email'].unique()

    print(f"  Found {len(customers_with_sku):,} customers whose first order contained '{target_sku}'")

    # Get cohort month for these customers
    cohort_df = first_orders[first_orders['Email'].isin(customers_with_sku)][['Email', 'Created at']].copy()
    cohort_df['Cohort_Month'] = cohort_df['Created at'].dt.tz_localize(None).dt.to_period('M')

    return cohort_df[['Email', 'Cohort_Month']]


def calculate_cumulative_ltv(df: pd.DataFrame, customer_cohorts: pd.DataFrame, start_month: str) -> pd.DataFrame:
    """
    Calculate cumulative LTV for each cohort over time.

    Returns a DataFrame with:
    - Rows: Cohort months
    - Columns: Months since first purchase (0, 1, 2, ...)
    - Values: Cumulative average LTV
    """

    # Show available cohorts for debugging
    print(f"  Available cohorts in data: {sorted(customer_cohorts['Cohort_Month'].unique())}")

    # Filter to only customers in our cohort
    target_customers = customer_cohorts['Email'].unique()
    df_filtered = df[df['Email'].isin(target_customers)].copy()

    # Get order totals (dedupe by order)
    order_totals = df_filtered.groupby(['Email', 'Name', 'Created at']).agg({
        'Total': 'first'
    }).reset_index()

    # Add cohort info
    order_totals = order_totals.merge(customer_cohorts, on='Email')

    # Calculate order month
    order_totals['Order_Month'] = order_totals['Created at'].dt.tz_localize(None).dt.to_period('M')

    # Calculate months since cohort
    order_totals['Months_Since_Cohort'] = (
        order_totals['Order_Month'].astype('int64') - order_totals['Cohort_Month'].astype('int64')
    )

    # Drop any rows with NaN values
    order_totals = order_totals.dropna(subset=['Months_Since_Cohort'])

    # Filter to start month and later
    start_period = pd.Period(start_month, freq='M')

    # Show what we're filtering
    cohorts_before_filter = customer_cohorts['Cohort_Month'].nunique()
    customer_cohorts_filtered = customer_cohorts[customer_cohorts['Cohort_Month'] >= start_period]
    cohorts_after_filter = customer_cohorts_filtered['Cohort_Month'].nunique()
    print(f"  Cohorts before date filter: {cohorts_before_filter}, after: {cohorts_after_filter}")

    order_totals_filtered = order_totals[order_totals['Cohort_Month'] >= start_period]

    if len(order_totals_filtered) == 0:
        print(f"\n  WARNING: No cohorts found starting {start_month}")
        print(f"  Your earliest cohort is: {customer_cohorts['Cohort_Month'].min()}")
        print(f"  Your latest cohort is: {customer_cohorts['Cohort_Month'].max()}")
        print(f"\n  Using ALL cohorts instead...")
        order_totals_filtered = order_totals
        customer_cohorts_filtered = customer_cohorts

    # Get unique cohorts
    cohorts = sorted(customer_cohorts_filtered['Cohort_Month'].unique())

    # Calculate max months, handling empty data
    if len(order_totals_filtered) == 0 or order_totals_filtered['Months_Since_Cohort'].isna().all():
        max_months = 1
    else:
        max_val = order_totals_filtered['Months_Since_Cohort'].max()
        max_months = int(max_val) + 1 if pd.notna(max_val) else 1

    print(f"  Analyzing {len(cohorts)} cohorts from {cohorts[0]} to {cohorts[-1]}")
    print(f"  Maximum months tracked: {max_months}")

    # Build cumulative LTV matrix
    results = []

    for cohort in cohorts:
        cohort_customers = customer_cohorts_filtered[customer_cohorts_filtered['Cohort_Month'] == cohort]['Email'].unique()
        cohort_orders = order_totals_filtered[order_totals_filtered['Email'].isin(cohort_customers)]

        n_customers = len(cohort_customers)

        # Calculate cumulative spend for each month
        cumulative_ltv = 0
        row = {'Cohort': str(cohort), 'Customers': n_customers}

        for month in range(int(max_months)):
            month_orders = cohort_orders[cohort_orders['Months_Since_Cohort'] <= month]
            if len(month_orders) > 0:
                total_spend = month_orders.groupby('Email')['Total'].sum().sum()
                avg_ltv = total_spend / n_customers
            else:
                avg_ltv = 0

            row[f'Month {month}'] = avg_ltv

        results.append(row)

    result_df = pd.DataFrame(results)
    result_df = result_df.set_index('Cohort')

    return result_df


def generate_report(result_df: pd.DataFrame, target_sku: str, output_path: str):
    """Generate heatmap and summary."""

    # Separate customers column from LTV data
    customers = result_df['Customers']
    ltv_data = result_df.drop(columns=['Customers'])

    # Print text summary
    print("\n" + "=" * 70)
    print(f"CUMULATIVE LTV REPORT - First SKU: {target_sku}")
    print("=" * 70)
    print(f"\nCustomers per Cohort:")
    for cohort, count in customers.items():
        print(f"  {cohort}: {count:,} customers")

    print(f"\nCumulative Average LTV by Month:")
    print(ltv_data.round(2).to_string())
    print("=" * 70)

    # Generate heatmap
    n_rows = len(ltv_data.index)
    n_cols = len(ltv_data.columns)

    fig_width = max(12, n_cols * 0.8)
    fig_height = max(6, n_rows * 0.6)

    plt.figure(figsize=(fig_width, fig_height))

    # Format annotations as dollars
    annot_labels = ltv_data.map(lambda x: f'${x:,.0f}' if pd.notna(x) and x > 0 else '')

    ax = sns.heatmap(
        ltv_data,
        annot=annot_labels,
        fmt='',
        cmap='YlGnBu',
        linewidths=0.5,
        linecolor='white',
        cbar_kws={'label': 'Cumulative Avg LTV ($)'}
    )

    plt.title(f'Cumulative LTV by Cohort Month\nFirst SKU: {target_sku}', fontsize=14, fontweight='bold', pad=20)
    plt.xlabel('Months Since First Purchase', fontsize=12)
    plt.ylabel('Cohort Month', fontsize=12)
    plt.xticks(rotation=0)
    plt.yticks(rotation=0)
    plt.tight_layout()

    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    print(f"\nHeatmap saved to: {output_path}")
    plt.show()


def main():
    parser = argparse.ArgumentParser(description='Cumulative LTV analysis for a specific first-purchase SKU.')
    parser.add_argument('input_files', nargs='+', help='Shopify order export CSV files')
    parser.add_argument('--sku', required=True, help='Target SKU to filter by (first purchase)')
    parser.add_argument('--start-month', default='2025-07', help='Start cohort month (YYYY-MM format, default: 2025-07)')
    parser.add_argument('--output', default='cumulative_ltv.png', help='Output filename')

    args = parser.parse_args()

    print(f"Loading orders from {len(args.input_files)} file(s):")
    df = load_shopify_orders(args.input_files)

    print(f"\nFinding customers whose first purchase was '{args.sku}':")
    customer_cohorts = get_customers_by_first_sku(df, args.sku)

    print(f"\nCalculating cumulative LTV (starting {args.start_month}):")
    result_df = calculate_cumulative_ltv(df, customer_cohorts, args.start_month)

    generate_report(result_df, args.sku, args.output)

    print("\nDone!")


if __name__ == '__main__':
    main()
