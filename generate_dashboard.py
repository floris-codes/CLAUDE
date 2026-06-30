#!/usr/bin/env python3
"""
Shopify Cohort LTV Dashboard Generator

Creates an interactive HTML dashboard with:
- Cohort LTV Heatmap
- Revenue Gap Analysis (subscription vs extra purchases)
- Top SKU performance metrics
- Cumulative LTV tracking

Usage:
    python generate_dashboard.py orders_export_*.csv --output dashboard.html
"""

import argparse
import pandas as pd
import numpy as np
from datetime import datetime
import json
import base64
from io import BytesIO

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import seaborn as sns
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


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
    df = df[df['Total'] > 0]

    return df


def identify_customer_cohorts(df: pd.DataFrame) -> pd.DataFrame:
    """Identify first SKU and cohort month for each customer."""
    df_sorted = df.sort_values(['Email', 'Created at', 'Name'])
    first_orders = df_sorted.groupby('Email').first().reset_index()
    first_order_names = first_orders[['Email', 'Name']].rename(columns={'Name': 'First_Order_Name'})

    first_order_items = df_sorted.merge(first_order_names, on='Email')
    first_order_items = first_order_items[first_order_items['Name'] == first_order_items['First_Order_Name']]

    def get_first_sku(group):
        return group['Lineitem name'].iloc[0]

    first_sku_df = first_order_items.groupby('Email').apply(
        get_first_sku, include_groups=False
    ).reset_index()
    first_sku_df.columns = ['Email', 'First_SKU']

    cohort_df = first_orders[['Email', 'Created at', 'Total']].copy()
    cohort_df['Cohort_Month'] = cohort_df['Created at'].dt.tz_localize(None).dt.to_period('M')
    cohort_df = cohort_df.rename(columns={'Total': 'First_Order_Total'})

    customer_cohorts = cohort_df.merge(first_sku_df, on='Email')
    return customer_cohorts


def calculate_customer_metrics(df: pd.DataFrame, customer_cohorts: pd.DataFrame) -> pd.DataFrame:
    """Calculate LTV and order metrics for each customer."""
    order_totals = df.groupby(['Email', 'Name']).agg({
        'Total': 'first',
        'Created at': 'first'
    }).reset_index()

    # Total LTV
    customer_ltv = order_totals.groupby('Email').agg({
        'Total': 'sum',
        'Name': 'count'
    }).reset_index()
    customer_ltv.columns = ['Email', 'LTV', 'Order_Count']

    # Merge with cohort info
    metrics = customer_cohorts.merge(customer_ltv, on='Email')

    # Calculate extra revenue (beyond first order)
    metrics['Extra_Revenue'] = metrics['LTV'] - metrics['First_Order_Total']
    metrics['Extra_Revenue'] = metrics['Extra_Revenue'].clip(lower=0)

    return metrics


def build_cohort_ltv_matrix(metrics: pd.DataFrame, min_volume: int = 10) -> pd.DataFrame:
    """Create pivot table of Average LTV by First SKU and Cohort Month."""
    sku_counts = metrics['First_SKU'].value_counts()
    valid_skus = sku_counts[sku_counts >= min_volume].index.tolist()

    if not valid_skus:
        valid_skus = sku_counts.head(10).index.tolist()

    filtered = metrics[metrics['First_SKU'].isin(valid_skus)]

    pivot = filtered.pivot_table(
        values='LTV',
        index='First_SKU',
        columns='Cohort_Month',
        aggfunc='mean'
    )

    pivot = pivot.reindex(sorted(pivot.columns), axis=1)
    row_means = pivot.mean(axis=1)
    pivot = pivot.loc[row_means.sort_values(ascending=False).index]

    return pivot


