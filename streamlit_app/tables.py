from __future__ import annotations

from typing import List
import pandas as pd
import streamlit as st


def render_anomalies_table(results: List[dict]) -> None:
    if not results:
        st.info('No anomalies detected')
        return
    df = pd.DataFrame(results)
    st.dataframe(df)
