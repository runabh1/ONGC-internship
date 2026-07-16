"""
Service layer for ONGC AI Cluster Monitor.

All database queries are here; API routes call these functions.
"""
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import and_, desc, func, select, text

from backend.db import get_session
from backend.models import (
    Alert, AnomalyEvent, Cluster, Incident, InfraCheck,
    MetricHistory, Node, NodeBaseline, NodeProcess, NodeUserSession,
)

RECENT_SECONDS = 300

# ---------------------------------------------------------------------------
# Cluster overview (summary bar)
# ---------------------------------------------------------------------------
async def get_cluster_overview() -> dict[str, Any]:
    async with get_session() as session:
        total_nodes      = await session.scalar(select(func.count()).select_from(Node)) or 0
        active_incidents = await session.scalar(select(func.count()).select_from(Incident).where(Incident.end_time.is_(None))) or 0
        active_alerts    = await session.scalar(select(func.count()).select_from(Alert).where(Alert.status == 'active')) or 0

        # Count nodes by status
        online_count  = await session.scalar(select(func.count()).select_from(Node).where(Node.status == 'online'))  or 0
        warning_count = await session.scalar(select(func.count()).select_from(Node).where(Node.status == 'warning')) or 0
        critical_count= await session.scalar(select(func.count()).select_from(Node).where(Node.status == 'critical'))or 0
        warmup_count  = await session.scalar(select(func.count()).select_from(Node).where(Node.status == 'warmup'))  or 0
        offline_count = await session.scalar(
            select(func.count()).select_from(Node).where(Node.status.in_(['unknown', 'offline']))
        ) or 0

        anomaly_cutoff   = datetime.utcnow() - timedelta(hours=24)
        active_anomalies = await session.scalar(
            select(func.count()).select_from(AnomalyEvent).where(
                and_(AnomalyEvent.resolved == False, AnomalyEvent.detected_at >= anomaly_cutoff)
            )
        ) or 0

        return {
            'total_nodes':      int(total_nodes),
            'healthy_nodes':    int(online_count),
            'warnings':         int(warning_count),
            'critical':         int(critical_count),
            'warmup':           int(warmup_count),
            'offline':          int(offline_count),
            'active_alerts':    int(active_alerts),
            'active_incidents': int(active_incidents),
            'active_anomalies': int(active_anomalies),
        }


# ---------------------------------------------------------------------------
# Cluster aggregate stats (avg CPU / MEM / Load / Disk across online nodes)
# ---------------------------------------------------------------------------
async def get_cluster_summary() -> dict[str, Any]:
    """Aggregate current metrics across all online nodes — for the cluster header bar."""
    async with get_session() as session:
        recent_cutoff = datetime.utcnow() - timedelta(seconds=RECENT_SECONDS)
        online_nodes = (await session.execute(
            select(Node).where(Node.status.in_(['online', 'warning', 'critical']))
        )).scalars().all()

        if not online_nodes:
            return {k: None for k in
                    ['avg_cpu', 'avg_mem', 'avg_load', 'avg_disk',
                     'total_net_rx', 'total_net_tx', 'node_count']}

        node_ids = [n.id for n in online_nodes]

        def _agg_metric(metric_name: str, agg_fn=func.avg):
            return select(agg_fn(MetricHistory.value)).where(
                and_(
                    MetricHistory.node_id.in_(node_ids),
                    MetricHistory.metric_name == metric_name,
                    MetricHistory.timestamp >= recent_cutoff,
                )
            )

        avg_cpu    = await session.scalar(_agg_metric('cpu_used_pct'))
        avg_mem    = await session.scalar(_agg_metric('memory_used_pct'))
        avg_load_1 = await session.scalar(_agg_metric('load_one'))
        avg_load_5 = await session.scalar(_agg_metric('load_five'))
        avg_load_15= await session.scalar(_agg_metric('load_fifteen'))
        avg_disk   = await session.scalar(_agg_metric('disk_used_pct'))
        total_rx   = await session.scalar(_agg_metric('net_rx_bytes', func.sum))
        total_tx   = await session.scalar(_agg_metric('net_tx_bytes', func.sum))

        def _r(v, d=1):
            return round(float(v), d) if v is not None else None

        return {
            'avg_cpu':      _r(avg_cpu),
            'avg_mem':      _r(avg_mem),
            'avg_load_1':   _r(avg_load_1, 2),
            'avg_load_5':   _r(avg_load_5, 2),
            'avg_load_15':  _r(avg_load_15, 2),
            'avg_disk':     _r(avg_disk),
            'total_net_rx': _r(total_rx, 0),
            'total_net_tx': _r(total_tx, 0),
            'node_count':   len(online_nodes),
        }


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------
async def _serialize_metric(metric: MetricHistory) -> dict[str, Any]:
    return {
        'id':          metric.id,
        'metric_name': metric.metric_name,
        'value':       metric.value,
        'timestamp':   metric.timestamp.isoformat() if metric.timestamp else None,
        'labels':      metric.labels,
    }

