from __future__ import annotations

import os
import pandas as pd
import streamlit as st
from typing import Any, Dict, List

# Import the verifier from tests so we reuse the same logic
from tests.verify_cluster import load_nodes, run_all_checks


def render_status_badge(ok: bool) -> str:
    return '✅' if ok else '❌'


def main() -> None:
    st.set_page_config(page_title='Infrastructure Checker', layout='wide')
    st.title('Infrastructure Checker (no ML)')
    st.markdown('This page performs read-only checks of SSH, Node Exporter, and Prometheus status.')

    nodes = load_nodes()
    st.write('Nodes to check:')
    st.write(nodes)

    if st.button('Run Checks'):
        with st.spinner('Running checks...'):
            results = run_all_checks()

        # Build a dataframe for nicer display
        rows = []
        for r in results:
            overall = bool(r.exporter and r.prometheus and r.ssh)
            rows.append({
                'node': r.name,
                'exporter': render_status_badge(r.exporter),
                'prometheus': render_status_badge(r.prometheus),
                'ssh': render_status_badge(r.ssh),
                'overall': render_status_badge(overall),
            })

        df = pd.DataFrame(rows)
        st.table(df)


main()