def build_revenue_gap_analysis(metrics: pd.DataFrame, min_volume: int = 5) -> pd.DataFrame:
    """
    Build Revenue Gap Analysis table showing:
    - Subscription/First Order Revenue
    - Extra Purchase Revenue
    - Total Revenue
    - Gap Amount and Percentage
    """
    sku_counts = metrics['First_SKU'].value_counts()
    valid_skus = sku_counts[sku_counts >= min_volume].index.tolist()[:15]

    filtered = metrics[metrics['First_SKU'].isin(valid_skus)]

    gap_analysis = filtered.groupby(['Cohort_Month', 'First_SKU']).agg({
        'First_Order_Total': 'sum',
        'Extra_Revenue': 'sum',
        'LTV': 'sum',
        'Email': 'count'
    }).reset_index()

    gap_analysis.columns = ['Cohort_Month', 'First_SKU', 'First_Order_Revenue', 'Extra_Revenue', 'Total_Revenue', 'Customers']

    gap_analysis['Avg_First_Order'] = gap_analysis['First_Order_Revenue'] / gap_analysis['Customers']
    gap_analysis['Avg_Extra_Revenue'] = gap_analysis['Extra_Revenue'] / gap_analysis['Customers']
    gap_analysis['Avg_LTV'] = gap_analysis['Total_Revenue'] / gap_analysis['Customers']
    gap_analysis['Gap_Pct'] = (gap_analysis['Extra_Revenue'] / gap_analysis['First_Order_Revenue'] * 100).round(1)
    gap_analysis['Gap_Pct'] = gap_analysis['Gap_Pct'].fillna(0)

    return gap_analysis


def build_monthly_summary(metrics: pd.DataFrame) -> pd.DataFrame:
    """Build monthly cohort summary."""
    summary = metrics.groupby('Cohort_Month').agg({
        'Email': 'count',
        'LTV': ['mean', 'median', 'sum'],
        'First_Order_Total': 'mean',
        'Extra_Revenue': 'mean',
        'Order_Count': 'mean'
    }).reset_index()

    summary.columns = ['Cohort_Month', 'Customers', 'Avg_LTV', 'Median_LTV', 'Total_Revenue',
                       'Avg_First_Order', 'Avg_Extra_Revenue', 'Avg_Orders']

    return summary


def generate_heatmap_base64(pivot: pd.DataFrame) -> str:
    """Generate heatmap and return as base64 encoded PNG."""
    if not HAS_MATPLOTLIB:
        return ""

    n_rows = len(pivot.index)
    n_cols = len(pivot.columns)
    fig_width = max(12, n_cols * 1.0)
    fig_height = max(6, n_rows * 0.4)
    fig_width = min(fig_width, 20)
    fig_height = min(fig_height, 16)

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    formatted_cols = [str(col) for col in pivot.columns]
    pivot_display = pivot.copy()
    pivot_display.columns = formatted_cols

    def truncate_label(label, max_len=40):
        return label[:max_len] + '...' if len(str(label)) > max_len else str(label)

    pivot_display.index = [truncate_label(idx) for idx in pivot_display.index]

    annot_labels = pivot_display.map(
        lambda x: f'${x:,.0f}' if pd.notna(x) else ''
    )

    sns.heatmap(
        pivot_display,
        annot=annot_labels,
        fmt='',
        cmap='YlGnBu',
        linewidths=0.5,
        linecolor='white',
        cbar_kws={'label': 'Avg LTV ($)', 'shrink': 0.8},
        mask=pivot_display.isna(),
        ax=ax
    )

    plt.title('Average Customer LTV by First Product & Cohort Month', fontsize=14, fontweight='bold', pad=20)
    plt.xlabel('Cohort Month', fontsize=12)
    plt.ylabel('First Product Purchased', fontsize=12)
    plt.xticks(rotation=45, ha='right', fontsize=9)
    plt.yticks(fontsize=9)
    plt.tight_layout()

    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=120, bbox_inches='tight', facecolor='white')
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
    plt.close()

    return img_base64


