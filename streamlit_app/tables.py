from __future__ import annotations

from typing import List
import pandas as pd
import streamlit as st
from dateutil import tz


def render_anomalies_table(results: List[dict]) -> None:
    if not results:
        st.info('No anomalies detected')
        return
    df = pd.DataFrame(results)
    # format timestamps (if present) to local timezone for display
    for col in ('timestamp', 'time'):
        if col in df.columns:
            try:
                local_tz = tz.tzlocal()
                df[col] = pd.to_datetime(df[col], utc=True).dt.tz_convert(local_tz).dt.strftime('%Y-%m-%d %H:%M:%S %Z')
            except Exception:
                df[col] = pd.to_datetime(df[col], utc=True).dt.strftime('%Y-%m-%d %H:%M:%S %Z')
            break
    st.dataframe(df)
