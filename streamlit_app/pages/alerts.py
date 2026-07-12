from __future__ import annotations

import datetime
import os
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from requests.exceptions import RequestException

from ml import (
    PrometheusClient,
    explain_anomalies,
    IsolationForestDetector,
    RollingMeanDetector,
    EWMAAnomalyDetector,
    ZScoreDetector,
    classify_severity,
)
from ml.feature_engineering import add_rolling_features


PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')
IST_TZ = ZoneInfo('Asia/Kolkata')


def format_ist_time(value: Any) -> str:
    if value is None:
        return '-'
    if hasattr(value, 'to_pydatetime'):
        value = value.to_pydatetime()
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone(IST_TZ).strftime('%Y-%m-%d %H:%M IST')
    return str(value)


def build_alerts(prometheus_url: str, hours: int = 1) -> list[dict[str, Any]]:
    client = PrometheusClient(prometheus_url)
    # use CPU utilization promql
    promql = '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    try:
        df = client.query_range(query=promql, start=(pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=hours)).isoformat(), end=pd.Timestamp.now(tz='UTC').isoformat(), step='30s')
    except RequestException:
        return []

    if df.empty:
        return []

    df = add_rolling_features(df)
    alerts: list[dict[str, Any]] = []
    detectors = [RollingMeanDetector(metric='cpu'), EWMAAnomalyDetector(metric='cpu'), ZScoreDetector(metric='cpu'), IsolationForestDetector(metric='cpu')]
    total_models = len(detectors)
    for inst in sorted(df['instance'].unique()):
        sub = df[df['instance'] == inst]
        for d in detectors:
            try:
                if hasattr(d, 'fit'):
                    d.fit(sub)
                preds = d.predict(sub)
                for p in preds:
                    if p.is_anomaly:
                        ts = pd.to_datetime(p.timestamp, utc=True)
                        alerts.append({
                            'instance': inst,
                            'model': type(d).__name__.replace('Detector', ''),
                            'score': float(p.score),
                            'timestamp': ts,
                            'local_timestamp': ts.astimezone(IST_TZ),
                            'details': p.details or {},
                            'value': p.details.get('value') if isinstance(p.details, dict) and 'value' in p.details else None,
                        })
            except Exception:
                continue

    if not alerts:
        return []

    # Aggregate alerts that refer to the same incident (same instance + consecutive timestamps)
    df_alerts = pd.DataFrame(alerts)
    df_alerts = df_alerts.sort_values(['instance', 'timestamp']).reset_index(drop=True)
    incidents: list[dict[str, Any]] = []
    current = None

    for _, row in df_alerts.iterrows():
        if current is None:
            current = {
                'instance': row['instance'],
                'start': row['timestamp'],
                'end': row['timestamp'],
                'models': {row['model']},
                'scores': [row['score']],
                'details': [row['details']],
                'values': [row['value']] if row.get('value') is not None else [],
            }
            continue

        if row['instance'] != current['instance'] or row['timestamp'] - current['end'] > pd.Timedelta(minutes=1.5):
            incidents.append(current)
            current = {
                'instance': row['instance'],
                'start': row['timestamp'],
                'end': row['timestamp'],
                'models': {row['model']},
                'scores': [row['score']],
                'details': [row['details']],
                'values': [row['value']] if row.get('value') is not None else [],
            }
        else:
            current['end'] = row['timestamp']
            current['models'].add(row['model'])
            current['scores'].append(row['score'])
            current['details'].append(row['details'])
            if row.get('value') is not None:
                current['values'].append(row['value'])

    if current is not None:
        incidents.append(current)

    consolidated: list[dict[str, Any]] = []
    for incident in incidents:
        models = sorted(incident['models'])
        votes = len(models)
        confidence = votes / total_models
        avg_score = float(sum(incident['scores']) / len(incident['scores'])) if incident['scores'] else 0.0
        peak_value = max(incident['values']) if incident['values'] else None
        sev = classify_severity(confidence, peak_value, float((incident['end'] - incident['start']).total_seconds()), 1)

        consolidated.append({
            'instance': incident['instance'],
            'start': incident['start'],
            'end': incident['end'],
            'duration': incident['end'] - incident['start'],
            'models': models,
            'votes': votes,
            'total_models': total_models,
            'confidence': confidence,
            'severity': sev,
            'avg_score': avg_score,
            'peak_value': max(incident['values']) if incident['values'] else None,
            'details': incident['details'],
        })

    return consolidated


