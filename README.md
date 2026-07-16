# ONGC Cluster Monitor

A local Docker Compose monitoring stack that runs Prometheus, Alertmanager, node_exporter, a FastAPI backend, and a React frontend dashboard.

This repo does not use Grafana. The frontend dashboard is served from the built React app on port `3001`.

## What this stack includes

- `db` — PostgreSQL for backend state and metrics history.
- `prometheus` — Prometheus server scraping `node_exporter` targets and exposing `/api/v1/`.
- `node_exporter` — exposes host metrics on port `9100`.
- `backend` — FastAPI service collecting data, health checking nodes, and serving the frontend API.
- `frontend` — React dashboard served by `serve` on port `3001`.

## Quick start (Docker)

1. Open a terminal in the repo root:

```powershell
cd C:\Users\aruna\OneDrive\Desktop\Ongc-cluster-monitor
```

2. Bring up the stack from the root `docker-compose.yml` file:

```powershell
docker compose up --build -d
```

3. Open these UIs:

- Frontend dashboard: http://localhost:3001
- Prometheus UI: http://localhost:9090
- Alertmanager UI: http://localhost:9093

> If `http://localhost:3000` opens Grafana or another service, it is not part of this repo. Use `http://localhost:3001` for this app.

## Stop the stack

```powershell
docker compose down
```

## Ports used by this project

- `3001` → React frontend
- `8000` → backend HTTP API
- `9090` → Prometheus UI/API
- `9093` → Alertmanager UI
- `9100` → node_exporter metrics endpoint
- `5432` → PostgreSQL

## Configuration files

- `config/prometheus.yml` — Prometheus scrape configuration.
- `config/nodes.yaml` — node_exporter targets for Prometheus.
- `config/rules/alert_rules.yml` — Prometheus alerting rules.
- `frontend/src/App.jsx` — main React dashboard application.
- `backend/` — backend service implementation and health check logic.

## Environment variables

The backend reads `.env` if present.

Common variables:

- `POSTGRES_PASSWORD` — PostgreSQL password (default is set in `docker-compose.yml`).
- `PROMETHEUS_URL` — backend Prometheus base URL (set to `http://prometheus:9090` inside Docker).
- `NODES` / `CLUSTER_NODES` — optional comma-separated node exporter targets for health checks.

## How the app works

1. `node_exporter` exposes host metrics at each configured target.
2. Prometheus scrapes those targets using `config/nodes.yaml`.
3. The backend reads Prometheus data and updates node health and metrics.
4. The React frontend queries the backend API and displays node status, charts, and anomaly analysis.

## Common problems

- If the frontend is not visible, open `http://localhost:3001`.
- If `http://localhost:3000` opens Grafana or another unrelated app, do not use it for this project.
- Confirm Prometheus is reachable at `http://localhost:9090`.
- Confirm your nodes in `config/nodes.yaml` are correct and reachable from the Docker host.

## Run locally without Docker

This repo is designed for Docker Compose, but if needed you can run the backend and frontend manually:

1. Create a Python virtual environment and install requirements:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Start the backend manually:

```powershell
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

3. Start the frontend manually by running the React app build or using a local server.

## Docker troubleshooting

- If build fails because files are missing, make sure the repo is not stored as OneDrive placeholders. Store the repo locally or mark it "Always keep on this device." 
- Use `docker compose logs -f backend` and `docker compose logs -f frontend` to inspect runtime issues.
- If port `3001` is already in use, stop the conflicting service or change the frontend port mapping in `docker-compose.yml`.

## Notes

- This project does not include Grafana.
- The frontend is served on `3001` by `serve` after the React build.
- Prometheus is used directly for metrics and alerting.
- The backend health checker relies on Prometheus `/api/v1/targets` and node exporter target health.

Get-NetTCPConnection -LocalPort 9090 | Format-List
Get-Process -Id (Get-NetTCPConnection -LocalPort 9090).OwningProcess
```

## Development notes and recommendations

- Do not import test modules from production code (avoid `tests.verify_cluster` imports in runtime pages).
- Keep heavy ML frameworks out of the container unless used at runtime. Move optional models behind feature flags or separate services.
- If you intend to demo on a machine with slow downloads, pre-build the `frontend` image and push it to a private registry, or vendor wheels into `pipcheck/` and reference them in the Dockerfile.

## Files to inspect when debugging

- `frontend/src/App.jsx` — main flow and sidebar controls.
- `frontend/src/*.py` — individual page implementations.
- `ml/prometheus_client.py` — Prometheus HTTP wrapper -> returns DataFrame with `timestamp` (seconds since epoch), `instance`, `value`.
- `ml/baseline_detector.py`, `ml/isolation_forest.py` — detector implementations and hyperparameters.
- `docker/Dockerfile.frontend` and `docker/docker-compose.yml` — container build/run definitions.

## Contact & contribution

If you want help cleaning up requirements or refactoring the detectors into a lightweight runtime-only image, open an issue or create a branch and I can help with the follow-up changes and rebuild.

---
Generated on: 2026-07-06

