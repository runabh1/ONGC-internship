# Mentor Summary: ONGC Cluster Monitor

## 1. Project purpose

This project is a React frontend monitoring dashboard for Prometheus-hosted Linux nodes, with an ML-based anomaly detection layer and alerting support.

It is designed to:
- query Prometheus metrics for CPU, load, memory, disk and network
- detect anomalies using several ML/statistical detectors
- synthesize a consensus severity per node
- show peak versus current values in the UI
- send email alerts for Critical incidents
- support auto-refresh for live data updates

## 2. What has been implemented so far

- Main dashboard in `frontend/src/App.jsx`
- Alerts page in `frontend/src/alerts.py`
- Supplemental chart/table pages
- Prometheus client wrapper in `ml/prometheus_client.py`
- Detector implementations in `ml/baseline_detector.py`, `ml/isolation_forest.py`, and `ml/lstm_detector.py`
- Email alerting with deduplication in `frontend/src/App.jsx`
- Auto-refresh sidebar toggle using HTML meta refresh
- Clear `peak` vs `current` value display in ML consensus
- Deployment-friendly config via env vars and `config/nodes.yaml`

## 3. Important code parts and where they live

### `frontend/src/App.jsx`
This is the core application and contains:
- app initialization and layout
- sidebar controls:
  - Prometheus URL
  - node instance selector or manual instance entry
  - metric selector
  - lookback hours slider
  - auto-refresh checkbox
- Prometheus metric loading with `load_metrics()`
- anomaly detector execution with `run_detectors()`
- summarization logic in `render_summary()`
- consensus rendering in `render_consensus()`
- chart rendering in `render_chart()`
- ML model insight UI in `render_ml_insights()`
- email alert logic:
  - `email_alerts_configured()`
  - `should_send_email_alert()`
  - `send_alert_email()`
  - `record_email_alert_sent()`

Important UI behavior:
- Auto-refresh uses `<meta http-equiv="refresh" content="30">`
- Severity is based on peak detected CPU values inside the lookback window
- Current live value is also shown for context
- Critical alerts trigger email only when configured

### `frontend/src/alerts.py`
This page builds an incident summary and does:
- query CPU utilization over a selected lookback window
- run all detectors again per node
- aggregate per-instance anomalies into incidents
- compute severity from model agreement
- display consolidated incidents with severity, duration, peak CPU, and detector details

Key functions:
- `build_alerts(prometheus_url, hours)`
- `main()` to render page filters and tables

### `frontend/src/charts.py`
This page provides a general chart interface using Prometheus queries and Plotly. It is a simple metric visualization helper.

### `frontend/src/tables.py`
This page produces operator-friendly value tables for an instance over time, merging CPU, memory, and disk data.

## 4. ML and anomaly detection algorithms used

### RollingMeanDetector
Defined in `ml/baseline_detector.py`.
- uses rolling median over a 3-sample window
- computes percentage deviation from baseline
- flags anomaly when deviation >= 20%
- useful for fast changes against local history

### EWMAAnomalyDetector
Defined in `ml/baseline_detector.py`.
- computes exponentially weighted moving average
- compares current value to EWMA expectation
- flags anomaly when deviation >= 20%
- sensitive to recent trends while smoothing noise

### ZScoreDetector
Defined in `ml/baseline_detector.py`.
- calculates a robust z-score using median and MAD
- flags anomaly when absolute z-score >= 2.5
- works well when data has a stable center and occasional spikes

