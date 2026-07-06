#!/usr/bin/env python3
"""
Shopify Cohort LTV Spreadsheet Generator

Exports the full cohort LTV analysis as an Excel workbook (.xlsx) with sheets:
- LTV Heatmap: Avg LTV matrix (First SKU x Cohort Month) with color scale
- Revenue Gap Analysis: first order vs. extra purchase revenue per cohort/SKU
- Monthly Summary: cohort-level metrics
- Top SKUs: SKUs ranked by average LTV
- Customer Detail: per-customer LTV records

Usage:
    python generate_spreadsheet.py orders_export_*.csv --output cohort_ltv_analysis.xlsx
"""

import argparse
import pandas as pd
from datetime import datetime

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter

from generate_dashboard import (
    load_shopify_orders,
    identify_customer_cohorts,
    calculate_customer_metrics,
    build_cohort_ltv_matrix,
    build_revenue_gap_analysis,
    build_monthly_summary,
)

HEADER_FILL = PatternFill(start_color='1D4ED8', end_color='1D4ED8', fill_type='solid')
HEADER_FONT = Font(bold=True, color='FFFFFF')
THIN_BORDER = Border(bottom=Side(style='thin', color='D1D5DB'))
CURRENCY_FMT = '$#,##0.00'
CURRENCY_FMT_0 = '$#,##0'
PCT_FMT = '0.0"%"'


def style_header(ws, n_cols, row=1):
    """Apply header styling to a row."""
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center')


def autofit_columns(ws, min_width=10, max_width=50):
    """Set column widths based on content length."""
    for col_cells in ws.columns:
        length = max((len(str(c.value)) for c in col_cells if c.value is not None), default=0)
        col_letter = get_column_letter(col_cells[0].column)
        ws.column_dimensions[col_letter].width = min(max(length + 2, min_width), max_width)


def add_ltv_color_scale(ws, first_row, last_row, first_col, last_col):
    """Add a green color scale to a numeric range (like the heatmap)."""
    rng = (f"{get_column_letter(first_col)}{first_row}:"
           f"{get_column_letter(last_col)}{last_row}")
    ws.conditional_formatting.add(rng, ColorScaleRule(
        start_type='min', start_color='FFFFE5',
        mid_type='percentile', mid_value=50, mid_color='7FCDBB',
        end_type='max', end_color='0C2C84'
    ))


def write_heatmap_sheet(writer, pivot: pd.DataFrame):
    """LTV matrix sheet with conditional color scale."""
    out = pivot.copy()
    out.columns = [str(c) for c in out.columns]
    out.index.name = 'First SKU'
    out = out.round(2)
    out.to_excel(writer, sheet_name='LTV Heatmap')

    ws = writer.sheets['LTV Heatmap']
    n_cols = len(out.columns) + 1
    n_rows = len(out.index) + 1
    style_header(ws, n_cols)

    for row in range(2, n_rows + 1):
        ws.cell(row=row, column=1).font = Font(bold=True)
        for col in range(2, n_cols + 1):
            ws.cell(row=row, column=col).number_format = CURRENCY_FMT_0

    add_ltv_color_scale(ws, 2, n_rows, 2, n_cols)
    autofit_columns(ws)
    ws.freeze_panes = 'B2'


