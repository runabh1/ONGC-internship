# ONGC AI Cluster Monitor — Technical Overview

> A complete guide to the system architecture, components, machine learning engine, and why it is a significant upgrade over Ganglia.

---

## Table of Contents

1. [What This System Does](#1-what-this-system-does)
2. [System Architecture](#2-system-architecture)
3. [Frontend — The Dashboard](#3-frontend--the-dashboard)
4. [Backend — The Brain](#4-backend--the-brain)
5. [Data Collection Pipeline](#5-data-collection-pipeline)
6. [Database Design](#6-database-design)
7. [ML Engine — Ensemble Anomaly Detection](#7-ml-engine--ensemble-anomaly-detection)
8. [Infrastructure Health Checker](#8-infrastructure-health-checker)
9. [Node Lifecycle — Warmup System](#9-node-lifecycle--warmup-system)
10. [ONGC Monitor vs Ganglia](#10-ongc-monitor-vs-ganglia)
11. [Technology Stack Summary](#11-technology-stack-summary)

---

## 1. What This System Does

The ONGC AI Cluster Monitor is a **real-time, AI-powered monitoring system** for Linux HPC (High-Performance Computing) clusters at ONGC field sites. It was built as a modern replacement for Ganglia.

At a high level, it:

- **Collects** system metrics (CPU, memory, disk, network, processes) from multiple Linux nodes every 60 seconds
- **Stores** those metrics in a PostgreSQL time-series database
- **Detects** anomalies automatically using 6 machine learning algorithms working together
- **Alerts** operators when a node is behaving abnormally — before it fails
- **Displays** everything through a modern, interactive web dashboard
- **Checks** infrastructure health (ping, SSH, Prometheus, node_exporter) every 30 seconds

The system has **zero hardcoded node IPs** — add a node to a YAML file and it appears automatically.

---

## 2. System Architecture

```
+=====================================================================+
|                        MONITORING PC (Docker)                        |
|                                                                       |
|  +----------------+     +--------------------+     +-------------+   |
|  |   FRONTEND     |     |      BACKEND       |     |  PROMETHEUS |   |
|  |  React SPA     |<--->|  FastAPI (Python)  |<--->|  TSDB       |   |
|  |  port 3001     | REST|  port 8000         |PromQL|  port 9090 |   |
|  +----------------+     +----+----------+----+     +------+------+   |
|                               |          |                |           |
|                        +------+--+  +----+-----+         | scrape    |
|                        |  ML     |  |PostgreSQL|         |           |
|                        | Engine  |  | Database |         |           |
|                        | (6 algos)|  | port 5432|         |           |
|                        +---------+  +----------+         |           |
|                                                           |           |
+===========================================================|===========+
                                                            |
               +--------------------------------------------+
               |  SSH (process data + health checks)
               |
   +-----------v-----------+-----------+-----------+
   |  Linux Node 1         |  Node 2   |  Node 3   |
   |  192.168.x.x          |           |           |
   |  node_exporter :9100  |  ...      |  ...      |
   +------------------------+-----------+-----------+
```

### How it all connects

| Connection | Protocol | What flows |
|-----------|---------|-----------|
| Frontend to Backend | REST/HTTP | Node data, metrics, anomalies, health status |
| Backend to Prometheus | PromQL HTTP | Raw metric values scraped every 60s |
| Prometheus to Nodes | HTTP scrape | node_exporter metrics every 15s |
| Backend to Nodes | SSH (asyncssh) | ps aux process list |
| Backend to Nodes | ICMP ping | Reachability check |

---

## 3. Frontend — The Dashboard

The entire frontend is a **single-page React application** — no page reloads, instant navigation.

### 3.1 Cluster Overview Bar

The top of the dashboard shows a live cluster summary:
- Total nodes, online/warning/critical/offline counts
- Average CPU % across all nodes
- Average Memory % across all nodes
- Average Load average
- Active anomaly count (last 24 hours)
- Active incidents and alerts

This bar auto-refreshes every 30 seconds.

### 3.2 Node Cards Grid

Each cluster node is displayed as a **card** showing:
- Node hostname / IP address
- Status badge: ONLINE / WARNING / CRITICAL / OFFLINE / WARMUP
- Current CPU %, Memory %, Disk %
- Sparkline charts (mini time-series) for CPU and memory — last 30 readings
- Active user count (from logind sessions)
- Running process count
- Latest anomaly severity (if any)

Clicking a card opens the **Node Detail Panel**.

### 3.3 Node Detail Panel (6 tabs)

When you click a node, a full-detail panel opens with these tabs:

#### Tab 1 — Overview
Key metrics at a glance: CPU breakdown (user/system/iowait/idle), memory, load averages (1, 5, 15 min), disk usage, network traffic (Rx/Tx bytes/s).

#### Tab 2 — Metrics
Time-series charts for every metric collected. Shows the last N data points as a line chart. Uses Recharts. Users can scroll through history.

#### Tab 3 — Users
Table of currently logged-in users (from SSH logind sessions): username, terminal, remote host, login time.

#### Tab 4 — Processes
Table of the **top 20 processes** (sorted by CPU usage) collected from the node via SSH ps aux. Shows PID, username, CPU%, MEM%, command, status.

#### Tab 5 — Anomalies
Feed of all anomaly events detected on this node. Shows: metric affected, severity (Low/Medium/High/Critical), ensemble score (0-1), which detectors fired, timestamp, and whether it was auto-resolved.

#### Tab 6 — Health
Infrastructure health checks: PING, SSH, PROMETHEUS, NODE EXPORTER — each with pass/fail status and detail message.

---

## 4. Backend — The Brain

The backend is built with **FastAPI** (Python 3.12), a modern async web framework.

### 4.1 Application Startup Sequence

When the container starts, the following happens in order:

1. Load .env variables
2. Initialize PostgreSQL database (create tables if missing)
3. Purge nodes not in nodes.yaml (keeps DB clean)
4. Register nodes from nodes.yaml (auto-creates Node records)
5. Run first Prometheus scrape (so UI has data immediately)
6. Launch background task: Metric Collector (runs every 60s)
7. Launch background task: Health Checker (runs every 30s)

### 4.2 REST API Endpoints

All endpoints are under the /api prefix:

| Endpoint | Method | Description |
|----------|--------|-------------|
| /api/nodes | GET | All nodes with latest metrics + sparklines |
| /api/nodes/{id} | GET | Single node detail + baselines |
| /api/nodes/{id}/metrics | GET | Time-series metrics for a node |
| /api/nodes/{id}/anomalies | GET | Anomaly events for a node |
| /api/nodes/{id}/health | GET | Latest infra health check results |
| /api/nodes/{id}/users | GET | Active logged-in users |
| /api/nodes/{id}/processes | GET | Top-20 process list |
| /api/cluster/overview | GET | Cluster-wide summary counts |
| /api/cluster/summary | GET | Cluster-wide avg CPU/MEM/Load/Disk |
| /api/cluster/history | GET | Time-series history for stacked chart |
| /api/anomalies | GET | Global anomaly feed (all nodes) |

Interactive API docs: http://localhost:8000/docs

---

## 5. Data Collection Pipeline

Every 60 seconds, the collector runs through 3 phases:

**Phase 1: Prometheus Metrics Collection**
For each of 18 PromQL queries, fetch data, normalize instance, save to metric_history table.

**Phase 2: Anomaly Detection**
For each node x 5 anomaly metrics, run 6-detector ensemble, save AnomalyEvent if severity >= Medium.

**Phase 3: SSH Process Collection**
For each online node, SSH connect, run ps aux, save top-20 processes to DB.

### Metrics Collected (18 PromQL queries)

| Category | Metric Name | Description |
|----------|------------|-------------|
| CPU | cpu_used_pct | Overall CPU utilization % |
| CPU | cpu_user_pct | CPU used by user processes |
| CPU | cpu_system_pct | CPU used by kernel |
| CPU | cpu_iowait_pct | CPU waiting for disk I/O |
| CPU | cpu_idle_pct | CPU idle % |
| Memory | memory_used_pct | RAM used % |
| Memory | memory_total_gb | Total RAM in GB |
| Load | load_one | 1-minute load average |
| Load | load_five | 5-minute load average |
| Load | load_fifteen | 15-minute load average |
| Network | net_rx_bytes | Network receive bytes/sec |
| Network | net_tx_bytes | Network transmit bytes/sec |
| Disk I/O | disk_read_bytes | Disk read bytes/sec |
| Disk I/O | disk_write_bytes | Disk write bytes/sec |
| Disk Space | disk_used_pct | Root filesystem used % |
| Users | node_logind_sessions | Logged-in user sessions |
| Processes | procs_running | Running process count |
| Processes | procs_blocked | Blocked process count |

---

## 6. Database Design

PostgreSQL is used with SQLAlchemy async ORM. All tables are auto-created on startup.

### Tables

```
clusters              — top-level cluster grouping
  nodes               — one row per monitored Linux node
    metric_history     — time-series: every metric reading (indexed)
    node_baselines     — per-metric baseline stats from warmup
    anomaly_events     — ML-detected anomaly events (indexed)
    infra_checks       — ping/ssh/prometheus/node_exporter results
    node_user_sessions — logged-in user sessions
    node_processes     — top-20 processes per node
    incidents          — high-level incident tracking
    alerts             — notification alert records
```

### Node Status Values

| Status | Meaning |
|--------|---------|
| warmup | Newly added or recovered — full anomaly detection suppressed |
| online | Normal operation |
| warning | CPU between 75-95% |
| critical | CPU above 95% |
| offline | Prometheus up metric = 0 |
| unknown | Initial state before first scrape |

---

## 7. ML Engine — Ensemble Anomaly Detection

Instead of simple threshold alerts, the system uses **6 different machine learning algorithms** working together as an ensemble.

### 7.1 Why an Ensemble?

No single algorithm is perfect:
- Threshold detectors miss subtle patterns (CPU at 87% for hours is bad even if below 90%)
- Statistical detectors can have false positives during expected load spikes
- ML models need data to train — they are blind on new nodes

By combining all of them with a weighted vote, weaknesses of each are cancelled out. The result is a single ensemble score from 0.0 to 1.0.

### 7.2 The 6 Detectors

#### Detector 1 — Static Threshold (Weight: 30%)
The most important detector. Checks if a metric crosses a configurable threshold. All thresholds set in .env — none are hardcoded.

```
CPU > 95%       -> Critical
CPU > 85%       -> High
CPU > 75%       -> Medium
Memory > 95%    -> Critical
Memory > 90%    -> High
Memory > 80%    -> Medium
I/O wait > 40%  -> High
Load > 30       -> Critical
Load > 15       -> High
Load > 8        -> Medium
```

Score scales proportionally — CPU at 99% scores higher than 96%.

#### Detector 2 — Z-Score Statistical (Weight: 15%)
After warmup, the system computes a baseline mean and standard deviation for each metric per node. Then for every new reading:

  z_score = (current_value - baseline_mean) / baseline_std

A z-score of 2.5 means the value is 2.5 standard deviations from this node's normal. This catches cases like a CPU that normally runs at 20% jumping to 60% — below the 75% threshold but statistically very unusual for this specific node.

Seeded from real baselines computed during the warmup window.

#### Detector 3 — EWMA Exponentially Weighted Moving Average (Weight: 15%)
Tracks a smoothed running average of the metric. If the current value deviates significantly from the EWMA, that signals an anomaly. EWMA is faster than a simple rolling average — recent values have exponentially more weight. Detects trends even when individual readings are not extreme.

Span: 12 data points. Threshold: 20% deviation.

#### Detector 4 — Rolling Mean Detector (Weight: 15%)
Uses a simple rolling window (last 15 readings). Measures deviation from recent window mean. Good at catching step changes — when a metric jumps to a new level and stays there.

#### Detector 5 — Isolation Forest (Weight: 15%)
A proper ML algorithm from scikit-learn. Works by randomly partitioning data — anomalous points (far from the cluster of normal values) are isolated in fewer splits than normal points.

- Requires at least 30 data points to train (activates ~30 min after node first seen)
- Trains on rolling window of last 200 readings
- Contamination: 5% (expects 5% of data to be anomalous)
- Good at detecting complex multivariate anomalies

#### Detector 6 — LSTM Autoencoder (Weight: 10%)
A deep learning model using LSTM neural networks. Trains to reconstruct sequences of normal metric readings. High reconstruction error = current pattern is unlike anything seen during normal operation.

- Requires at least 12 data points to activate
- Sequence length: 10 time steps
- Best at detecting temporal patterns — e.g. a metric that oscillates normally but suddenly flattens

### 7.3 Ensemble Score Calculation

```
ensemble_score = (
    0.30 x threshold_score     +
    0.15 x rolling_mean_score  +
    0.15 x ewma_score          +
    0.15 x zscore_score        +
    0.15 x isolation_forest    +  (0.0 if < 30 samples)
    0.10 x lstm_score             (0.0 if < 12 samples)
)
```

All scores normalized to 0-1 before combining. Weights re-normalized when a detector is unavailable.

### 7.4 Severity Mapping

| Ensemble Score | Severity | Action |
|---------------|----------|--------|
| >= 0.90 | Critical | AnomalyEvent saved to DB |
| >= 0.75 | High | AnomalyEvent saved to DB |
| >= 0.50 | Medium | AnomalyEvent saved to DB |
| >= 0.30 | Low | Logged only, not shown as active |
| < 0.30 | Normal | No action |

### 7.5 Deduplication and Auto-Resolve

**Deduplication:** Only one open AnomalyEvent per (node, metric) at a time — no spam.

**Auto-resolve:** When ensemble score drops below 0.15, all open events for that (node, metric) are automatically marked resolved=True with a resolved_at timestamp.

### 7.6 Metrics Monitored for Anomalies

Detection runs on 5 metrics per node per cycle:
- cpu_used_pct — overall CPU utilization
- memory_used_pct — RAM usage
- disk_used_pct — root filesystem fullness
- cpu_iowait_pct — I/O wait (storage bottleneck indicator)
- load_one — 1-minute load average

---

## 8. Infrastructure Health Checker

A health checker runs every 30 seconds and performs 4 independent checks per node:

### Check 1 — PING
Sends ICMP ping to the node IP using the Linux ping -c 1 command inside the container.

### Check 2 — Node Exporter HTTP
HTTP GET to http://<node_ip>:9100/metrics. Checks status=200 and that body contains node_cpu_seconds_total.

### Check 3 — Prometheus Scrape
Queries Prometheus /api/v1/targets and checks that the node appears as a target with health=up. This confirms Prometheus is successfully scraping the node.

### Check 4 — SSH Connectivity
Attempts a real SSH connection and runs echo ok. Confirms SSH authentication works end-to-end. Skipped if SSH_KEY_PATH is not set.

### Status Derivation

| Condition | Status |
|-----------|--------|
| Ping fails AND not all of SSH/Prometheus pass | offline |
| Node exporter or Prometheus fails | critical |
| Only SSH fails | warning |
| All pass | no override (collector manages status) |

---

## 9. Node Lifecycle — Warmup System

When a node is first seen or comes back online, it enters **WARMUP** mode for 5 minutes (configurable via WARMUP_PERIOD_SECONDS).

### Why warmup?

When a node starts, its metrics are not representative of normal behavior yet. Training anomaly detectors on startup data leads to false positives.

### Warmup Sequence

```
Node appears in Prometheus
        |
Status set to 'warmup'
        |
Metrics collected normally
        |
Anomaly detection runs BUT only Critical threshold events fire
Statistical and ML detectors are suppressed
        |
After WARMUP_PERIOD_SECONDS (default: 5 min):
        |
System computes NodeBaseline from warmup window:
  mean, std, p95, p99 for each anomaly metric
        |
Baselines stored to DB and used to seed ZScore detector
        |
Status transitions to 'online'
        |
Full 6-detector ensemble becomes active
```

If a node goes offline and comes back, it re-enters warmup automatically.

---

## 10. ONGC Monitor vs Ganglia

Ganglia is the traditional HPC monitoring tool used at many ONGC sites. Here is a detailed comparison:

| Feature | Ganglia | ONGC AI Monitor |
|---------|---------|----------------|
| Metric collection | RRDtool + gmond daemon on each node | Prometheus node_exporter (standard, maintained) |
| Storage | Round-robin RRD files (history lost when RRD overflows) | PostgreSQL — unlimited retention, full SQL |
| Anomaly detection | None — manual thresholds only | 6-algorithm ensemble ML engine |
| False positive rate | High — single threshold, many false alerts | Low — 6 detectors must agree |
| Baseline learning | None | Per-node, per-metric: mean, std, p95, p99 |
| Auto-resolve | Must be manually acknowledged | Anomalies auto-resolve when metrics recover |
| Process monitoring | Via gmond module | SSH ps aux — top 20 by CPU, username, PID |
| User tracking | Basic | Login sessions via logind + SSH |
| Frontend | Old PHP Ganglia Web UI | Modern React SPA — fast, interactive |
| Charts | Static GIF images | Interactive SVG charts with hover/zoom |
| Node addition | Edit gmond.conf on every node + restart | Edit one YAML file on monitoring server |
| Infrastructure health | Basic up/down | 4-layer: ping + SSH + Prometheus + node_exporter |
| REST API | None | Full REST API with Swagger docs |
| Docker deployment | Complex manual setup | docker compose up --build -d |
| Windows monitoring PC | Difficult | Fully supported via Docker Desktop |
| Warmup protection | None | 5-minute warmup before full detection |
| Configurable thresholds | Edit config + restart | Set in .env — no code changes |
| Historical analysis | Limited by RRD resolution | Full history at any resolution |

### Why this matters for ONGC

**1. Earlier warnings, fewer false alarms**
Ganglia fires the moment a metric crosses 90%. The ONGC monitor waits until 2-3 detectors agree. Far fewer false alarms, while still catching real problems earlier because the Z-Score detector catches a node at 60% CPU that normally runs at 15%.

**2. Adapts to each node individually**
Two nodes with different workloads have completely different baselines. The ONGC monitor learns each node's normal behavior independently. Ganglia uses the same thresholds for all nodes.

**3. Self-healing visibility**
The auto-resolve system shows not just that an anomaly happened but when it resolved. Perfect for post-incident analysis.

**4. Modern, maintainable technology**
Ganglia uses C and PHP, last significantly updated years ago. ONGC monitor uses Python 3.12, FastAPI, React 18, PostgreSQL 15, Prometheus — all actively maintained.

**5. No agents to configure**
Prometheus node_exporter is a single static binary, no configuration needed. Ganglia required gmond to be configured and maintained on every node separately.

**6. Expandable via API**
- Add email/SMS alerts (config already in .env)
- Add more ML detectors
- Export data to Excel/CSV
- Integrate with PagerDuty or any alert system
Ganglia has no API and no clean extension points.

---

## 11. Technology Stack Summary

| Layer | Technology | Why Chosen |
|-------|-----------|-----------|
| Frontend | React 18, MUI, Recharts | Modern, fast, component-based UI |
| Backend | FastAPI (Python 3.12) | Async-first, fast, auto-generates API docs |
| ML | scikit-learn, NumPy, pandas | Industry standard ML in Python |
| Deep Learning | LSTM Autoencoder (Keras) | Temporal anomaly detection |
| Database | PostgreSQL 15 | Reliable, time-series queries, full SQL |
| Metrics | Prometheus + node_exporter | Industry standard for Linux metrics |
| SSH | asyncssh | Non-blocking SSH for process collection |
| Containerization | Docker + Docker Compose | One command deployment |
| ORM | SQLAlchemy 2.0 async | Type-safe async database access |
| Web server | Uvicorn | Production ASGI server |

### End-to-End Data Flow

```
node_exporter (Linux node, :9100)
    Prometheus scrapes every 15s
        Backend PromQL query every 60s
            Stores to PostgreSQL metric_history
                6-detector ensemble runs on latest values
                    AnomalyEvents saved to PostgreSQL
                        React frontend polls REST API every 30s
                            Dashboard updates

Backend every 60s:
    SSH to each online node
        ps aux --sort=-%cpu | head -20
            Top-20 processes saved to node_processes table

Health Checker every 30s:
    ICMP ping
    HTTP GET node_ip:9100/metrics
    HTTP GET Prometheus /api/v1/targets
    asyncssh connect + echo ok
        Results saved to infra_checks table
```

---

*ONGC AI Cluster Monitor — Built for ONGC Cinnamara, Jorhat*
*A modern, AI-powered replacement for Ganglia*
