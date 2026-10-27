
"""SGX Equity Research Screener with shared-password access."""

import asyncio
import hmac
import subprocess
import sys
from datetime import date
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from playwright.async_api import async_playwright

from sector_rankings import show_sector_rankings
from table_styles import style_stock_table


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="SGX Equity Screener",
    page_icon="📈",
    layout="wide",
)

SGX_URL = (
    "https://investors.sgx.com/market/stock-screener"
    "?from=/market/securities"
)


# ============================================================
# PASSWORD AUTHENTICATION
# ============================================================

def require_password():
    """Allow access only after entering the shared password."""

    if st.session_state.get("authenticated", False):
        return

    st.title("🔐 SGX Equity Research Screener")
    st.caption(
        "Enter the shared team password to access the screener."
    )

    with st.form("login"):
        password = st.text_input(
            "Password",
            type="password",
        )

        submitted = st.form_submit_button(
            "Log in",
            type="primary",
        )

    if submitted:
        try:
            expected = st.secrets["APP_PASSWORD"]
        except (KeyError, FileNotFoundError):
            st.error(
                "APP_PASSWORD is not configured in Streamlit Secrets."
            )
            st.stop()

        if hmac.compare_digest(
            password.encode(),
            str(expected).encode(),
        ):
            st.session_state.authenticated = True
            st.rerun()

        st.error("Incorrect password.")

    st.stop()


require_password()


with st.sidebar:
    st.write("🔓 Team access")

    if st.button("Log out"):
        st.session_state.authenticated = False
        st.session_state.pop("sgx_data", None)
        st.rerun()


# ============================================================
# PLAYWRIGHT CHROMIUM INSTALLATION
# ============================================================

@st.cache_resource(show_spinner="Installing Chromium browser...")
def ensure_chromium():
    """
    Install Chromium on the Streamlit Cloud server.

    Streamlit Cloud installs the Playwright Python package,
    but the browser executable must be installed separately.
    """

    subprocess.run(
        [
            sys.executable,
            "-m",
            "playwright",
            "install",
            "chromium",
        ],
        check=True,
        timeout=240,
    )

    return True


# ============================================================
# SGX COLUMN CONFIGURATION
# ============================================================

COLUMN_NAMES = {
    "companyName": "Company",
    "stockCode": "SGX Code",
    "sector": "Sector",
    "marketCapitalization": "Market Cap (M)",
    "salesTTM": "Revenue TTM (M)",
    "priceToEarningsRatio": "P/E",
    "priceToBookRatio": "P/B",
    "returnOnAvgCommonEquity": "ROE (%)",
    "dividendYield": "Dividend Yield (%)",
    "fourWeekPricePercentChange": "4W Change (%)",
    "thirteenWeekPricePercentChange": "13W Change (%)",
    "twentySixWeekPricePercentChange": "26W Change (%)",
    "fiftyTwoWeekPricePercentChange": "52W Change (%)",
    "netProfitMargin": "Net Profit Margin (%)",
    "priceToCashFlowPerShareRatio": "Price/CF",
    "totalDebtToTotalEquityRatio": "Debt/Equity",
    "salesPercentageChange": "Revenue Growth (%)",
    "priceCurrCode": "Currency",
}

COLUMN_ORDER = [
    "Company",
    "SGX Code",
    "Sector",
    "Currency",
    "Market Cap (M)",
    "P/E",
    "P/B",
    "ROE (%)",
    "Dividend Yield (%)",
    "4W Change (%)",
    "13W Change (%)",
    "26W Change (%)",
    "52W Change (%)",
    "Revenue TTM (M)",
    "Revenue Growth (%)",
    "Net Profit Margin (%)",
    "Price/CF",
    "Debt/Equity",
]

FRACTION_PERCENT_COLUMNS = [
    "ROE (%)",
    "Dividend Yield (%)",
    "Net Profit Margin (%)",
    "Revenue Growth (%)",
]

PRICE_CHANGE_COLUMNS = [
    "4W Change (%)",
    "13W Change (%)",
    "26W Change (%)",
    "52W Change (%)",
]


# ============================================================
# FIND SGX RECORDS
# ============================================================

def find_records(data):
    """Find stock screener records in a nested SGX JSON response."""

    if isinstance(data, list):

        if data and isinstance(data[0], dict):

            keys = set().union(
                *(
                    item.keys()
                    for item in data[:10]
                    if isinstance(item, dict)
                )
            )

            if "stockCode" in keys and (
                "companyName" in keys
                or "priceToEarningsRatio" in keys
            ):
                return data

        for item in data:
            result = find_records(item)

            if result is not None:
                return result

    elif isinstance(data, dict):

        for value in data.values():
            result = find_records(value)

            if result is not None:
                return result

    return None


# ============================================================
# FETCH SGX DATA USING PLAYWRIGHT
# ============================================================

