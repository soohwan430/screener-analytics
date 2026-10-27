"""SGX sector rankings: Top 10 and Bottom 10 stocks."""
import numpy as np
import pandas as pd
import streamlit as st
from table_styles import style_stock_table

RANKING_METRICS = [
    ("Valuation - P/E", "P/E", True),
    ("Profitability - ROE", "ROE (%)", True),
    ("Revenue Growth", "Revenue Growth (%)", True),
    ("Price Momentum - 4W", "4W Change (%)", True),
    ("Price Momentum - 13W", "13W Change (%)", True),
    ("Price Momentum - 26W", "26W Change (%)", True),
    ("Price Momentum - 52W", "52W Change (%)", True),
    ("Dividend Yield", "Dividend Yield (%)", True),
]


def build_rankings(df, metric, higher_is_better=True, n=10):
    """Build disjoint Top 10 and Bottom 10 lists from finite values."""
    required_columns = ["Company", "SGX Code", metric]
    if not all(col in df.columns for col in required_columns):
        return pd.DataFrame(), pd.DataFrame(), 0
    valid = df[required_columns].copy()
    valid[metric] = pd.to_numeric(valid[metric], errors="coerce")
    valid[metric] = valid[metric].replace([np.inf, -np.inf], np.nan)
    valid = valid.dropna(subset=[metric])
    valid = valid.sort_values(
        by=[metric, "Company", "SGX Code"],
        ascending=[not higher_is_better, True, True],
        kind="stable",
    ).reset_index(drop=True)
    count = len(valid)
    if count == 0:
        return pd.DataFrame(), pd.DataFrame(), 0
    top_n = min(n, (count + 1) // 2)
    bottom_n = min(n, count - top_n)
    top = valid.head(top_n).copy()
    bottom = valid.tail(bottom_n).iloc[::-1].copy()
    top.insert(0, "Rank", range(1, len(top) + 1))
    bottom.insert(0, "Rank", range(1, len(bottom) + 1))
    return top, bottom, count


def show_sector_rankings(sector_df, key_prefix):
    """Display eight ranking sections within each sector tab."""
    st.markdown("### Sector Rankings")
    st.caption(
        "Top 10 and Bottom 10 stocks by individual financial metrics. "
        "Rankings are based on available SGX screener data."
    )
    for title, metric, higher_is_better in RANKING_METRICS:
        st.markdown(f"#### {title}")
        top, bottom, eligible_count = build_rankings(
            sector_df, metric, higher_is_better
        )
        if eligible_count == 0:
            st.info(f"No valid {metric} values are available.")
            st.divider()
            continue
        left, right = st.columns(2)
        number_format = "%.2fx" if metric == "P/E" else "%.2f%%"
        column_config = {
            "Rank": st.column_config.NumberColumn("Rank", format="%d"),
            metric: st.column_config.NumberColumn(metric, format=number_format),
        }
        with left:
            st.markdown("**Top 10**")
            st.dataframe(
                style_stock_table(top), hide_index=True,
                use_container_width=True, column_config=column_config, height=390,
            )
        with right:
            st.markdown("**Bottom 10**")
            st.dataframe(
                style_stock_table(bottom), hide_index=True,
                use_container_width=True, column_config=column_config, height=390,
            )
        if eligible_count < 20:
            st.caption(
                f"Only {eligible_count} eligible stocks. "
                "Lists are shortened to avoid duplicates."
            )
        if metric == "P/E":
            st.caption(
                "Negative P/E values are included when they fall within the "
                "lowest-ranked companies. Negative P/E generally indicates "
                "negative earnings and is not a meaningful valuation multiple."
            )
        st.divider()