async def _serialize_node(node: Node, latest_metrics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        'id':               node.id,
        'hostname':         node.hostname,
        'ip_address':       node.ip_address,
        'cluster_id':       node.cluster_id,
        'status':           node.status,
        'os_version':       node.os_version,
        'architecture':     node.architecture,
        'boot_time':        node.boot_time.isoformat() if node.boot_time else None,
        'warmup_started_at':node.warmup_started_at.isoformat() if node.warmup_started_at else None,
        'warmup_ends_at':   node.warmup_ends_at.isoformat()   if node.warmup_ends_at   else None,
        'created_at':       node.created_at.isoformat()       if node.created_at       else None,
        'latest_metrics':   latest_metrics or [],
    }

# ---------------------------------------------------------------------------
# Node list (cards + grid data)
# ---------------------------------------------------------------------------
async def get_nodes() -> list[dict[str, Any]]:
    async with get_session() as session:
        nodes = (await session.execute(select(Node).order_by(Node.hostname))).scalars().all()
        results: list[dict[str, Any]] = []
        for node in nodes:
            raw_history = (await session.execute(
                select(MetricHistory)
                .where(MetricHistory.node_id == node.id)
                .order_by(desc(MetricHistory.timestamp))
                .limit(600)  # 20 metrics × 30 points
            )).scalars().all()

            # Sparklines: last 30 per metric (ascending time)
            sparklines_raw: dict[str, list] = {}
            for m in raw_history:
                bucket = sparklines_raw.setdefault(m.metric_name, [])
                if len(bucket) < 30:
                    bucket.append({'value': m.value,
                                   'timestamp': m.timestamp.isoformat() if m.timestamp else None})
            sparklines = {k: list(reversed(v)) for k, v in sparklines_raw.items()}

            # Latest value per metric
            latest_per_metric: dict[str, dict] = {}
            for m in raw_history:
                if m.metric_name not in latest_per_metric:
                    latest_per_metric[m.metric_name] = {
                        'metric_name': m.metric_name,
                        'value':       m.value,
                        'timestamp':   m.timestamp.isoformat() if m.timestamp else None,
                    }
            latest = list(latest_per_metric.values())

            # Anomaly count (last 24h, unresolved)
            anomaly_cutoff = datetime.utcnow() - timedelta(hours=24)
            anomaly_count  = await session.scalar(
                select(func.count()).select_from(AnomalyEvent).where(and_(
                    AnomalyEvent.node_id  == node.id,
                    AnomalyEvent.resolved == False,
                    AnomalyEvent.detected_at >= anomaly_cutoff,
                ))
            ) or 0

            latest_anomaly = (await session.execute(
                select(AnomalyEvent)
                .where(AnomalyEvent.node_id == node.id)
                .order_by(desc(AnomalyEvent.detected_at))
                .limit(1)
            )).scalar_one_or_none()

            # Active users from Prometheus node_logind_sessions
            logind = latest_per_metric.get('node_logind_sessions')
            user_count = int(round(logind['value'])) if logind is not None else 0

            # Running processes: prefer Prometheus metric `procs_running`,
            # otherwise fall back to recent `NodeProcess` rows collected via SSH.
            procs_m = latest_per_metric.get('procs_running')
            if procs_m is not None:
                try:
                    running_procs = int(round(procs_m['value']))
                except Exception:
                    running_procs = 0
            else:
                from backend.models import NodeProcess
                recent_cutoff = datetime.utcnow() - timedelta(seconds=RECENT_SECONDS)
                proc_count = await session.scalar(
                    select(func.count()).select_from(NodeProcess).where(
                        and_(NodeProcess.node_id == node.id, NodeProcess.collected_at >= recent_cutoff)
                    )
                ) or 0
                running_procs = int(proc_count)

            node_dict = await _serialize_node(node, latest_metrics=latest)
            node_dict['sparklines']       = sparklines
            node_dict['active_anomalies'] = int(anomaly_count)
            node_dict['active_users']     = user_count
            node_dict['running_procs']    = running_procs
            node_dict['latest_anomaly']   = {
                'severity':    latest_anomaly.severity,
                'description': latest_anomaly.description,
                'detected_at': latest_anomaly.detected_at.isoformat() if latest_anomaly else None,
            } if latest_anomaly else None
            results.append(node_dict)
        return results