async def fetch_sgx_data():
    """Capture the SGX stock screener API response."""

    captured = []

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
        )

        try:
            page = await browser.new_page(
                viewport={
                    "width": 1600,
                    "height": 1000,
                }
            )

            pending = []

            async def capture_response(response):

                if (
                    "/stockscreener/v2.0/all" in response.url
                    and response.status == 200
                ):
                    try:
                        captured.append(await response.json())
                    except Exception:
                        pass

            def on_response(response):
                pending.append(
                    asyncio.create_task(
                        capture_response(response)
                    )
                )

            page.on("response", on_response)

            await page.goto(
                SGX_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            for _ in range(30):

                if captured:
                    break

                await page.wait_for_timeout(1000)

            if pending:
                await asyncio.gather(
                    *pending,
                    return_exceptions=True,
                )

            if not captured:
                raise RuntimeError(
                    "SGX stock screener response was not detected."
                )

            return captured[0]

        finally:
            await browser.close()


# ============================================================
# CLEAN SGX DATA
# ============================================================

def clean_sgx_data(raw_data):

    records = find_records(raw_data)

    if records is None:
        raise ValueError(
            "Could not identify stock records in SGX response."
        )

    df = pd.json_normalize(records).rename(
        columns=COLUMN_NAMES
    )

    df = df[
        [
            col
            for col in COLUMN_ORDER
            if col in df.columns
        ]
    ].copy()

    if "SGX Code" not in df.columns:
        raise ValueError("SGX Code column is missing.")

    df["SGX Code"] = (
        df["SGX Code"]
        .astype("string")
        .str.strip()
    )

    df = df.dropna(subset=["SGX Code"])

    df = df[
        df["SGX Code"] != ""
    ].drop_duplicates(
        subset=["SGX Code"]
    )

    for col in ("Company", "Sector", "Currency"):

        if col not in df.columns:
            df[col] = "Not Available"

        else:
            df[col] = (
                df[col]
                .fillna("Not Available")
                .replace("", "Not Available")
            )

    df["Sector"] = df["Sector"].replace(
        {
            "Not Available": "Unclassified",
            "-": "Unclassified",
        }
    )

    text_columns = {
        "Company",
        "SGX Code",
        "Sector",
        "Currency",
    }

    numeric_columns = [
        col
        for col in df.columns
        if col not in text_columns
    ]

    for col in numeric_columns:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    for col in (
        "Market Cap (M)",
        "Revenue TTM (M)",
    ):
        if col in df.columns:
            df[col] = df[col] / 1_000_000

    for col in FRACTION_PERCENT_COLUMNS:
        if col in df.columns:
            df[col] = df[col] * 100

    df[numeric_columns] = (
        df[numeric_columns].round(2)
    )

    return df.sort_values(
        "Company",
        key=lambda s: s.str.lower(),
    ).reset_index(drop=True)


# ============================================================
# TABLE NUMBER FORMATTING
# ============================================================

def display_config(columns):

    config = {}

    for col in columns:

        if (
            col in PRICE_CHANGE_COLUMNS
            or col in FRACTION_PERCENT_COLUMNS
        ):
            config[col] = st.column_config.NumberColumn(
                col,
                format="%.2f%%",
            )

        elif col in (
            "P/E",
            "P/B",
            "Price/CF",
            "Debt/Equity",
        ):
            config[col] = st.column_config.NumberColumn(
                col,
                format="%.2fx",
            )

        elif col in (
            "Market Cap (M)",
            "Revenue TTM (M)",
        ):
            config[col] = st.column_config.NumberColumn(
                col,
                format="%.2f",
            )

    return config



# ============================================================
# INDEX MEMBERSHIP SNAPSHOTS — EFFECTIVE SEPTEMBER 2026
# ============================================================
# STI: September 2026 FTSE Russell review (no changes).
# Next 50: June 2026 list, adjusted for September 2026 review:
# GuocoLand (F17) removed; SBS Transit (S61) added.
# Membership is a snapshot, NOT an automatically updated index feed.

STI_CODES = set("""
A17U C38U 9CI C09 D05 D01 J69U BUOU G13 H78 J36 AJBU BN4
ME8U M44U N2IU O39 S58 5E2 U96 C6L S68 Z74 S63 Y92 U11
U14 V03 F34 BS6
""".split())

NEXT50_CODES = set("""
AWX O5RU F9D BEC HMN AU8U CY6U J85 8C8U OU8 G92 C52
544 9A4U Q5T EB5 F03 E28 E5H S61 H02 H22 AIY A7RU
K71U JYEU CJLU NTDU VC2 P52 C2PU PCT OYY BSL AP4
CRPU OV8 S59 P40U CC3 T82U BVA UIBU ULG 558 U10
T6I YF8 8YZ Z25
""".split())

INDEX_AS_OF = date(2026, 9, 28)
NEXT_REVIEW_MONTH = "December 2026"
STI_SOURCE = "https://www.lseg.com/en/ftse-russell/indices/sgx-st"
NEXT50_SOURCE = "https://links.sgx.com/1.0.0/corporate-announcements/NBNRMXZ0LOQICNLX/3bb133b9e4c57b118a2221407f3e3b296858c087da6c8b8c51b2d520082d4304"

# Market capitalisation in SGD millions. These are custom thresholds.
MID_MIN = 1000.0
MID_MAX = 10000.0
SMALL_MIN = 300.0


def normalized_code(value):
    return str(value).strip().upper().replace(".SI", "").replace("-SG", "")


def add_memberships(df):
    df = df.copy()
    codes = df["SGX Code"].map(normalized_code)
    df["STI Member"] = codes.isin(STI_CODES)
    df["Next 50 Member"] = codes.isin(NEXT50_CODES)
    # Currency conversion matters: only SGD-quoted stocks are assigned
    # custom market-cap tiers. Non-SGD stocks are left unclassified.
    cap = pd.to_numeric(df["Market Cap (M)"], errors="coerce") if "Market Cap (M)" in df else pd.Series(float("nan"), index=df.index)
    sgd = df["Currency"].astype(str).str.upper().eq("SGD")
    df["Cap Tier"] = "Unclassified"
    df.loc[sgd & cap.ge(MID_MIN) & cap.lt(MID_MAX), "Cap Tier"] = "Mid Cap"
    df.loc[sgd & cap.ge(SMALL_MIN) & cap.lt(MID_MIN), "Cap Tier"] = "Small Cap"
    df.loc[sgd & cap.lt(SMALL_MIN), "Cap Tier"] = "Micro Cap"
    df.loc[sgd & cap.ge(MID_MAX), "Cap Tier"] = "Large Cap"
    return df


def numeric_metrics(df):
    return [c for c in COLUMN_ORDER if c in df.columns and c not in
            {"Company", "SGX Code", "Sector", "Currency"} and
            pd.api.types.is_numeric_dtype(df[c])]


def summary_table(df):
    cols = numeric_metrics(df)
    if df.empty or not cols:
        return pd.DataFrame()
    vals = df[cols].replace([float("inf"), float("-inf")], float("nan"))
    result = pd.DataFrame({
        "Metric": cols,
        "Mean": vals.mean().values,
        "Median": vals.median().values,
        "25th Percentile": vals.quantile(.25).values,
        "75th Percentile": vals.quantile(.75).values,
        "Valid N": vals.count().values,
    })
    return result.round(2)



def boxplot_dashboard(df, key_prefix="stats", selected_code=None):
    """Readable vertical distributions with optional highlighted company."""
    metrics = numeric_metrics(df)
    if df.empty or not metrics:
        st.info("No numeric observations available for boxplots.")
        return

    view = st.radio(
        "Chart scale", ["Focused (readable)", "Full range (all outliers)"],
        horizontal=True, key=f"{key_prefix}_scale",
        help="Focused view zooms to the boxplot whiskers. Outliers beyond the axis are hidden, but remain in the calculations.",
    )
    st.caption(
        "The box shows the 25th–75th percentiles and the line shows the median. "
        "Orange X = searched company. Focused view may hide distant outliers; "
        "switch to Full range to inspect all observations."
    )
    selected_row = None
    if selected_code is not None and "SGX Code" in df.columns:
        matches = df.loc[df["SGX Code"].astype(str) == str(selected_code)]
        if not matches.empty:
            selected_row = matches.iloc[0]

    for start in range(0, len(metrics), 3):
        cols = st.columns(3, gap="medium")
        for offset, metric in enumerate(metrics[start:start + 3]):
            with cols[offset]:
                values = pd.to_numeric(df[metric], errors="coerce").replace(
                    [float("inf"), float("-inf")], float("nan")
                )
                valid = values.notna()
                subset = df.loc[valid]
                numbers = values.loc[valid]
                st.markdown(
                    f"<div style='text-align:center;font-size:20px;font-weight:750;"
                    f"margin:8px 0 2px 0'>{escape(metric)}</div>",
                    unsafe_allow_html=True,
                )
                if numbers.empty:
                    st.caption("No data")
                    continue
                fig = go.Figure()
                fig.add_trace(go.Box(
                    y=numbers.tolist(), x=["Peers"] * len(numbers),
                    name="Peers", boxpoints="outliers",
                    marker=dict(color="#E45752", size=5),
                    line=dict(color="#168A83", width=2),
                    fillcolor="rgba(22,138,131,0.25)",
                    customdata=subset[["Company", "SGX Code"]].astype(str).values,
                    hovertemplate=(
                        "%{customdata[0]} (%{customdata[1]})<br>"
                        + escape(metric) + ": %{y:,.2f}<extra></extra>"
                    ), showlegend=False,
                ))
                selected_value = None
                if selected_row is not None:
                    selected_value = pd.to_numeric(
                        pd.Series([selected_row[metric]]), errors="coerce"
                    ).iloc[0]
                    if pd.notna(selected_value) and pd.api.types.is_number(selected_value):
                        fig.add_trace(go.Scatter(
                            x=["Peers"], y=[float(selected_value)], mode="markers",
                            name="Searched company",
                            marker=dict(symbol="x", size=19, color="#E87520", line=dict(width=3, color="#E87520")),
                            hovertemplate=(
                                escape(str(selected_row["Company"])) + " ("
                                + escape(str(selected_code)) + ")<br>"
                                + escape(metric) + ": %{y:,.2f}<extra>Selected</extra>"
                            ), showlegend=False,
                        ))
                    else:
                        st.caption("Selected company: N/A")

                axis = dict(tickfont=dict(size=16), nticks=5, zeroline=False,
                            gridcolor="rgba(128,128,128,0.15)", automargin=True)
                if view.startswith("Focused") and len(numbers) >= 4:
                    q1, q3 = numbers.quantile([.25, .75])
                    iqr = q3 - q1
                    if iqr > 0:
                        lower = float(q1 - 1.5 * iqr)
                        upper = float(q3 + 1.5 * iqr)
                    else:
                        lower, upper = float(numbers.quantile(.05)), float(numbers.quantile(.95))
                    if lower == upper:
                        pad = max(abs(lower) * .1, 1)
                        lower -= pad
                        upper += pad
                    # Keep the searched stock visible even when outside the usual whiskers.
                    if selected_value is not None and pd.notna(selected_value):
                        lower = min(lower, float(selected_value))
                        upper = max(upper, float(selected_value))
                    pad = (upper - lower) * .08
                    axis["range"] = [lower - pad, upper + pad]
                fig.update_layout(
                    height=300,
                    margin=dict(l=22, r=12, t=18, b=12),
                    xaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
                    yaxis=axis,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(size=16),
                    hoverlabel=dict(font_size=15),
                    hovermode="closest",
                )
                st.plotly_chart(
                    fig, use_container_width=True,
                    config={"displayModeBar": False, "responsive": True},
                    key=f"{key_prefix}_box_{start + offset}",
                )


def category_comparison_plot(groups, metric, scale="Focused (readable)"):
    """Compare true stock-level category distributions with vertical boxplots."""
    fig = go.Figure()
    all_values = []
    plotted = 0
    for name, group_df in groups.items():
        values = pd.to_numeric(group_df[metric], errors="coerce").replace(
            [float("inf"), float("-inf")], float("nan")
        )
        valid = values.notna()
        if not valid.any():
            continue
        numbers = values.loc[valid]
        all_values.extend(numbers.tolist())
        plotted += 1
        fig.add_trace(go.Box(
            y=numbers.tolist(), name=name,
            boxpoints="outliers",
            quartilemethod="linear",
            marker=dict(color="#E45752", size=5),
            line=dict(color="#168A83", width=2),
            fillcolor="rgba(22,138,131,0.28)",
            customdata=group_df.loc[valid, ["Company", "SGX Code"]].astype(str).values,
            hovertemplate=("%{customdata[0]} (%{customdata[1]})<br>"
                           + escape(metric) + ": %{y:,.2f}<extra></extra>"),
            showlegend=False,
        ))
    if not plotted:
        st.info("No valid observations for the selected categories and metric.")
        return

    yaxis = dict(tickfont=dict(size=16), title_font=dict(size=17),
                 gridcolor="rgba(128,128,128,0.15)", automargin=True)
    if scale.startswith("Focused") and len(all_values) >= 4:
        # Pool category whisker endpoints for a readable shared axis.
        # This changes the VIEW only; all observations remain in the boxplot calculations.
        endpoints = []
        for trace in fig.data:
            series = pd.Series(trace.y, dtype=float)
            q1, q3 = series.quantile([.25, .75])
            iqr = q3 - q1
            if iqr > 0:
                within = series[(series >= q1 - 1.5 * iqr) &
                                (series <= q3 + 1.5 * iqr)]
                if not within.empty:
                    endpoints.extend([float(within.min()), float(within.max())])
            else:
                endpoints.extend([float(q1), float(q3)])
        if endpoints:
            lower, upper = min(endpoints), max(endpoints)
            if lower == upper:
                pad = max(abs(lower) * .1, 1)
            else:
                pad = (upper - lower) * .08
            yaxis["range"] = [lower - pad, upper + pad]

    fig.update_layout(
        height=480, margin=dict(l=45, r=12, t=25, b=135),
        font=dict(size=16), hoverlabel=dict(font_size=15),
        xaxis=dict(tickangle=-40, tickfont=dict(size=15), automargin=True),
        yaxis=yaxis, yaxis_title=metric, boxmode="group",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False, "responsive": True},
                    key="cross_category_box")