### IsolationForestDetector
Defined in `ml/isolation_forest.py`.
- uses scikit-learn`s `IsolationForest`
- fits on the value series
- anomalies are instances with label -1
- anomaly score is derived from the model decision function
- good for unsupervised outlier detection on CPU values

### LSTMAutoencoderDetector
Defined in `ml/lstm_detector.py`.
- uses a small TensorFlow LSTM autoencoder
- trains on sequences of recent values
- computes reconstruction error for anomaly detection
- threshold is mean + 2*std of reconstruction error
- not the main runtime detector in the current app, but available as an advanced option

## 5. Data and API flow

### Prometheus API
Prometheus is accessed through `ml/prometheus_client.py`.
- `query()` calls `/api/v1/query`
- `query_range()` calls `/api/v1/query_range`
- results are converted into pandas dataframes by `ml.utils.to_dataframe()`

### Metric discovery
- `get_prometheus_instances()` queries `up`
- if Prometheus is unavailable or returns no hosts, user enters instance manually

### Data processing
- `load_metrics()` fetches metric values for the selected window
- `add_rolling_features()` adds moving-average and z-score features
- detectors produce `AnomalyResult` objects
- `explain_anomalies()` converts them into JSON-friendly dictionaries

### Consensus and severity
- `render_consensus()` gathers anomaly votes per node
- severity is computed with `compute_consensus_severity()` (in alerts page)
- current value and peak value are both shown:
  - peak = highest anomaly value inside the window
  - current = latest metric value

## 6. Email alerting

Configured with environment variables:
- `EMAIL_USER`
- `EMAIL_PASSWORD`
- `ALERT_EMAIL_TO`
- `ALERT_EMAIL_FROM`
- `EMAIL_SMTP_SERVER` (default `smtp.gmail.com`)
- `EMAIL_SMTP_PORT` (default `587`)

Behavior:
- only `Critical` severity triggers email
- alert emails are deduplicated for each node/severity pair
- repeated critical alerts for the same node are suppressed for 30 minutes
- if email settings are missing, the app still runs but no email is sent

## 7. Important APIs and libraries used

- React frontend (`frontend`) for dashboard UI
- Pandas (`pandas`) for time series and data frames
- Plotly (`plotly.express`, `plotly.graph_objects`) for charts
- scikit-learn (`IsolationForest`) for unsupervised outlier detection
- TensorFlow (`tensorflow` or `tensorflow-cpu`) for LSTM autoencoder
- Requests (`requests`) for Prometheus HTTP API calls
- smtplib and email.message for SMTP email alerts
- PyYAML only for optional config, not required by main app

## 8. Deployment and configuration

- The main app is launched with:
  - `frontend run frontend/src/App.jsx`
- Use env vars to point to Prometheus and SMTP settings
- `config/nodes.yaml` is an optional node list file for helpers/tests
- No hardcoded production node IPs remain in the main dashboard logic
- `.env.example` has the current recommended variables

## 9. Testing and validation

- Basic tests and validation scripts exist under `tests/`
- Example commands:
  - `pytest tests/test_exporter.py`
  - `pytest tests/test_prometheus.py`
  - `python tests/verify_cluster.py`

## 10. How to explain it to the mentor

1. Start with the problem statement:
   - We built a Prometheus-backed frontend dashboard with ML-based node anomaly detection and alerting.
2. Describe the key user flows:
   - discover nodes, select metrics, view consensus, send critical alerts.
3. Highlight the most important files:
   - `frontend/src/App.jsx` for the main dashboard
   - `frontend/src/alerts.py` for incident consolidation
   - `ml/prometheus_client.py` for Prometheus integration
   - `ml/*_detector.py` for the anomaly models
4. Explain the ML detectors and why they were chosen:
   - rolling mean for local change detection
   - EWMA for recent trend smoothing
   - z-score for statistical spike detection
   - Isolation Forest for unsupervised outlier detection
   - LSTM autoencoder as an advanced sequence model
5. Emphasize the additions made:
   - peak/current severity clarity
   - email alert deduplication
   - auto-refresh toggle
   - no hardcoded node IPs in main logic
6. Finish with deployment notes:
   - use env vars to configure Prometheus and SMTP
   - the dashboard can run in any environment with Prometheus access

---

This file is intended as a single summary you can share directly with your mentor, covering architecture, key code, APIs, ML algorithms, and the current implementation status.