# ---------------------------------------------------------------------------
# Cluster history (Ganglia stacked area chart) — PostgreSQL date_trunc
# ---------------------------------------------------------------------------
async def get_cluster_history(
    metric_name: str = 'cpu_used_pct',
    hours: int = 1,
) -> list[dict[str, Any]]:
    async with get_session() as session:
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        # Determine downsampling bucket
        if hours > 168:      # > 1 week → bucket by day
            trunc_unit = 'day'
        elif hours > 24:     # > 1 day  → bucket by hour
            trunc_unit = 'hour'
        else:                # ≤ 1 day  → raw points
            trunc_unit = None

        if trunc_unit:
            # PostgreSQL date_trunc for downsampling
            ts_expr = func.date_trunc(trunc_unit, MetricHistory.timestamp).label('ts')
            rows = (await session.execute(
                select(ts_expr, func.avg(MetricHistory.value).label('val'), Node.hostname)
                .join(Node, MetricHistory.node_id == Node.id)
                .where(and_(
                    MetricHistory.metric_name == metric_name,
                    MetricHistory.timestamp   >= cutoff,
                ))
                .group_by('ts', Node.hostname)
                .order_by('ts')
            )).all()
            return [{'hostname': r.hostname,
                     'timestamp': r.ts.isoformat() if hasattr(r.ts, 'isoformat') else str(r.ts),
                     'value': round(float(r.val), 3)} for r in rows]
        else:
            rows = (await session.execute(
                select(MetricHistory.timestamp, MetricHistory.value, Node.hostname)
                .join(Node, MetricHistory.node_id == Node.id)
                .where(and_(
                    MetricHistory.metric_name == metric_name,
                    MetricHistory.timestamp   >= cutoff,
                ))
                .order_by(MetricHistory.timestamp)
            )).all()
            return [{'hostname': r.hostname,
                     'timestamp': r.timestamp.isoformat(),
                     'value': round(float(r.value), 3)} for r in rows]


# ---------------------------------------------------------------------------
# Node detail
# ---------------------------------------------------------------------------
async def get_node_detail(node_id: int) -> dict[str, Any] | None:
    async with get_session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            return None
        latest = (await session.execute(
            select(MetricHistory)
            .where(MetricHistory.node_id == node.id)
            .order_by(desc(MetricHistory.timestamp))
            .limit(40)
        )).scalars().all()
        metrics = [{'metric_name': m.metric_name, 'value': m.value,
                    'timestamp': m.timestamp.isoformat() if m.timestamp else None,
                    'labels': m.labels} for m in latest]
        # Baselines
        baselines = (await session.execute(
            select(NodeBaseline).where(NodeBaseline.node_id == node.id)
        )).scalars().all()
        bl_data = [{
            'metric_name': b.metric_name, 'mean': b.mean, 'std': b.std,
            'p95': b.p95, 'p99': b.p99,
            'computed_at': b.computed_at.isoformat() if b.computed_at else None,
        } for b in baselines]

        result = await _serialize_node(node, latest_metrics=metrics)
        result['baselines'] = bl_data
        return result