def write_gap_sheet(writer, gap: pd.DataFrame):
    """Revenue Gap Analysis sheet."""
    out = gap.copy()
    out['Cohort_Month'] = out['Cohort_Month'].astype(str)
    out = out[['Cohort_Month', 'First_SKU', 'Customers',
               'First_Order_Revenue', 'Extra_Revenue', 'Total_Revenue',
               'Avg_First_Order', 'Avg_Extra_Revenue', 'Avg_LTV', 'Gap_Pct']]
    out.columns = ['Cohort Month', 'First SKU', 'Customers',
                   'First Order Revenue', 'Extra Revenue', 'Total Revenue',
                   'Avg First Order', 'Avg Extra Revenue', 'Avg LTV', 'Gap %']
    out = out.sort_values(['Cohort Month', 'Gap %'], ascending=[True, False])
    out.to_excel(writer, sheet_name='Revenue Gap Analysis', index=False)

    ws = writer.sheets['Revenue Gap Analysis']
    style_header(ws, len(out.columns))
    for row in range(2, len(out) + 2):
        for col in range(4, 10):  # currency columns D-I
            ws.cell(row=row, column=col).number_format = CURRENCY_FMT
        ws.cell(row=row, column=10).number_format = PCT_FMT

    # Color scale on Gap % so strong repeat-revenue cohorts stand out
    add_ltv_color_scale(ws, 2, len(out) + 1, 10, 10)
    autofit_columns(ws)
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f"A1:{get_column_letter(len(out.columns))}{len(out) + 1}"


def write_monthly_sheet(writer, summary: pd.DataFrame):
    """Monthly cohort summary sheet."""
    out = summary.copy()
    out['Cohort_Month'] = out['Cohort_Month'].astype(str)
    out.columns = ['Cohort Month', 'Customers', 'Avg LTV', 'Median LTV', 'Total Revenue',
                   'Avg First Order', 'Avg Extra Revenue', 'Avg Orders']
    out.to_excel(writer, sheet_name='Monthly Summary', index=False)

    ws = writer.sheets['Monthly Summary']
    style_header(ws, len(out.columns))
    for row in range(2, len(out) + 2):
        for col in range(3, 8):  # currency columns C-G
            ws.cell(row=row, column=col).number_format = CURRENCY_FMT
        ws.cell(row=row, column=8).number_format = '0.00'
    autofit_columns(ws)
    ws.freeze_panes = 'A2'


def write_top_sku_sheet(writer, metrics: pd.DataFrame):
    """Top SKUs ranked by average LTV."""
    top = metrics.groupby('First_SKU').agg(
        Customers=('Email', 'count'),
        Avg_LTV=('LTV', 'mean'),
        Median_LTV=('LTV', 'median'),
        Avg_First_Order=('First_Order_Total', 'mean'),
        Avg_Extra_Revenue=('Extra_Revenue', 'mean'),
        Total_Revenue=('LTV', 'sum'),
    ).sort_values('Avg_LTV', ascending=False).reset_index()
    top.insert(0, 'Rank', range(1, len(top) + 1))
    top.columns = ['Rank', 'First SKU', 'Customers', 'Avg LTV', 'Median LTV',
                   'Avg First Order', 'Avg Extra Revenue', 'Total Revenue']
    top.to_excel(writer, sheet_name='Top SKUs', index=False)

    ws = writer.sheets['Top SKUs']
    style_header(ws, len(top.columns))
    for row in range(2, len(top) + 2):
        for col in range(4, 9):  # currency columns D-H
            ws.cell(row=row, column=col).number_format = CURRENCY_FMT
    add_ltv_color_scale(ws, 2, len(top) + 1, 4, 4)
    autofit_columns(ws)
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f"A1:{get_column_letter(len(top.columns))}{len(top) + 1}"


def write_customer_sheet(writer, metrics: pd.DataFrame):
    """Per-customer detail records."""
    out = metrics.copy()
    out['Cohort_Month'] = out['Cohort_Month'].astype(str)
    out = out[['Email', 'Cohort_Month', 'First_SKU', 'Order_Count',
               'First_Order_Total', 'Extra_Revenue', 'LTV']]
    out.columns = ['Email', 'Cohort Month', 'First SKU', 'Orders',
                   'First Order Total', 'Extra Revenue', 'LTV']
    out = out.sort_values('LTV', ascending=False)
    out.to_excel(writer, sheet_name='Customer Detail', index=False)

    ws = writer.sheets['Customer Detail']
    style_header(ws, len(out.columns))
    for row in range(2, len(out) + 2):
        for col in range(5, 8):  # currency columns E-G
            ws.cell(row=row, column=col).number_format = CURRENCY_FMT
    autofit_columns(ws)
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f"A1:{get_column_letter(len(out.columns))}{len(out) + 1}"