def peer_table(df, selected_code=None, key="peer"):
    """Show peers and emphasize the selected stock using Pandas Styler."""
    if df.empty:
        st.info("No stocks to display.")
        return
    df = df[[c for c in COLUMN_ORDER if c in df.columns]].copy()
    selected_code = normalized_code(selected_code) if selected_code else None
    if selected_code:
        df["_selected"] = df["SGX Code"].map(normalized_code).eq(selected_code)
        df = df.sort_values(["_selected", "Company"], ascending=[False, True])
        flags = df.pop("_selected")
        styled = style_stock_table(df)
        # Preserve existing conditional formatting while bolding selected row.
        styled = styled.apply(
            lambda row: ["font-weight: 800; background-color: #FFF1C7" if flags.loc[row.name] else "" for _ in row],
            axis=1,
        )
    else:
        styled = style_stock_table(df)
    st.dataframe(styled, column_config=display_config(df.columns),
                 hide_index=True, use_container_width=True, height=520)
    st.download_button("Download displayed table (CSV)",
                       df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"sgx_{key}.csv", mime="text/csv", key=f"download_{key}")


def category_data(df):
    groups = {"All SGX": df, "STI": df[df["STI Member"]],
              "iEdge Next 50": df[df["Next 50 Member"]]}
    for name in ("Large Cap", "Mid Cap", "Small Cap", "Micro Cap"):
        groups[name] = df[df["Cap Tier"] == name]
    for sector in sorted(df["Sector"].dropna().unique()):
        groups[f"Sector: {sector}"] = df[df["Sector"] == sector]
    return groups


