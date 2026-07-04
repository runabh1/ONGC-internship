from __future__ import annotations

import os

import pandas as pd
import streamlit as st
from requests.exceptions import RequestException

from ml import PrometheusClient

PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')


def normalize_instance(instance: str) -> str:
    if ':' in instance and instance.count(':') == 1:
        host, port = instance.split(':', 1)
        if port.isdigit():
            return host
    return instance


def load_metrics(instance: str, metric: str, hours: int, prometheus_url: str) -> pd.DataFrame:
    client = PrometheusClient(prometheus_url)
    normalized_instance = normalize_instance(instance)
    query = f'{metric}{{instance="{normalized_instance}"}}'
    now = pd.Timestamp.now(tz='UTC')
    start = now - pd.Timedelta(hours=hours)
    return client.query_range(query=query, start=start.isoformat(), end=now.isoformat(), step='30s')


def main() -> None:
    st.set_page_config(page_title='Tables', layout='wide')
    st.title('Tables')
    st.sidebar.header('Table Settings')

    prometheus_url = st.sidebar.text_input('Prometheus URL', PROMETHEUS_URL)
    instance = st.sidebar.text_input('Node instance', '192.168.56.101:9100')
    metric_label = st.sidebar.selectbox('Metric', ['CPU utilization (%)', 'Memory usage (%)', 'Disk usage (%)'])
    hours = st.sidebar.slider('Lookback hours', 1, 24, 3)

    # build combined table: CPU %, Memory %, Disk % for the instance
    normalized_instance = normalize_instance(instance)
    CLIENT = PrometheusClient(prometheus_url)
    now = pd.Timestamp.now(tz='UTC')
    start = now - pd.Timedelta(hours=hours)
    queries = {
        'CPU': '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle", instance="%s"}[5m])) * 100)' % normalized_instance,
        'Memory': '100 * (1 - (node_memory_MemAvailable_bytes{instance="%s"} / node_memory_MemTotal_bytes{instance="%s"}))' % (normalized_instance, normalized_instance),
        'Disk': '100 * (1 - (node_filesystem_avail_bytes{mountpoint="/", instance="%s"} / node_filesystem_size_bytes{mountpoint="/", instance="%s"}))' % (normalized_instance, normalized_instance),
    }

    try:
        df_cpu = CLIENT.query_range(query=queries['CPU'], start=start.isoformat(), end=now.isoformat(), step='30s')
        df_mem = CLIENT.query_range(query=queries['Memory'], start=start.isoformat(), end=now.isoformat(), step='30s')
        df_disk = CLIENT.query_range(query=queries['Disk'], start=start.isoformat(), end=now.isoformat(), step='30s')
    except RequestException as exc:
        st.error(f'Cannot connect to Prometheus at {prometheus_url}: {exc}')
        return
    except Exception as exc:
        st.error(str(exc))
        return

    if df_cpu.empty and df_mem.empty and df_disk.empty:
        st.warning('No metrics returned for this instance. Verify the instance label.')
        return

    # merge by timestamp using nearest join
    def prep(d: pd.DataFrame, name: str) -> pd.DataFrame:
        if d.empty:
            return pd.DataFrame()
        d['timestamp'] = pd.to_datetime(d['timestamp'], unit='s', utc=True)
        d = d.sort_values('timestamp')
        return d[['timestamp', 'value']].rename(columns={'value': name})

    a = prep(df_cpu, 'cpu')
    b = prep(df_mem, 'memory')
    c = prep(df_disk, 'disk')

    # merge asof on timestamp
    merged = a
    if not merged.empty and not b.empty:
        merged = pd.merge_asof(merged, b, on='timestamp')
    if not merged.empty and not c.empty:
        merged = pd.merge_asof(merged, c, on='timestamp')

    # simplify for operators
    merged['node'] = instance
    merged = merged.rename(columns={'timestamp': 'time', 'cpu': 'cpu_percent', 'memory': 'memory_percent', 'disk': 'disk_percent'})
    st.subheader('Operator-friendly table')
    st.dataframe(merged.tail(200).reset_index(drop=True))


main()
