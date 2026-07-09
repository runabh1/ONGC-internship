from __future__ import annotations

import os
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from requests.exceptions import RequestException

from ml import (
    PrometheusClient,
    explain_anomalies,
    IsolationForestDetector,
    RollingMeanDetector,
    EWMAAnomalyDetector,
    ZScoreDetector,
    detect_warmup_period,
    filter_startup_samples,
    Incident,
    IncidentManager,
    calculate_recovery_percentage,
)
from ml.feature_engineering import add_rolling_features

PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')
METRIC_OPTIONS: dict[str, str] = {
    'CPU utilization (%)': '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
    'Load 1m': 'node_load1',
    'Memory available (bytes)': 'node_memory_MemAvailable_bytes',
    'CPU total counter (debug)': 'node_cpu_seconds_total',
}

# ============================================================================
# METRIC CONFIGURATION SYSTEM - Defines metadata for each metric
# ============================================================================

METRIC_CONFIG: dict[str, dict[str, Any]] = {
    'CPU utilization (%)': {
        'display_name': 'CPU Utilization',
        'unit': '%',
        'unit_display': '%',
        'card_label_template': '{base} CPU',  # e.g., "Average CPU", "Current CPU"
        'short_label': 'CPU',
        'format_func': lambda v: f"{v:.1f}%",
        'thresholds': {
            'healthy': (0, 50),
            'medium': (50, 70),
            'high': (70, 90),
            'critical': (90, float('inf')),
        },
    },
    'Memory available (bytes)': {
        'display_name': 'Memory Available',
        'unit': 'bytes',
        'unit_display': 'GB',
        'card_label_template': '{base} Memory Available',  # e.g., "Average Memory Available"
        'short_label': 'Memory',
        'format_func': lambda v: format_bytes(v),
        'thresholds': {
            'healthy': (30, float('inf')),  # > 30% available
            'warning': (15, 30),
            'critical': (0, 15),
        },
    },
    'Load 1m': {
        'display_name': 'Load Average (1m)',
        'unit': 'load',
        'unit_display': 'load',
        'card_label_template': '{base} Load',  # e.g., "Average Load", "Current Load"
        'short_label': 'Load',
        'format_func': lambda v: f"{v:.2f}",
        'thresholds': {
            'healthy': (0, 2),
            'medium': (2, 4),
            'high': (4, 8),
            'critical': (8, float('inf')),
        },
    },
    'CPU total counter (debug)': {
        'display_name': 'CPU Counter (Debug)',
        'unit': 'seconds',
        'unit_display': 's',
        'card_label_template': '{base} CPU Counter',
        'short_label': 'CPU Counter',
        'format_func': lambda v: f"{v:.0f}s",
        'thresholds': {
            'healthy': (0, 100000),
            'medium': (100000, 500000),
            'high': (500000, 1000000),
            'critical': (1000000, float('inf')),
        },
    },
}


def format_bytes(value: float) -> str:
    """Convert bytes to human-readable format (B, KB, MB, GB, TB)."""
    if value < 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(value)
    unit_idx = 0
    
    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1
    
    if unit_idx == 0:  # Bytes - show as integer
        return f"{int(size)} {units[unit_idx]}"
    else:
        return f"{size:.2f} {units[unit_idx]}"


def get_metric_config(metric_name: str) -> dict[str, Any]:
    """Get configuration for a metric. Defaults to generic config if not found."""
    if metric_name in METRIC_CONFIG:
        return METRIC_CONFIG[metric_name]
    
    # Fallback generic config
    return {
        'display_name': metric_name,
        'unit': '',
        'unit_display': '',
        'card_label_template': '{base} {metric}',
        'short_label': metric_name,
        'format_func': lambda v: f"{v:.2f}",
        'thresholds': {
            'healthy': (0, 50),
            'medium': (50, 70),
            'high': (70, 90),
            'critical': (90, float('inf')),
        },
    }


def format_metric_value(value: float, metric_name: str) -> str:
    """Format a metric value according to metric-specific rules."""
    config = get_metric_config(metric_name)
    format_func = config.get('format_func')
    if format_func:
        try:
            return format_func(value)
        except (TypeError, ValueError):
            pass
    return f"{value:.2f}"


def get_metric_label(base_label: str, metric_name: str) -> str:
    """Generate dynamic metric label (e.g., 'Average CPU' or 'Average Memory Available')."""
    config = get_metric_config(metric_name)
    template = config.get('card_label_template', '{base}')
    return template.format(base=base_label, metric=config.get('short_label', ''))


def classify_health(value: float, metric_name: str) -> tuple[str, str]:
    """
    Classify health status based on value and metric-specific thresholds.
    
    Returns: (status, emoji) tuple
    """
    config = get_metric_config(metric_name)
    thresholds = config.get('thresholds', {})
    
    # For memory available, use percentage-based logic if value looks like bytes
    if 'Memory' in metric_name and value > 100:
        # Assume it's bytes, convert to percentage (rough estimate assuming ~4GB total)
        # For production, this should come from actual system memory
        pct_available = min((value / (4 * 1024 * 1024 * 1024)) * 100, 100)
        value = pct_available
    
    # Check thresholds in order
    for status in ['critical', 'high', 'medium', 'warning', 'healthy']:
        if status in thresholds:
            min_val, max_val = thresholds[status]
            if min_val <= value < max_val:
                emoji_map = {
                    'healthy': '🟢',
                    'medium': '🟡',
                    'warning': '🟡',
                    'high': '🟠',
                    'critical': '🔴',
                }
                return status.capitalize(), emoji_map.get(status, '🟡')
    
    return 'Unknown', '⚪'


def current_value_status(current_value: float, metric_name: str) -> str:
    """
    Classify current status based on value and metric-specific thresholds.
    
    Returns status as string: 'Normal', 'Low', 'Medium', 'High', 'Critical'
    """
    status, _ = classify_health(current_value, metric_name)
    
    # Map friendly names to status names used elsewhere
    status_map = {
        'Healthy': 'Normal',
        'Warning': 'Low',
        'Medium': 'Medium',
        'High': 'High',
        'Critical': 'Critical',
    }
    return status_map.get(status, 'Normal')