# ============================================================
# MAIN APPLICATION
# ============================================================

st.title("SGX Equity Research Screener")
st.caption("Stock search · Sector peers · Index membership · Market-cap tiers · Statistics")

if "sgx_data" not in st.session_state:
    st.session_state.sgx_data = None

if st.button("Refresh SGX Data", type="primary"):
    with st.spinner("Fetching and cleaning SGX stock data..."):
        try:
            ensure_chromium()
            raw = asyncio.run(fetch_sgx_data())
            st.session_state.sgx_data = clean_sgx_data(raw)
            st.success(f"Loaded {len(st.session_state.sgx_data):,} records.")
        except Exception as exc:
            st.error(f"Unable to fetch SGX data: {exc}")

if st.session_state.sgx_data is None:
    st.warning("Click 'Refresh SGX Data' to load the stock screener.")
    st.stop()

full_df = add_memberships(st.session_state.sgx_data)
full_df["Sector"] = full_df["Sector"].fillna("Unclassified").replace(
    {"Not Available": "Unclassified", "-": "Unclassified", "": "Unclassified"})

# Automatic research-attention lists: latest SGX cross-sectional data only.
# These are screening signals, not recommendations or verified event alerts.
def watchlist_page():
    st.header("Research Attention List")
    st.caption(
        "Automatically identifies up to 30 SGX stocks per signal using the latest "
        "refreshed snapshot. Research leads only, not investment recommendations."
    )

    st.subheader("Attention List Filters")
    st.caption(
        "Choose any combination of index and size groups. Multiple selections are "
        "combined with OR; the market-cap cutoff is applied in addition."
    )
    group_options = [
        "STI", "iEdge Next 50", "Large Cap", "Mid Cap",
        "Small Cap", "Micro Cap",
    ]
    selected_groups = st.multiselect(
        "Size & Index (leave blank for all SGX stocks)",
        options=group_options,
        key="attention_groups",
    )
    enable_cap_cutoff = st.checkbox(
        "Apply minimum market capitalization (S$ million)",
        key="attention_cap_enabled",
    )
    min_cap = None
    if enable_cap_cutoff:
        min_cap = st.number_input(
            "Minimum market cap (S$M)",
            min_value=0.0,
            value=300.0,
            step=100.0,
            key="attention_min_cap",
            help="Only SGD-quoted securities are included when this cutoff is active.",
        )

    universe = full_df.copy()
    if selected_groups:
        group_mask = pd.Series(False, index=universe.index)
        if "STI" in selected_groups:
            group_mask |= universe["STI Member"].fillna(False)
        if "iEdge Next 50" in selected_groups:
            group_mask |= universe["Next 50 Member"].fillna(False)
        tiers = [name for name in selected_groups if name.endswith("Cap")]
        if tiers:
            group_mask |= universe["Cap Tier"].isin(tiers)
        universe = universe.loc[group_mask].copy()

    if min_cap is not None:
        market_caps = pd.to_numeric(universe["Market Cap (M)"], errors="coerce")
        universe = universe.loc[
            universe["Currency"].eq("SGD") & market_caps.ge(min_cap)
        ].copy()
        st.caption(
            "Market-cap cutoff applies only to SGD-quoted securities because "
            "the SGX market-cap values have not been FX-converted."
        )

    st.caption(f"Screening universe: {len(universe):,} of {len(full_df):,} SGX stocks.")
    momentum_metric = st.selectbox(
        "Price momentum period (Strong Price Momentum category)",
        options=[
            "4W Change (%)", "13W Change (%)",
            "26W Change (%)", "52W Change (%)",
        ],
        index=1,
        key="attention_momentum_period",
        format_func=lambda x: {
            "4W Change (%)": "4 weeks",
            "13W Change (%)": "13 weeks",
            "26W Change (%)": "26 weeks",
            "52W Change (%)": "52 weeks",
        }[x],
    )

    # Compute sector benchmarks from the full SGX universe so narrowing the
    # attention-list filters does not silently change the comparison baseline.
    metric_columns = [
        "Revenue Growth (%)", "P/B", "P/E", "ROE (%)",
        "Net Profit Margin (%)", "Dividend Yield (%)",
        "4W Change (%)", "13W Change (%)", "26W Change (%)",
        "52W Change (%)", "Debt/Equity",
    ]
    benchmark_source = full_df.copy()
    for col in metric_columns:
        benchmark_source[col] = pd.to_numeric(
            benchmark_source[col], errors="coerce"
        )
        universe[col] = pd.to_numeric(universe[col], errors="coerce")
    sector_sizes = benchmark_source.groupby("Sector")["SGX Code"].count()
    universe["_sector_n"] = universe["Sector"].map(sector_sizes)
    for col in metric_columns:
        sector_medians = benchmark_source.groupby("Sector")[col].median()
        universe[f"_median_{col}"] = universe["Sector"].map(sector_medians)

    momentum_thresholds = {
        "4W Change (%)": 5.0,
        "13W Change (%)": 10.0,
        "26W Change (%)": 15.0,
        "52W Change (%)": 20.0,
    }
    categories = [
        ("Revenue Growth Standouts", "Revenue Growth (%)", "high",
         "Revenue growth at least 20% and above the full-sector median."),
        ("Deep P/B Discounts", "P/B", "low",
         "Positive P/B below 1.0x and below 75% of the full-sector median."),
        ("Low P/E Relative to Peers", "P/E", "low",
         "Positive P/E below 15x and below 75% of the full-sector median."),
        ("Profitability Standouts", "ROE (%)", "high",
         "ROE at least 15%, above the full-sector median, with positive net profit margin."),
        ("Dividend Yield Outliers", "Dividend Yield (%)", "high",
         "Dividend yield at least 5% and above the full-sector median."),
        ("Strong Price Momentum", momentum_metric, "high",
         f"{momentum_metric} at least {momentum_thresholds[momentum_metric]:g}% "
         "and above the full-sector median."),
        ("Leverage Watch", "Debt/Equity", "high",
         "Debt/equity at least 1.0x and above the full-sector median."),
    ]
    st.info(
        "Sector comparisons require at least 5 constituents in the full SGX "
        "sector. Missing metrics are excluded. Stocks may appear in multiple lists."
    )
    tabs = st.tabs([name for name, _, _, _ in categories])
    for tab, (name, metric, direction, explanation) in zip(tabs, categories):
        with tab:
            st.caption(explanation)
            d = universe.loc[
                universe["_sector_n"].ge(5)
                & universe[metric].notna()
                & universe[f"_median_{metric}"].notna()
            ].copy()
            med = d[f"_median_{metric}"]
            v = d[metric]
            if name == "Revenue Growth Standouts":
                mask = (v >= 20) & (v > med)
            elif name == "Deep P/B Discounts":
                mask = (v > 0) & (v < 1) & (med > 0) & (v < med * .75)
            elif name == "Low P/E Relative to Peers":
                mask = (v > 0) & (v < 15) & (med > 0) & (v < med * .75)
            elif name == "Profitability Standouts":
                mask = (v >= 15) & (v > med) & (d["Net Profit Margin (%)"] > 0)
            elif name == "Dividend Yield Outliers":
                mask = (v >= 5) & (v > med)
            elif name == "Strong Price Momentum":
                mask = (v >= momentum_thresholds[momentum_metric]) & (v > med)
            else:
                mask = (v >= 1) & (v > med)
            d = d.loc[mask].sort_values(
                metric, ascending=(direction == "low")
            ).head(30)
            st.metric("Stocks flagged", len(d))
            if d.empty:
                st.info("No stocks meet this screen with the current filters.")
                continue
            view = d[["Company", "SGX Code", "Sector", "Market Cap (M)", metric]].copy()
            view[f"Sector Median ({metric})"] = d[f"_median_{metric}"].values
            if name == "Profitability Standouts":
                view["Net Profit Margin (%)"] = d["Net Profit Margin (%)"].values
            st.dataframe(
                style_stock_table(view), hide_index=True,
                use_container_width=True,
            )
            st.download_button(
                "Export flagged stocks (CSV)",
                view.to_csv(index=False).encode("utf-8-sig"),
                file_name="sgx_" + name.lower().replace(" ", "_").replace("/", "_") + ".csv",
                mime="text/csv",
                key="attention_" + metric.replace("/", "_") + name[:4],
            )
    st.caption(
        "Source: current SGX stock screener snapshot. Market-cap tiers are "
        "custom SGD classifications; index membership uses the existing snapshot. "
        "Refresh SGX Data to update these lists."
    )

