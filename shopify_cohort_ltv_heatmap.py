#!/usr/bin/env python3
"""
Shopify Cohort LTV Heatmap Generator

Analyzes Shopify orders_export.csv to create a heatmap showing Average LTV
by First Product Purchased and Cohort Month.

Usage:
    python shopify_cohort_ltv_heatmap.py orders_export.csv [--min-volume 10] [--output heatmap.png]
"""

import argparse
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from datetime import datetime


def load_shopify_orders(filepath: str) -> pd.DataFrame:
    """
    Load and parse a standard Shopify orders export CSV.

    Standard Shopify columns used:
    - Name: Order number/name
    - Email: Customer email (unique identifier)
    - Created at: Order timestamp
    - Lineitem name: Product/SKU name
    - Total: Order total amount
    - Financial Status: Payment status (paid, pending, etc.)
    """
    df = pd.read_csv(filepath, low_memory=False)

    # Validate required columns exist
    required_cols = ['Name', 'Email', 'Created at', 'Lineitem name', 'Total']
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Parse dates
    df['Created at'] = pd.to_datetime(df['Created at'], utc=True)

    # Clean Total column (remove currency symbols if present, handle strings)
    if df['Total'].dtype == 'object':
        df['Total'] = df['Total'].replace(r'[\$,]', '', regex=True).astype(float)

    # Filter to only paid/completed orders (exclude cancelled, refunded, etc.)
    if 'Financial Status' in df.columns:
        valid_statuses = ['paid', 'partially_paid', 'authorized']
        df = df[df['Financial Status'].str.lower().isin(valid_statuses)]

    # Drop rows with missing critical data
    df = df.dropna(subset=['Email', 'Created at', 'Lineitem name', 'Total'])

    # Normalize email for consistent customer identification
    df['Email'] = df['Email'].str.lower().str.strip()

    return df


