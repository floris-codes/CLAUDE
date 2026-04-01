"""
Shopify Cohort LTV Heatmap — Streamlit App

Upload your Shopify orders_export.csv file(s) and instantly visualize
Average Customer LTV by First Product Purchased and Cohort Month.
"""

import io
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
import streamlit as st


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Shopify Cohort LTV Heatmap",
    page_icon="📊",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Data processing (reused from shopify_cohort_ltv_heatmap.py)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_and_combine_csvs(uploaded_files) -> pd.DataFrame:
    """Load one or more Shopify order export CSVs and combine them."""
    frames = []
    for f in uploaded_files:
        df = pd.read_csv(f, low_memory=False)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    required_cols = ["Name", "Email", "Created at", "Lineitem name", "Total"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        st.error(f"Missing required columns: {missing}")
        st.stop()

    df["Created at"] = pd.to_datetime(df["Created at"], utc=True)

    if df["Total"].dtype == "object":
        df["Total"] = df["Total"].replace(r"[\$,]", "", regex=True).astype(float)

    if "Financial Status" in df.columns:
        valid = ["paid", "partially_paid", "authorized"]
        df = df[df["Financial Status"].str.lower().isin(valid)]

    df = df.dropna(subset=["Email", "Created at", "Lineitem name", "Total"])
    df["Email"] = df["Email"].str.lower().str.strip()

    return df


def identify_first_sku(df: pd.DataFrame) -> pd.DataFrame:
    df_sorted = df.sort_values(["Email", "Created at", "Name"])
    first_orders = df_sorted.groupby("Email").first().reset_index()
    first_order_names = first_orders[["Email", "Name"]].rename(
        columns={"Name": "First_Order_Name"}
    )

    first_items = df_sorted.merge(first_order_names, on="Email")
    first_items = first_items[first_items["Name"] == first_items["First_Order_Name"]]

    def _first_sku(group):
        unique = group["Lineitem name"].unique()
        if len(unique) == 1:
            return unique[0]
        elif len(unique) > 1:
            return group["Lineitem name"].iloc[0]
        return "Unknown"

    sku_df = (
        first_items.groupby("Email")
        .apply(_first_sku, include_groups=False)
        .reset_index()
    )
    sku_df.columns = ["Email", "First_SKU"]

    cohort_df = first_orders[["Email", "Created at"]].copy()
    cohort_df["Cohort_Month"] = (
        cohort_df["Created at"].dt.tz_localize(None).dt.to_period("M")
    )

    return cohort_df[["Email", "Cohort_Month"]].merge(sku_df, on="Email")


def calculate_ltv(df: pd.DataFrame) -> pd.DataFrame:
    order_totals = df.groupby(["Email", "Name"]).agg(Total=("Total", "first")).reset_index()
    return order_totals.groupby("Email").agg(LTV=("Total", "sum")).reset_index()


def build_pivot(
    cohorts: pd.DataFrame, ltv: pd.DataFrame, min_vol: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    merged = cohorts.merge(ltv, on="Email")
    counts = merged["First_SKU"].value_counts()
    valid = counts[counts >= min_vol].index
    if valid.empty:
        return pd.DataFrame(), merged

    filtered = merged[merged["First_SKU"].isin(valid)]
    pivot = filtered.pivot_table(
        values="LTV", index="First_SKU", columns="Cohort_Month", aggfunc="mean"
    )
    pivot = pivot.reindex(sorted(pivot.columns), axis=1)
    pivot = pivot.loc[pivot.mean(axis=1).sort_values(ascending=False).index]
    return pivot, merged


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------
def render_heatmap(pivot: pd.DataFrame) -> plt.Figure:
    n_rows, n_cols = pivot.shape
    fig_w = max(12, min(n_cols * 1.3, 26))
    fig_h = max(6, min(n_rows * 0.6, 22))

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    display = pivot.copy()
    display.columns = [str(c) for c in display.columns]
    display.index = [
        (s[:48] + "...") if len(str(s)) > 48 else str(s) for s in display.index
    ]

    annot = display.map(lambda x: f"${x:,.0f}" if pd.notna(x) else "")

    sns.heatmap(
        display,
        annot=annot,
        fmt="",
        cmap="YlGnBu",
        linewidths=0.5,
        linecolor="white",
        cbar_kws={"label": "Average LTV ($)", "shrink": 0.8},
        mask=display.isna(),
        ax=ax,
    )

    ax.set_title(
        "Average Customer LTV by First Product & Cohort Month",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )
    ax.set_xlabel("Cohort Month", fontsize=12)
    ax.set_ylabel("First Product Purchased", fontsize=12)
    ax.tick_params(axis="x", rotation=45, labelsize=9)
    ax.tick_params(axis="y", labelsize=9)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Shopify Cohort LTV Heatmap")
st.markdown(
    "Upload your Shopify **orders_export.csv** file(s) to visualize "
    "Average Customer LTV segmented by the first product each customer purchased."
)

uploaded = st.file_uploader(
    "Upload Shopify order export CSV(s)",
    type=["csv"],
    accept_multiple_files=True,
    help="Standard Shopify orders export with columns: Name, Email, Created at, Lineitem name, Total",
)

if not uploaded:
    st.info("Upload one or more Shopify order export CSVs to get started.")
    st.stop()

# Sidebar controls
with st.sidebar:
    st.header("Settings")
    min_volume = st.slider(
        "Min first-purchases per SKU",
        min_value=1,
        max_value=100,
        value=10,
        step=1,
        help="SKUs with fewer first-purchases than this are excluded to reduce noise.",
    )

# Process data
with st.spinner("Processing orders..."):
    df = load_and_combine_csvs(uploaded)

cohorts = identify_first_sku(df)
ltv = calculate_ltv(df)
pivot, merged = build_pivot(cohorts, ltv, min_volume)

# Summary metrics
st.markdown("---")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Customers", f"{len(merged):,}")
col2.metric("Avg LTV", f"${merged['LTV'].mean():,.2f}")
col3.metric("Median LTV", f"${merged['LTV'].median():,.2f}")
col4.metric(
    "Cohort Range",
    f"{merged['Cohort_Month'].min()} — {merged['Cohort_Month'].max()}",
)

if pivot.empty:
    st.warning(
        f"No SKUs have {min_volume}+ first-purchases. "
        "Lower the threshold in the sidebar."
    )
    st.stop()

# Heatmap
st.markdown("---")
fig = render_heatmap(pivot)
st.pyplot(fig)

# Download button for the image
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
buf.seek(0)
st.download_button(
    "Download heatmap as PNG",
    data=buf,
    file_name="cohort_ltv_heatmap.png",
    mime="image/png",
)

# Top SKUs table
st.markdown("---")
st.subheader("Top First-Purchase SKUs by Average LTV")
top = (
    merged.groupby("First_SKU")
    .agg(
        avg_ltv=("LTV", "mean"),
        median_ltv=("LTV", "median"),
        customers=("Email", "count"),
    )
    .sort_values("avg_ltv", ascending=False)
    .head(15)
)
top.columns = ["Avg LTV ($)", "Median LTV ($)", "Customers"]
top["Avg LTV ($)"] = top["Avg LTV ($)"].map("${:,.2f}".format)
top["Median LTV ($)"] = top["Median LTV ($)"].map("${:,.2f}".format)
st.dataframe(top, use_container_width=True)