def main() -> None:
    st.set_page_config(page_title='Alerts', layout='wide')
    st.title('Alerts')

    prometheus_url = st.sidebar.text_input('Prometheus URL', PROMETHEUS_URL)
    lookback = st.sidebar.selectbox('Last', ['1 hour', '3 hours', '6 hours', '12 hours', '24 hours'], index=0)
    hours = int(lookback.split()[0])
    min_conf_models = st.sidebar.slider('Minimum agreeing models', 1, 4, 2)
    detector_filter = st.sidebar.selectbox('Detector', ['All', 'RollingMean', 'EWMA', 'ZScore', 'IsolationForest'])
    severity_filter = st.sidebar.selectbox('Severity', ['All', 'Critical', 'High', 'Medium'], index=0)
    node_filter = st.sidebar.text_input('Node (instance)', 'All')

    with st.spinner('Scanning for anomalies...'):
        consolidated = build_alerts(prometheus_url, hours=hours)

    if not consolidated:
        st.success('No alerts detected')
        return

    dfc = pd.DataFrame(consolidated)

    # Apply filters
    if detector_filter != 'All':
        dfc = dfc[dfc['models'].apply(lambda ms: any(detector_filter in m for m in ms))]
    if severity_filter != 'All':
        dfc = dfc[dfc['severity'] == severity_filter]
    if node_filter and node_filter != 'All':
        dfc = dfc[dfc['instance'].str.contains(node_filter)]
    if min_conf_models > 1:
        dfc = dfc[dfc['votes'] >= min_conf_models]

    if dfc.empty:
        st.info('No alerts match the current filters')
        return

    # Sort by severity and time
    severity_rank = {'Critical': 0, 'High': 1, 'Medium': 2}
    dfc['rank'] = dfc['severity'].map(severity_rank)
    dfc = dfc.sort_values(['rank', 'end'], ascending=[True, False]).reset_index(drop=True)

    # Display consolidated incidents
    emoji = {'Critical': '🔴', 'High': '🟠', 'Medium': '🟡'}

    # Top summary card
    counts = dfc['severity'].value_counts().to_dict()
    critical = counts.get('Critical', 0)
    high = counts.get('High', 0)
    medium = counts.get('Medium', 0)
    low = counts.get('Low', 0)
    affected_nodes = dfc['instance'].nunique()
    c1, c2, c3, c4, c5 = st.columns([1,1,1,1,2])
    c1.metric('Critical', critical, delta='')
    c2.metric('High', high, delta='')
    c3.metric('Medium', medium, delta='')
    c4.metric('Low', low, delta='')
    c5.markdown(f"**Affected nodes:** {affected_nodes}")
    for _, row in dfc.iterrows():
        sev = row['severity']
        if sev == 'Low':
            continue
        start_local = row['start'].astimezone(IST_TZ)
        end_local = row['end'].astimezone(IST_TZ)
        title = f"{emoji.get(sev, '')} {sev} — Node: {row['instance']} — {start_local.strftime('%Y-%m-%d %H:%M IST')} to {end_local.strftime('%H:%M IST')}"
        st.markdown(f"**{title}**")
        if row.get('peak_value') is not None:
            try:
                st.write(f"Peak CPU: {float(row['peak_value']):.2f}%")
            except Exception:
                st.write(f"Peak CPU: {row['peak_value']}")
        st.write(f"Detected by: {', '.join(row['models'])}")
        st.write(f"Confidence: {int(row['votes'])}/{int(row['total_models'])} models agree")
        st.write(f"Duration: {str(row['duration'])}")
        st.write('Recommendation: Investigate high CPU usage, check top CPU processes, review recent deployments.')
        # Show combined details from detectors
        with st.expander('Detector details'):
            for det in row['details']:
                st.json(det)
        st.markdown('---')


if __name__ == '__main__':
    main()
