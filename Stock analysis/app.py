"""SGX Equity Research Screener with shared-password access."""
import asyncio
import hmac
import pandas as pd
import streamlit as st
from playwright.async_api import async_playwright
from sector_rankings import show_sector_rankings
from table_styles import style_stock_table

SGX_URL = "https://investors.sgx.com/market/stock-screener?from=/market/securities"
st.set_page_config(page_title="SGX Equity Screener", page_icon="📈", layout="wide")

def require_password():
    if st.session_state.get("authenticated", False):
        return
    st.title("🔐 SGX Equity Research Screener")
    st.caption("Enter the shared team password to access the screener.")
    with st.form("login"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary")
    if submitted:
        try:
            expected = st.secrets["APP_PASSWORD"]
        except (KeyError, FileNotFoundError):
            st.error("APP_PASSWORD is not configured in Streamlit Secrets.")
            st.stop()
        if hmac.compare_digest(password.encode(), str(expected).encode()):
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

COLUMN_NAMES = {
    "companyName": "Company", "stockCode": "SGX Code", "sector": "Sector",
    "marketCapitalization": "Market Cap (M)", "salesTTM": "Revenue TTM (M)",
    "priceToEarningsRatio": "P/E", "priceToBookRatio": "P/B",
    "returnOnAvgCommonEquity": "ROE (%)", "dividendYield": "Dividend Yield (%)",
    "fourWeekPricePercentChange": "4W Change (%)",
    "thirteenWeekPricePercentChange": "13W Change (%)",
    "twentySixWeekPricePercentChange": "26W Change (%)",
    "fiftyTwoWeekPricePercentChange": "52W Change (%)",
    "netProfitMargin": "Net Profit Margin (%)",
    "priceToCashFlowPerShareRatio": "Price/CF",
    "totalDebtToTotalEquityRatio": "Debt/Equity",
    "salesPercentageChange": "Revenue Growth (%)", "priceCurrCode": "Currency",
}
COLUMN_ORDER = [
    "Company", "SGX Code", "Sector", "Currency", "Market Cap (M)",
    "P/E", "P/B", "ROE (%)", "Dividend Yield (%)", "4W Change (%)",
    "13W Change (%)", "26W Change (%)", "52W Change (%)", "Revenue TTM (M)",
    "Revenue Growth (%)", "Net Profit Margin (%)", "Price/CF", "Debt/Equity",
]
FRACTION_PERCENT_COLUMNS = [
    "ROE (%)", "Dividend Yield (%)", "Net Profit Margin (%)", "Revenue Growth (%)"
]
PRICE_CHANGE_COLUMNS = [
    "4W Change (%)", "13W Change (%)", "26W Change (%)", "52W Change (%)"
]

def find_records(data):
    """Find stock screener records in a nested SGX JSON response."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            keys = set().union(*(item.keys() for item in data[:10] if isinstance(item, dict)))
            if "stockCode" in keys and (
                "companyName" in keys or "priceToEarningsRatio" in keys
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

async def fetch_sgx_data():
    captured = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        pending = []
        async def capture_response(response):
            if "/stockscreener/v2.0/all" in response.url and response.status == 200:
                try:
                    captured.append(await response.json())
                except Exception:
                    pass
        def on_response(response):
            pending.append(asyncio.create_task(capture_response(response)))
        page.on("response", on_response)
        try:
            await page.goto(SGX_URL, wait_until="domcontentloaded", timeout=60000)
            for _ in range(30):
                if captured:
                    break
                await page.wait_for_timeout(1000)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not captured:
                raise RuntimeError("SGX stock screener response was not detected.")
            return captured[0]
        finally:
            await browser.close()

def clean_sgx_data(raw_data):
    records = find_records(raw_data)
    if records is None:
        raise ValueError("Could not identify stock records in SGX response.")
    df = pd.json_normalize(records).rename(columns=COLUMN_NAMES)
    df = df[[col for col in COLUMN_ORDER if col in df.columns]].copy()
    if "SGX Code" not in df.columns:
        raise ValueError("SGX Code column is missing.")
    df["SGX Code"] = df["SGX Code"].astype("string").str.strip()
    df = df.dropna(subset=["SGX Code"])
    df = df[df["SGX Code"] != ""].drop_duplicates(subset=["SGX Code"])
    for col in ("Company", "Sector", "Currency"):
        if col not in df.columns:
            df[col] = "Not Available"
        else:
            df[col] = df[col].fillna("Not Available").replace("", "Not Available")
    df["Sector"] = df["Sector"].replace({"Not Available": "Unclassified", "-": "Unclassified"})
    text_columns = {"Company", "SGX Code", "Sector", "Currency"}
    numeric_columns = [col for col in df.columns if col not in text_columns]
    for col in numeric_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("Market Cap (M)", "Revenue TTM (M)"):
        if col in df.columns:
            df[col] = df[col] / 1_000_000
    for col in FRACTION_PERCENT_COLUMNS:
        if col in df.columns:
            df[col] = df[col] * 100
    df[numeric_columns] = df[numeric_columns].round(2)
    return df.sort_values("Company", key=lambda s: s.str.lower()).reset_index(drop=True)

def display_config(columns):
    config = {}
    for col in columns:
        if col in PRICE_CHANGE_COLUMNS or col in FRACTION_PERCENT_COLUMNS:
            config[col] = st.column_config.NumberColumn(col, format="%.2f%%")
        elif col in ("P/E", "P/B", "Price/CF", "Debt/Equity"):
            config[col] = st.column_config.NumberColumn(col, format="%.2fx")
        elif col in ("Market Cap (M)", "Revenue TTM (M)"):
            config[col] = st.column_config.NumberColumn(col, format="%.2f")
    return config

st.title("SGX Equity Research Screener")
st.caption("SGX stock screener | Valuation ratios | Price performance | Sector explorer")
if "sgx_data" not in st.session_state:
    st.session_state.sgx_data = None
if st.button("Refresh SGX Data", type="primary"):
    with st.spinner("Fetching and cleaning SGX stock data..."):
        try:
            raw = asyncio.run(fetch_sgx_data())
            st.session_state.sgx_data = clean_sgx_data(raw)
            st.success(f"Loaded {len(st.session_state.sgx_data):,} records.")
        except Exception as exc:
            st.error(f"Unable to fetch SGX data: {exc}")
if st.session_state.sgx_data is None:
    st.warning("Click 'Refresh SGX Data' to load the stock screener.")
else:
    full_df = st.session_state.sgx_data.copy()
    full_df["Sector"] = (
        full_df["Sector"].fillna("Unclassified")
        .replace({"Not Available": "Unclassified", "-": "Unclassified", "": "Unclassified"})
    )
    st.header("SGX Sector Explorer")
    st.caption("Choose a tab to view that sector's own rankings and table. Scroll the tab bar horizontally to see all sectors.")
    sector_counts = full_df.groupby("Sector").size().sort_index()
    sector_names = sector_counts.index.tolist()
    tab_labels = [f"All SGX ({len(full_df):,})"] + [
        f"{sector} ({int(sector_counts[sector])})" for sector in sector_names
    ]
    tabs = st.tabs(tab_labels)
    for tab_index, tab in enumerate(tabs):
        with tab:
            sector = None if tab_index == 0 else sector_names[tab_index - 1]
            sector_df = full_df.copy() if sector is None else full_df.loc[full_df["Sector"] == sector].copy()
            title = "All SGX Stocks" if sector is None else sector
            st.subheader(title)
            st.metric("Records", f"{len(sector_df):,}")
            show_sector_rankings(sector_df, key_prefix=f"tab_{tab_index}")
            st.subheader("Full Stock Table")
            search = st.text_input(
                "Search company or SGX code", placeholder="e.g. DBS, CapitaLand, D05",
                key=f"search_tab_{tab_index}",
            )
            if search:
                mask = (
                    sector_df["Company"].astype(str).str.contains(search, case=False, na=False, regex=False)
                    | sector_df["SGX Code"].astype(str).str.contains(search, case=False, na=False, regex=False)
                )
                sector_df = sector_df.loc[mask]
            st.write(f"Showing **{len(sector_df):,}** records")
            st.dataframe(
                style_stock_table(sector_df),
                column_config=display_config(sector_df.columns),
                hide_index=True, use_container_width=True, height=550,
            )
            filename_sector = "all" if sector is None else sector.lower().replace(" ", "_")
            st.download_button(
                f"Download {title} CSV",
                sector_df.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"sgx_{filename_sector}.csv", mime="text/csv",
                key=f"download_tab_{tab_index}",
            )
