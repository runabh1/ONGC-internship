from __future__ import annotations

import os
import sys
from pathlib import Path
import pandas as pd
import streamlit as st
from typing import Any, Dict, List

# Ensure the repository root is on PYTHONPATH when Streamlit runs from a non-root cwd.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import the verifier from tests so we reuse the same logic
import tests.verify_cluster
from tests.verify_cluster import load_nodes, run_all_checks

# Force refresh - v3


def render_status_badge(ok: bool) -> str:
    return '✅' if ok else '❌'


def main() -> None:
    st.set_page_config(page_title='Infrastructure Checker', layout='wide')
    st.title('Infrastructure Checker (no ML)')
    st.markdown('This page performs read-only checks of SSH, Node Exporter, and Prometheus status.')

    nodes = load_nodes()
    st.write('Nodes to check:')
    st.write(nodes)

    # SSH credentials section
    st.markdown('### SSH Credentials (Optional)')
    st.markdown('_Leave blank to skip SSH authentication checks (only test port connectivity)._')
    
    col1, col2 = st.columns(2)
    with col1:
        username = st.text_input('SSH Username', value='root', key='ssh_username')
    with col2:
        password = st.text_input('SSH Password', type='password', key='ssh_password', help='Password for SSH authentication')
    
    if st.button('Run Checks'):
        with st.spinner('Running checks...'):
            # Pass password only if provided
            results = run_all_checks(
                password=password if password else None,
                username=username
            )

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
