DEPLOYMENT GUIDE — ONGC Cluster Monitor

Purpose
- This document explains how to deploy the ONGC Cluster Monitor stack on a Linux server (recommended: Ubuntu 22.04 LTS / Debian 12). It covers prerequisites, Docker Compose deployment, environment configuration, persistence, networking, basic troubleshooting, and production hardening.

Assumptions
- You have root or sudo access on the target server ("ONGC server").
- The server has internet access to pull Docker images.
- You want to run the full stack via Docker Compose included with the repo.

Quick summary (commands you'll run)
```bash
# on the server (as a sudo user)
# 1. install docker & compose
sudo apt update && sudo apt install -y ca-certificates curl gnupg lsb-release
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# log out + log in again or restart shell
# 2. clone repo
git clone <repo-url> ongc-cluster-monitor && cd ongc-cluster-monitor
# 3. copy .env and edit secrets
cp .env.example .env
# 4. run (build & start)
docker compose up -d --build
# 5. check logs
docker compose logs -f backend
```

1) Server prerequisites
- OS: Ubuntu 22.04 LTS or equivalent (other distros OK but commands differ).
- Recommended: 2+ CPU, 4+ GB RAM, disk depending on Prometheus retention.
- Open firewall ports you need: 80/443 (if exposing UI), 8000 (backend, internal), 9090 (Prometheus if external), 3001 (frontend host port if you expose it), 9100 (node exporter on nodes).

2) Install Docker & Docker Compose
- Install Docker (recommended script) or follow distro instructions:
  - curl -fsSL https://get.docker.com | sh
  - Add your deploy user to `docker` group: `sudo usermod -aG docker deployuser`
- Install Docker Compose plugin (if not present):
  - On modern Docker, use `docker compose` (plugin); else install `docker-compose`.

3) Clone repository
- Example:
  - `git clone https://github.com/<your-org>/ongc-cluster-monitor.git`
  - `cd ongc-cluster-monitor`

4) Configure environment (`.env`)
- Copy example and edit secrets and host-specific settings:
  - `cp .env.example .env` (or edit existing `.env`) — the repo contains `.env` used in dev; for production maintain secret store instead.
- Important variables to set in `.env`:
  - `POSTGRES_PASSWORD` (if using bundled postgres in compose) or set `DATABASE_URL` to external DB connection string.
  - `PROMETHEUS_URL`: if running Prometheus in the same compose, set `http://prometheus:9090`.
  - `SSH_KEY_PATH`: set to the absolute path where private key will be available inside the backend runtime. If you plan to run backend in Docker, you will mount the private key path from the host into the backend container and set this value accordingly (e.g. `/root/.ssh/id_rsa`). If you do not want SSH checks, set this value to empty to skip SSH checks.
  - `SSH_USERNAME`: username used to connect to nodes.
  - `COLLECT_INTERVAL`, `HEALTH_CHECK_INTERVAL`, `WARMUP_PERIOD_SECONDS` as needed.

5) Preparing SSH key for backend (if you want SSH health checks)
- On the server, create or copy the private key to a secure path, e.g. `/home/deployuser/.ssh/id_rsa`.
- Secure the key: `chmod 600 /home/deployuser/.ssh/id_rsa`
- Update `docker-compose.yml` for the backend service mount (example in repo already mounts `C:/Users/aruna/.ssh` for Windows; on Linux change to host path):
```yaml
services:
  backend:
    volumes:
      - /home/deployuser/.ssh/id_rsa:/root/.ssh/id_rsa:ro
    environment:
      - SSH_KEY_PATH=/root/.ssh/id_rsa
      - SSH_USERNAME=ubuntu
```
- If you do not want to mount the file, leave `SSH_KEY_PATH` empty in `.env` and SSH checks will be skipped.

6) Persistent storage (volumes)
- The Compose file already defines volumes for `postgres_data` and `prometheus_data`. These are persisted on the Docker host. Ensure the host disk has enough space for Prometheus TSDB retention.
- If you need custom locations, edit `docker-compose.yml` and change volume mapping or use named volumes pointing to host paths.

