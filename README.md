# ONGC Cluster Monitor

Comprehensive monitoring stack that combines Prometheus for metrics collection, a Streamlit dashboard for visualization and operator interaction, and a small ML layer for anomaly detection (rolling mean, EWMA, Z-score, Isolation Forest). The project is designed to run locally via Docker Compose for demos and can also be run in a development environment.

## Quick overview

- Prometheus: scrapes `node_exporter` targets and stores time-series metrics.
- Alertmanager: receives Prometheus alerts (email/webhook routing configurable).
- Streamlit app: UI for running ML detectors, viewing charts/tables, and sending email alerts (optional).
- ML detectors: implemented in `ml/` — provide `fit`, `predict`, `score`, and `explain` interfaces.

This README documents how to install, run, develop, test, and troubleshoot the stack.

**Quick start (Docker)**

1. Clone the repo and change directory:

```bash
git clone <your-repo-url>
cd Ongc-cluster-monitor
```

2. (Optional) Copy the example env file and edit values:

```bash
cp .env.example .env
```

3. Start the stack (from repo root):

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

Open:
- Streamlit dashboard: http://localhost:8501
- Prometheus UI: http://localhost:9090
- Alertmanager UI: http://localhost:9093

**Stopping**

```bash
docker compose -f docker/docker-compose.yml down
```

## Environment variables

- `PROMETHEUS_URL` — Prometheus base URL used by the Streamlit app (default: `http://prometheus:9090` when running in Docker).
- `EMAIL_USER`, `EMAIL_PASSWORD`, `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM`, `EMAIL_SMTP_SERVER`, `EMAIL_SMTP_PORT` — configure email alerting (optional). If not set, email alerts are disabled.
- `NODES` / `CLUSTER_NODES` — comma-separated list of node_exporter targets for the infra checker page.

See the top of `docker/docker-compose.yml` and `streamlit_app/app.py` for how these variables are read.

## Prometheus configuration

- Targets: `config/nodes.yaml` (list node_exporter targets)
- Prometheus config: `config/prometheus.yml`
- Alert rules: `config/rules/alert_rules.yml`

Edit these files and restart the `prometheus` service via Docker Compose to apply changes.

## Streamlit app internals (high level)

- Entry: `streamlit_app/app.py` — handles UI, datasets, detector orchestration, and alerting logic.
- Pages: `streamlit_app/pages/` contains `charts.py`, `tables.py`, `alerts.py`, `infra_checker.py`.
- Charts: Plotly is used for time-series visualizations; anomalies are shown as markers.
- Tables: timestamps are converted to local time for operator-friendly display.

**Workflow Diagram**

```mermaid
flowchart TD
    A[Node hosts with node_exporter]
    B[Prometheus server]
    C[Streamlit dashboard]
    D[ML detectors]
    E[Chart / Table / Incident summary]
    F[Optional Email alert]
    G[Alertmanager]

    A -->|HTTP scrape metrics| B
    B -->|stores time-series data| B
    C -->|query_range(metric, start, end)| B
    B -->|returns metric series| C
    C -->|builds DataFrame and filters instance| D
    D -->|anomaly labels, scores, explanations| C
    C -->|renders dashboards and tables| E
    C -->|sends critical alert if configured| F
    B -->|runs alert rules| G
    G -->|handles alerts independently| F
```

### Detailed workflow

1. `node_exporter` runs on each monitored host and exposes metrics like CPU, memory, disk, and load.
2. Prometheus scrapes those hosts regularly and stores the raw time-series metrics in its database.
3. When the user selects a node, metric, and lookback range in Streamlit, the app issues a Prometheus `query_range` request.
4. The Prometheus client in `ml/prometheus_client.py` converts the returned JSON into a Pandas DataFrame with timestamps, instance labels, metric values, and metadata.
5. Streamlit filters the DataFrame by the selected instance (or processes all nodes) and passes the relevant series to each detector in `ml/`.
6. The detector implementations evaluate the series:
   - Rolling Mean compares the current value to a recent moving baseline.
   - EWMA computes a smoothed expected value and flags deviations.
   - Z-Score uses robust statistics to detect outliers.
   - Isolation Forest uses unsupervised model scoring to find anomalous points.
