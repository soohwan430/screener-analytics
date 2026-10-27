
"""SGX Equity Research Screener with shared-password access."""

import asyncio
import hmac
import subprocess
import sys
from datetime import date
from html import escape

import pandas as pd
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

if len(STI_CODES) != 30 or len(NEXT50_CODES) != 50:
    st.error("Index membership snapshot has an unexpected number of tickers.")

with st.expander("Index data sources and classification methodology", expanded=False):
    st.write(f"Index constituent snapshots effective **{INDEX_AS_OF:%d %B %Y}**. Next scheduled reviews: **{NEXT_REVIEW_MONTH}**. Membership does not update automatically.")
    st.markdown(f"[FTSE Russell STI source]({STI_SOURCE}) · [SGX Next 50 September review]({NEXT50_SOURCE})")
    st.write("Custom SGD-denominated market-cap tiers: Large ≥ S$10bn; Mid S$1bn–<S$10bn; Small S$300m–<S$1bn; Micro <S$300m. Non-SGD stocks remain unclassified; these are not official index definitions.")
    st.write("Index tabs contain matched stocks from the SGX screener, which may be fewer than the official constituent counts. Financial statistics exclude missing values on a per-metric basis.")

if date.today() >= date(2026, 12, 1):
    st.warning("Index membership snapshot may be outdated: December 2026 review is due. Update STI_CODES and NEXT50_CODES after verifying official changes.")

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
        peers = full_df[full_df["Sector"] == sector].copy()
        st.subheader(f"{selected['Company']} ({selected['SGX Code']})")
        st.caption(f"Sector: {sector} · {len(peers)} companies · Selected stock highlighted in bold")
        peer_table(peers, selected["SGX Code"], "search_peers")
        with st.expander("Peer group statistics"):
            st.dataframe(summary_table(peers), hide_index=True, use_container_width=True)

st.header("SGX Sector & Market Explorer")
st.caption("Index tabs use September 2026 membership snapshots. Market-cap tiers are custom SGD thresholds.")
groups = category_data(full_df)
labels = [f"{name} ({len(data):,})" for name, data in groups.items()]
tabs = st.tabs(labels)
for idx, ((name, data), tab) in enumerate(zip(groups.items(), tabs)):
    with tab:
        st.subheader(name)
        st.metric("SGX records", f"{len(data):,}")
        if name in ("STI", "iEdge Next 50"):
            expected = 30 if name == "STI" else 50
            if len(data) != expected:
                st.warning(f"Matched {len(data)} of {expected} snapshot tickers in the SGX screener. Missing counters are not included in the statistics.")
        if data.empty:
            st.info("No records in this category.")
            continue
        show_sector_rankings(data, key_prefix=f"category_{idx}")
        st.subheader("Full Stock Table")
        filter_text = st.text_input("Filter company or ticker", key=f"filter_{idx}")
        visible = data
        if filter_text:
            visible = data[
                data["Company"].astype(str).str.contains(filter_text, case=False, na=False, regex=False)
                | data["SGX Code"].astype(str).str.contains(filter_text, case=False, na=False, regex=False)]
        st.write(f"Showing **{len(visible):,}** records")
        peer_table(visible, key=f"category_{idx}")
        st.subheader("Category Statistics")
        st.dataframe(summary_table(data), hide_index=True, use_container_width=True)

st.header("Cross-Category Statistical Comparison")
st.caption("Each row is a sector or market category. Index membership overlaps with sector and cap-tier groups. Counts are based on available SGX screener records.")
metric_options = numeric_metrics(full_df)
if metric_options:
    metric = st.selectbox("Choose financial metric", metric_options, index=metric_options.index("P/E") if "P/E" in metric_options else 0)
    rows = []
    for name, data in groups.items():
        v = pd.to_numeric(data[metric], errors="coerce").replace([float("inf"), float("-inf")], float("nan")).dropna()
        rows.append({"Category": name, "Stocks": len(data), "Valid N": len(v),
                     "Mean": v.mean(), "Median": v.median(),
                     "25th Percentile": v.quantile(.25), "75th Percentile": v.quantile(.75)})
    stats = pd.DataFrame(rows).round(2)
    st.dataframe(stats, hide_index=True, use_container_width=True)
    st.download_button("Download comparison CSV", stats.to_csv(index=False).encode("utf-8-sig"),
                       file_name="sgx_category_statistics.csv", mime="text/csv")