def write_overview_sheet(writer, metrics: pd.DataFrame, input_files: list):
    """Overview sheet with key stats."""
    stats = [
        ('Report', 'Shopify Cohort LTV Analysis'),
        ('Generated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        ('Source Files', ', '.join(input_files)),
        ('', ''),
        ('Total Customers', len(metrics)),
        ('Total Revenue', round(metrics['LTV'].sum(), 2)),
        ('Average LTV', round(metrics['LTV'].mean(), 2)),
        ('Median LTV', round(metrics['LTV'].median(), 2)),
        ('Avg Orders / Customer', round(metrics['Order_Count'].mean(), 2)),
        ('Avg First Order', round(metrics['First_Order_Total'].mean(), 2)),
        ('Avg Extra Revenue', round(metrics['Extra_Revenue'].mean(), 2)),
        ('Cohort Period', f"{metrics['Cohort_Month'].min()} to {metrics['Cohort_Month'].max()}"),
        ('Unique First SKUs', metrics['First_SKU'].nunique()),
    ]
    out = pd.DataFrame(stats, columns=['Metric', 'Value'])
    out.to_excel(writer, sheet_name='Overview', index=False)

    ws = writer.sheets['Overview']
    style_header(ws, 2)
    currency_rows = {6, 7, 8, 10, 11}  # 1-based data rows for $ metrics (+1 for header)
    for row in range(2, len(out) + 2):
        ws.cell(row=row, column=1).font = Font(bold=True)
        if row in {r + 1 for r in currency_rows}:
            ws.cell(row=row, column=2).number_format = CURRENCY_FMT
    autofit_columns(ws, max_width=80)


def main():
    parser = argparse.ArgumentParser(
        description='Export Shopify cohort LTV analysis as an Excel spreadsheet.'
    )
    parser.add_argument('input_files', nargs='+', help='Shopify orders_export.csv file(s)')
    parser.add_argument('--min-volume', type=int, default=10,
                        help='Min first purchases for SKU inclusion in heatmap (default: 10)')
    parser.add_argument('--output', default='cohort_ltv_analysis.xlsx',
                        help='Output Excel file (default: cohort_ltv_analysis.xlsx)')

    args = parser.parse_args()

    print(f"Loading orders from {len(args.input_files)} file(s):")
    df = load_shopify_orders(args.input_files)
    print(f"Total: {len(df):,} valid line items")

    print("Identifying customer cohorts...")
    customer_cohorts = identify_customer_cohorts(df)
    print(f"Found {len(customer_cohorts):,} unique customers")

    print("Calculating customer metrics...")
    metrics = calculate_customer_metrics(df, customer_cohorts)

    print("Building analysis tables...")
    pivot = build_cohort_ltv_matrix(metrics, args.min_volume)
    gap_analysis = build_revenue_gap_analysis(metrics)
    monthly_summary = build_monthly_summary(metrics)

    print(f"Writing Excel workbook: {args.output}")
    with pd.ExcelWriter(args.output, engine='openpyxl') as writer:
        write_overview_sheet(writer, metrics, args.input_files)
        write_heatmap_sheet(writer, pivot)
        write_gap_sheet(writer, gap_analysis)
        write_monthly_sheet(writer, monthly_summary)
        write_top_sku_sheet(writer, metrics)
        write_customer_sheet(writer, metrics)

    print(f"\n✅ Spreadsheet saved to: {args.output}")
    print("   Sheets: Overview, LTV Heatmap, Revenue Gap Analysis, Monthly Summary, Top SKUs, Customer Detail")


if __name__ == '__main__':
    main()
