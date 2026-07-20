# ONGC AI Cluster Monitor

A full-stack AI-powered cluster monitoring system for ONGC field sites. It collects real-time metrics from Linux nodes via SSH and Prometheus, detects anomalies using machine learning, and presents everything through a clean web dashboard.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Recent Updates (July 2026)](#2-recent-updates-july-2026)
3. [Prerequisites](#3-prerequisites)
4. [Step 1 — Clone the Repository](#4-step-1--clone-the-repository)
5. [Step 2 — Configure SSH Keys](#5-step-2--configure-ssh-keys)
6. [Step 3 — Configure Environment Variables](#6-step-3--configure-environment-variables)
7. [Step 4 — Register Your Nodes (UI-Based)](#7-step-4--register-your-nodes-ui-based)
8. [Step 5 — Build and Start with Docker](#8-step-5--build-and-start-with-docker)
9. [Step 6 — Verify Everything is Running](#9-step-6--verify-everything-is-running)
10. [Accessing the Dashboard](#10-accessing-the-dashboard)
11. [Installing Node Exporter on Linux Nodes](#11-installing-node-exporter-on-linux-nodes)
12. [Stopping and Restarting](#12-stopping-andrestarting)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Architecture Overview

```
+----------------------------------------------------------+
|                  Monitoring PC (Windows)                  |
|                                                           |
|  +-----------+   +-----------+   +------------+          |
|  | Frontend  |   |  Backend  |   | Prometheus |          |
|  |  React    |<--| FastAPI   |<--|  + TSDB    |          |
|  |  :3001    |   |  :8000    |   |   :9090    |          |
|  +-----------+   +-----+-----+   +------+-----+          |
|                        |  SSH            | Scrape         |
+------------------------|-----------------|-----------+    |
                         |                 |
         +---------------v-----------------v-----------+
         |              Linux Cluster Nodes            |
         |  Node 1         Node 2          Node 3      |
         | 192.168.x.x  192.168.x.x   192.168.x.x     |
         | node_exporter node_exporter node_exporter   |
         |   :9100          :9100           :9100      |
         +----------------------------------------------+
```

---

## 2. Recent Updates (July 2026)

The application has been finalized with the following production-ready features:

* **Node Management UI (No More YAML Editing):** Nodes are now managed entirely through the dashboard. Click **⚙️ Settings → Add Node**, enter the IP address and SSH username, validate connectivity live, and save. The system automatically updates `config/nodes.yaml` and reloads Prometheus — no terminal or file editing required.

* **Per-Node SSH Usernames:** Every node in the cluster can have a **different SSH username**. This is essential for ONGC field deployments where different Linux servers may have different user accounts (e.g., `arunabh` on node-1, `oilrig` on node-2). The username is set per-node in the Settings UI and stored in the database. The global `SSH_USERNAME` in `.env` serves as a fallback for nodes with no username specified.

* **Machine Learning Baseline Backfilling:** Anomaly detection models (Z-Score, EWMA, Isolation Forest) automatically seed themselves from up to 7 days of historical metric data.

* **Deep OS Metadata via SSH:** The backend fetches `os_version`, `architecture`, and `boot_time` via SSH and displays them in the Node Drilldown UI.

* **Process Sanitization:** Internal monitoring processes are filtered from the Top 20 Process list.

* **Incident Lifecycle Automation:** Resolving an anomaly from the UI cascades to close associated Incidents and Alerts.

---

## 3. Prerequisites

Install the following on the **monitoring PC (Windows)** before you begin:

| Software | Minimum Version | Download Link |
|----------|----------------|---------------|
| **Git** | 2.x | https://git-scm.com/download/win |
| **Docker Desktop** | 4.x | https://www.docker.com/products/docker-desktop |

> **Important:** After installing Docker Desktop, make sure it is **running** (whale icon in the system tray) before proceeding.

---

## 4. Step 1 — Clone the Repository

Open **PowerShell** and run:

```powershell
git clone https://github.com/YOUR_ORG/ongc-cluster-monitor.git
cd ongc-cluster-monitor
```

> Replace `YOUR_ORG` with the actual GitHub organization or username where this repository is hosted.

---

## 5. Step 2 — Configure SSH Keys

The backend connects to Linux nodes over SSH to collect process data. You need an SSH private key that has access to all the nodes.

### Option A — Use an existing key

If you already have an SSH key pair, place your **private key** at:

```
C:\Users\<YourWindowsUsername>\.ssh\id_rsa
```

### Option B — Generate a new key pair

Open PowerShell and run:

```powershell
ssh-keygen -t rsa -b 4096 -f "$env:USERPROFILE\.ssh\id_rsa" -N ""
```

Then copy the **public key** to each Linux node you want to monitor:

```powershell
# Run this for each node — replace USERNAME and NODE_IP
type "$env:USERPROFILE\.ssh\id_rsa.pub" | ssh USERNAME@NODE_IP "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

> Replace `USERNAME` with the Linux username (e.g. `arunabh`) and `NODE_IP` with each node's IP address.

---

## 6. Step 3 — Configure Environment Variables

Copy the example environment file:

```powershell
copy .env.example .env
# On Linux: cp .env.example .env
```

Edit the following values in `.env`:

```ini
# ── Database ────────────────────────────────────────────────────
# Set a strong password (use the same password in both lines below)
POSTGRES_PASSWORD=YourStrongPassword123
DATABASE_URL=postgresql+asyncpg://postgres:YourStrongPassword123@db:5432/ongc

# ── Prometheus ──────────────────────────────────────────────────
PROMETHEUS_URL=http://prometheus:9090

# ── SSH Configuration ───────────────────────────────────────────
# This path is INSIDE the Docker container — do NOT change it
SSH_KEY_PATH=/root/.ssh/id_rsa

# The Linux username used to SSH into the nodes
SSH_USERNAME=your_linux_username

# ── Email Alerts (optional) ─────────────────────────────────────
EMAIL_USER=youremail@gmail.com
EMAIL_PASSWORD=your_gmail_app_password
ALERT_EMAIL_TO=alerts@example.com
```

> **Note on SSH_KEY_PATH:** The value must stay as `/root/.ssh/id_rsa`. This is the path **inside the Docker container**.

> **Note on SSH_USERNAME:** This is the **default fallback** username used for nodes that do not have a specific SSH username configured in the Node Management UI. When you add a node through the Settings page, you can set its own SSH username — this overrides `SSH_USERNAME` for that specific node. This allows each Linux server in your ONGC cluster to have a different login account.

---

## 7. Step 4 — Register Your Nodes (UI-Based)

In this version, **you do not need to manually edit `config/nodes.yaml`**. Nodes are registered through the dashboard's built-in **Settings** page after the stack is running.

> **Existing users:** If you have nodes already in `config/nodes.yaml`, they are automatically imported into the database on first startup — nothing will be lost.

### Step-by-step: Adding a node from the UI

1. Open the dashboard at **http://localhost:3001**
2. Click **⚙️ Settings** in the top-right corner of the navigation bar
3. Click **+ Add Node**
4. Fill in the form:

   | Field | Example | Notes |
   |-------|---------|-------|
   | **IP Address** | `192.168.1.101` | Required. Must be reachable from the monitoring PC |
   | **Label** | `node-1` | Optional friendly display name |
   | **SSH Username** | `arunabh` | The Linux username for SSH on this specific node |
   | **Port** | `9100` | Default — only change if node_exporter runs on a different port |

5. Click **🔍 Validate Connectivity** — this runs three checks:
   - ✅ **Ping** — ICMP reachability
   - ✅ **Port 9100** — TCP connection to node_exporter
   - ✅ **SSH** — Key-based login test

6. Click **💾 Save Node** — the system will:
   - Save the node to PostgreSQL
   - Regenerate `config/nodes.yaml`
   - Reload Prometheus (via `/-/reload`)
   - Begin monitoring within ~60 seconds (one collection cycle)

### Per-node SSH usernames

Each node can have its own SSH username — this is critical in ONGC environments where different servers may be set up with different accounts:

```
Node 192.168.1.101  →  SSH username: arunabh   (set in the UI)
Node 192.168.1.102  →  SSH username: oilrig    (set in the UI)
Node 192.168.1.103  →  SSH username: (empty)   →  falls back to SSH_USERNAME in .env
```

Behind the scenes the system uses the per-node username for:
- SSH process collection
- SSH health checks
- SSH connectivity validation

---

## 8. Step 5 — Build and Start with Docker

From the project root directory in PowerShell, run:

```powershell
docker compose up --build -d
```

This single command will:

| Step | What happens |
|------|-------------|
| Pull images | Downloads Python, Node.js, PostgreSQL, Prometheus base images (first time only) |
| Build backend | Compiles the FastAPI + ML Python image |
| Build frontend | Compiles the React production bundle |
| Start services | Launches all 5 containers in the background |

> **First build time:** 5–10 minutes depending on internet speed (downloads ~500 MB of dependencies).
> **Subsequent starts:** Under 30 seconds (uses cached layers).

To watch the build in real time (optional):

```powershell
docker compose up --build
```

(without `-d` so output prints to terminal — press `Ctrl+C` to stop watching, containers keep running)

---

## 8. Step 6 — Verify Everything is Running

```powershell
docker compose ps
```

Expected output — all containers should show **Up**:

```
NAME                 IMAGE                           STATUS
ongc_backend         ongc-cluster-monitor-backend    Up
ongc_frontend        ongc-cluster-monitor-frontend   Up
ongc_node_exporter   prom/node-exporter:latest       Up
ongc_postgres        postgres:15-alpine              Up (healthy)
ongc_prometheus      prom/prometheus:latest          Up
```

If any container shows **Exit** or **Restarting**, check its logs:

```powershell
docker compose logs backend     # backend errors
docker compose logs db          # database errors
docker compose logs frontend    # frontend errors
```

---

## 9. Accessing the Dashboard

Once all containers are running, open a browser on the monitoring PC:

| Service | URL | Description |
|---------|-----|-------------|
| **Main Dashboard** | http://localhost:3001 | React UI — cluster monitoring |
| **API** | http://localhost:8000 | FastAPI backend |
| **API Docs** | http://localhost:8000/docs | Swagger interactive API docs |
| **Prometheus** | http://localhost:9090 | Raw Prometheus queries |

---

## 10. Installing Node Exporter on Linux Nodes

Each Linux node being monitored must have **node_exporter** running. Connect to each node via SSH and run:

```bash
# 1. Download node_exporter
wget https://github.com/prometheus/node_exporter/releases/download/v1.7.0/node_exporter-1.7.0.linux-amd64.tar.gz

# 2. Extract
tar xvf node_exporter-1.7.0.linux-amd64.tar.gz

# 3. Move binary to system path
sudo mv node_exporter-1.7.0.linux-amd64/node_exporter /usr/local/bin/

# 4. Create a systemd service (auto-starts on boot)
sudo tee /etc/systemd/system/node_exporter.service > /dev/null <<EOF
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
User=nobody
ExecStart=/usr/local/bin/node_exporter
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

# 5. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable node_exporter
sudo systemctl start node_exporter

# 6. Check status
sudo systemctl status node_exporter
```

**Verify it's working** — from the monitoring PC, open a browser and go to:

```
http://<NODE_IP>:9100/metrics
```

You should see a page of Prometheus metrics text.

> **Firewall note:** Ensure port **9100** is open on each Linux node for incoming TCP connections from the monitoring PC's IP address.

---

## 11. Stopping and Restarting

```powershell
# Stop all containers (data is preserved in Docker volumes)
docker compose down

# Start again (very fast — no rebuild needed)
docker compose up -d

# Full rebuild (use after code changes)
docker compose up --build -d

# Rebuild only the backend (faster if only backend changed)
docker compose up --build -d backend

# View live logs from all services
docker compose logs -f

# View logs from one service
docker compose logs -f backend
```

---

## 12. Troubleshooting

### PING shows "ping subprocess error"
The backend container cannot reach the node.
- Check the node is powered on and reachable: `ping 192.168.56.101` from the monitoring PC
- Check that a firewall is not blocking ICMP on the node

---

### SSH shows "No such file or directory: '/root/.ssh/id_rsa'"
The SSH key is not mounted into the container.

1. Verify the key exists: `dir "$env:USERPROFILE\.ssh\id_rsa"`
2. Check `docker-compose.yml` has the correct Windows username in the volume path
3. Rebuild after fixing: `docker compose up --build -d backend`

---

### SSH shows "Permission denied (publickey)"
The public key hasn't been authorized on the Linux node. Run:

```powershell
type "$env:USERPROFILE\.ssh\id_rsa.pub" | ssh USERNAME@NODE_IP "cat >> ~/.ssh/authorized_keys"
```

---

### "No process data available" in dashboard
Process data is collected via SSH. If SSH is failing, this tab will be empty. Fix the SSH issue above first.

---

### A container keeps restarting
Check its logs for the error:

```powershell
docker compose logs --tail=50 backend
```

---

### Port already in use
If a port conflicts with another application, edit the left-hand port in `docker-compose.yml`:

```yaml
ports:
  - "3002:3000"   # Changed frontend from 3001 to 3002
```

Then restart: `docker compose up -d`

---

### Docker Desktop not running
You will see: `error during connect: ... pipe/docker_engine`

Start Docker Desktop from the Start Menu. Wait for the whale icon to appear in the system tray (can take 1–2 minutes), then retry.

---

## 13. Deploying the Monitoring Stack on Linux

If you decide to deploy this entire monitoring stack (Docker Compose) on a **Linux machine** instead of Windows, make the following two adjustments to `docker-compose.yml`:

1. **SSH Mount:** Under the `backend` service, use `~/.ssh` instead of `C:/Users/...`:
   ```yaml
     volumes:
       - ./config:/app/config:ro
       - ~/.ssh:/root/.ssh:ro
   ```

2. **Node Exporter (Optional):** If you want to monitor the main Linux monitoring node itself, update the `node_exporter` service in `docker-compose.yml` to mount the host filesystem properly (this doesn't work on Windows, which is why it's omitted by default):
   ```yaml
     node_exporter:
       image: prom/node-exporter:latest
       container_name: ongc_node_exporter
       ports:
         - "9100:9100"
       restart: unless-stopped
       volumes:
         - /proc:/host/proc:ro
         - /sys:/host/sys:ro
         - /:/rootfs:ro
       command:
         - '--path.procfs=/host/proc'
         - '--path.sysfs=/host/sys'
         - '--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)'
         - '--collector.logind'
   ```

---

## Quick Reference Card

```powershell
# ── First-time setup ─────────────────────────────────────────
git clone <repo-url>
cd ongc-cluster-monitor
copy .env.example .env
# Edit .env  →  set passwords, SSH_USERNAME (default/fallback)
# Edit docker-compose.yml  →  update Windows username in SSH mount
docker compose up --build -d
# Then: open http://localhost:3001 → ⚙️ Settings → Add Node

# ── Adding/removing nodes ─────────────────────────────────────
# Open http://localhost:3001 → click ⚙️ Settings
# Use Add Node / Edit / Delete buttons in the UI
# No YAML editing needed — Prometheus reloads automatically

# ── Daily use ────────────────────────────────────────────────
docker compose up -d          # Start
docker compose down           # Stop
docker compose ps             # Check status
docker compose logs -f        # Live logs
```

---

*Developed for ONGC Cinnamara, Jorhat — AI-Powered Cluster Monitoring System*
