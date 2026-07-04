# ONGC Cluster Monitor

A Docker-based monitoring stack for Prometheus node metrics, ML-driven anomaly detection, and incident alerting.

This repository is designed to be cloned and run on a different machine with minimal setup. The recommended deployment path is Docker Compose, which starts:

- Prometheus for metric collection
- Alertmanager for alert routing
- Streamlit for the dashboard UI

## What this app does

- Queries Prometheus for node metrics such as CPU utilization, memory availability, and load.
- Detects anomalies with multiple ML detectors: rolling mean, EWMA, z-score, and Isolation Forest.
- Aggregates the per-node ML severity into a single consensus view.
- Displays incidents and incident groups in the dashboard and alert pages.
- Supports email notifications for `Critical` incidents when SMTP credentials are configured.
- Supports auto-refresh every 30 seconds from the sidebar.

## Prerequisites

Install the following on the machine that will run the stack:

- Git
- Docker Desktop (or Docker Engine + Docker Compose)

## 1. Clone and enter the repo

```bash
git clone <your-repo-url>
cd Ongc-cluster-monitor
```

## 2. Create a local environment file

Copy the example file and edit it for your own environment:

```bash
copy .env.example .env
```

On Linux/macOS, use:

```bash
cp .env.example .env
```

Edit `.env` and replace the placeholder values. At minimum, set the SMTP values if you want email alerts:

```env
PROMETHEUS_URL=http://localhost:9090
EMAIL_USER=youremail@gmail.com
EMAIL_PASSWORD=your_app_password
ALERT_EMAIL_TO=alerts@example.com,ops@example.com
ALERT_EMAIL_FROM=youremail@gmail.com
EMAIL_SMTP_SERVER=smtp.gmail.com
EMAIL_SMTP_PORT=587
```

The infra checker page also reads node addresses from `.env` using either `NODES` or `CLUSTER_NODES`.

Use a comma-separated list of hosts, and the app will default to port `9100` when the port is omitted:

```env
NODES=192.168.56.101,192.168.56.102,192.168.56.103
```

If you want to be explicit, you can include the port for each host:

```env
NODES=192.168.56.101:9100,192.168.56.102:9100
```

> The app will still start without email settings, but email alerts will be disabled.

## 3. Configure your own node targets

Prometheus targets are defined in [config/nodes.yaml](config/nodes.yaml).

Replace the sample entries with your own node_exporter hosts or IP addresses:

```yaml
- labels:
    job: node_exporter
  targets:
    - 'YOUR_NODE_IP_1:9100'
    - 'YOUR_NODE_IP_2:9100'
```

Each target should be the host/IP where `node_exporter` is running and exposing port `9100`.

If you need to change the scrape behavior, edit [config/prometheus.yml](config/prometheus.yml) as well.

## 4. Start the stack

From the repository root, run:

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

This will build the Streamlit image and start:

- Prometheus: http://localhost:9090
- Alertmanager: http://localhost:9093
- Streamlit dashboard: http://localhost:8501

## 5. Confirm everything is running

Open the URLs above in your browser.

Expected results:

- Prometheus shows targets under the `node_exporter` job.
- The Streamlit dashboard loads and shows node metrics or an empty state if no targets are reachable yet.
- Alertmanager loads the web UI without errors.

## 6. Stop the stack

```bash
docker compose -f docker/docker-compose.yml down
```

## Project structure

- [streamlit_app/app.py](streamlit_app/app.py) — main dashboard and alerting logic.
- [streamlit_app/pages/alerts.py](streamlit_app/pages/alerts.py) — alert listing and incident consolidation page.
- [streamlit_app/pages/charts.py](streamlit_app/pages/charts.py) — secondary chart view.
- [streamlit_app/pages/tables.py](streamlit_app/pages/tables.py) — secondary table view.
- [streamlit_app/pages/infra_checker.py](streamlit_app/pages/infra_checker.py) — infrastructure health check using SSH and Prometheus.
- [ml](ml) — anomaly detector implementations and Prometheus client.
- [config/nodes.yaml](config/nodes.yaml) — Prometheus node target configuration.
- [docker/Dockerfile.streamlit](docker/Dockerfile.streamlit) — container build definition for the Streamlit app.

## Optional local development

If you want to run the app directly instead of via Docker:

```bash
pip install -r requirements.txt
streamlit run streamlit_app/app.py
```

## Notes for email alerts

- Email alerts are only sent when the app sees a `Critical` incident and the SMTP settings are present.
- For Gmail, use an app password instead of your regular account password.
- If SMTP is not configured, the app will still run normally and skip email delivery.