def identify_first_sku_per_customer(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each customer, identify:
    - Their first order date (cohort month)
    - The first SKU/product in their first order

    If the first order has multiple line items, we take the first one listed
    or label as "Bundle/Mixed" if there are multiple distinct products.
    """
    # Sort by customer and order date
    df_sorted = df.sort_values(['Email', 'Created at', 'Name'])

    # Get the first order for each customer
    first_orders = df_sorted.groupby('Email').first().reset_index()
    first_order_names = first_orders[['Email', 'Name']].rename(columns={'Name': 'First_Order_Name'})

    # Join back to get all line items from first orders
    first_order_items = df_sorted.merge(first_order_names, on='Email')
    first_order_items = first_order_items[first_order_items['Name'] == first_order_items['First_Order_Name']]

    # Determine first SKU per customer
    def get_first_sku(group):
        unique_items = group['Lineitem name'].unique()
        if len(unique_items) == 1:
            return unique_items[0]
        elif len(unique_items) > 1:
            # Return first item listed, or mark as Bundle/Mixed
            # Using first listed item for simplicity as per requirements
            return group['Lineitem name'].iloc[0]
        return 'Unknown'

    first_sku_df = first_order_items.groupby('Email').apply(
        get_first_sku, include_groups=False
    ).reset_index()
    first_sku_df.columns = ['Email', 'First_SKU']

    # Get cohort month (month of first order)
    cohort_df = first_orders[['Email', 'Created at']].copy()
    # Remove timezone before converting to period to avoid warning
    cohort_df['Cohort_Month'] = cohort_df['Created at'].dt.tz_localize(None).dt.to_period('M')
    cohort_df = cohort_df[['Email', 'Cohort_Month']]

    # Merge first SKU with cohort info
    customer_cohorts = cohort_df.merge(first_sku_df, on='Email')

    return customer_cohorts


def calculate_customer_ltv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate Lifetime Value (LTV) for each customer.
    LTV = Sum of all order totals for that customer.

    Note: Shopify exports have one row per line item, so we need to
    aggregate at the order level first to avoid double-counting totals.
    """
    # Get unique order totals (Total is the same for all line items in an order)
    order_totals = df.groupby(['Email', 'Name']).agg({
        'Total': 'first'  # Total is order-level, same for all line items
    }).reset_index()

    # Sum all orders per customer for LTV
    customer_ltv = order_totals.groupby('Email').agg({
        'Total': 'sum'
    }).reset_index()
    customer_ltv.columns = ['Email', 'LTV']

    return customer_ltv


def create_cohort_ltv_matrix(
    customer_cohorts: pd.DataFrame,
    customer_ltv: pd.DataFrame,
    min_volume: int = 10
) -> pd.DataFrame:
    """
    Create a pivot table of Average LTV by First SKU and Cohort Month.

    Filters out SKUs with fewer than min_volume first purchases.
    """
    # Merge cohort info with LTV
    cohort_ltv = customer_cohorts.merge(customer_ltv, on='Email')

    # Count first purchases per SKU to filter low-volume
    sku_counts = cohort_ltv['First_SKU'].value_counts()
    valid_skus = sku_counts[sku_counts >= min_volume].index.tolist()

    print(f"Total unique First SKUs: {len(sku_counts)}")
    print(f"SKUs with >= {min_volume} first purchases: {len(valid_skus)}")

    if len(valid_skus) == 0:
        raise ValueError(f"No SKUs have >= {min_volume} first purchases. Try lowering --min-volume")

    # Filter to valid SKUs
    cohort_ltv_filtered = cohort_ltv[cohort_ltv['First_SKU'].isin(valid_skus)]

    # Create pivot table: Average LTV per First SKU per Cohort Month
    pivot = cohort_ltv_filtered.pivot_table(
        values='LTV',
        index='First_SKU',
        columns='Cohort_Month',
        aggfunc='mean'
    )

    # Sort columns chronologically (they should be already, but ensure it)
    pivot = pivot.reindex(sorted(pivot.columns), axis=1)

    # Sort rows by total average LTV (highest at top)
    row_means = pivot.mean(axis=1)
    pivot = pivot.loc[row_means.sort_values(ascending=False).index]

    return pivot


def generate_heatmap(
    pivot: pd.DataFrame,
    output_path: str = 'cohort_ltv_heatmap.png',
    title: str = 'Average Customer LTV by First Product & Cohort Month'
) -> None:
    """
    Generate and save a heatmap visualization.

    X-Axis: Cohort Month
    Y-Axis: First Product Bought (SKU/Name)
    Color Intensity: Average LTV ($)
    Cell Text: Dollar values
    """
    # Calculate figure size based on data dimensions
    n_rows = len(pivot.index)
    n_cols = len(pivot.columns)

    # Dynamic sizing: min 12x8, scale up for more data
    fig_width = max(12, n_cols * 1.2)
    fig_height = max(8, n_rows * 0.5)

    # Cap maximum size for readability
    fig_width = min(fig_width, 24)
    fig_height = min(fig_height, 20)

    plt.figure(figsize=(fig_width, fig_height))

    # Format column labels (cohort months) for better readability
    formatted_cols = [str(col) for col in pivot.columns]
    pivot_display = pivot.copy()
    pivot_display.columns = formatted_cols

    # Truncate long SKU names for Y-axis readability
    def truncate_label(label, max_len=50):
        return label[:max_len] + '...' if len(str(label)) > max_len else str(label)

    pivot_display.index = [truncate_label(idx) for idx in pivot_display.index]

    # Create annotation labels with dollar formatting
    annot_labels = pivot_display.map(
        lambda x: f'${x:,.0f}' if pd.notna(x) else ''
    )

    # Generate heatmap
    ax = sns.heatmap(
        pivot_display,
        annot=annot_labels,
        fmt='',
        cmap='YlGnBu',
        linewidths=0.5,
        linecolor='white',
        cbar_kws={'label': 'Average LTV ($)', 'shrink': 0.8},
        mask=pivot_display.isna()  # Mask NaN cells
    )

    # Styling
    plt.title(title, fontsize=14, fontweight='bold', pad=20)
    plt.xlabel('Cohort Month', fontsize=12)
    plt.ylabel('First Product Purchased', fontsize=12)

    # Rotate x-axis labels for readability
    plt.xticks(rotation=45, ha='right', fontsize=9)
    plt.yticks(fontsize=9)

    # Adjust layout to prevent label cutoff
    plt.tight_layout()

    # Save figure
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    print(f"Heatmap saved to: {output_path}")

    # Also display if in interactive environment
    plt.show()


def print_summary_stats(
    customer_cohorts: pd.DataFrame,
    customer_ltv: pd.DataFrame,
    pivot: pd.DataFrame
) -> None:
    """Print summary statistics about the analysis."""
    merged = customer_cohorts.merge(customer_ltv, on='Email')

    print("\n" + "="*60)
    print("COHORT LTV ANALYSIS SUMMARY")
    print("="*60)
    print(f"Total Customers Analyzed: {len(merged):,}")
    print(f"Cohort Period: {merged['Cohort_Month'].min()} to {merged['Cohort_Month'].max()}")
    print(f"Unique First SKUs (after filtering): {len(pivot.index)}")
    print(f"\nOverall LTV Statistics:")
    print(f"  Mean LTV:   ${merged['LTV'].mean():,.2f}")
    print(f"  Median LTV: ${merged['LTV'].median():,.2f}")
    print(f"  Min LTV:    ${merged['LTV'].min():,.2f}")
    print(f"  Max LTV:    ${merged['LTV'].max():,.2f}")

    # Top 5 First SKUs by average LTV
    top_skus = merged.groupby('First_SKU')['LTV'].mean().sort_values(ascending=False).head(5)
    print(f"\nTop 5 First SKUs by Average LTV:")
    for i, (sku, ltv) in enumerate(top_skus.items(), 1):
        sku_display = sku[:40] + '...' if len(str(sku)) > 40 else sku
        print(f"  {i}. {sku_display}: ${ltv:,.2f}")

    print("="*60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Generate a Cohort LTV Heatmap from Shopify orders export.'
    )
    parser.add_argument(
        'input_file',
        help='Path to Shopify orders_export.csv file'
    )
    parser.add_argument(
        '--min-volume',
        type=int,
        default=10,
        help='Minimum number of first purchases for a SKU to be included (default: 10)'
    )
    parser.add_argument(
        '--output',
        default='cohort_ltv_heatmap.png',
        help='Output filename for the heatmap image (default: cohort_ltv_heatmap.png)'
    )

    args = parser.parse_args()

    print(f"Loading orders from: {args.input_file}")
    df = load_shopify_orders(args.input_file)
    print(f"Loaded {len(df):,} line items from orders")

    print("Identifying first SKU per customer...")
    customer_cohorts = identify_first_sku_per_customer(df)
    print(f"Identified {len(customer_cohorts):,} unique customers")

    print("Calculating customer LTV...")
    customer_ltv = calculate_customer_ltv(df)

    print(f"Building cohort matrix (min volume: {args.min_volume})...")
    pivot = create_cohort_ltv_matrix(customer_cohorts, customer_ltv, args.min_volume)

    print_summary_stats(customer_cohorts, customer_ltv, pivot)

    print("Generating heatmap...")
    generate_heatmap(pivot, args.output)

    print("Done!")


if __name__ == '__main__':
    main()