# ---------------------------------------------------------------------------
# Node metrics time-series
# ---------------------------------------------------------------------------
async def get_node_metrics(
    node_id: int,
    metric_name: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    async with get_session() as session:
        stmt = (
            select(MetricHistory)
            .where(MetricHistory.node_id == node_id)
            .order_by(desc(MetricHistory.timestamp))
            .limit(limit)
        )
        if metric_name:
            stmt = stmt.where(MetricHistory.metric_name == metric_name)
        metrics = (await session.execute(stmt)).scalars().all()
        return [await _serialize_metric(m) for m in metrics]


# ---------------------------------------------------------------------------
# Anomaly feed
# ---------------------------------------------------------------------------
async def get_anomaly_feed(limit: int = 100) -> list[dict[str, Any]]:
    async with get_session() as session:
        events = (await session.execute(
            select(AnomalyEvent, Node.hostname)
            .join(Node, Node.id == AnomalyEvent.node_id)
            .order_by(desc(AnomalyEvent.detected_at))
            .limit(limit)
        )).all()
        return [{
            'id':           e.AnomalyEvent.id,
            'node_id':      e.AnomalyEvent.node_id,
            'hostname':     e.hostname,
            'detected_at':  e.AnomalyEvent.detected_at.isoformat() if e.AnomalyEvent.detected_at else None,
            'resolved_at':  e.AnomalyEvent.resolved_at.isoformat() if e.AnomalyEvent.resolved_at else None,
            'metric_name':  e.AnomalyEvent.metric_name,
            'anomaly_score':e.AnomalyEvent.anomaly_score,
            'metric_value': e.AnomalyEvent.metric_value,
            'severity':     e.AnomalyEvent.severity,
            'detector':     e.AnomalyEvent.detector,
            'description':  e.AnomalyEvent.description,
            'resolved':     e.AnomalyEvent.resolved,
        } for e in events]


async def get_node_anomalies(node_id: int, limit: int = 50) -> list[dict[str, Any]]:
    async with get_session() as session:
        events = (await session.execute(
            select(AnomalyEvent, Node.hostname)
            .join(Node, Node.id == AnomalyEvent.node_id)
            .where(AnomalyEvent.node_id == node_id)
            .order_by(desc(AnomalyEvent.detected_at))
            .limit(limit)
        )).all()
        return [{
            'id':           e.AnomalyEvent.id,
            'node_id':      e.AnomalyEvent.node_id,
            'hostname':     e.hostname,
            'detected_at':  e.AnomalyEvent.detected_at.isoformat() if e.AnomalyEvent.detected_at else None,
            'resolved_at':  e.AnomalyEvent.resolved_at.isoformat() if e.AnomalyEvent.resolved_at else None,
            'metric_name':  e.AnomalyEvent.metric_name,
            'anomaly_score':e.AnomalyEvent.anomaly_score,
            'metric_value': e.AnomalyEvent.metric_value,
            'severity':     e.AnomalyEvent.severity,
            'detector':     e.AnomalyEvent.detector,
            'description':  e.AnomalyEvent.description,
            'resolved':     e.AnomalyEvent.resolved,
        } for e in events]


async def resolve_anomaly(node_id: int, anomaly_id: int) -> dict[str, Any] | None:
    async with get_session() as session:
        ev = await session.get(AnomalyEvent, anomaly_id)
        if ev is None or ev.node_id != node_id:
            return None
        ev.resolved    = True
        ev.resolved_at = datetime.utcnow()
        await session.commit()
        return {'id': ev.id, 'resolved': True, 'resolved_at': ev.resolved_at.isoformat()}


# ---------------------------------------------------------------------------
# Infrastructure health
# ---------------------------------------------------------------------------
async def get_node_health(node_id: int) -> dict[str, Any]:
    """Return the latest result for each check_type for a node."""
    async with get_session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            return {}
        # Latest row per check_type
        checks = (await session.execute(
            select(InfraCheck)
            .where(InfraCheck.node_id == node_id)
            .order_by(desc(InfraCheck.checked_at))
            .limit(40)
        )).scalars().all()

        latest_per_type: dict[str, dict] = {}
        for c in checks:
            if c.check_type not in latest_per_type:
                latest_per_type[c.check_type] = {
                    'check_type': c.check_type,
                    'passed':     c.passed,
                    'detail':     c.detail,
                    'checked_at': c.checked_at.isoformat() if c.checked_at else None,
                }
        return {
            'node_id':  node_id,
            'hostname': node.hostname,
            'status':   node.status,
            'checks':   list(latest_per_type.values()),
        }


# ---------------------------------------------------------------------------
# User sessions & processes
# ---------------------------------------------------------------------------
async def get_node_users(node_id: int) -> list[dict[str, Any]]:
    async with get_session() as session:
        cutoff = datetime.utcnow() - timedelta(minutes=15)
        sessions = (await session.execute(
            select(NodeUserSession)
            .where(and_(NodeUserSession.node_id == node_id,
                        NodeUserSession.collected_at >= cutoff))
            .order_by(desc(NodeUserSession.collected_at))
        )).scalars().all()
        return [{'id': s.id, 'username': s.username, 'terminal': s.terminal,
                 'remote_host': s.remote_host,
                 'login_time': s.login_time.isoformat() if s.login_time else None,
                 'collected_at': s.collected_at.isoformat() if s.collected_at else None,
                 } for s in sessions]


async def get_node_processes(node_id: int) -> list[dict[str, Any]]:
    async with get_session() as session:
        cutoff = datetime.utcnow() - timedelta(minutes=15)
        processes = (await session.execute(
            select(NodeProcess)
            .where(and_(NodeProcess.node_id == node_id,
                        NodeProcess.collected_at >= cutoff))
            .order_by(desc(NodeProcess.cpu_pct))
            .limit(20)
        )).scalars().all()
        # Determine total process count for this node.
        # Prefer the SSH-collected procs_running metric (labels=None) from `ps -e | wc -l`
        # over the Prometheus node_exporter metric (labels={}) which only counts running processes.
        # Fall back to Prometheus if SSH metric is not available.
        total_count = 0
        try:
            # First, try to get SSH-collected metric (labels IS NULL)
            latest_proc_metric = (await session.execute(
                select(MetricHistory.value)
                .where(and_(MetricHistory.node_id == node_id,
                            MetricHistory.metric_name == 'procs_running',
                            MetricHistory.labels.is_(None)))
                .order_by(desc(MetricHistory.timestamp))
                .limit(1)
            )).scalar_one_or_none()
            
            # Fall back to any procs_running if SSH metric not found
            if latest_proc_metric is None:
                latest_proc_metric = (await session.execute(
                    select(MetricHistory.value)
                    .where(and_(MetricHistory.node_id == node_id,
                                MetricHistory.metric_name == 'procs_running'))
                    .order_by(desc(MetricHistory.timestamp))
                    .limit(1)
                )).scalar_one_or_none()
            
            if latest_proc_metric is not None:
                try:
                    total_count = int(round(float(latest_proc_metric)))
                except Exception:
                    total_count = 0
        except Exception:
            total_count = 0

        return {
            'total': int(total_count),
            'processes': [
                {'id': p.id, 'pid': p.pid, 'username': p.username,
                 'cpu_pct': p.cpu_pct, 'mem_pct': p.mem_pct,
                 'command': p.command, 'status': p.status,
                 'collected_at': p.collected_at.isoformat() if p.collected_at else None,
                 } for p in processes
            ]
        }