def generate_html_dashboard(
    pivot: pd.DataFrame,
    gap_analysis: pd.DataFrame,
    monthly_summary: pd.DataFrame,
    metrics: pd.DataFrame,
    heatmap_base64: str
) -> str:
    """Generate complete HTML dashboard."""

    # Prepare data for JavaScript
    pivot_json = pivot.fillna(0).to_dict()
    gap_json = gap_analysis.to_dict('records')
    summary_json = monthly_summary.to_dict('records')

    # Convert Period objects to strings
    for row in gap_json:
        row['Cohort_Month'] = str(row['Cohort_Month'])
    for row in summary_json:
        row['Cohort_Month'] = str(row['Cohort_Month'])

    # Top SKUs by LTV
    top_skus = metrics.groupby('First_SKU').agg({
        'LTV': 'mean',
        'Email': 'count',
        'Extra_Revenue': 'mean'
    }).sort_values('LTV', ascending=False).head(10).reset_index()
    top_skus.columns = ['SKU', 'Avg_LTV', 'Customers', 'Avg_Extra_Revenue']
    top_skus_json = top_skus.to_dict('records')

    # Summary stats
    total_customers = len(metrics)
    total_revenue = metrics['LTV'].sum()
    avg_ltv = metrics['LTV'].mean()
    avg_orders = metrics['Order_Count'].mean()
    cohort_range = f"{metrics['Cohort_Month'].min()} to {metrics['Cohort_Month'].max()}"

    generated_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shopify Cohort LTV Dashboard</title>
    <style>
        :root {{
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --gray-50: #f9fafb;
            --gray-100: #f3f4f6;
            --gray-200: #e5e7eb;
            --gray-300: #d1d5db;
            --gray-600: #4b5563;
            --gray-800: #1f2937;
            --gray-900: #111827;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: var(--gray-100);
            color: var(--gray-800);
            line-height: 1.6;
        }}

        .container {{
            max-width: 1600px;
            margin: 0 auto;
            padding: 24px;
        }}

        header {{
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
            color: white;
            padding: 32px 24px;
            margin-bottom: 24px;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }}

        header h1 {{
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 8px;
        }}

        header p {{
            opacity: 0.9;
            font-size: 0.95rem;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}

        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            border: 1px solid var(--gray-200);
        }}

        .stat-card .label {{
            font-size: 0.85rem;
            color: var(--gray-600);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}

        .stat-card .value {{
            font-size: 1.75rem;
            font-weight: 700;
            color: var(--gray-900);
        }}

        .stat-card.primary .value {{ color: var(--primary); }}
        .stat-card.success .value {{ color: var(--success); }}
        .stat-card.warning .value {{ color: var(--warning); }}

        .card {{
            background: white;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            border: 1px solid var(--gray-200);
            margin-bottom: 24px;
            overflow: hidden;
        }}

        .card-header {{
            padding: 16px 20px;
            border-bottom: 1px solid var(--gray-200);
            background: var(--gray-50);
        }}

        .card-header h2 {{
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--gray-800);
        }}

        .card-body {{
            padding: 20px;
        }}

        .heatmap-container {{
            overflow-x: auto;
            text-align: center;
        }}

        .heatmap-container img {{
            max-width: 100%;
            height: auto;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
        }}

        th, td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--gray-200);
        }}

        th {{
            background: var(--gray-50);
            font-weight: 600;
            color: var(--gray-600);
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.5px;
        }}

        tr:hover td {{
            background: var(--gray-50);
        }}

        .number {{ text-align: right; font-variant-numeric: tabular-nums; }}

        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
        }}

        .badge-success {{ background: #d1fae5; color: #065f46; }}
        .badge-warning {{ background: #fef3c7; color: #92400e; }}
        .badge-danger {{ background: #fee2e2; color: #991b1b; }}

        .truncate {{
            max-width: 300px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .tabs {{
            display: flex;
            border-bottom: 1px solid var(--gray-200);
            margin-bottom: 0;
        }}

        .tab {{
            padding: 12px 20px;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            color: var(--gray-600);
            font-weight: 500;
            transition: all 0.2s;
        }}

        .tab:hover {{ color: var(--primary); }}

        .tab.active {{
            border-bottom-color: var(--primary);
            color: var(--primary);
        }}

        .tab-content {{
            display: none;
        }}

        .tab-content.active {{
            display: block;
        }}

        .grid-2 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 24px;
        }}

        .progress-bar {{
            background: var(--gray-200);
            border-radius: 4px;
            height: 8px;
            overflow: hidden;
        }}

        .progress-bar .fill {{
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s;
        }}

        .progress-bar .fill.primary {{ background: var(--primary); }}
        .progress-bar .fill.success {{ background: var(--success); }}

        footer {{
            text-align: center;
            padding: 24px;
            color: var(--gray-600);
            font-size: 0.85rem;
        }}

        @media (max-width: 768px) {{
            .container {{ padding: 16px; }}
            .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .grid-2 {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Shopify Cohort LTV Dashboard</h1>
            <p>Customer Lifetime Value Analysis by First Product Purchased | Generated: {generated_date}</p>
        </header>

        <div class="stats-grid">
            <div class="stat-card primary">
                <div class="label">Total Customers</div>
                <div class="value">{total_customers:,}</div>
            </div>
            <div class="stat-card success">
                <div class="label">Total Revenue</div>
                <div class="value">${total_revenue:,.0f}</div>
            </div>
            <div class="stat-card">
                <div class="label">Average LTV</div>
                <div class="value">${avg_ltv:,.2f}</div>
            </div>
            <div class="stat-card">
                <div class="label">Avg Orders/Customer</div>
                <div class="value">{avg_orders:.2f}</div>
            </div>
            <div class="stat-card warning">
                <div class="label">Cohort Period</div>
                <div class="value" style="font-size:1rem;">{cohort_range}</div>
            </div>
        </div>

        <div class="card">
            <div class="tabs">
                <div class="tab active" onclick="showTab('heatmap')">LTV Heatmap</div>
                <div class="tab" onclick="showTab('gap')">Revenue Gap Analysis</div>
                <div class="tab" onclick="showTab('monthly')">Monthly Summary</div>
                <div class="tab" onclick="showTab('topsku')">Top SKUs</div>
            </div>

            <div id="heatmap" class="tab-content active">
                <div class="card-body">
                    <div class="heatmap-container">
                        {"<img src='data:image/png;base64," + heatmap_base64 + "' alt='LTV Heatmap'>" if heatmap_base64 else "<p>Heatmap generation requires matplotlib. Install with: pip install matplotlib seaborn</p>"}
                    </div>
                </div>
            </div>

            <div id="gap" class="tab-content">
                <div class="card-header">
                    <h2>Revenue Gap Analysis</h2>
                    <p style="font-size:0.85rem;color:var(--gray-600);margin-top:4px;">
                        Shows first order revenue vs. additional purchases per cohort/SKU
                    </p>
                </div>
                <div class="card-body" style="overflow-x:auto;">
                    <table id="gapTable">
                        <thead>
                            <tr>
                                <th>Cohort</th>
                                <th>First SKU</th>
                                <th class="number">Customers</th>
                                <th class="number">Avg 1st Order</th>
                                <th class="number">Avg Extra Rev</th>
                                <th class="number">Avg LTV</th>
                                <th class="number">Gap %</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>

            <div id="monthly" class="tab-content">
                <div class="card-header">
                    <h2>Monthly Cohort Summary</h2>
                </div>
                <div class="card-body" style="overflow-x:auto;">
                    <table id="monthlyTable">
                        <thead>
                            <tr>
                                <th>Cohort Month</th>
                                <th class="number">Customers</th>
                                <th class="number">Avg LTV</th>
                                <th class="number">Median LTV</th>
                                <th class="number">Total Revenue</th>
                                <th class="number">Avg 1st Order</th>
                                <th class="number">Avg Extra Rev</th>
                                <th class="number">Avg Orders</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>

            <div id="topsku" class="tab-content">
                <div class="card-header">
                    <h2>Top SKUs by Average LTV</h2>
                </div>
                <div class="card-body">
                    <table id="topSkuTable">
                        <thead>
                            <tr>
                                <th style="width:50%">First SKU</th>
                                <th class="number">Customers</th>
                                <th class="number">Avg LTV</th>
                                <th class="number">Avg Extra Rev</th>
                                <th style="width:15%">LTV Distribution</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>

        <footer>
            Generated by Shopify Cohort LTV Dashboard Generator | {generated_date}
        </footer>
    </div>

    <script>
        const gapData = {json.dumps(gap_json)};
        const summaryData = {json.dumps(summary_json)};
        const topSkuData = {json.dumps(top_skus_json)};

        function formatCurrency(val) {{
            return '$' + Number(val).toLocaleString(undefined, {{minimumFractionDigits: 0, maximumFractionDigits: 0}});
        }}

        function formatCurrency2(val) {{
            return '$' + Number(val).toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
        }}

        function getGapBadge(pct) {{
            if (pct >= 50) return '<span class="badge badge-success">' + pct.toFixed(1) + '%</span>';
            if (pct >= 20) return '<span class="badge badge-warning">' + pct.toFixed(1) + '%</span>';
            return '<span class="badge badge-danger">' + pct.toFixed(1) + '%</span>';
        }}

        function truncate(str, len) {{
            return str.length > len ? str.substring(0, len) + '...' : str;
        }}

        function renderGapTable() {{
            const tbody = document.querySelector('#gapTable tbody');
            const sorted = [...gapData].sort((a, b) => b.Gap_Pct - a.Gap_Pct);
            tbody.innerHTML = sorted.slice(0, 50).map(row => `
                <tr>
                    <td>${{row.Cohort_Month}}</td>
                    <td class="truncate" title="${{row.First_SKU}}">${{truncate(row.First_SKU, 40)}}</td>
                    <td class="number">${{row.Customers}}</td>
                    <td class="number">${{formatCurrency2(row.Avg_First_Order)}}</td>
                    <td class="number">${{formatCurrency2(row.Avg_Extra_Revenue)}}</td>
                    <td class="number">${{formatCurrency2(row.Avg_LTV)}}</td>
                    <td class="number">${{getGapBadge(row.Gap_Pct)}}</td>
                </tr>
            `).join('');
        }}

        function renderMonthlyTable() {{
            const tbody = document.querySelector('#monthlyTable tbody');
            tbody.innerHTML = summaryData.map(row => `
                <tr>
                    <td>${{row.Cohort_Month}}</td>
                    <td class="number">${{row.Customers.toLocaleString()}}</td>
                    <td class="number">${{formatCurrency2(row.Avg_LTV)}}</td>
                    <td class="number">${{formatCurrency2(row.Median_LTV)}}</td>
                    <td class="number">${{formatCurrency(row.Total_Revenue)}}</td>
                    <td class="number">${{formatCurrency2(row.Avg_First_Order)}}</td>
                    <td class="number">${{formatCurrency2(row.Avg_Extra_Revenue)}}</td>
                    <td class="number">${{row.Avg_Orders.toFixed(2)}}</td>
                </tr>
            `).join('');
        }}

        function renderTopSkuTable() {{
            const tbody = document.querySelector('#topSkuTable tbody');
            const maxLtv = Math.max(...topSkuData.map(r => r.Avg_LTV));
            tbody.innerHTML = topSkuData.map(row => `
                <tr>
                    <td class="truncate" title="${{row.SKU}}">${{truncate(row.SKU, 50)}}</td>
                    <td class="number">${{row.Customers.toLocaleString()}}</td>
                    <td class="number">${{formatCurrency2(row.Avg_LTV)}}</td>
                    <td class="number">${{formatCurrency2(row.Avg_Extra_Revenue)}}</td>
                    <td>
                        <div class="progress-bar">
                            <div class="fill primary" style="width:${{(row.Avg_LTV/maxLtv*100).toFixed(1)}}%"></div>
                        </div>
                    </td>
                </tr>
            `).join('');
        }}

        function showTab(tabId) {{
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab[onclick="showTab('${{tabId}}')"]`).classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }}

        // Initialize
        renderGapTable();
        renderMonthlyTable();
        renderTopSkuTable();
    </script>
</body>
</html>'''

    return html


def main():
    parser = argparse.ArgumentParser(
        description='Generate an interactive Shopify Cohort LTV Dashboard.'
    )
    parser.add_argument(
        'input_files',
        nargs='+',
        help='Shopify orders_export.csv file(s)'
    )
    parser.add_argument(
        '--min-volume',
        type=int,
        default=10,
        help='Min first purchases for SKU inclusion (default: 10)'
    )
    parser.add_argument(
        '--output',
        default='cohort_ltv_dashboard.html',
        help='Output HTML file (default: cohort_ltv_dashboard.html)'
    )

    args = parser.parse_args()

    print(f"Loading orders from {len(args.input_files)} file(s):")
    df = load_shopify_orders(args.input_files)
    print(f"Total: {len(df):,} valid line items")

    print("Identifying customer cohorts...")
    customer_cohorts = identify_customer_cohorts(df)
    print(f"Found {len(customer_cohorts):,} unique customers")

    print("Calculating customer metrics...")
    metrics = calculate_customer_metrics(df, customer_cohorts)

    print("Building LTV heatmap matrix...")
    pivot = build_cohort_ltv_matrix(metrics, args.min_volume)
    print(f"Matrix: {len(pivot.index)} SKUs x {len(pivot.columns)} cohort months")

    print("Building revenue gap analysis...")
    gap_analysis = build_revenue_gap_analysis(metrics)

    print("Building monthly summary...")
    monthly_summary = build_monthly_summary(metrics)

    print("Generating heatmap image...")
    heatmap_base64 = generate_heatmap_base64(pivot)

    print("Generating HTML dashboard...")
    html = generate_html_dashboard(
        pivot, gap_analysis, monthly_summary, metrics, heatmap_base64
    )

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n✅ Dashboard saved to: {args.output}")
    print(f"   Open in your browser to view the interactive dashboard.")


if __name__ == '__main__':
    main()
