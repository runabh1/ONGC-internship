# ONGC Cluster Monitor

A Streamlit-based monitoring dashboard for Prometheus node metrics, enhanced with ML-driven anomaly detection and incident alerting.

## What this app does

- Queries Prometheus for node metrics such as CPU utilization.
- Detects anomalies using multiple ML detectors: rolling mean, EWMA, z-score, and Isolation Forest.
- Synthesizes a per-node ML consensus severity score.
- Shows peak vs current CPU values clearly in the `ML Consensus` section.
- Summarizes recent incidents across nodes and merges consecutive anomaly events into incident groups.
- Supports email notifications for `Critical` incidents with deduplication.
- Supports auto-refresh every 30 seconds from the sidebar.

## Features implemented

- **Dynamic Prometheus node discovery** via the `up` metric.
- **Manual node input fallback** when auto-discovery fails.
- **Incident consolidation** for consecutive anomaly timestamps.
- **Local timezone display** for alert timestamps.
- **ML consensus visibility** with peak and current CPU values.
- **Email alerts** for Critical node incidents.
- **Email deduplication** so the same node/severity pair is not emailed more than once every 30 minutes.
- **Auto-refresh toggle** for live dashboard updates every 30 seconds.

## Project structure

- `streamlit_app/app.py` — main dashboard and alerting logic.
- `streamlit_app/pages/alerts.py` — alert listing and incident consolidation page.
- `streamlit_app/pages/charts.py` — secondary chart view.
- `streamlit_app/pages/tables.py` — secondary table view.
- `streamlit_app/pages/infra_checker.py` — infrastructure health check using SSH and Prometheus.
- `ml/` — anomaly detector implementations and Prometheus client.
- `config/nodes.yaml` — optional Prometheus node target configuration.
- `tests/` — smoke tests, exporter tests, and verification scripts.

## Setup

### Requirements

Install dependencies from `requirements.txt`:

```bash
pip install -r requirements.txt
```

### Environment variables

Create a `.env` file or set the following environment variables before running the app.

Required variables for email alerts:

- `EMAIL_USER` — SMTP login username (usually your email address)
- `EMAIL_PASSWORD` — SMTP password or Gmail app password
- `ALERT_EMAIL_TO` — comma-separated list of recipient email addresses

Optional email variables:

- `EMAIL_SMTP_SERVER` — SMTP server hostname (default: `smtp.gmail.com`)
- `EMAIL_SMTP_PORT` — SMTP port (default: `587`)
- `ALERT_EMAIL_FROM` — sender address (default: `EMAIL_USER`)

Example `.env` entries:

```env
EMAIL_USER=youremail@gmail.com
EMAIL_PASSWORD=your_app_password
ALERT_EMAIL_TO=alerts@example.com,ops@example.com
ALERT_EMAIL_FROM=youremail@gmail.com
EMAIL_SMTP_SERVER=smtp.gmail.com
EMAIL_SMTP_PORT=587
```

### Prometheus configuration

- The app uses `PROMETHEUS_URL` to connect to Prometheus. It defaults to `http://localhost:9090`.
- Node IPs are discovered from Prometheus via the `up` metric.
- If Prometheus discovery fails, the sidebar allows manual node instance input.

### Optional `config/nodes.yaml`

This repository includes `config/nodes.yaml` for target configuration. It is primarily used by verification scripts, not the main Streamlit app.

## Running the app

Launch the dashboard:

```bash
streamlit run streamlit_app/app.py
```

Then use the sidebar to:

- select Prometheus URL
- pick a node or `All nodes`
- choose a metric
- adjust the lookback window
- enable `Auto-refresh every 30s`

## Email alert behavior

- Alerts are sent only for node severity `Critical`.
- Email notifications are deduplicated per node/severity pair.
- The same node and severity combination will not be emailed more than once every 30 minutes.
- The app still runs normally if email settings are missing; email alerts are simply disabled.

## Deployment notes

- For a different cluster, you do not need to modify `app.py`.
- Users can deploy against new machines by pointing Prometheus at the new targets and/or entering the new node instance in the sidebar.
- Verification and test scripts can also use the `NODES` environment variable.

## Testing and verification

- `pytest tests/test_exporter.py`
- `pytest tests/test_prometheus.py`
- `python tests/verify_cluster.py`

## Troubleshooting

- If the app fails to connect, verify that `PROMETHEUS_URL` is correct.
- If email fails, verify that `EMAIL_USER`, `EMAIL_PASSWORD`, and `ALERT_EMAIL_TO` are set and valid.
- For Gmail, use an app password instead of your normal account password.
