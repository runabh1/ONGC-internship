"""
FastAPI router for ONGC AI Cluster Monitor.

Endpoints:
  GET  /api/status
  GET  /api/cluster/overview
  GET  /api/cluster/summary          — aggregate CPU/MEM/Load/Disk/Net across nodes
  GET  /api/cluster/nodes            — list with status + latest metrics + sparklines
  GET  /api/cluster/history          — time-series for stacked area chart (all nodes, one metric)
  GET  /api/cluster/anomaly-feed     — global anomaly timeline
  GET  /api/cluster/node/{id}
  GET  /api/cluster/node/{id}/metrics
  GET  /api/cluster/node/{id}/anomalies
  POST /api/cluster/node/{id}/anomalies/{anomaly_id}/resolve
  GET  /api/cluster/node/{id}/health — infra check results (ping/node_exporter/prometheus/ssh)
  GET  /api/cluster/node/{id}/users
  GET  /api/cluster/node/{id}/processes
  GET  /api/cluster/node/{id}/export — CSV or JSON metric export
  WS   /ws/live                      — push updates to frontend every 30s
"""
from typing import Optional
import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from backend.services import (
    get_cluster_overview,
    get_cluster_summary,
    get_cluster_history,
    get_node_detail,
    get_node_metrics,
    get_nodes,
    get_node_anomalies,
    get_anomaly_feed,
    get_node_users,
    get_node_processes,
    get_node_health,
    resolve_anomaly,
    get_incidents_feed,
    get_alerts_feed,
)
from backend.collector import backfill_baselines

router = APIRouter()

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
@router.get('/status')
def status():
    return {'status': 'ok', 'time': datetime.utcnow().isoformat()}

# ---------------------------------------------------------------------------
# Cluster overview + aggregate summary
# ---------------------------------------------------------------------------
@router.get('/cluster/overview')
async def cluster_overview():
    return await get_cluster_overview()

@router.get('/cluster/summary')
async def cluster_summary():
    """Aggregate avg CPU / MEM / Load / Disk and total Net across online nodes."""
    return await get_cluster_summary()

# ---------------------------------------------------------------------------
# Node list and detail
# ---------------------------------------------------------------------------
@router.get('/cluster/nodes')
async def cluster_nodes():
    return await get_nodes()

@router.get('/cluster/node/{node_id}')
async def cluster_node_detail(node_id: int):
    node = await get_node_detail(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail='Node not found')
    return node

# ---------------------------------------------------------------------------
# Node metrics time-series
# ---------------------------------------------------------------------------
@router.get('/cluster/node/{node_id}/metrics')
async def cluster_node_metrics(
    node_id: int,
    metric_name: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=2000),
):
    return await get_node_metrics(node_id, metric_name=metric_name, limit=limit)

# ---------------------------------------------------------------------------
# Cluster history chart (Ganglia stacked area)
# ---------------------------------------------------------------------------
@router.get('/cluster/history')
async def cluster_history(
    metric: str = Query('cpu_used_pct'),
    hours:  int = Query(1, ge=1, le=8760),
):
    """Time-series for ALL nodes for a single metric, server-side downsampled."""
    return await get_cluster_history(metric_name=metric, hours=hours)

# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------
@router.get('/cluster/anomaly-feed')
async def anomaly_feed(limit: int = Query(100, ge=1, le=500)):
    return await get_anomaly_feed(limit=limit)

@router.get('/cluster/node/{node_id}/anomalies')
async def node_anomalies(node_id: int, limit: int = Query(50, ge=1, le=200)):
    return await get_node_anomalies(node_id, limit=limit)

@router.post('/cluster/node/{node_id}/anomalies/{anomaly_id}/resolve')
async def resolve_node_anomaly(node_id: int, anomaly_id: int):
    """Mark a specific anomaly as resolved."""
    result = await resolve_anomaly(node_id, anomaly_id)
    if result is None:
        raise HTTPException(status_code=404, detail='Anomaly not found')
    return result


# ---------------------------------------------------------------------------
# Incidents & Alerts feeds
# ---------------------------------------------------------------------------
@router.get('/cluster/incidents')
async def cluster_incidents(limit: int = Query(100, ge=1, le=500)):
    """All open + recent incidents across the cluster."""
    return await get_incidents_feed(limit=limit)

@router.get('/cluster/alerts')
async def cluster_alerts(limit: int = Query(100, ge=1, le=500)):
    """All active + recent alerts across the cluster."""
    return await get_alerts_feed(limit=limit)