7) Starting the stack
- From the repo root:
```bash
docker compose pull
docker compose up -d --build
```
- Check service health and logs:
```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f prometheus
```

8) Verify endpoints
- Backend status: `curl http://127.0.0.1:8000/api/status`
- Nodes: `curl http://127.0.0.1:8000/api/cluster/nodes`
- Prometheus UI (if listening on host): `http://<server-ip>:9090`

9) Optional: run backend outside Docker (virtualenv)
- For development or debugging, you can run `uvicorn` directly on the server using a Python virtualenv. Make sure `.env` is configured for host values (DB pointing to service or host).
- Activate venv, then run:
```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

10) Systemd service (recommended for production)
- Create a systemd unit to keep the stack running on boot. Example for running `docker compose up` as `deployuser`:
```ini
[Unit]
Description=ONGC Monitor Compose
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/ongc-cluster-monitor
RemainAfterExit=yes
User=deployuser
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```
- Save as `/etc/systemd/system/ongc-monitor.service`, then `sudo systemctl daemon-reload`, `sudo systemctl enable --now ongc-monitor`.

11) Database reset & migrations
- The backend will auto-create tables on startup (see `backend.db.init_db`). For ad-hoc resets (development only) use the provided script `backend/scripts/reset_db.py`. Run inside the backend container or a virtualenv where the repo is available.
- Example inside container:
```bash
docker compose exec backend python backend/scripts/reset_db.py
```

12) Prometheus & node_exporter setup
- The repo uses a Prometheus container and a node_exporter container. For monitoring remote nodes, install `node_exporter` on each node and add their `targets` to `config/nodes.yaml`.
- Ensure firewall allows port `9100` from Prometheus to the nodes.
- Example `config/nodes.yaml` entries:
```yaml
- name: local
  targets:
    - 192.168.56.101:9100
    - 192.168.56.102:9100
```

13) Reverse proxy & TLS (recommended)
- Use Nginx or Caddy in front of the frontend/backend to provide HTTPS and route paths.
- Example Nginx snippet (proxy to backend on port 8000):
```
location /api/ {
    proxy_pass http://127.0.0.1:8000/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

14) Troubleshooting
- Port in use: `sudo netstat -tulpn | grep :9090` (or `docker compose ps`) — if Docker Prometheus is running, stop it before running a local binary.
- Backend logs: `docker compose logs backend --tail 200 -f` or `docker compose exec backend tail -n 200 /path/to/log`.
- Health checker SSH error: ensure `SSH_KEY_PATH` matches mounted key path and key file permissions are strict.
- Prometheus targets show `down`: check `config/nodes.yaml` and network connectivity from Prometheus container to node_exporter ports.

15) Backups & maintenance
- Stop stack: `docker compose down` (note: this will stop containers; data volumes remain).
- Backup Postgres: `docker exec -t ongc_postgres pg_dumpall -c -U postgres > dump.sql`
- Backup Prometheus TSDB via volume snapshots or use remote_write for long-term storage.

16) Security notes
- Do not commit `.env` with secrets to source control.
- Use `docker secrets` or external secrets manager for production secrets.
- Ensure the SSH private key has `600` permissions.
- Avoid mounting host root ("/") into containers. Mount only the needed key file or config directories.

17) Useful commands
- `docker compose ps`
- `docker compose logs -f backend`
- `docker compose exec backend python -c "import os; print(os.getenv('SSH_KEY_PATH'))"`
- `curl http://127.0.0.1:8000/api/status`
- `curl http://127.0.0.1:9090/api/v1/targets | jq .` (verify Prometheus targets)

18) If something fails
- Collect logs from `backend`, `prometheus`, `db` and `node_exporter` containers and attach them for debugging.
- Check network/firewall between Prometheus and node_exporters.

Questions & next steps
- Do you want a systemd unit file created and added to the repo?
- Do you want me to prepare a secure `docker-compose.prod.yml` variant and an example `nginx` site config for TLS?


End of guide.
