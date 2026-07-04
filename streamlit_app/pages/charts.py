from __future__ import annotations

import os

import pandas as pd
import plotly.express as px
import streamlit as st
from requests.exceptions import RequestException

from ml import PrometheusClient

PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')


METRIC_QUERIES = {
    'CPU utilization (%)': '100 - (avg by(instance) (rate(node_cpu_seconds_total{{mode="idle", instance="{instance}"}}[5m])) * 100)',
    'Memory usage (%)': '100 * (1 - (node_memory_MemAvailable_bytes{{instance="{instance}"}} / node_memory_MemTotal_bytes{{instance="{instance}"}}))',
    'Disk usage (%)': '100 * (1 - (node_filesystem_avail_bytes{{mountpoint="/", instance="{instance}"}} / node_filesystem_size_bytes{{mountpoint="/", instance="{instance}"}}))',
    'Network RX (bytes/s)': 'sum by(instance) (rate(node_network_receive_bytes_total{{instance="{instance}", device!="lo"}}[5m]))',
    'Network TX (bytes/s)': 'sum by(instance) (rate(node_network_transmit_bytes_total{{instance="{instance}", device!="lo"}}[5m]))',
}


def normalize_instance(instance: str) -> str:
    if ':' in instance and instance.count(':') == 1:
        host, port = instance.split(':', 1)
        if port.isdigit():
            return host
    return instance


def load_metrics_for(instance: str, query_template: str, hours: int, prometheus_url: str) -> pd.DataFrame:
    client = PrometheusClient(prometheus_url)
    normalized_instance = normalize_instance(instance)
    query = query_template.format(instance=normalized_instance)
    now = pd.Timestamp.now(tz='UTC')
    start = now - pd.Timedelta(hours=hours)
    df = client.query_range(query=query, start=start.isoformat(), end=now.isoformat(), step='30s')
    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    return df


def main() -> None:
    st.set_page_config(page_title='Charts', layout='wide')
    st.title('Charts')
    st.sidebar.header('Chart Settings')

    prometheus_url = st.sidebar.text_input('Prometheus URL', PROMETHEUS_URL)
    instance = st.sidebar.text_input('Node instance', '192.168.56.101:9100')
    metric_label = st.sidebar.selectbox('Metric', list(METRIC_QUERIES.keys()))
    hours = st.sidebar.slider('Lookback hours', 1, 24, 3)

    try:
        df = load_metrics_for(instance, METRIC_QUERIES[metric_label], hours, prometheus_url)
    except RequestException as exc:
        st.error(f'Cannot connect to Prometheus at {prometheus_url}: {exc}')
        return
    except Exception as exc:
        st.error(str(exc))
        return

    if df.empty:
        st.warning('No data returned for this query. Verify the instance and metric.')
        return

    fig = px.line(df, x='timestamp', y='value', color='instance', title=f'{metric_label} over time')
    st.plotly_chart(fig, use_container_width=True)


main()