SMTP_SERVER = os.getenv('EMAIL_SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('EMAIL_SMTP_PORT', '587'))
SMTP_USER = os.getenv('EMAIL_USER')
SMTP_PASSWORD = os.getenv('EMAIL_PASSWORD')
ALERT_EMAIL_TO = [addr.strip() for addr in os.getenv('ALERT_EMAIL_TO', '').split(',') if addr.strip()]
ALERT_EMAIL_FROM = os.getenv('ALERT_EMAIL_FROM', SMTP_USER)


def get_status_badge_html(status: str) -> str:
    """Generate HTML badge for status."""
    status_lower = status.lower()
    badge_class = f'badge badge-{status_lower}' if status_lower in ['healthy', 'warning', 'high', 'critical'] else 'badge badge-warning'
    return f'<span class="{badge_class}">{status}</span>'

# Enhanced UI styles for production-grade monitoring dashboard
_STYLES = """
<style>
    .card {
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 12px;
        background: #f7f9fb;
        border-left: 4px solid #2196F3;
    }
    .metric-title {
        font-weight: 600;
        font-size: 0.95em;
        color: #666;
        margin-bottom: 8px;
    }
    .metric-value {
        font-size: 1.8em;
        font-weight: bold;
        color: #222;
        margin: 4px 0;
    }
    .health-healthy {
        color: #4CAF50;
        font-weight: bold;
    }
    .health-warning {
        color: #FF9800;
        font-weight: bold;
    }
    .health-high {
        color: #FF5722;
        font-weight: bold;
    }
    .health-critical {
        color: #F44336;
        font-weight: bold;
    }
    .badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 0.85em;
        font-weight: 600;
        margin-right: 4px;
        margin-bottom: 4px;
    }
    .badge-healthy {
        background: #E8F5E9;
        color: #2E7D32;
    }
    .badge-warning {
        background: #FFF3E0;
        color: #E65100;
    }
    .badge-high {
        background: #FFEBEE;
        color: #C62828;
    }
    .badge-critical {
        background: #FFCDD2;
        color: #B71C1C;
    }
    .incident-card {
        border-left: 4px solid #F44336;
        padding: 12px;
        margin-bottom: 12px;
        background: #FFEBEE;
        border-radius: 4px;
    }
    .incident-recovered {
        border-left: 4px solid #4CAF50;
        background: #E8F5E9;
    }
    .detector-check {
        margin: 4px 0;
        font-size: 0.95em;
    }
    .detector-yes {
        color: #4CAF50;
    }
    .detector-no {
        color: #999;
    }
</style>
"""


def get_prometheus_instances(prometheus_url: str) -> list[str]:
    """
    Get list of monitored node instances only (excluding Prometheus/AlertManager).
    
    Filters by Prometheus 'job' label to return only node_exporter instances.
    Excludes monitoring infrastructure services (Prometheus, AlertManager, etc).
    """
    client = PrometheusClient(prometheus_url)
    try:
        df = client.query('up')
    except RequestException:
        return []

    if df.empty:
        return []

    # Primary filter: check job label for node_exporter
    node_instances = []
    if 'labels' in df.columns:
        for idx, row in df.iterrows():
            labels = row.get('labels', {})
            if isinstance(labels, dict):
                job = labels.get('job', '')
                instance = row.get('instance', '')
                
                # Keep only node_exporter jobs
                if job == 'node_exporter' and instance:
                    node_instances.append(instance)
    
    # If job label filtering worked, return those results
    if node_instances:
        return sorted(node_instances)
    
    # Fallback: filter by instance pattern (exclude Prometheus/AlertManager)
    instances = df['instance'].dropna().unique().tolist()
    filtered = []
    for inst in instances:
        inst_str = str(inst).lower()
        # Exclude Prometheus and AlertManager
        if 'localhost:9090' in inst_str or '127.0.0.1:9090' in inst_str or ':9093' in inst_str:
            continue
        if inst_str in ['prometheus:9090', 'alertmanager:9093']:
            continue
        # Include everything else
        filtered.append(inst)
    
    if filtered:
        return sorted(filtered)
    
    # Last resort: return all instances
    return sorted(instances)


def load_metrics(metric_query: str, prometheus_url: str, hours: int) -> pd.DataFrame:
    client = PrometheusClient(prometheus_url)
    now = pd.Timestamp.now(tz='UTC')
    start = now - pd.Timedelta(hours=hours)
    df = client.query_range(query=metric_query, start=start.isoformat(), end=now.isoformat(), step='30s')
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    return df


def filter_instances(df: pd.DataFrame, instance: str) -> pd.DataFrame:
    if instance == 'All nodes':
        return df
    return df[df['instance'] == instance].reset_index(drop=True)


def email_alerts_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD and ALERT_EMAIL_TO and ALERT_EMAIL_FROM)


def init_alert_session_state() -> None:
    st.session_state.setdefault('email_alert_history', {})
    st.session_state.setdefault('email_alert_errors', [])


def should_send_email_alert(instance: str, severity: str) -> bool:
    key = f'{instance}|{severity}'
    history = st.session_state['email_alert_history']
    last_sent = history.get(key)
    if last_sent is None:
        return True
    return datetime.now(timezone.utc) - last_sent >= timedelta(minutes=30)


def record_email_alert_sent(instance: str, severity: str) -> None:
    key = f'{instance}|{severity}'
    st.session_state['email_alert_history'][key] = datetime.now(timezone.utc)


def clear_email_alert_history(instance: str) -> None:
    history = st.session_state['email_alert_history']
    for key in list(history.keys()):
        if key.startswith(f'{instance}|'):
            history.pop(key, None)


def send_alert_email(
    instance: str,
    severity: str,
    peak_value: float | None,
    current_value: float | None,
    skip_due_to_warmup: bool = False,
) -> Tuple[bool, str | None]:
    """
    Send an email alert for anomalies.
    
    Alerts are suppressed during startup warmup period to prevent false positives
    caused by unstable Prometheus metrics during initialization.
    
    Args:
        instance: Node instance identifier
        severity: Severity level (Normal, Low, Medium, High, Critical)
        peak_value: Peak metric value observed in lookback window
        current_value: Latest metric value
        skip_due_to_warmup: If True, suppress alert with warmup reason
    
    Returns:
        Tuple of (success: bool, error_message: str | None)
    """
    if skip_due_to_warmup:
        return False, 'Alert suppressed during startup warmup period. System is collecting baseline metrics.'
    
    if not email_alerts_configured():
        return False, 'Email alerting is not configured.'

    recipients = ALERT_EMAIL_TO
    body = [
        f'ONGC AI Monitoring Alert for {instance}',
        f'Severity: {severity}',
        f'Peak CPU: {peak_value:.2f}%' if peak_value is not None else 'Peak CPU: unknown',
        f'Current CPU: {current_value:.2f}%' if current_value is not None else 'Current CPU: unknown',
        f'Prometheus: {PROMETHEUS_URL}',
        '',
        'This alert was generated by the streamlit monitoring dashboard.',
    ]
    message = EmailMessage()
    message['From'] = ALERT_EMAIL_FROM
    message['To'] = ', '.join(recipients)
    message['Subject'] = f'[ONGC AI Monitoring] {instance} Critical Severity'
    message.set_content('\n'.join(body))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            # SMTP_USER and SMTP_PASSWORD are guaranteed to be non-None by email_alerts_configured() check above
            smtp.login(str(SMTP_USER), str(SMTP_PASSWORD))
            smtp.send_message(message)
        return True, None
    except Exception as exc:
        return False, str(exc)


