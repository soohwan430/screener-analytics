"""Shared Excel-style conditional formatting for SGX stock tables."""
import numpy as np
import pandas as pd

POSITIVE_COLUMNS = {
    "ROE (%)", "Revenue Growth (%)", "Net Profit Margin (%)",
    "4W Change (%)", "13W Change (%)", "26W Change (%)", "52W Change (%)",
}
GREEN_BG = "#DCFCE7"
GREEN_TEXT = "#166534"
RED_BG = "#FEE2E2"
RED_TEXT = "#991B1B"


def colour_cell(value, column):
    """Return CSS for a financial metric cell."""
    if pd.isna(value):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(number):
        return ""
    if column == "P/E":
        if number < 0:
            return f"background-color: {RED_BG}; color: {RED_TEXT};"
        return ""
    if column in POSITIVE_COLUMNS:
        if number > 0:
            return f"background-color: {GREEN_BG}; color: {GREEN_TEXT};"
        if number < 0:
            return f"background-color: {RED_BG}; color: {RED_TEXT};"
    return ""


def style_stock_table(df):
    """Apply conditional formatting without changing the DataFrame."""
    styled = df.style
    for column in df.columns:
        if column in POSITIVE_COLUMNS or column == "P/E":
            styled = styled.map(
                lambda value, col=column: colour_cell(value, col),
                subset=[column],
            )
    return styled