7. Each detector returns anomaly events, numeric scores, and human-readable explanation details.
8. Streamlit aggregates these outputs into an overall consensus per node, then renders:
   - time-series charts with anomaly markers,
   - operator tables with localized timestamps,
   - incident summaries and severity information.
9. If SMTP is configured and a critical incident is detected, the app can send an email alert. This is optional and separate from Prometheus Alertmanager.
10. In parallel, Prometheus alert rules in `config/rules/alert_rules.yml` can also fire alerts through Alertmanager, which is handled independently of the Streamlit ML path.

## Cluster status determination

This app determines cluster status using three checks per node:

- `node_exporter`: verifies that the node exposes metrics on port `9100`.
- `Prometheus scrape target`: confirms Prometheus has an active `node_exporter` target for that node and that the health status is `up`.
- `SSH` connectivity: optional check to verify port `22` is reachable, and if a password is supplied and `paramiko` is installed, it can also verify SSH login.

The infrastructure status page is implemented in `streamlit_app/pages/infra_checker.py`, and the actual check logic is in `tests/verify_cluster.py`.

A node is marked as healthy only if all available checks pass. The page shows:

- `exporter`: whether the node_exporter endpoint is reachable.
- `prometheus`: whether Prometheus is scraping that node successfully.
- `ssh`: whether SSH port 22 is reachable / authenticated.
- `overall`: a combined healthy indicator based on the above checks.

**Status categories in the current implementation**

This app does not currently assign explicit text labels like `healthy`, `degraded`, or `critical` in the source code. Instead, it uses boolean pass/fail badges for each check, and `overall` is true only when all three checks pass.

A practical interpretation is:

- `Healthy`: all three checks pass.
- `Degraded`: one or more checks fail.
- `Critical`: all checks fail or the node is unreachable.

However, these descriptive categories are not stored or displayed by name in the current page; only the individual check statuses and the combined `overall` result are shown.

## Deployment for ONGC devices

For ONGC deployment, the monitored device setup is split into two parts:

1. Node-level deployment on each target machine:
   - Install and run `node_exporter` on every server or VM you want to monitor.
   - Ensure `node_exporter` is accessible at `http://<node-ip>:9100/metrics`.
   - Optionally allow SSH access on port 22 for infrastructure checks.

2. Monitoring stack deployment on a central host:
   - Run the Docker Compose stack from this repo on a central monitoring machine.
   - This central host runs Prometheus, Alertmanager, and the Streamlit dashboard.
   - The central host queries `node_exporter` targets and stores metrics in Prometheus.

### How to deploy

1. Install `node_exporter` on each target node.
2. Add each node target to `config/nodes.yaml`, or set `NODES` / `CLUSTER_NODES` in `.env`.
3. Start the stack on the central host:

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

4. Open the dashboard:

- Streamlit: `http://<monitor-host>:8501`
- Prometheus: `http://<monitor-host>:9090`
- Alertmanager: `http://<monitor-host>:9093`

### Notes for ONGC deployment

- The app does not need to run on every monitored device. Only `node_exporter` must run there.
- Prometheus and Streamlit can both run centrally, connecting to all node exporters over the network.
- If SSH-based status checks are desired, make sure port 22 is reachable and provide credentials in the infra checker page.

Core ML detector implementations are in `ml/`:
- `ml/baseline_detector.py` — Rolling Mean, EWMA, Z-Score detectors (robust statistics; tuned defaults included).
- `ml/isolation_forest.py` — Isolation Forest wrapper.
- `ml/prometheus_client.py` — lightweight Prometheus query wrapper (returns Pandas DataFrames).