# ---------------------------------------------------------------------------
# Infrastructure health
# ---------------------------------------------------------------------------
@router.get('/cluster/node/{node_id}/health')
async def node_health(node_id: int):
    """Individual check results: ping, node_exporter, prometheus, ssh."""
    return await get_node_health(node_id)

# ---------------------------------------------------------------------------
# User sessions and processes
# ---------------------------------------------------------------------------
@router.get('/cluster/node/{node_id}/users')
async def node_users(node_id: int):
    return await get_node_users(node_id)

@router.get('/cluster/node/{node_id}/processes')
async def node_processes(node_id: int):
    return await get_node_processes(node_id)

# ---------------------------------------------------------------------------
# CSV / JSON Export
# ---------------------------------------------------------------------------
from fastapi.responses import StreamingResponse, JSONResponse
import io
import csv

@router.get('/cluster/node/{node_id}/export')
async def export_node_metrics(
    node_id: int,
    fmt:     str = Query('csv', regex='^(csv|json)$'),
    metric_name: Optional[str] = Query(None),
    hours:   int = Query(24, ge=1, le=8760),
):
    """Export a node's metric history as CSV or JSON."""
    from datetime import timedelta
    from backend.db import get_session
    from backend.models import MetricHistory, Node
    from sqlalchemy import select, and_, desc

    async with get_session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail='Node not found')
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        stmt = (
            select(MetricHistory)
            .where(and_(
                MetricHistory.node_id   == node_id,
                MetricHistory.timestamp >= cutoff,
            ))
            .order_by(MetricHistory.timestamp)
        )
        if metric_name:
            stmt = stmt.where(MetricHistory.metric_name == metric_name)
        rows = (await session.execute(stmt)).scalars().all()

    data = [
        {'hostname': node.hostname, 'metric': r.metric_name,
         'value': r.value, 'timestamp': r.timestamp.isoformat()}
        for r in rows
    ]
    fname_base = f'{node.hostname}_metrics_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}'

    if fmt == 'json':
        return JSONResponse(content=data, headers={
            'Content-Disposition': f'attachment; filename="{fname_base}.json"'
        })

    # CSV
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=['hostname', 'metric', 'value', 'timestamp'])
    writer.writeheader()
    writer.writerows(data)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{fname_base}.csv"'}
    )


# ---------------------------------------------------------------------------
# Node baselines
# ---------------------------------------------------------------------------
@router.get('/cluster/node/{node_id}/baselines')
async def node_baselines(node_id: int):
    """Return the ML baselines computed during warmup for a node."""
    from backend.db import get_session
    from backend.models import NodeBaseline, Node
    from sqlalchemy import select
    async with get_session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail='Node not found')
        rows = (await session.execute(
            select(NodeBaseline).where(NodeBaseline.node_id == node_id)
        )).scalars().all()
        return [
            {'metric_name': r.metric_name, 'mean': r.mean, 'std': r.std,
             'p95': r.p95, 'p99': r.p99}
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Admin — on-demand backfill
# ---------------------------------------------------------------------------
@router.post('/admin/backfill-baselines')
async def trigger_backfill():
    """Trigger an immediate ML baseline backfill from metric history."""
    try:
        await backfill_baselines()
        return {'status': 'ok', 'message': 'Baseline backfill completed'}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

# ---------------------------------------------------------------------------
# WebSocket — live push
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active = [c for c in self.active if c is not ws]

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@router.websocket('/ws/live')
async def websocket_live(websocket: WebSocket):
    """
    Push metrics_update, anomaly, and status_change messages to the frontend.
    Sends a full snapshot every 30 s; clients should use this to refresh without polling.
    """
    await manager.connect(websocket)
    try:
        while True:
            try:
                overview  = await get_cluster_overview()
                nodes     = await get_nodes()
                feed      = await get_anomaly_feed(limit=20)
                summary   = await get_cluster_summary()
                incidents = await get_incidents_feed(limit=20)
                alerts    = await get_alerts_feed(limit=20)
                await websocket.send_text(json.dumps({
                    'type': 'metrics_update',
                    'payload': {
                        'overview':  overview,
                        'nodes':     nodes,
                        'feed':      feed,
                        'summary':   summary,
                        'incidents': incidents,
                        'alerts':    alerts,
                        'ts':        datetime.utcnow().isoformat(),
                    }
                }))
            except Exception as exc:
                pass  # don't kill the loop on a transient DB error
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
