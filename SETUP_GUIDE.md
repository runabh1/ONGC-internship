# ONGC AI Cluster Monitor — Complete Setup Guide

> **Monitoring PC:** Windows 10/11
> **Nodes being monitored:** Linux machines (Ubuntu / CentOS / RHEL etc.)

This document walks you through **every single step** — from a fresh Windows PC with nothing installed, all the way to a fully running monitoring dashboard with Docker. Follow in order, do not skip steps.

---

## Table of Contents

1. [Phase 0 — Install Prerequisites on Windows](#phase-0--install-prerequisites-on-windows)
2. [Phase 1 — Clone the Repository](#phase-1--clone-the-repository)
3. [Phase 2 — Set Up SSH Keys](#phase-2--set-up-ssh-keys)
4. [Phase 3 — Install Node Exporter on Each Linux Node](#phase-3--install-node-exporter-on-each-linux-node)
5. [Phase 4 — Configure the Project Files](#phase-4--configure-the-project-files)
6. [Phase 5 — Build and Launch with Docker](#phase-5--build-and-launch-with-docker)
7. [Phase 6 — Verify Everything is Running](#phase-6--verify-everything-is-running)
8. [Phase 7 — Access the Dashboard](#phase-7--access-the-dashboard)
9. [Daily Operations Cheatsheet](#daily-operations-cheatsheet)
10. [Troubleshooting](#troubleshooting)

---

## Phase 0 — Install Prerequisites on Windows

You need **two programs** on your Windows monitoring PC before anything else.

---

### 0.1 Install Git

1. Go to: https://git-scm.com/download/win
2. Download the **64-bit** installer and run it
3. Accept all defaults during installation (click Next → Next → Install)
4. When done, open **PowerShell** and verify:

```powershell
git --version
```

Expected output (version may differ):
```
git version 2.45.0.windows.1
```

---

### 0.2 Install Docker Desktop

1. Go to: https://www.docker.com/products/docker-desktop
2. Download **Docker Desktop for Windows** and run the installer
3. During install:
   - Keep **"Use WSL 2 instead of Hyper-V"** checked (recommended)
   - Keep **"Add Docker to PATH"** checked
4. After installation, **restart your PC** if prompted
5. Open Docker Desktop from the Start Menu — wait until you see the **whale icon** in the system tray (bottom-right corner). This takes 1-2 minutes on first launch.

Verify Docker is working — open PowerShell and run:

```powershell
docker --version
docker compose version
```

Expected output:
```
Docker version 26.x.x, build ...
Docker Compose version v2.x.x
```

> **IMPORTANT:** Docker Desktop must be **running** (whale icon visible in tray) every time you want to start the monitoring system. If Docker is not running you will get `error during connect: ... pipe/docker_engine`.

---

## Phase 1 — Clone the Repository

Open **PowerShell** (press `Win + X` -> Windows PowerShell or Terminal).

Navigate to where you want to store the project. For example, your Desktop:

```powershell
cd "$env:USERPROFILE\Desktop"
```

Clone the repository:

```powershell
git clone https://github.com/YOUR_ORG/ongc-cluster-monitor.git
```

> Replace `YOUR_ORG` with the actual GitHub username or organization that owns this repo.

Move into the project folder:

```powershell
cd ongc-cluster-monitor
```

Confirm the files are there:

```powershell
dir
```

You should see files like `.env.example`, `docker-compose.yml`, `backend/`, `frontend/`, `config/` etc.

---

## Phase 2 — Set Up SSH Keys

The backend connects to each Linux node over **SSH** to collect process-level data (top 20 processes, OS info, etc.). You need an SSH key pair for this.

---

### 2.1 Check if you already have an SSH key

```powershell
dir "$env:USERPROFILE\.ssh"
```

If you see `id_rsa` and `id_rsa.pub` already listed — skip to step 2.3.

---

### 2.2 Generate a new SSH key pair

If you do NOT have keys yet, generate them:

```powershell
ssh-keygen -t rsa -b 4096 -f "$env:USERPROFILE\.ssh\id_rsa" -N ""
```

- `-t rsa`  : RSA algorithm
- `-b 4096` : 4096-bit key (strong)
- `-f ...`  : save location (Windows .ssh folder)
- `-N ""`   : no passphrase (required so Docker can use the key without prompts)

This creates two files:
- `C:\Users\<YourUsername>\.ssh\id_rsa`     → private key (keep secret, never share)
- `C:\Users\<YourUsername>\.ssh\id_rsa.pub` → public key (copy to Linux nodes)

---

### 2.3 Copy the public key to each Linux node

Run this command **once for each Linux node**, replacing `USERNAME` and `NODE_IP`:

```powershell
type "$env:USERPROFILE\.ssh\id_rsa.pub" | ssh USERNAME@NODE_IP "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh"
```

**Replace:**
- `USERNAME` → the Linux username on that node (e.g. `arunabh`, `root`, `ongc`)
- `NODE_IP`  → the IP address of the Linux node (e.g. `192.168.1.101`)

You will be asked for the Linux user's **password** this one time. After this, future connections will use the key (no password needed).

**Example for 3 nodes:**

```powershell
type "$env:USERPROFILE\.ssh\id_rsa.pub" | ssh arunabh@192.168.1.101 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
type "$env:USERPROFILE\.ssh\id_rsa.pub" | ssh arunabh@192.168.1.102 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
type "$env:USERPROFILE\.ssh\id_rsa.pub" | ssh arunabh@192.168.1.103 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

---

### 2.4 Test the SSH connection (no password prompt = success)

```powershell
ssh -i "$env:USERPROFILE\.ssh\id_rsa" USERNAME@NODE_IP "hostname"
```

You should see the hostname of the Linux node printed with NO password prompt. If it asks for a password, the key was not copied correctly — repeat step 2.3.

---

## Phase 3 — Install Node Exporter on Each Linux Node

**Node Exporter** is a small program that runs on each Linux node and exposes system metrics (CPU, RAM, disk, network) on port **9100** for Prometheus to scrape.

SSH into each Linux node from PowerShell:

```powershell
ssh USERNAME@NODE_IP
```

Once inside the Linux node, run the following commands one by one:

```bash
# Step 1: Download node_exporter
wget https://github.com/prometheus/node_exporter/releases/download/v1.7.0/node_exporter-1.7.0.linux-amd64.tar.gz

# Step 2: Extract the archive
tar xvf node_exporter-1.7.0.linux-amd64.tar.gz

# Step 3: Move binary to system path
sudo mv node_exporter-1.7.0.linux-amd64/node_exporter /usr/local/bin/

# Step 4: Clean up downloaded files
rm -rf node_exporter-1.7.0.linux-amd64 node_exporter-1.7.0.linux-amd64.tar.gz

# Step 5: Create a dedicated system user (security best practice)
sudo useradd --no-create-home --shell /bin/false node_exporter 2>/dev/null || true
sudo chown node_exporter:node_exporter /usr/local/bin/node_exporter

# Step 6: Create the systemd service file
sudo tee /etc/systemd/system/node_exporter.service > /dev/null <<EOF
[Unit]
Description=Prometheus Node Exporter
Documentation=https://github.com/prometheus/node_exporter
After=network.target

[Service]
User=node_exporter
Group=node_exporter
Type=simple
ExecStart=/usr/local/bin/node_exporter
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Step 7: Reload systemd and enable the service (starts automatically on boot)
sudo systemctl daemon-reload
sudo systemctl enable node_exporter
sudo systemctl start node_exporter

# Step 8: Check the status — should show "active (running)"
sudo systemctl status node_exporter
```

Expected output of `systemctl status`:
```
node_exporter.service - Prometheus Node Exporter
     Loaded: loaded (/etc/systemd/system/node_exporter.service; enabled; ...)
     Active: active (running) since ...
```

---

### 3.1 Open firewall port 9100 on each Linux node

**If using firewalld (CentOS / RHEL / Fedora):**

```bash
sudo firewall-cmd --permanent --add-port=9100/tcp
sudo firewall-cmd --reload
```

**If using ufw (Ubuntu / Debian):**

```bash
sudo ufw allow 9100/tcp
sudo ufw reload
```

**Verify port 9100 is reachable from the Windows monitoring PC:**

```powershell
Test-NetConnection -ComputerName NODE_IP -Port 9100
```

Expected:
```
TcpTestSucceeded : True
```

Also open a browser on the Windows PC and go to `http://NODE_IP:9100/metrics` — you should see a wall of Prometheus metric text.

> **Repeat Phase 3 for every Linux node you want to monitor.**

---

## Phase 4 — Configure the Project Files

All config changes are made on the **Windows monitoring PC**, inside the cloned project folder.

---

### 4.1 Create the .env file

In PowerShell, inside the project folder:

```powershell
copy .env.example .env
```

Open `.env` in Notepad (or any text editor) and set the values below.

The complete `.env` file should look like this:

```ini
# ============================================================
# ONGC AI Cluster Monitor — Environment Configuration
# ============================================================

# --- Database ---
# IMPORTANT: Use "db" as the hostname (that is the Docker service name)
DATABASE_URL=postgresql+asyncpg://postgres:YourStrongPassword@db:5432/ongc
POSTGRES_PASSWORD=YourStrongPassword

# --- Prometheus ---
# Do NOT change this — prometheus is the Docker service name
PROMETHEUS_URL=http://prometheus:9090

# --- Collector ---
COLLECT_INTERVAL=60

# --- Health Checker ---
HEALTH_CHECK_INTERVAL=30
NODE_EXPORTER_PORT=9100

# --- Warmup ---
# Seconds a new node spends in WARMUP before anomaly detection activates
WARMUP_PERIOD_SECONDS=300

# --- Anomaly Thresholds ---
CPU_CRITICAL_PCT=95
CPU_HIGH_PCT=85
CPU_MEDIUM_PCT=75
MEM_CRITICAL_PCT=95
MEM_HIGH_PCT=90
MEM_MEDIUM_PCT=80
DISK_CRITICAL_PCT=95
DISK_HIGH_PCT=85
IOWAIT_HIGH_PCT=40
IOWAIT_MEDIUM_PCT=20
LOAD_CRITICAL=30
LOAD_HIGH=15
LOAD_MEDIUM=8

# --- SSH ---
# DO NOT CHANGE SSH_KEY_PATH — it is the path inside the Docker container
SSH_KEY_PATH=/root/.ssh/id_rsa
# CHANGE THIS: put your Linux SSH username here
SSH_USERNAME=arunabh

# --- Email Alerts (optional — leave blank to disable) ---
EMAIL_USER=
EMAIL_PASSWORD=
ALERT_EMAIL_TO=
```

> **Critical — Do NOT change `SSH_KEY_PATH`**. The value `/root/.ssh/id_rsa` is the path inside the Docker container. Your Windows `.ssh` folder is mounted into the container automatically (configured in the next step).

---

### 4.2 Update docker-compose.yml — set your Windows username

Open `docker-compose.yml` in a text editor. Find the `backend:` service and its `volumes:` block:

```yaml
  backend:
    ...
    volumes:
      - ./config:/app/config:ro
      - "C:/Users/aruna/.ssh:/root/.ssh:ro"   ← Change "aruna" to YOUR Windows username
```

Find your Windows username:

```powershell
echo $env:USERNAME
```

Change `aruna` to your actual username. Example — if your username is `john`:

```yaml
      - "C:/Users/john/.ssh:/root/.ssh:ro"
```

Save the file.

---

### 4.3 Update config/nodes.yaml — add your Linux node IPs

Open `config/nodes.yaml` in a text editor and replace the example IPs with your real node IPs:

```yaml
- labels:
    job: node_exporter
  targets:
    - '192.168.1.101:9100'   # Node 1 — replace with your actual IP
    - '192.168.1.102:9100'   # Node 2 — replace with your actual IP
    - '192.168.1.103:9100'   # Node 3 — replace with your actual IP
    # Add more lines here for additional nodes
```

> The port must stay `:9100` — that is where node_exporter listens.

---

### 4.4 Summary of files you edited

| File | What you changed |
|------|-----------------|
| `.env` | Set `SSH_USERNAME`, `POSTGRES_PASSWORD`, `DATABASE_URL` |
| `docker-compose.yml` | Changed Windows username in SSH volume mount |
| `config/nodes.yaml` | Put your actual Linux node IP addresses |

---

## Phase 5 — Build and Launch with Docker

Make sure:
- Docker Desktop is running (whale icon in tray)
- You are inside the project folder in PowerShell

```powershell
# Confirm you are in the right directory
pwd
# Should print something like: C:\Users\aruna\Desktop\ongc-cluster-monitor
```

Run the single build-and-launch command:

```powershell
docker compose up --build -d
```

**What happens step by step:**

| Step | What happens | Approx. time |
|------|-------------|--------------|
| Pull `postgres:15-alpine` | Downloads PostgreSQL image from Docker Hub | 1-2 min (first time only) |
| Pull `prom/prometheus:latest` | Downloads Prometheus image | 1-2 min (first time only) |
| Pull `prom/node-exporter:latest` | Downloads Node Exporter image | ~30 sec (first time only) |
| Pull `python:3.12-slim` | Downloads Python base image for backend | 1-2 min (first time only) |
| Pull `node:20-alpine` | Downloads Node.js base image for frontend | 1-2 min (first time only) |
| **Build backend image** | Installs FastAPI, SQLAlchemy, asyncssh, ML libs etc. | 3-6 min |
| **Build frontend image** | Runs `npm ci` + `npm run build` for React | 2-4 min |
| Start all 5 containers | Launches db, prometheus, node_exporter, backend, frontend | ~30 sec |

> First build total time: **10-15 minutes** depending on internet speed (downloads ~600 MB)
> Subsequent starts (no `--build`): **under 30 seconds** (uses Docker layer cache)

---

### 5.1 Watch the build in real time (optional)

If you want to see exactly what Docker is doing during the build, omit the `-d` flag:

```powershell
docker compose up --build
```

Build logs will print to your terminal. Press `Ctrl+C` when done watching — the containers keep running in the background.

---

### 5.2 What each container does

| Container | Image | Port | Role |
|-----------|-------|------|------|
| `ongc_postgres` | `postgres:15-alpine` | 5433 (host) | Stores all metrics, anomalies, incidents |
| `ongc_prometheus` | `prom/prometheus:latest` | 9090 | Scrapes node_exporter every 15 seconds |
| `ongc_node_exporter` | `prom/node-exporter:latest` | 9100 | Minimal local node exporter (Windows-safe) |
| `ongc_backend` | Custom Python 3.12 | 8000 | FastAPI API + ML engine + SSH collector |
| `ongc_frontend` | Custom Node 20 | 3001 | React dashboard (served as static build) |

---

## Phase 6 — Verify Everything is Running

### 6.1 Check container status

```powershell
docker compose ps
```

All 5 containers should show `running` or `Up`:

```
NAME                  IMAGE                            STATUS
ongc_postgres         postgres:15-alpine               Up (healthy)
ongc_prometheus       prom/prometheus:latest           Up
ongc_node_exporter    prom/node-exporter:latest        Up
ongc_backend          ongc-cluster-monitor-backend     Up
ongc_frontend         ongc-cluster-monitor-frontend    Up
```

`ongc_postgres` specifically should show **(healthy)** — the backend waits for this before connecting.

---

### 6.2 Check backend startup logs

```powershell
docker compose logs backend --tail=50
```

Look for these lines confirming successful startup:

```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

If you see database connection errors, wait 30 seconds and check again (DB might still be initializing).

---

### 6.3 Test the API is responding

```powershell
Invoke-WebRequest -Uri http://localhost:8000/health -UseBasicParsing
```

Expected: `StatusCode : 200`

Or open a browser: http://localhost:8000/docs — you should see the Swagger API docs.

---

### 6.4 Check Prometheus is scraping your Linux nodes

Open a browser: http://localhost:9090/targets

Your Linux nodes should appear. If State shows **UP** — Prometheus is scraping them successfully.
If State shows **DOWN** — there is a network or firewall issue (see Troubleshooting).

---

## Phase 7 — Access the Dashboard

Once all containers are running, open a browser on the Windows monitoring PC:

| Service | URL | Description |
|---------|-----|-------------|
| **Main Dashboard** | http://localhost:3001 | React monitoring UI |
| **API** | http://localhost:8000 | FastAPI backend |
| **API Docs (Swagger)** | http://localhost:8000/docs | Interactive API documentation |
| **Prometheus UI** | http://localhost:9090 | Raw metric queries |

The dashboard starts populating with data within **60 seconds** (one collection cycle). Anomaly detection becomes accurate after **5 minutes** (warmup period ends).

---

## Daily Operations Cheatsheet

```powershell
# ─── Start the system (after PC restart) ───────────────────────────────
docker compose up -d

# ─── Stop the system (data is preserved in Docker volumes) ─────────────
docker compose down

# ─── Check if everything is running ────────────────────────────────────
docker compose ps

# ─── View live logs from all services ──────────────────────────────────
docker compose logs -f

# ─── View logs from one service ────────────────────────────────────────
docker compose logs -f backend
docker compose logs -f db
docker compose logs -f frontend

# ─── Rebuild after code changes ────────────────────────────────────────
docker compose up --build -d

# ─── Rebuild only the backend (faster if only backend changed) ─────────
docker compose up --build -d backend

# ─── Restart one service without rebuilding ────────────────────────────
docker compose restart backend

# ─── Open a shell inside a running container (for debugging) ───────────
docker exec -it ongc_backend bash
docker exec -it ongc_postgres psql -U postgres -d ongc

# ─── Nuclear reset (DELETES ALL DATA including the database!) ──────────
docker compose down -v
docker compose up --build -d
```

> WARNING: `docker compose down -v` deletes the postgres_data and prometheus_data volumes — all historical metrics will be permanently lost. Only use this for a completely fresh start.

---

## Troubleshooting

---

### Docker Desktop not running

**Error:** `error during connect: ... The system cannot find the file specified`

**Fix:** Open Docker Desktop from the Start Menu. Wait for the whale icon to appear in the system tray. Can take 1-2 minutes on first launch.

---

### A container exits immediately or keeps restarting

**Check its logs:**

```powershell
docker compose logs --tail=100 backend
docker compose logs --tail=100 db
```

Common cause: `backend` starts slightly before `db` is fully ready. Wait 30 seconds and run `docker compose ps` again. If still restarting, the logs will print the exact error.

---

### SSH key not found inside the container

**Error in backend logs:** `No such file or directory: '/root/.ssh/id_rsa'`

**Fix:**

Step 1 — Verify the key exists on Windows:
```powershell
dir "$env:USERPROFILE\.ssh\id_rsa"
```

Step 2 — Verify docker-compose.yml has your correct Windows username:
```yaml
- "C:/Users/YOUR_ACTUAL_USERNAME/.ssh:/root/.ssh:ro"
```

Step 3 — Rebuild:
```powershell
docker compose up --build -d backend
```

---

### SSH: Permission denied (publickey)

**Error:** `Permission denied (publickey)` in backend logs when trying to connect to a Linux node.

**Fix:** The public key was not authorized on that node. Run:

```powershell
type "$env:USERPROFILE\.ssh\id_rsa.pub" | ssh USERNAME@NODE_IP "cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Then test:
```powershell
ssh -i "$env:USERPROFILE\.ssh\id_rsa" USERNAME@NODE_IP "echo Connection OK"
```

---

### Node shows as DOWN in Prometheus

Open http://localhost:9090/targets — node appears with State: DOWN.

| Cause | Fix |
|-------|-----|
| node_exporter not running on Linux node | SSH into node: `sudo systemctl start node_exporter` |
| Port 9100 blocked by firewall | SSH into node: `sudo firewall-cmd --add-port=9100/tcp --permanent && sudo firewall-cmd --reload` |
| Wrong IP in config/nodes.yaml | Edit `config/nodes.yaml` with correct IP, then: `docker compose restart prometheus` |
| Node is powered off | Power on the node |

---

### No process data shown in dashboard

Process data comes exclusively from SSH. If SSH is failing, the Processes tab will be empty. Fix the SSH connection first (see above).

---

### Port already in use

**Error:** `Bind for 0.0.0.0:3001 failed: port is already allocated`

Edit `docker-compose.yml` and change the LEFT port number (the right side is inside Docker):

```yaml
# Frontend — change left port from 3001 to something else
ports:
  - "3002:3000"

# Backend — change left port from 8000 to something else
ports:
  - "8001:8000"
```

Then: `docker compose up -d`

Access dashboard at the new port: http://localhost:3002

---

### Build fails with "no space left on device"

Docker is out of disk space. Clean up:

```powershell
# Remove unused images, stopped containers, unused networks
docker system prune -f

# Also remove unused volumes (CAREFUL — removes data from stopped containers)
docker volume prune -f
```

Then retry: `docker compose up --build -d`

---

### Prometheus targets show no data / empty dashboard after startup

Wait at least 60 seconds after first launch — the backend needs one full collection cycle.

If still empty after 2 minutes:
1. Check http://localhost:9090/targets — are nodes UP?
2. Check backend logs: `docker compose logs backend --tail=50`
3. Make sure `config/nodes.yaml` has the correct IPs

---

## Architecture Reference

```
  Windows Monitoring PC
  +-----------------------------------------------------------+
  |                                                           |
  |  +-----------+   +-----------+   +------------+          |
  |  | Frontend  |   |  Backend  |   | Prometheus |          |
  |  |  React    |<--| FastAPI   |<--|  + TSDB    |          |
  |  |  :3001    |   |  :8000    |   |   :9090    |          |
  |  +-----------+   +-----+-----+   +------+-----+          |
  |                        |  SSH            | Scrape         |
  |                  +-----+-----+           |               |
  |                  | PostgreSQL|           |               |
  |                  |  :5432    |           |               |
  |                  +-----------+           |               |
  +------------------------------------------|---------------+
                                             |
       +--------------------------------------v--------------+
       |              Linux Cluster Nodes                     |
       |  Node 1           Node 2           Node 3           |
       | 192.168.x.x     192.168.x.x     192.168.x.x        |
       | node_exporter   node_exporter   node_exporter       |
       |   :9100           :9100           :9100             |
       +------------------------------------------------------+
```

---

## Pre-Flight Checklist

Before running `docker compose up --build -d`, tick every item:

- [ ] Git is installed (`git --version` works in PowerShell)
- [ ] Docker Desktop is installed and **running** (whale icon visible in tray)
- [ ] Repository is cloned and you are **inside the project folder** in PowerShell
- [ ] SSH key exists at `C:\Users\<you>\.ssh\id_rsa`
- [ ] Public key copied to **every** Linux node (`ssh USERNAME@NODE_IP "echo OK"` works without password prompt)
- [ ] `node_exporter` is **installed and running** on every Linux node (`systemctl status node_exporter` shows active)
- [ ] Port 9100 is **open** on every Linux node (`Test-NetConnection -ComputerName NODE_IP -Port 9100` shows TcpTestSucceeded: True)
- [ ] `.env` file exists (created from `.env.example`) with correct `SSH_USERNAME`
- [ ] `docker-compose.yml` has **your Windows username** in the SSH volume mount line
- [ ] `config/nodes.yaml` has your **actual Linux node IP addresses**

If all boxes are ticked, run `docker compose up --build -d` and get a coffee.

---

*ONGC AI Cluster Monitor — Developed for ONGC Cinnamara, Jorhat — AI-Powered Cluster Monitoring System*