Detector defaults (current recommended):
- Rolling Mean: window=15, threshold=0.20
- EWMA: span=12, threshold=0.20
- Z-Score: threshold=2.5
- Isolation Forest: contamination=0.03

You can change these defaults in the detector modules under `ml/`.

## Running locally (development)

1. Create Python venv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate    # or .venv\\Scripts\\activate on Windows
pip install -r requirements.txt
```

2. Run Streamlit directly:

```bash
streamlit run streamlit_app/app.py
```

This is useful for fast iteration while developing detectors or UI.

## Tests

The `tests/` directory includes lightweight checks. Run pytest from the repo root:

```bash
pytest -q
```

Example test added: `tests/test_detector_sensitivity.py` verifies detector default parameters.

## Docker image build notes and performance

- The Streamlit image installs packages from `requirements.txt` during build. `tensorflow-cpu` is present in `requirements.txt` and is large; it significantly increases build time and image size.
- If you do not need TensorFlow functionality, remove `tensorflow-cpu==2.18.1` from `requirements.txt` to speed up builds and reduce image size, then rebuild the `streamlit` image:

```bash
# edit requirements.txt: remove tensorflow-cpu
docker compose -f docker/docker-compose.yml build --no-cache streamlit
docker compose -f docker/docker-compose.yml up -d --force-recreate streamlit
```

## Troubleshooting

- Port 9090 already in use: On Windows, a local Prometheus process (`prometheus.exe`) can bind 9090 and block Docker's Prometheus container. Stop the local process or change the port mapping.
- Alertmanager config errors: if Alertmanager fails with `invalid URL: unsupported scheme \"\"`, check `config/alertmanager.yml` for email SMTP placeholders — either supply valid SMTP settings or disable email receivers.
- Streamlit runtime errors referencing `st.rerun`: older code used `st.rerun()`; the supported API is `st.experimental_rerun()` — source has been updated. Rebuild the Streamlit image after code edits.
- Slow Docker builds: remove heavy packages (TensorFlow) or build a smaller image using a pared-down `requirements.txt` for the Docker build.

## Debugging tips & common commands

Tail Streamlit logs:

```bash
docker compose -f docker/docker-compose.yml logs --tail 200 streamlit
```

Rebuild streamlit only (useful after code edits):

```bash
docker compose -f docker/docker-compose.yml build --no-cache streamlit
docker compose -f docker/docker-compose.yml up -d --force-recreate streamlit
```

Check which process holds port 9090 on Windows (PowerShell):

```powershell
Get-NetTCPConnection -LocalPort 9090 | Format-List
Get-Process -Id (Get-NetTCPConnection -LocalPort 9090).OwningProcess
```

## Development notes and recommendations

- Do not import test modules from production code (avoid `tests.verify_cluster` imports in runtime pages).
- Keep heavy ML frameworks out of the container unless used at runtime. Move optional models behind feature flags or separate services.
- If you intend to demo on a machine with slow downloads, pre-build the `streamlit` image and push it to a private registry, or vendor wheels into `pipcheck/` and reference them in the Dockerfile.

## Files to inspect when debugging

- `streamlit_app/app.py` — main flow and sidebar controls.
- `streamlit_app/pages/*.py` — individual page implementations.
- `ml/prometheus_client.py` — Prometheus HTTP wrapper -> returns DataFrame with `timestamp` (seconds since epoch), `instance`, `value`.
- `ml/baseline_detector.py`, `ml/isolation_forest.py` — detector implementations and hyperparameters.
- `docker/Dockerfile.streamlit` and `docker/docker-compose.yml` — container build/run definitions.

## Contact & contribution

If you want help cleaning up requirements or refactoring the detectors into a lightweight runtime-only image, open an issue or create a branch and I can help with the follow-up changes and rebuild.

---
Generated on: 2026-07-06