# ============================================================================
# PRODUCTION DASHBOARD HELPERS
# ============================================================================

def init_incident_manager_state() -> IncidentManager:
    """Initialize or retrieve incident manager from session state."""
    if 'incident_manager' not in st.session_state:
        st.session_state['incident_manager'] = IncidentManager(recovery_threshold_minutes=2)
    return st.session_state['incident_manager']


def render_cluster_health_dashboard(
    instances: list[str],
    latest_values: dict[str, float],
    incident_manager: IncidentManager,
    metric_name: str = 'CPU utilization (%)',
) -> None:
    """
    Render the main cluster health dashboard.
    
    Shows:
    - Cluster overall health status
    - Nodes online count
    - Current average metric value
    - Open incidents
    - Resolved incidents (24h)
    - Last incident time
    """
    st.markdown(_STYLES, unsafe_allow_html=True)
    
    # Calculate cluster metrics
    online_nodes = len(instances)
    total_nodes = len(instances)  # Simplified - in production, may have offline nodes
    
    avg_value = sum(latest_values.values()) / len(latest_values) if latest_values else 0.0
    health_status, health_emoji = classify_health(avg_value, metric_name)
    
    incident_summary = incident_manager.get_incident_summary()
    
    # Determine cluster-wide health
    if incident_summary['active_incidents'] > 0:
        cluster_status = '🔴 Critical' if avg_value >= 80 else '🟠 High'
    elif health_status in ['High', 'Critical']:
        cluster_status = f'{health_emoji} {health_status}'
    else:
        cluster_status = '🟢 Healthy'
    
    st.markdown('## Cluster Health Dashboard')
    
    # Main metrics row
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    
    config = get_metric_config(metric_name)
    avg_label = get_metric_label('Avg', metric_name)
    avg_formatted = format_metric_value(avg_value, metric_name)
    
    with col1:
        st.markdown(f"""
        <div class='card'>
            <div class='metric-title'>Overall Status</div>
            <div class='metric-value'>{cluster_status}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"""
        <div class='card'>
            <div class='metric-title'>Nodes Online</div>
            <div class='metric-value'>{online_nodes}/{total_nodes}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown(f"""
        <div class='card'>
            <div class='metric-title'>{avg_label}</div>
            <div class='metric-value'>{avg_formatted}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        st.markdown(f"""
        <div class='card'>
            <div class='metric-title'>Open Incidents</div>
            <div class='metric-value'>{incident_summary['active_incidents']}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col5:
        st.markdown(f"""
        <div class='card'>
            <div class='metric-title'>Resolved (24h)</div>
            <div class='metric-value'>{incident_summary['resolved_24h']}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col6:
        last_time = incident_summary['last_incident']
        if last_time:
            time_str = last_time.strftime('%H:%M UTC')
        else:
            time_str = '-'
        st.markdown(f"""
        <div class='card'>
            <div class='metric-title'>Last Incident</div>
            <div class='metric-value'>{time_str}</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown('---')


def render_current_health_section(
    latest_values: dict[str, float],
    instances: list[str],
    metric_name: str = 'CPU utilization (%)',
) -> None:
    """
    Render the Current Cluster Health section.
    
    Shows live metrics only (latest values, not historical analysis).
    Metric-aware labels and formatting.
    """
    st.markdown('### 📊 Current Cluster Health')
    st.markdown('_Based on latest Prometheus values_')
    
    if not latest_values:
        st.info('No current data available')
        return
    
    # Aggregate current metrics
    current_values = [latest_values.get(inst, 0) for inst in instances]
    avg_current = sum(current_values) / len(current_values) if current_values else 0
    max_current = max(current_values) if current_values else 0
    min_current = min(current_values) if current_values else 0
    
    config = get_metric_config(metric_name)
    
    col1, col2, col3, col4 = st.columns(4)
    
    health_status, emoji = classify_health(avg_current, metric_name)
    avg_label = get_metric_label('Average', metric_name)
    max_label = get_metric_label('Max', metric_name)
    min_label = get_metric_label('Min', metric_name)
    
    with col1:
        st.markdown(f"{emoji} **{health_status}**")
        st.metric(avg_label, format_metric_value(avg_current, metric_name))
    
    with col2:
        st.metric(max_label, format_metric_value(max_current, metric_name))
    
    with col3:
        st.metric(min_label, format_metric_value(min_current, metric_name))
    
    with col4:
        # For CPU and Load, show nodes below threshold. For Memory, show nodes with good availability
        if 'CPU' in metric_name or 'Load' in metric_name:
            healthy_nodes = sum(1 for v in current_values if v < 50)
            st.metric('Nodes <50%', f"{healthy_nodes}/{len(instances)}")
        else:
            healthy_nodes = sum(1 for v in current_values if v > 30)
            st.metric('Healthy Nodes', f"{healthy_nodes}/{len(instances)}")
    
    # Per-node current status
    st.markdown('**Per-Node Current Status**')
    node_data = []
    current_label = get_metric_label('Current', metric_name)
    for inst in sorted(instances):
        val = latest_values.get(inst, 0)
        status, emoji = classify_health(val, metric_name)
        node_data.append({
            'Node': inst,
            current_label: format_metric_value(val, metric_name),
            'Status': f"{emoji} {status}",
        })
    
    st.dataframe(pd.DataFrame(node_data), use_container_width=True, hide_index=True)
    st.markdown('---')


def render_historical_analysis_section(
    results: dict[str, Any],
    incident_manager: IncidentManager,
    anomalies_df: pd.DataFrame,
    metric_name: str = 'CPU utilization (%)',
) -> None:
    """
    Render Historical AI Analysis section.
    
    Shows incidents detected over the lookback window.
    Metric-aware incident card rendering.
    """
    st.markdown('### 📈 Historical AI Analysis')
    st.markdown('_Anomalies detected over selected lookback window_')
    
    incident_summary = incident_manager.get_incident_summary()
    
    if incident_summary['active_incidents'] == 0 and incident_summary['resolved_24h'] == 0:
        st.success('✅ No incidents detected in the selected timeframe')
        return
    
    # Show active incidents
    if incident_summary['active_incidents'] > 0:
        st.markdown(f"**🔴 {incident_summary['active_incidents']} Active Incident(s)**")
        for incident in incident_manager.active_incidents.values():
            render_incident_card(incident, incident_mgr=incident_manager, metric_name=metric_name)
    
    # Show recent recovered incidents
    if incident_summary['resolved_24h'] > 0:
        st.markdown(f"**🟢 {incident_summary['resolved_24h']} Resolved Incident(s) (Last 24h)**")
        recent_recovered = [
            inc for inc in incident_manager.recovered_incidents
            if inc.end_time and (datetime.now(timezone.utc) - inc.end_time) < timedelta(hours=24)
        ]
        for incident in recent_recovered[-3:]:  # Show last 3
            render_incident_card(incident, incident_mgr=incident_manager, metric_name=metric_name)
    
    st.markdown('---')


def render_incident_card(incident: Incident, incident_mgr: IncidentManager | None = None, metric_name: str = 'CPU utilization (%)') -> None:
    """Render a single incident card with metric-aware labels."""
    status_color = '#4CAF50' if incident.status == 'Recovered' else '#F44336'
    status_bg = '#E8F5E9' if incident.status == 'Recovered' else '#FFEBEE'
    
    start_str = incident.start_time.strftime('%H:%M UTC') if incident.start_time else '-'
    end_str = incident.end_time.strftime('%H:%M UTC') if incident.end_time else 'Ongoing'
    
    # Use provided incident manager or create temporary one for classification
    mgr = incident_mgr if incident_mgr else IncidentManager()
    severity = mgr.classify_severity(
        incident.confidence_score,
        incident.peak_value,
        incident.duration_seconds,
        len(incident.affected_nodes)
    )
    
    peak_label = get_metric_label('Peak', metric_name)
    peak_formatted = format_metric_value(incident.peak_value, metric_name)
    
    st.markdown(f"""
    <div class='incident-card' style='border-left-color: {status_color}; background: {status_bg}'>
        <div style='display: flex; justify-content: space-between;'>
            <div>
                <b>Incident {incident.incident_id}</b> — {incident.status}
            </div>
            <div>{get_status_badge_html(severity)}</div>
        </div>
        <div style='margin-top: 8px; font-size: 0.9em;'>
            <b>{peak_label}:</b> {peak_formatted} | 
            <b>Duration:</b> {incident.duration_str} | 
            <b>Affected Nodes:</b> {len(incident.affected_nodes)}
        </div>
        <div style='margin-top: 4px; font-size: 0.9em;'>
            <b>Time:</b> {start_str} → {end_str}
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_detector_consensus_improved(
    results: dict[str, Any],
    latest_values: dict[str, float],
    warmup_info: dict[str, Any] | None = None,
    metric_name: str = 'CPU utilization (%)',
) -> None:
    """
    Render improved consensus display with checkmarks for each model.
    Metric-aware labels and formatting.
    """
    st.markdown('### 🤖 ML Model Consensus')
    st.markdown('_Per-node anomaly detection results_')
    
    in_warmup: bool = bool(warmup_info and warmup_info.get('in_warmup', False)) if warmup_info else False
    
    if in_warmup:
        st.info("⏳ System in warmup period — alerts disabled")
    
    # Collect per-node results
    per_instance_data: dict[str, dict[str, Any]] = {}
    
    for model_name, info in results.items():
        if 'anomalies' not in info:
            continue
        anomalies = info.get('anomalies', [])
        for a in anomalies:
            inst = a.get('instance', 'unknown')
            if inst not in per_instance_data:
                per_instance_data[inst] = {
                    'detectors': set(),
                    'peak': 0,
                    'confidence': 0,
                    'values': [],
                }
            per_instance_data[inst]['detectors'].add(model_name)
            val = a.get('details', {}).get('value') if isinstance(a.get('details'), dict) else a.get('value')
            if isinstance(val, (int, float)):
                per_instance_data[inst]['values'].append(val)
                per_instance_data[inst]['peak'] = max(per_instance_data[inst]['peak'], val)
    
    if not per_instance_data:
        st.write('✅ No anomalies detected by any model')
        return
    
    # Render per-node consensus
    peak_label = get_metric_label('Peak', metric_name)
    current_label = get_metric_label('Current', metric_name)
    
    for inst, data in sorted(per_instance_data.items()):
        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.markdown(f"**{inst}**")
            
            # Detector checkmarks
            detector_list = ['Rolling Mean', 'EWMA', 'Robust Z Score', 'Isolation Forest']
            for detector in detector_list:
                check = '✔️' if detector in data['detectors'] else '  '
                color = 'color: #4CAF50' if detector in data['detectors'] else 'color: #ccc'
                st.markdown(
                    f'<div class="detector-check" style="{color}">{check} {detector}</div>',
                    unsafe_allow_html=True
                )
            
            votes = len(data['detectors'])
            confidence = votes / 4
            st.markdown(f"**Consensus:** {votes}/4 models — {int(confidence * 100)}%")
        
        with col2:
            st.metric('Peak CPU', f"{data['peak']:.1f}%")
            current = latest_values.get(inst, 0)
            recovery = calculate_recovery_percentage(current, data['peak'])
            st.metric('Recovered', f"{recovery:.0f}%")


def render_header() -> None:
    st.set_page_config(page_title='ONGC AI Monitoring', layout='wide')
    st.title('ONGC AI Monitoring')
    st.markdown('A machine intelligence layer that complements Grafana for anomaly detection.')


def render_sidebar(instances: list[str]) -> tuple[str, str, int, str, bool]:
    st.sidebar.header('AI Dashboard')
    prometheus_url = st.sidebar.text_input('Prometheus URL', PROMETHEUS_URL, key='prometheus_url')
    if instances:
        instance = st.sidebar.selectbox('Node instance', ['All nodes'] + instances, key='node_instance')
    else:
        st.sidebar.markdown('No instances discovered from Prometheus. Enter a valid instance label manually.')
        instance = st.sidebar.text_input('Node instance', '192.168.56.101:9100', key='node_instance_manual')

    metric = st.sidebar.selectbox('Metric', list(METRIC_OPTIONS.keys()), index=0, key='metric')
    hours = st.sidebar.slider('Lookback hours', 1, 24, 3, key='hours')
    auto_refresh = st.sidebar.checkbox('Auto-refresh every 30s', value=False, key='auto_refresh')
    return instance, metric, hours, prometheus_url, auto_refresh


def run_detectors(
    df: pd.DataFrame, metric_name: str, instance: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Run all anomaly detectors on the input data.
    
    Preprocessing pipeline:
    1. Fetch metrics from Prometheus
    2. Remove startup warm-up samples (handles unreliable rate() calculation)
    3. Clean dataframe (add rolling features)
    4. Run all detectors (unchanged)
    5. Aggregate results
    6. Generate dashboard score
    
    Args:
        df: DataFrame with metrics (timestamp, instance, value, etc.)
        metric_name: Name of the metric for detector identification
        instance: Instance filter ('All nodes' or specific instance name)
    
    Returns:
        Tuple of (results_dict, warmup_info_dict)
        - results_dict: Anomaly detection results per model
        - warmup_info_dict: Information about startup warmup period
    
    Why startup samples are filtered:
    - Prometheus rate() requires historical samples to calculate rates correctly
    - Startup metrics can spike artificially (cache warming, I/O initialization)
    - Dashboard filters these unreliable samples to prevent false positives
    - This follows production monitoring best practices
    """
    # Apply startup warmup filtering
    # This removes the first 5 minutes and/or first 10 samples to avoid
    # startup anomalies caused by rate() calculation and system initialization
    df_filtered, warmup_info = filter_startup_samples(df, warmup_minutes=5, sample_threshold=10)
    
    # Use filtered data for detectors, but preserve original data shape for later display
    df_for_detection = df_filtered
    
    # If analyzing all nodes, run detectors per-instance and aggregate
    if instance == 'All nodes' and 'instance' in df_for_detection.columns:
        unique_instances = df_for_detection['instance'].unique()
        all_results: dict[str, Any] = {}
        
        for inst in unique_instances:
            inst_df = df_for_detection[df_for_detection['instance'] == inst].reset_index(drop=True)
            if inst_df.empty:
                continue
            # Recursively call without 'All nodes' to process single instance
            inst_results, _ = run_detectors(inst_df, metric_name, instance=None)
            all_results[str(inst)] = inst_results
        
        return all_results, warmup_info
    
    detectors = {
        'Rolling Mean': RollingMeanDetector(metric=metric_name),
        'EWMA': EWMAAnomalyDetector(metric=metric_name),
        'Z Score': ZScoreDetector(metric=metric_name),
        'Isolation Forest': IsolationForestDetector(metric=metric_name),
    }
    results: dict[str, Any] = {}

    for name, detector in detectors.items():
        try:
            if hasattr(detector, 'fit') and name in ['Z Score', 'Isolation Forest']:
                detector.fit(df_for_detection)
            anomalies = detector.predict(df_for_detection)
            results[name] = {
                'summary': detector.explain(df_for_detection),
                'anomalies': explain_anomalies(anomalies),
                'score': detector.score(df_for_detection),
            }
        except Exception as exc:
            results[name] = {'error': str(exc)}

    return results, warmup_info


def summarize_recent_incidents(anomalies_df: pd.DataFrame) -> dict[str, Any]:
    if anomalies_df.empty:
        return {
            'incident_count': 0,
            'affected_nodes': 0,
            'last_anomaly': None,
            'has_incidents': False,
        }

    incidents: list[dict[str, Any]] = []
    for instance, group in anomalies_df.groupby('instance'):
        sorted_group = group.sort_values('timestamp').reset_index(drop=True)
        sorted_group['timestamp'] = pd.to_datetime(sorted_group['timestamp'], utc=True)
        current_start = sorted_group.loc[0, 'timestamp']
        current_end = current_start
        for idx in range(1, len(sorted_group)):
            ts = sorted_group.loc[idx, 'timestamp']
            if isinstance(ts, pd.Timestamp) and isinstance(current_end, pd.Timestamp) and ts - current_end <= pd.Timedelta(minutes=1.5):
                current_end = ts
            else:
                incidents.append({'instance': instance, 'start': current_start, 'end': current_end})
                current_start = ts
                current_end = ts
        incidents.append({'instance': instance, 'start': current_start, 'end': current_end})

    last_anomaly = max(item['end'] for item in incidents)
    return {
        'incident_count': len(incidents),
        'affected_nodes': len({item['instance'] for item in incidents}),
        'last_anomaly': last_anomaly,
        'has_incidents': len(incidents) > 0,
    }


def compute_node_consensus(results: dict[str, Any], instances: list[str]) -> dict[str, dict[str, Any]]:
    total_models = len(results)
    per_instance_votes: dict[str, set[str]] = {}
    per_instance_values: dict[str, list[float]] = {}

    for model_name, info in results.items():
        anomalies = info.get('anomalies', []) if isinstance(info, dict) else []
        for a in anomalies:
            inst = str(a.get('instance', 'unknown'))
            per_instance_votes.setdefault(inst, set()).add(model_name)
            value = None
            if isinstance(a.get('details'), dict):
                value = a['details'].get('value')
            if value is None:
                value = a.get('value')
            if isinstance(value, (int, float)):
                per_instance_values.setdefault(inst, []).append(float(value))

    node_consensus: dict[str, dict[str, Any]] = {}
    for inst in instances:
        votes = len(per_instance_votes.get(inst, set()))
        confidence = float(votes / total_models) if total_models else 0.0
        peak_value = max(per_instance_values.get(inst, [])) if per_instance_values.get(inst) else None
        severity = compute_consensus_severity(confidence, peak_value) if votes else 'Normal'
        node_consensus[inst] = {
            'instance': inst,
            'votes': votes,
            'confidence': confidence,
            'peak_value': peak_value,
            'severity': severity,
            'models': sorted(per_instance_votes.get(inst, [])),
        }

    return node_consensus


def render_summary(
    df: pd.DataFrame,
    metric_name: str,
    results: dict[str, Any],
    anomalies_df: pd.DataFrame,
    warmup_info: dict[str, Any] | None = None,
) -> None:
    """
    Render the cluster summary with current and historical status.
    
    Improvements:
    - Shows current metric status separately from historical anomaly analysis
    - During startup warmup, displays "Collecting baseline..." and defers analysis
    - Explains why current and historical may differ
    - Metric-aware labels and formatting
    
    Args:
        df: DataFrame with all metrics
        metric_name: Name of the metric being analyzed
        results: Anomaly detection results per model
        anomalies_df: DataFrame with detected anomalies
        warmup_info: Startup warmup status info from filter_startup_samples()
    """
    st.markdown(_STYLES, unsafe_allow_html=True)
    instances = sorted(df['instance'].unique().tolist())
    latest = df.sort_values('timestamp').groupby('instance').tail(1)
    avg_value = float(df['value'].mean()) if not df.empty else 0.0

    # Handle warmup period UI
    in_warmup: bool = bool(warmup_info and warmup_info.get('in_warmup', False)) if warmup_info else False
    if in_warmup and warmup_info:
        st.warning(
            f"🔄 **Collecting baseline...** {warmup_info.get('reason', 'System initializing.')}\n\n"
            "The anomaly detection models are warming up and collecting initial metrics. "
            "Critical alerts are disabled during this period to prevent false positives from startup spikes. "
            f"Estimated warmup end: {warmup_info.get('warmup_end_time', 'pending')}"
        )

    node_consensus = compute_node_consensus(results, instances)
    max_anomaly_score = max((info['confidence'] for info in node_consensus.values()), default=0.0)
    severity_rank = {'Normal': 0, 'Low': 1, 'Medium': 2, 'High': 3, 'Critical': 4}
    worst_node = max(node_consensus.values(), key=lambda info: severity_rank.get(info['severity'], 0)) if node_consensus else None
    worst_severity = worst_node['severity'] if worst_node is not None else 'Normal'
    
    # Determine historical status (based on anomaly detection over lookback window)
    if worst_severity == 'Critical':
        historical_status = 'Critical'
        historical_reason = 'Worst anomaly severity is Critical in the selected lookback window.'
    elif worst_severity != 'Normal':
        historical_status = 'Degraded'
        historical_reason = f'Cluster is degraded because the worst observed anomaly severity is {worst_severity}.'
    else:
        historical_status = 'Healthy'
        historical_reason = 'No anomalies were detected in the selected lookback window.'

    # Get current status (based only on latest metric values)
    latest_statuses = [current_value_status(float(row['value']), metric_name) for _, row in latest.iterrows()] if not latest.empty else []
    current_severity_rank = {'Normal': 0, 'Low': 1, 'Medium': 2, 'High': 3, 'Critical': 4}
    current_cluster_status = max(latest_statuses, key=lambda s: current_severity_rank.get(s, 0)) if latest_statuses else 'Normal'

    incident_summary = summarize_recent_incidents(anomalies_df)
    config = get_metric_config(metric_name)
    short_label = config.get('short_label', 'metric')
    history_line = (
        f"Last 1h: {incident_summary['incident_count']} incident(s), affected {incident_summary['affected_nodes']} node(s), last at {incident_summary['last_anomaly'].strftime('%Y-%m-%d %H:%M')}"
        if incident_summary['has_incidents']
        else 'Last 1h: No incidents detected.'
    )

    # Summary cards: Separate CURRENT status from HISTORICAL analysis
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    with col1:
        # Two-part status: current vs historical
        current_section = f"<div style='margin-bottom:12px;'><div style='font-size:0.9em;color:#666;'>Current {short_label} Status</div><h4 style='margin:4px 0;'>{current_cluster_status}</h4></div>"
        historical_section = f"<div><div style='font-size:0.9em;color:#666;'>Historical AI Analysis</div><h4 style='margin:4px 0;'>{historical_status}</h4><div style='font-size:0.85em;'>{historical_reason}</div></div>"
        
        warmup_note = "<div style='margin-top:8px; padding:8px; background:#fff3cd; border-left:4px solid #ffc107; font-size:0.85em;'>⏳ Baseline collection active — alerts disabled</div>" if in_warmup else ""
        
        explanation = (
            "<div style='margin-top:12px; padding:8px; background:#f0f4f8; border-radius:4px; font-size:0.85em;'>"
            "<b>Why they differ:</b> Current status shows live metrics (latest value). "
            "Historical analysis shows anomalies detected over your selected lookback window using AI models."
            "</div>"
        )
        
        st.markdown(
            f"<div class='card'>"
            f"{current_section}"
            f"{historical_section}"
            f"{warmup_note}"
            f"<div style='margin-top:8px;'>AI anomaly score: <b>{max_anomaly_score:.2f}</b></div>"
            f"<div>{history_line}</div>"
            f"{explanation}"
            f"</div>",
            unsafe_allow_html=True,
        )
    
    col2.metric('Nodes monitored', len(instances))
    col3.metric('Metric', metric_name)
    avg_label = get_metric_label('Average', metric_name)
    col4.metric(avg_label, format_metric_value(avg_value, metric_name))

    with st.expander('Latest node values'):
        st.dataframe(latest[['instance', 'metric_name', 'value', 'timestamp']].reset_index(drop=True))

    st.markdown('---')

    # Per-node health cards (show latest metric per node)
    st.markdown('**Per-node summary (Current values)**')
    node_cols = st.columns(len(instances) if len(instances) <= 4 else 4)
    idx = 0
    latest_by_instance = latest.set_index('instance') if not latest.empty else None
    current_label = get_metric_label('Current', metric_name)
    for inst in instances:
        col = node_cols[idx % len(node_cols)]
        with col:
            if latest_by_instance is not None and inst in latest_by_instance.index:
                row = latest_by_instance.loc[inst]
                val = float(row['value'])
                st.markdown(f"**{inst}**")
                st.metric(current_label, format_metric_value(val, metric_name))
            else:
                st.markdown(f"**{inst}**")
                st.write('No data')
        idx += 1
    st.markdown('---')


def compute_consensus_severity(confidence: float, peak_value: float | None, metric_name: str = 'CPU utilization (%)') -> str:
    """Compute consensus severity based on confidence and metric-specific thresholds."""
    config = get_metric_config(metric_name)
    thresholds = config.get('thresholds', {})
    
    # For memory-like metrics, convert to percentage
    if peak_value is not None and 'Memory' in metric_name and peak_value > 100:
        pct_available = min((peak_value / (4 * 1024 * 1024 * 1024)) * 100, 100)
        peak_value = pct_available
    
    if peak_value is not None:
        # Check threshold boundaries for low values
        if 'healthy' in thresholds:
            min_val, max_val = thresholds['healthy']
            if peak_value >= min_val and peak_value < max_val:
                if confidence > 0.75:
                    return 'High'
                return 'Low'
    
    # Standard confidence-based severity
    if confidence > 0.75:
        return 'Critical'
    if confidence > 0.5:
        return 'High'
    if confidence > 0.25:
        return 'Medium'
    return 'Low'


def render_auto_refresh(auto_refresh: bool) -> None:
    if not auto_refresh:
        return

    interval_seconds = 30
    now = time.time()
    last_refresh = st.session_state.get('last_refresh', 0.0)
    if now - last_refresh >= interval_seconds:
        st.session_state['last_refresh'] = now
        st.rerun()


def render_consensus(
    results: dict[str, Any],
    latest_values: dict[str, float],
    warmup_info: dict[str, Any] | None = None,
    metric_name: str = 'CPU utilization (%)',
) -> None:
    """
    Render per-node anomaly consensus.
    
    Improvements:
    - Skips email alerts during startup warmup period
    - Explains that severity reflects peak values, not current status
    - Metric-aware labels and formatting
    
    Args:
        results: Anomaly detection results from all models
        latest_values: Latest metric values per instance
        warmup_info: Startup warmup status (alerts disabled if in_warmup=True)
        metric_name: Name of the selected metric
    """
    # Build consensus across models per instance
    st.markdown('### ML Consensus')
    peak_label = get_metric_label('Peak', metric_name)
    current_label = get_metric_label('Current', metric_name)
    st.markdown(f'_Severity reflects the {peak_label.lower()} value observed within the selected lookback window, not the current live value._')
    
    in_warmup: bool = bool(warmup_info and warmup_info.get('in_warmup', False)) if warmup_info else False
    if in_warmup:
        st.info("⏳ Email alerts are disabled during baseline collection to prevent false positives.")
    
    # collect anomalies per model per instance
    per_instance_votes: dict[str, list[str]] = {}
    per_instance_values: dict[str, list[float]] = {}
    for model_name, info in results.items():
        anomalies = info.get('anomalies', []) if isinstance(info, dict) else []
        for a in anomalies:
            inst = a.get('instance', 'unknown')
            per_instance_votes.setdefault(inst, []).append(model_name)
            value = None
            if isinstance(a.get('details'), dict):
                value = a['details'].get('value')
            if value is None:
                value = a.get('value')
            if isinstance(value, (int, float)):
                per_instance_values.setdefault(inst, []).append(float(value))

    if not per_instance_votes:
        st.write('No anomalies detected by any model.')
        return

    for inst, voters in per_instance_votes.items():
        total_models = len(results)
        votes = len(set(voters))
        confidence = votes / total_models
        peak_value = max(per_instance_values.get(inst, [])) if per_instance_values.get(inst) else None
        current_value = latest_values.get(inst)
        severity = compute_consensus_severity(confidence, peak_value, metric_name)
        pct = int(confidence * 100)

        header = f"**{inst}** — Severity: **{severity}**"
        if peak_value is not None and current_value is not None:
            header += f" ({peak_label.lower()} {format_metric_value(peak_value, metric_name)}, {current_label.lower()} {format_metric_value(current_value, metric_name)})"
        elif peak_value is not None:
            header += f" ({peak_label.lower()} {format_metric_value(peak_value, metric_name)})"
        elif current_value is not None:
            header += f" ({current_label.lower()} {format_metric_value(current_value, metric_name)})"
        st.markdown(header)

        if current_value is not None:
            current_status = current_value_status(current_value, metric_name)
            st.markdown(f"Current status: **{current_status}** — {format_metric_value(current_value, metric_name)}")

        # visual confidence
        st.progress(confidence)
        if peak_value is not None:
            st.markdown(f"{peak_label}: {format_metric_value(peak_value, metric_name)}")
        
        # Send email alert, but skip if in warmup period
        if severity == 'Critical' and should_send_email_alert(inst, severity):
            success, error = send_alert_email(
                inst,
                severity,
                peak_value,
                current_value,
                skip_due_to_warmup=in_warmup,
            )
            if success:
                record_email_alert_sent(inst, severity)
                st.success(f'Email alert sent for {inst} (Critical).')
            elif error and 'warmup' not in error.lower():
                st.error(f'Failed to send email alert for {inst}: {error}')
                st.session_state['email_alert_errors'].append(f'{inst}: {error}')
        
        # Check if severity seems low due to threshold values
        config = get_metric_config(metric_name)
        thresholds = config.get('thresholds', {})
        if severity == 'Low' and peak_value is not None and 'healthy' in thresholds:
            min_val, max_val = thresholds['healthy']
            if peak_value >= min_val and peak_value < max_val:
                st.info(f'Severity is set to Low because the detected {peak_label.lower()} {format_metric_value(peak_value, metric_name)} is within healthy range, even though models agreed on an anomaly.')
        
        st.markdown(f"Confidence: {pct}% — Models: {', '.join(sorted(set(voters)))}")


def render_chart(df: pd.DataFrame, metric_name: str, anomalies: pd.DataFrame | None = None) -> None:
    if df.empty:
        st.warning('No data to chart.')
        return

    fig = px.line(df, x='timestamp', y='value', color='instance', title=f'{metric_name} over time')
    fig.update_layout(legend_title_text='Instance')
    if anomalies is not None and not anomalies.empty:
        try:
            scatter = go.Scatter(
                x=anomalies['timestamp'],
                y=anomalies['value'],
                mode='markers',
                marker=dict(color='red', size=8),
                name='Anomalies',
            )
            fig.add_trace(scatter)
        except Exception:
            pass
    st.plotly_chart(fig, use_container_width=True)


def render_individual_node_charts(df: pd.DataFrame, metric_name: str, anomalies: pd.DataFrame | None = None) -> None:
    """Render individual charts for each node."""
    if df.empty:
        return
    
    # Get unique instances and sort them
    instances = sorted(df['instance'].unique())
    
    if len(instances) <= 1:
        return
    
    st.markdown('---')
    st.markdown('### Individual Node Charts')
    
    # Create columns for 3 nodes (or fewer if available)
    cols = st.columns(min(3, len(instances)))
    
    for idx, instance in enumerate(instances):
        col = cols[idx % 3]
        
        with col:
            # Filter data for this instance
            instance_data = df[df['instance'] == instance]
            
            # Filter anomalies for this instance
            instance_anomalies = None
            if anomalies is not None and not anomalies.empty:
                instance_anomalies = anomalies[anomalies['instance'] == instance]
            
            # Create chart for this instance
            fig = px.line(
                instance_data,
                x='timestamp',
                y='value',
                title=f'{instance}',
                markers=False
            )
            fig.update_layout(
                height=350,
                showlegend=False,
                hovermode='x unified'
            )
            
            # Add anomaly markers if present
            if instance_anomalies is not None and not instance_anomalies.empty:
                try:
                    scatter = go.Scatter(
                        x=instance_anomalies['timestamp'],
                        y=instance_anomalies['value'],
                        mode='markers',
                        marker=dict(color='red', size=8),
                        name='Anomalies',
                    )
                    fig.add_trace(scatter)
                except Exception:
                    pass
            
            st.plotly_chart(fig, use_container_width=True)


def render_ml_insights(results: dict[str, Any]) -> None:
    st.markdown('### ML model insights')
    for name, info in results.items():
        if 'error' in info:
            st.error(f'{name}: {info["error"]}')
            continue
        score = info.get('score', 0.0)
        summary = info.get('summary', {})
        anomalies = info.get('anomalies', [])
        with st.expander(f"{name} — score {score:.2f} ({len(anomalies)} anomalies)"):
            st.write('Summary:')
            st.json(summary)
            if anomalies:
                st.write('Recent anomalies:')
                st.table(pd.DataFrame(anomalies).tail(5))
            # suggestions
            if score > 0.7:
                st.warning('High anomaly score — investigate processes, CPU-bound tasks, or memory leaks.')
            elif score > 0.3:
                st.info('Moderate anomalies — keep monitoring and review trends.')
            else:
                st.success('No significant anomalies detected by this model.')
            st.write('Recommended action:')
            st.markdown('- Check top CPU processes: `top` / `ps aux --sort=-%cpu`')
            st.markdown('- Verify container limits or runaway jobs')
            st.write('---')


def main() -> None:
    render_header()
    
    # Initialize incident manager (persistent across reruns)
    incident_manager = init_incident_manager_state()
    
    instances = get_prometheus_instances(PROMETHEUS_URL)
    instance, selected_metric, hours, prometheus_url, auto_refresh = render_sidebar(instances)
    render_auto_refresh(auto_refresh)
    metric_query = METRIC_OPTIONS[selected_metric]

    try:
        df = load_metrics(metric_query, prometheus_url, hours)
    except RequestException as exc:
        st.error(f'Cannot connect to Prometheus at {prometheus_url}: {exc}')
        return
    except Exception as exc:
        st.error(str(exc))
        return

    if df.empty:
        st.warning('No metrics returned from Prometheus. Verify instance and metric names.')
        return

    df = filter_instances(df, instance)
    if df.empty:
        st.warning('No data returned for the selected instance. Try All nodes or another instance.')
        return

    init_alert_session_state()
    df = add_rolling_features(df)
    
    # Run detectors and capture warmup info
    results, warmup_info = run_detectors(df, selected_metric, instance)
    
    # Normalize results structure: if per-instance, convert to model-centric format
    if instance == 'All nodes' and 'instance' in df.columns and isinstance(results, dict):
        # Check if results is per-instance (first key is an instance name, value is dict of models)
        first_value = next(iter(results.values())) if results else None
        if isinstance(first_value, dict) and 'Rolling Mean' in first_value:
            # Per-instance format - convert to model-centric
            model_centric_results: dict[str, Any] = {
                'Rolling Mean': {'anomalies': [], 'summary': {}, 'score': 0.0},
                'EWMA': {'anomalies': [], 'summary': {}, 'score': 0.0},
                'Z Score': {'anomalies': [], 'summary': {}, 'score': 0.0},
                'Isolation Forest': {'anomalies': [], 'summary': {}, 'score': 0.0},
            }
            for inst_name, inst_models in results.items():
                for model_name, model_data in inst_models.items():
                    if isinstance(model_data, dict) and 'anomalies' in model_data:
                        model_centric_results[model_name]['anomalies'].extend(model_data['anomalies'])
                        model_centric_results[model_name]['summary'] = model_data.get('summary', {})
                        # Use MAX score across instances (any anomaly in any instance should be reported)
                        current_score = model_data.get('score', 0.0)
                        model_centric_results[model_name]['score'] = max(model_centric_results[model_name]['score'], current_score)
            results = model_centric_results

    latest_values = (
        df.sort_values('timestamp')
          .groupby('instance')
          .tail(1)
          .set_index('instance')['value']
          .astype(float)
          .rename_axis('instance')
          .reset_index()
    )
    latest_values = {str(row['instance']): float(row['value']) for _, row in latest_values.iterrows()}

    # aggregate anomalies across models for chart highlighting
    anomalies_list: list[dict] = []
    for info in results.values():
        if isinstance(info, dict) and 'anomalies' in info and info['anomalies']:
            anomalies_list.extend([a for a in info['anomalies'] if a.get('is_anomaly')])
    anomalies_df = pd.DataFrame(anomalies_list) if anomalies_list else pd.DataFrame()
    if not anomalies_df.empty and 'timestamp' in anomalies_df.columns:
        anomalies_df['timestamp'] = pd.to_datetime(anomalies_df['timestamp'], utc=True)
    
    # ========================================================================
    # INCIDENT LIFECYCLE MANAGEMENT
    # ========================================================================
    
    # Create incidents from anomalies (if any new anomalies detected)
    if not anomalies_df.empty:
        # Calculate consensus confidence from results
        max_confidence = 0.0
        all_detectors = []
        for model_name, info in results.items():
            if isinstance(info, dict) and 'score' in info:
                max_confidence = max(max_confidence, info['score'])
                if info['score'] > 0:
                    all_detectors.append(model_name)
        
        # Only create incident if confidence is significant
        if max_confidence > 0.25:
            incident = incident_manager.create_incident(
                anomalies_df,
                all_detectors,
                max_confidence,
                current_time=datetime.now(timezone.utc)
            )
    
    # Update recovery status for active incidents
    incident_manager.update_incident_recovery(
        latest_values,
        normal_threshold=50.0,
        current_time=datetime.now(timezone.utc)
    )
    
    # ========================================================================
    # RENDER IMPROVED DASHBOARD
    # ========================================================================
    
    # 1. Cluster Health Dashboard (top-level summary)
    render_cluster_health_dashboard(instances, latest_values, incident_manager, metric_name=selected_metric)
    
    # 2. Current Health Section (live metrics only)
    render_current_health_section(latest_values, instances, metric_name=selected_metric)
    
    # 3. Historical AI Analysis Section (incidents and anomalies)
    render_historical_analysis_section(results, incident_manager, anomalies_df, metric_name=selected_metric)
    
    # 4. Improved ML Consensus with checkmarks
    render_detector_consensus_improved(results, latest_values, warmup_info=warmup_info, metric_name=selected_metric)
    
    st.markdown('---')
    
    # 5. Charts (existing functionality)
    render_chart(df, selected_metric, anomalies=anomalies_df)
    render_individual_node_charts(df, selected_metric, anomalies=anomalies_df)
    render_ml_insights(results)


if __name__ == '__main__':
    main()