# ============================================================
# INDEPENDENT NAVIGATION PAGES
# ============================================================


def company_search_page():
    st.header("Company Search & Sector Peers")
    query = st.text_input("Search company name or SGX ticker", placeholder="DBS, CapitaLand, D05...")
    if query.strip():
        matched = full_df[
            full_df["Company"].astype(str).str.contains(query.strip(), case=False, na=False, regex=False)
            | full_df["SGX Code"].astype(str).str.contains(query.strip(), case=False, na=False, regex=False)
        ]
        if matched.empty:
            st.info("No matching company or ticker found.")
        else:
            options = matched.index.tolist()
            chosen = st.selectbox("Choose a company", options,
                format_func=lambda i: f"{full_df.at[i, 'Company']} ({full_df.at[i, 'SGX Code']})")
            selected = full_df.loc[chosen]
            sector = selected["Sector"]
            st.subheader(f"{selected['Company']} ({selected['SGX Code']})")
            # Optional peer universe: sector peers by default, or the company's
            # market-cap tier. STI and Next 50 members share one pooled index
            # universe here (not two separate peer groups).
            use_size_peers = st.checkbox(
                "Compare within the same size / index group",
                value=False,
                key="search_same_size_group",
                help=(
                    "For STI or iEdge Next 50 members, compare against the combined "
                    "STI + Next 50 universe. For other companies, compare against "
                    "the same market-cap tier. Index membership is based on "
                    "September 2026 snapshots."
                ),
            )
            if use_size_peers:
                is_index_member = bool(selected["STI Member"]) or bool(selected["Next 50 Member"])
                if is_index_member:
                    peer_mask = (full_df["STI Member"].fillna(False)
                                 | full_df["Next 50 Member"].fillna(False))
                    peer_description = "Combined STI + iEdge Next 50"
                else:
                    cap_tier = str(selected["Cap Tier"])
                    if cap_tier == "Unclassified":
                        st.warning(
                            "This stock has no assigned SGD market-cap tier. "
                            "Showing its sector peers instead."
                        )
                        peer_mask = full_df["Sector"].eq(sector)
                        peer_description = f"Sector: {sector} (size unavailable)"
                    else:
                        peer_mask = full_df["Cap Tier"].eq(cap_tier)
                        peer_description = f"Size group: {cap_tier}"

                # Analysts may narrow the size/index universe to the same
                # industry, without losing the ability to compare across sectors.
                same_sector_only = st.checkbox(
                    "Also restrict to the same sector",
                    value=False,
                    key="search_size_same_sector",
                )
                if same_sector_only:
                    peer_mask &= full_df["Sector"].eq(sector)
                    peer_description += f" · Sector: {sector}"
                peers = full_df.loc[peer_mask].copy()
            else:
                peers = full_df[full_df["Sector"] == sector].copy()
                peer_description = f"Sector: {sector}"

            # Always include the searched company if its classification is
            # missing or if it is not found in the chosen peer universe.
            if chosen not in peers.index:
                peers = pd.concat([full_df.loc[[chosen]], peers]).loc[
                    lambda frame: ~frame.index.duplicated(keep="first")
                ]
            st.caption(
                f"{peer_description} · {len(peers)} companies · "
                "Selected stock highlighted in bold"
            )
            peer_table(peers, selected["SGX Code"], "search_peers")
            with st.expander("Peer Group Distributions", expanded=False):
                boxplot_dashboard(
                    peers, key_prefix="company_peers",
                    selected_code=selected["SGX Code"],
                )



