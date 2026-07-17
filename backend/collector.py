"""
Metrics collector for ONGC AI Cluster Monitor.

Sources:
  1. nodes.yaml  — static list of node targets (add/remove nodes here)
  2. Prometheus  — real-time metric scraping via PromQL

Ganglia-style metrics collected per node:
  CPU: idle%, user%, system%, iowait%, used%
  Memory: used%, total_gb
  Load: load1, load5, load15
  Network: rx_bytes/s, tx_bytes/s
  Disk: read_bytes/s, write_bytes/s, used%
  Users: logind sessions
  Processes: running, blocked

No hardcoded IPs. No fake/demo data. Add nodes to config/nodes.yaml
and they appear automatically. Remove them and they are purged on restart.
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Tuple

import numpy as np
import yaml
import pandas as pd
from ml.prometheus_client import PrometheusClient
from backend.db import AsyncSessionLocal, engine
from backend.models import (
    Base, MetricHistory, Node, NodeBaseline,
    NodeUserSession, NodeProcess, AnomalyEvent,
    Alert, Incident, InfraCheck, Cluster,
)
from sqlalchemy import select, delete, and_

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (all from .env / environment)
# ---------------------------------------------------------------------------
PROM_URL          = os.getenv('PROMETHEUS_URL',       'http://localhost:9090')
COLLECT_INTERVAL  = int(os.getenv('COLLECT_INTERVAL', '60'))
WARMUP_SECONDS    = int(os.getenv('WARMUP_PERIOD_SECONDS', '300'))
SSH_KEY_PATH      = os.getenv('SSH_KEY_PATH', '')
SSH_USERNAME      = os.getenv('SSH_USERNAME', 'ubuntu')

NODES_YAML = os.getenv(
    'NODES_YAML',
    str(Path(__file__).parent.parent / 'config' / 'nodes.yaml')
)

# ---------------------------------------------------------------------------
# Ganglia-style PromQL metric definitions
# Each entry: (metric_name_stored, promql_expression, aggregation)
# aggregation: 'avg' | 'sum' | None
# ---------------------------------------------------------------------------
PROM_METRICS: List[Tuple[str, str, Optional[str]]] = [
    # CPU breakdown (%)
    ('cpu_idle_pct',    '100 * rate(node_cpu_seconds_total{mode="idle"}[2m])',    'avg'),
    ('cpu_user_pct',    '100 * rate(node_cpu_seconds_total{mode="user"}[2m])',    'avg'),
    ('cpu_system_pct',  '100 * rate(node_cpu_seconds_total{mode="system"}[2m])',  'avg'),
    ('cpu_iowait_pct',  '100 * rate(node_cpu_seconds_total{mode="iowait"}[2m])',  'avg'),
    ('cpu_used_pct',
     '100 * (1 - avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[2m])))',
     None),
    # Memory
    ('memory_used_pct',
     '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)',
     None),
    ('memory_total_gb', 'node_memory_MemTotal_bytes / 1073741824', None),
    # Load average
    ('load_one',     'node_load1',  None),
    ('load_five',    'node_load5',  None),
    ('load_fifteen', 'node_load15', None),
    # Network (bytes/s)
    ('net_rx_bytes',
     'sum by(instance) (rate(node_network_receive_bytes_total{device!="lo"}[2m]))',
     None),
    ('net_tx_bytes',
     'sum by(instance) (rate(node_network_transmit_bytes_total{device!="lo"}[2m]))',
     None),
    # Disk I/O (bytes/s)
    ('disk_read_bytes',
     'sum by(instance) (rate(node_disk_read_bytes_total[2m]))',
     None),
    ('disk_write_bytes',
     'sum by(instance) (rate(node_disk_written_bytes_total[2m]))',
     None),
    # Disk space (%)
    ('disk_used_pct',
     '100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})',
     None),
    # Logged-in users (logind collector — enabled by default on systemd Linux)
    ('node_logind_sessions', 'sum by(instance) (node_logind_sessions)', None),
    # Running / blocked processes
    ('procs_running', 'node_procs_running', None),
    ('procs_blocked', 'node_procs_blocked', None),
]

# Metrics that anomaly detection is run on
ANOMALY_METRICS = [
    'cpu_used_pct', 'memory_used_pct', 'disk_used_pct',
    'cpu_iowait_pct', 'load_one',
]

# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------
async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Ensure the default cluster exists
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(Cluster).limit(1))
        if existing is None:
            session.add(Cluster(name='ONGC HPC Cluster'))
            await session.commit()

# ---------------------------------------------------------------------------
# YAML node registry helpers
# ---------------------------------------------------------------------------
def load_targets_from_yaml() -> List[Tuple[str, str]]:
    try:
        with open(NODES_YAML) as f:
            data = yaml.safe_load(f)
        targets = []
        for entry in (data or []):
            for t in entry.get('targets', []):
                host, _, port = t.partition(':')
                targets.append((host.strip(), port.strip() or '9100'))
        return targets
    except Exception as exc:
        logger.warning('Could not read nodes.yaml: %s', exc)
        return []


def normalize_instance(instance: str) -> str:
    """Strip port suffix from Prometheus instance labels (e.g. '192.168.1.1:9100' → '192.168.1.1')."""
    if ':' in instance and not instance.startswith('['):
        host, _, _ = instance.rpartition(':')
        return host
    return instance


async def get_or_create_node(session, hostname: str, ip: str = '') -> Node:
    stmt = select(Node).where(Node.hostname == hostname)
    node = (await session.execute(stmt)).scalar_one_or_none()
    if node is None:
        # Get default cluster
        cluster = await session.scalar(select(Cluster).limit(1))
        node = Node(
            hostname=hostname,
            ip_address=ip or hostname,
            cluster_id=cluster.id if cluster else None,
            status='warmup',
            warmup_started_at=datetime.utcnow(),
            warmup_ends_at=datetime.utcnow() + timedelta(seconds=WARMUP_SECONDS),
        )
        session.add(node)
        await session.flush()
        logger.info('NEW NODE %s — warmup starts, ends at %s', hostname, node.warmup_ends_at)
    elif ip and not node.ip_address:
        node.ip_address = ip
    return node


async def register_yaml_nodes() -> None:
    targets = load_targets_from_yaml()
    if not targets:
        return
    async with AsyncSessionLocal() as session:
        for ip, _port in targets:
            await get_or_create_node(session, hostname=ip, ip=ip)
        await session.commit()
    logger.info('Registered %d node(s) from nodes.yaml', len(targets))


async def cleanup_unregistered_nodes() -> None:
    targets = load_targets_from_yaml()
    valid_hostnames = {ip for ip, _ in targets}
    if not valid_hostnames:
        logger.warning('cleanup_unregistered_nodes: nodes.yaml is empty — skipping')
        return
    async with AsyncSessionLocal() as session:
        all_nodes = (await session.execute(select(Node))).scalars().all()
        fake_nodes = [n for n in all_nodes if n.hostname not in valid_hostnames]
        if not fake_nodes:
            logger.info('No unregistered nodes to clean up')
            return
        fake_ids = [n.id for n in fake_nodes]
        logger.info('Removing %d unregistered node(s): %s',
                    len(fake_nodes), [n.hostname for n in fake_nodes])
        for Model in (MetricHistory, NodeUserSession, NodeProcess,
                      AnomalyEvent, Alert, Incident, InfraCheck, NodeBaseline):
            await session.execute(delete(Model).where(Model.node_id.in_(fake_ids)))
        await session.execute(delete(Node).where(Node.id.in_(fake_ids)))
        await session.commit()
        logger.info('Purged %d fake node(s)', len(fake_nodes))

# ---------------------------------------------------------------------------
# Prometheus helpers
# ---------------------------------------------------------------------------
def _prom_query(expr: str) -> pd.DataFrame:
    return PrometheusClient(PROM_URL).query(expr)


async def fetch_metric(expr: str) -> pd.DataFrame:
    try:
        return await asyncio.to_thread(_prom_query, expr)
    except Exception as exc:
        logger.debug('Prometheus query failed [%s]: %s', expr[:60], exc)
        return pd.DataFrame()

# ---------------------------------------------------------------------------
# Prometheus metric collection
# ---------------------------------------------------------------------------
async def collect_prometheus_metrics() -> None:
    """Collect all Ganglia-style metrics from Prometheus and persist to DB."""
    now = datetime.utcnow()
    async with AsyncSessionLocal() as session:
        for metric_name, expr, aggregation in PROM_METRICS:
            df = await fetch_metric(expr)
            if df.empty:
                continue
            if aggregation and 'instance' in df.columns:
                if aggregation == 'avg':
                    df = df.groupby('instance', as_index=False)['value'].mean()
                elif aggregation == 'sum':
                    df = df.groupby('instance', as_index=False)['value'].sum()

            for _, row in df.iterrows():
                raw_instance = str(row.get('instance') or '').strip()
                if not raw_instance:
                    continue
                instance = normalize_instance(raw_instance)
                node = await get_or_create_node(session, hostname=instance, ip=instance)
                val = float(row.get('value') or 0.0)
                session.add(MetricHistory(
                    node_id=node.id,
                    metric_name=metric_name,
                    timestamp=now,
                    value=round(val, 4),
                    labels='{}',
                ))

        # ---- Status update from 'up' metric + CPU ----
        df_up = await fetch_metric('up')
        up_status: dict[str, float] = {}
        if not df_up.empty:
            for _, row in df_up.iterrows():
                raw = str(row.get('instance') or '').strip()
                if not raw or raw in ('localhost:9090',):
                    continue
                up_status[normalize_instance(raw)] = float(row.get('value') or 0.0)

        df_cpu = await fetch_metric(
            '100 * (1 - avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[2m])))'
        )
        cpu_vals: dict[str, float] = {}
        if not df_cpu.empty:
            for _, row in df_cpu.iterrows():
                raw = str(row.get('instance') or '').strip()
                if raw:
                    cpu_vals[normalize_instance(raw)] = float(row.get('value') or 0.0)

        nodes_in_db = (await session.execute(select(Node))).scalars().all()
        for node in nodes_in_db:
            is_up = up_status.get(node.hostname, 0.0)

            # Handle warmup → online transition
            if node.status == 'warmup' and node.warmup_ends_at:
                if is_up > 0.0 and datetime.utcnow() >= node.warmup_ends_at:
                    await _complete_warmup(session, node)
                    continue  # status set by _complete_warmup

            if is_up == 0.0:
                if node.status not in ('warmup',):
                    node.status = 'offline'
            elif node.status not in ('warmup',):
                cpu = cpu_vals.get(node.hostname, 0.0)
                node.status = (
                    'critical' if cpu > float(os.getenv('CPU_CRITICAL_PCT', '95')) else
                    'warning'  if cpu > float(os.getenv('CPU_MEDIUM_PCT',  '75')) else
                    'online'
                )

            # Handle offline → warmup re-entry
            if node.status == 'offline' and is_up > 0.0:
                node.status = 'warmup'
                node.warmup_started_at = datetime.utcnow()
                node.warmup_ends_at = datetime.utcnow() + timedelta(seconds=WARMUP_SECONDS)
                logger.info('Node %s back online — re-entering warmup', node.hostname)

        await session.commit()


async def _complete_warmup(session, node: Node) -> None:
    """Compute NodeBaseline from warmup window, seed ensemble, transition to online."""
    warmup_start = node.warmup_started_at or (datetime.utcnow() - timedelta(seconds=WARMUP_SECONDS))
    logger.info('Completing warmup for node %s — computing baselines', node.hostname)

    for metric_name in ANOMALY_METRICS:
        rows = (await session.execute(
            select(MetricHistory.value)
            .where(and_(
                MetricHistory.node_id == node.id,
                MetricHistory.metric_name == metric_name,
                MetricHistory.timestamp >= warmup_start,
            ))
        )).scalars().all()

        if len(rows) < 5:
            continue

        arr = np.array([float(v) for v in rows])
        mean = float(np.mean(arr))
        std  = float(max(np.std(arr), 0.01))
        p95  = float(np.percentile(arr, 95))
        p99  = float(np.percentile(arr, 99))

        # Upsert NodeBaseline
        existing = await session.scalar(
            select(NodeBaseline).where(and_(
                NodeBaseline.node_id    == node.id,
                NodeBaseline.metric_name == metric_name,
            ))
        )
        if existing:
            existing.mean = mean; existing.std = std
            existing.p95  = p95;  existing.p99 = p99
            existing.computed_at = datetime.utcnow()
        else:
            session.add(NodeBaseline(
                node_id=node.id, metric_name=metric_name,
                mean=mean, std=std, p95=p95, p99=p99,
            ))

        # Seed ensemble with real baselines
        try:
            from ml.ensemble import seed_ensemble_from_baseline
            seed_ensemble_from_baseline(node.id, metric_name, mean, std)
        except Exception as exc:
            logger.debug('Could not seed ensemble for %s/%s: %s', node.hostname, metric_name, exc)

        logger.info(
            'Baseline [%s] %s — mean=%.2f std=%.2f p95=%.2f p99=%.2f',
            node.hostname, metric_name, mean, std, p95, p99
        )

    node.status = 'online'
    logger.info('Node %s warmup complete → ONLINE', node.hostname)


# ---------------------------------------------------------------------------
# Ensemble-based anomaly detection
# ---------------------------------------------------------------------------
async def collect_anomaly_events() -> None:
    """
    Run per-node, per-metric ensemble anomaly detection after each collection cycle.

    Warmup gate: nodes in 'warmup' status skip the full ensemble.
    Only threshold-Critical events are emitted during warmup (so obvious fires are caught).
    Auto-resolve: when ensemble_score < SEV_RESOLVE for an open event → mark resolved.
    Deduplication: one open AnomalyEvent per (node_id, metric_name) at a time.
    """
    from ml.ensemble import get_ensemble, SEV_RESOLVE, SEV_LOW
    now = datetime.utcnow()
    dedup_window = timedelta(seconds=max(COLLECT_INTERVAL * 3, 180))
    recent_window = timedelta(seconds=max(COLLECT_INTERVAL * 3, 180))

    async with AsyncSessionLocal() as session:
        nodes = (await session.execute(select(Node))).scalars().all()

        for node in nodes:
            in_warmup = (node.status == 'warmup')

            # Load baselines once per node (used to seed ensemble if not seeded yet)
            baselines: dict[str, NodeBaseline] = {}
            for bl in (await session.execute(
                select(NodeBaseline).where(NodeBaseline.node_id == node.id)
            )).scalars().all():
                baselines[bl.metric_name] = bl

            for metric_key in ANOMALY_METRICS:
                # Get latest metric value
                latest_row = await session.scalar(
                    select(MetricHistory.value)
                    .where(and_(
                        MetricHistory.node_id    == node.id,
                        MetricHistory.metric_name == metric_key,
                        MetricHistory.timestamp   >= now - recent_window,
                    ))
                    .order_by(MetricHistory.timestamp.desc())
                    .limit(1)
                )
                if latest_row is None:
                    continue
                value = float(latest_row)

                # Get baseline for seeding
                bl = baselines.get(metric_key)
                ens = get_ensemble(
                    node.id, metric_key,
                    baseline_mean=bl.mean if bl else None,
                    baseline_std=bl.std  if bl else None,
                )

                result = ens.evaluate(value, now)

                # In warmup: only emit Critical threshold events
                if in_warmup:
                    if result.severity != 'Critical':
                        continue

                # --- Handle anomaly emit ---
                if result.severity in ('Medium', 'High', 'Critical'):
                    # Check for existing open event (dedup)
                    existing_open = await session.scalar(
                        select(AnomalyEvent).where(and_(
                            AnomalyEvent.node_id     == node.id,
                            AnomalyEvent.metric_name == metric_key,
                            AnomalyEvent.resolved    == False,
                            AnomalyEvent.detected_at >= now - dedup_window,
                        )).limit(1)
                    )
                    if existing_open is None:
                        session.add(AnomalyEvent(
                            node_id       = node.id,
                            detected_at   = now,
                            metric_name   = metric_key,
                            anomaly_score = result.ensemble_score,
                            metric_value  = result.metric_value,
                            severity      = result.severity,
                            detector      = result.detector_scores.get('detectors', 'Ensemble'),
                            description   = result.description,
                            resolved      = False,
                        ))
                        logger.info(
                            'ANOMALY [%s] %s — %s=%.2f score=%.3f [%s]',
                            result.severity, node.hostname, metric_key,
                            value, result.ensemble_score,
                            result.detector_scores.get('detectors','')
                        )
                elif result.severity == 'Low':
                    logger.debug(
                        'SUPPRESSED low-severity anomaly for %s %s score=%.3f',
                        node.hostname, metric_key, result.ensemble_score
                    )

                # --- Auto-resolve open events when score drops ---
                elif result.ensemble_score < SEV_RESOLVE:
                    open_events = (await session.execute(
                        select(AnomalyEvent).where(and_(
                            AnomalyEvent.node_id     == node.id,
                            AnomalyEvent.metric_name == metric_key,
                            AnomalyEvent.resolved    == False,
                        ))
                    )).scalars().all()
                    for ev in open_events:
                        ev.resolved    = True
                        ev.resolved_at = now
                        logger.info('RESOLVED %s — %s score=%.3f',
                                    node.hostname, metric_key, result.ensemble_score)

        await session.commit()


# ---------------------------------------------------------------------------
# SSH-based per-process collection
# ---------------------------------------------------------------------------
async def collect_node_processes() -> None:
    """
    Collect per-process data from each online node via SSH.

    Requires SSH_KEY_PATH and SSH_USERNAME to be set in .env.
    Skips gracefully if asyncssh is not installed or SSH is not configured.

    Runs `ps aux` on each node and saves the top 20 processes (by CPU) to the
    NodeProcess table, replacing any stale rows for that node.
    """
    if not SSH_KEY_PATH:
        logger.debug('Process collection skipped — SSH_KEY_PATH not set in .env')
        return
    try:
        import asyncssh  # type: ignore
    except ImportError:
        logger.warning(
            'Process collection skipped — asyncssh not installed. '
            'Run: pip install asyncssh'
        )
        return

    now = datetime.utcnow()
    async with AsyncSessionLocal() as session:
        nodes = (
            await session.execute(
                select(Node).where(
                    Node.status.in_(['online', 'warning', 'critical', 'warmup'])
                )
            )
        ).scalars().all()

        for node in nodes:
            ip = node.ip_address or node.hostname
            try:
                async with asyncssh.connect(
                    ip,
                    username=SSH_USERNAME,
                    client_keys=[SSH_KEY_PATH],
                    known_hosts=None,
                    connect_timeout=5,
                ) as conn:
                    count_result = await conn.run(
                        'ps -e --no-headers 2>/dev/null | wc -l',
                        timeout=10,
                    )
                    total_procs = 0
                    if count_result.exit_status == 0:
                        try:
                            total_procs = int(count_result.stdout.strip())
                        except ValueError:
                            total_procs = 0

                    result = await conn.run(
                        'ps aux --no-headers --sort=-%cpu 2>/dev/null | head -20',
                        timeout=10,
                    )
                    if result.exit_status != 0 or not result.stdout:
                        continue

                    # Delete old process rows for this node
                    await session.execute(
                        delete(NodeProcess).where(NodeProcess.node_id == node.id)
                    )

                    # Parse the top-20 process snapshot for the detailed list.
                    # Columns: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND
                    lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
                    for line in lines:
                        parts = line.split(None, 10)
                        if len(parts) < 11:
                            continue
                        try:
                            pid      = int(parts[1])
                            cpu_pct  = float(parts[2])
                            mem_pct  = float(parts[3])
                            stat     = parts[7]          # STAT column
                            command  = parts[10][:512]   # full COMMAND
                            username = parts[0][:64]
                        except (ValueError, IndexError):
                            continue

                        session.add(NodeProcess(
                            node_id=node.id,
                            pid=pid,
                            username=username,
                            cpu_pct=round(cpu_pct, 2),
                            mem_pct=round(mem_pct, 2),
                            command=command,
                            status=stat,
                            collected_at=now,
                        ))

                    # Persist the actual process count as a MetricHistory row so
                    # services can use it as the authoritative total for the UI.
                    try:
                        session.add(MetricHistory(
                            node_id=node.id,
                            metric_name='procs_running',
                            timestamp=now,
                            value=float(total_procs),
                            labels=None,
                        ))
                    except Exception:
                        logger.debug('Failed to persist procs_running metric for %s', node.hostname)

                    # ── User sessions via `w` ──────────────────────────────
                    # `w -hi` output: USER TTY FROM LOGIN@ IDLE JCPU PCPU WHAT
                    # Example 1: arunabh  pts/0  192.168.56.1  07:32  ...
                    # Example 2: arunabh         192.168.56.1  07:38  ... (No TTY)
                    try:
                        who_result = await conn.run(
                            'w -hi 2>/dev/null',
                            timeout=10,
                        )
                        # Replace stale user session rows for this node
                        await session.execute(
                            delete(NodeUserSession).where(NodeUserSession.node_id == node.id)
                        )
                        
                        num_users = 0
                        if who_result.exit_status == 0 and who_result.stdout.strip():
                            who_lines = [l for l in who_result.stdout.strip().splitlines() if l.strip()]
                            for wl in who_lines:
                                wparts = wl.split()
                                if len(wparts) < 2:
                                    continue
                                uname = wparts[0][:128]
                                
                                # If the second column is an IP (contains '.') or IPv6/display (':'), TTY is missing
                                if '.' in wparts[1] or ':' in wparts[1] and 'tty' not in wparts[1] and 'pts' not in wparts[1]:
                                    terminal = None
                                    remote = wparts[1][:128]
                                else:
                                    terminal = wparts[1][:64]
                                    remote = wparts[2][:128] if len(wparts) > 2 else None
                                
                                # Ignore non-interactive background SSH sessions (like our own collector)
                                if terminal is None:
                                    continue
                                
                                session.add(NodeUserSession(
                                    node_id=node.id,
                                    username=uname,
                                    terminal=terminal,
                                    remote_host=remote,
                                    login_time=now,  # w login time format varies too much, use 'now'
                                    collected_at=now,
                                ))
                                num_users += 1
                                
                            logger.info(
                                'Collected %d user session(s) from %s',
                                len(who_lines), node.hostname,
                            )
                            
                            # Persist to MetricHistory so it overrides Prometheus logind count
                            try:
                                session.add(MetricHistory(
                                    node_id=node.id,
                                    metric_name='node_logind_sessions',
                                    timestamp=now,
                                    value=float(num_users),
                                    labels=None,
                                ))
                            except Exception:
                                pass
                                
                    except Exception as exc:
                        logger.debug('User session collection failed for %s: %s', ip, exc)

                    logger.info('Collected processes from %s (%s)', node.hostname, ip)

            except asyncio.TimeoutError:
                logger.debug('SSH timeout collecting processes from %s', ip)
            except Exception as exc:
                logger.debug('SSH process collection failed for %s: %s', ip, exc)

        await session.commit()


# ---------------------------------------------------------------------------
# Startup seed
# ---------------------------------------------------------------------------
async def collect_demo_metrics() -> None:
    """
    One-time startup initialisation:
    1. Purge any unregistered nodes.
    2. Register real nodes from nodes.yaml.
    3. Run the first Prometheus scrape + process collection.
    """
    await cleanup_unregistered_nodes()
    await register_yaml_nodes()
    try:
        await collect_prometheus_metrics()
    except Exception as exc:
        logger.warning('Initial Prometheus collection failed (will retry): %s', exc)
    try:
        await collect_node_processes()
    except Exception as exc:
        logger.warning('Initial process collection failed (will retry): %s', exc)


# ---------------------------------------------------------------------------
# Main collector loop
# ---------------------------------------------------------------------------
async def run_collector() -> None:
    await init_db()
    await cleanup_unregistered_nodes()
    await register_yaml_nodes()
    logger.info('Collector started — Prometheus: %s | Interval: %ds | Warmup: %ds',
                PROM_URL, COLLECT_INTERVAL, WARMUP_SECONDS)
    while True:
        try:
            await collect_prometheus_metrics()
            await collect_anomaly_events()
            await collect_node_processes()
        except Exception as exc:
            logger.error('Collector cycle error: %s', exc)
        await asyncio.sleep(COLLECT_INTERVAL)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_collector())