def market_explorer_page():
    st.header("Top & Bottom Overview")
    st.caption(
        "Select any combination of size/index groups and sectors. "
        "Selections within a group are combined using OR; "
        "size/index and sector selections are combined using AND. "
        "Index membership uses September 2026 snapshots."
    )

    size_names = ["STI", "iEdge Next 50", "Large Cap", "Mid Cap", "Small Cap", "Micro Cap"]
    sectors = sorted(full_df["Sector"].dropna().astype(str).unique().tolist())

    with st.container(border=True):
        st.subheader("Choose your universe")
        st.caption("Leave a group unchecked to include all stocks from that group.")
        st.markdown("**Size & Index**")
        size_cols = st.columns(3)
        selected_sizes = []
        for i, name in enumerate(size_names):
            with size_cols[i % 3]:
                if st.checkbox(name, key=f"overview_size_{i}"):
                    selected_sizes.append(name)

        st.divider()
        st.markdown("**Industry Sector**")
        sector_cols = st.columns(3)
        selected_sectors = []
        for i, name in enumerate(sectors):
            with sector_cols[i % 3]:
                if st.checkbox(name, key=f"overview_sector_{i}"):
                    selected_sectors.append(name)

        if st.button("Clear selections", key="overview_clear"):
            for key in list(st.session_state):
                if key.startswith("overview_size_") or key.startswith("overview_sector_"):
                    del st.session_state[key]
            st.rerun()

    data = full_df.copy()
    if selected_sizes:
        size_mask = pd.Series(False, index=data.index)
        if "STI" in selected_sizes:
            size_mask |= data["STI Member"].fillna(False)
        if "iEdge Next 50" in selected_sizes:
            size_mask |= data["Next 50 Member"].fillna(False)
        cap_tiers = [name for name in selected_sizes if name in
                     {"Large Cap", "Mid Cap", "Small Cap", "Micro Cap"}]
        if cap_tiers:
            size_mask |= data["Cap Tier"].isin(cap_tiers)
        data = data.loc[size_mask]
    if selected_sectors:
        data = data.loc[data["Sector"].isin(selected_sectors)]

    st.metric("Stocks in selected universe", f"{len(data):,}")
    active = []
    if selected_sizes:
        active.append("Size/Index: " + ", ".join(selected_sizes))
    if selected_sectors:
        active.append("Sectors: " + ", ".join(selected_sectors))
    st.caption(" | ".join(active) if active else "All SGX stocks selected")

    with st.expander("Cross-category statistical comparison", expanded=False):
        st.caption("Compare individual categories across the full SGX universe. Index, cap and sector groups can overlap.")
        groups = category_data(full_df)
        metric_options = numeric_metrics(full_df)
        if metric_options:
            metric = st.selectbox(
                "Choose financial metric", metric_options,
                index=metric_options.index("P/E") if "P/E" in metric_options else 0,
                key="overview_comparison_metric",
            )
            # Always compare every available index, cap tier, and sector.
            # No category multiselect: only the financial metric is user-selected.
            # This also avoids stale Streamlit session-state selections hiding groups.
            selected_categories = list(groups.keys())
            st.caption(f"Showing all {len(selected_categories)} categories by default: "
                       "indices, market-cap tiers, and every available sector.")
            comparison_scale = st.radio(
                "Chart scale",
                ["Focused (readable)", "Full range (all outliers)"],
                horizontal=True, key="overview_comparison_scale",
            )
            st.caption("Each category uses its own stock-level observations. "
                       "Focused view hides distant outliers visually, not statistically. "
                       "Categories may overlap.")
            category_comparison_plot(
                {name: groups[name] for name in selected_categories},
                metric, comparison_scale,
            )

    if data.empty:
        st.info("No SGX stocks match this combination. Uncheck one or more options.")
    else:
        with st.expander("Category Distributions", expanded=False):
            boxplot_dashboard(data, key_prefix="overview")

        st.subheader("Top & Bottom Rankings")
        show_sector_rankings(data, key_prefix="overview_combined")

        st.subheader("Full Stock Table")
        filter_text = st.text_input("Search within selected stocks by company or ticker", key="overview_search")
        visible = data
        if filter_text.strip():
            term = filter_text.strip()
            visible = data.loc[
                data["Company"].astype(str).str.contains(term, case=False, na=False, regex=False)
                | data["SGX Code"].astype(str).str.contains(term, case=False, na=False, regex=False)
            ]
        st.write(f"Showing **{len(visible):,}** records")
        peer_table(visible, key="overview_combined")




# ============================================================
# SGX-STYLE ADVANCED STOCK FILTER
# ============================================================

def stock_filter_page():
    st.header("SGX Stock Filter")
    st.caption("Select the filters you want to apply. All enabled filters must match. Values use the units shown in the SGX stock table.")

    filter_metrics = [
        ("Revenue TTM (M)", "Total Revenue (M)"),
        ("Market Cap (M)", "Market Cap (M)"),
        ("P/E", "P/E"),
        ("Dividend Yield (%)", "Dividend Yield (%)"),
        ("4W Change (%)", "4-Week Price Change (%)"),
        ("13W Change (%)", "13-Week Price Change (%)"),
        ("26W Change (%)", "26-Week Price Change (%)"),
        ("52W Change (%)", "52-Week Price Change (%)"),
        ("Net Profit Margin (%)", "Net Profit Margin (%)"),
        ("ROE (%)", "ROE (%)"),
        ("P/B", "Price / Book"),
        ("Price/CF", "Price / Cash Flow"),
        ("Debt/Equity", "Debt / Equity"),
        ("Revenue Growth (%)", "Revenue Growth (%)"),
    ]

    conditions = []
    selected_sectors = []
    sector_enabled = False
    group_enabled = False
    selected_groups = []

    with st.expander("Filter by", expanded=True):
        group_enabled = st.checkbox("Size & Index", key="screen_group_enabled")
        group_options = ["STI", "iEdge Next 50", "Large Cap", "Mid Cap", "Small Cap", "Micro Cap"]
        selected_groups = st.multiselect(
            "Choose index or market-cap groups",
            group_options,
            disabled=not group_enabled,
            placeholder="All stocks" if not group_enabled else "Select groups",
            key="screen_groups",
        )
        st.caption("Multiple selected groups are combined using OR. Index membership uses the September 2026 snapshot; cap tiers use custom SGD market-cap thresholds.")
        st.divider()

        sector_enabled = st.checkbox("Sector", key="screen_sector_enabled")
        sectors = sorted(full_df["Sector"].dropna().astype(str).unique().tolist())
        selected_sectors = st.multiselect(
            "Choose sectors", sectors, disabled=not sector_enabled,
            placeholder="All sectors" if not sector_enabled else "Select sectors",
            key="screen_sectors",
        )
        st.divider()

        for col, label in filter_metrics:
            if col not in full_df.columns:
                continue
            series = pd.to_numeric(full_df[col], errors="coerce")
            series = series.replace([float("inf"), float("-inf")], float("nan")).dropna()
            if series.empty:
                continue
            low = float(series.min())
            high = float(series.max())
            enabled = st.checkbox(label, key=f"screen_on_{col}")
            lower_col, upper_col = st.columns(2)
            with lower_col:
                lower_text = st.text_input(
                    "Minimum", placeholder=f"{low:,.2f}",
                    disabled=not enabled, key=f"screen_min_{col}",
                    label_visibility="visible",
                )
            with upper_col:
                upper_text = st.text_input(
                    "Maximum", placeholder=f"{high:,.2f}",
                    disabled=not enabled, key=f"screen_max_{col}",
                    label_visibility="visible",
                )
            if enabled:
                try:
                    minimum = float(lower_text.replace(",", "")) if lower_text.strip() else None
                    maximum = float(upper_text.replace(",", "")) if upper_text.strip() else None
                    if minimum is not None and maximum is not None and minimum > maximum:
                        st.warning(f"{label}: minimum exceeds maximum.")
                    conditions.append((col, minimum, maximum))
                except ValueError:
                    st.warning(f"{label}: enter a valid number.")
                    conditions.append((col, float("inf"), float("-inf")))
            st.divider()

        if st.button("Clear all filters", key="screen_clear_filters"):
            for key in list(st.session_state):
                if key.startswith("screen_") and key != "screen_clear_filters":
                    del st.session_state[key]
            st.rerun()

    st.divider()
    filtered = full_df.copy()
    if group_enabled and selected_groups:
        group_mask = pd.Series(False, index=filtered.index)
        if "STI" in selected_groups:
            group_mask |= filtered["STI Member"].fillna(False)
        if "iEdge Next 50" in selected_groups:
            group_mask |= filtered["Next 50 Member"].fillna(False)
        selected_tiers = [g for g in selected_groups if g in {"Large Cap", "Mid Cap", "Small Cap", "Micro Cap"}]
        if selected_tiers:
            group_mask |= filtered["Cap Tier"].isin(selected_tiers)
        filtered = filtered.loc[group_mask]
    if sector_enabled and selected_sectors:
        filtered = filtered[filtered["Sector"].isin(selected_sectors)]
    for col, minimum, maximum in conditions:
        values = pd.to_numeric(filtered[col], errors="coerce")
        if minimum is not None:
            filtered = filtered.loc[values >= minimum]
            values = pd.to_numeric(filtered[col], errors="coerce")
        if maximum is not None:
            filtered = filtered.loc[values <= maximum]

    st.subheader("Filtered SGX Stocks")
    st.metric("Matching stocks", f"{len(filtered):,}")
    st.caption(f"{len(filtered):,} of {len(full_df):,} SGX stocks match your active filters.")
    search_text = st.text_input("Search results by company or ticker", key="screen_results_search")
    if search_text.strip():
        term = search_text.strip()
        filtered = filtered[
            filtered["Company"].astype(str).str.contains(term, case=False, regex=False, na=False)
            | filtered["SGX Code"].astype(str).str.contains(term, case=False, regex=False, na=False)
        ]
    if filtered.empty:
        st.info("No stocks match these filters. Widen the ranges or clear some filters.")
    else:
        peer_table(filtered, key="advanced_filter")
        with st.expander("Filtered Stock Distributions", expanded=False):
            boxplot_dashboard(filtered, key_prefix="filtered")


# Three research sections appear as horizontal tabs beneath the refresh button.
# The shared SGX dataset stays in session state when switching tabs.
search_tab, explorer_tab, filter_tab, watchlist_tab = st.tabs(
    [
        "🔎 Company Search & Sector Peers",
        "📊 Top & Bottom Overview",
        "🎛️ SGX Stock Filter",
        "🚩 Research Attention List",
    ]
)

with search_tab:
    company_search_page()

with explorer_tab:
    market_explorer_page()

with filter_tab:
    stock_filter_page()

with watchlist_tab:
    watchlist_page()

# Reference notes remain available below the research tabs.
if len(STI_CODES) != 30 or len(NEXT50_CODES) != 50:
    st.error("Index membership snapshot has an unexpected number of tickers.")

with st.expander("Index data sources and classification methodology", expanded=False):
    st.write(f"Index constituent snapshots effective **{INDEX_AS_OF:%d %B %Y}**. Next scheduled reviews: **{NEXT_REVIEW_MONTH}**. Membership does not update automatically.")
    st.markdown(f"[FTSE Russell STI source]({STI_SOURCE}) · [SGX Next 50 September review]({NEXT50_SOURCE})")
    st.write("Custom SGD-denominated market-cap tiers: Large ≥ S$10bn; Mid S$1bn–<S$10bn; Small S$300m–<S$1bn; Micro <S$300m. Non-SGD stocks remain unclassified; these are not official index definitions.")
    st.write("Index tabs contain matched stocks from the SGX screener, which may be fewer than the official constituent counts. Financial statistics exclude missing values on a per-metric basis.")

if date.today() >= date(2026, 12, 1):
    st.warning("Index membership snapshot may be outdated: December 2026 review is due. Update STI_CODES and NEXT50_CODES after verifying official changes.")

