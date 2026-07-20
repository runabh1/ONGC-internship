"""
FastAPI router for ONGC AI Cluster Monitor.

Endpoints:
  GET    /api/status
  GET    /api/cluster/overview
  GET    /api/cluster/summary
  GET    /api/cluster/nodes
  GET    /api/cluster/history
  GET    /api/cluster/anomaly-feed
  GET    /api/cluster/node/{id}
  GET    /api/cluster/node/{id}/metrics
  GET    /api/cluster/node/{id}/anomalies
  POST   /api/cluster/node/{id}/anomalies/{anomaly_id}/resolve
  GET    /api/cluster/node/{id}/health
  GET    /api/cluster/node/{id}/users
  GET    /api/cluster/node/{id}/processes
  GET    /api/cluster/node/{id}/export
  WS     /ws/live

  --- Node Management (replaces manual nodes.yaml editing) ---
  GET    /api/nodes/managed                — list all managed nodes
  POST   /api/nodes/managed                — add a new node
  PUT    /api/nodes/managed/{id}           — update a node
  DELETE /api/nodes/managed/{id}           — remove a node
  POST   /api/nodes/managed/{id}/validate  — run connectivity check
  POST   /api/nodes/managed/reload-prometheus — regenerate YAML + reload
"""
from typing import Optional
import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
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
from backend.db import get_session
from backend.models import ManagedNode
from backend.node_manager import validate_node, sync_prometheus
from sqlalchemy import select

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
# Node Management — CRUD + validate + Prometheus sync
# ---------------------------------------------------------------------------

class ManagedNodeCreate(BaseModel):
    ip_address:         str         = Field(..., description='IPv4 address of the node')
    label:              str | None  = Field(None, description='Optional friendly name')
    ssh_username:       str | None  = Field(None, description='SSH username (stored for display)')
    node_exporter_port: int         = Field(9100, description='node_exporter port')
    enabled:            bool        = Field(True)

class ManagedNodeUpdate(BaseModel):
    ip_address:         str | None  = None
    label:              str | None  = None
    ssh_username:       str | None  = None
    node_exporter_port: int | None  = None
    enabled:            bool | None = None


def _node_to_dict(n: ManagedNode) -> dict:
    return {
        'id':                 n.id,
        'ip_address':         n.ip_address,
        'label':              n.label,
        'ssh_username':       n.ssh_username,
        'node_exporter_port': n.node_exporter_port,
        'enabled':            n.enabled,
        'validation_status':  n.validation_status,
        'validation_detail':  n.validation_detail,
        'last_validated_at':  n.last_validated_at.isoformat() if n.last_validated_at else None,
        'added_at':           n.added_at.isoformat() if n.added_at else None,
    }


@router.get('/nodes/managed')
async def list_managed_nodes():
    """Return all managed nodes from the DB."""
    async with get_session() as session:
        rows = (await session.execute(select(ManagedNode).order_by(ManagedNode.added_at))).scalars().all()
        return [_node_to_dict(n) for n in rows]


@router.post('/nodes/managed', status_code=201)
async def add_managed_node(body: ManagedNodeCreate):
    """Add a new node. Automatically regenerates nodes.yaml and reloads Prometheus."""
    async with get_session() as session:
        existing = (await session.execute(
            select(ManagedNode).where(ManagedNode.ip_address == body.ip_address)
        )).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail=f'Node {body.ip_address} already exists')

        node = ManagedNode(
            ip_address=body.ip_address,
            label=body.label,
            ssh_username=body.ssh_username,
            node_exporter_port=body.node_exporter_port,
            enabled=body.enabled,
        )
        session.add(node)
        await session.commit()
        await session.refresh(node)

        # Sync Prometheus
        all_nodes = (await session.execute(select(ManagedNode))).scalars().all()
        await sync_prometheus(all_nodes)

        return _node_to_dict(node)


@router.put('/nodes/managed/{node_id}')
async def update_managed_node(node_id: int, body: ManagedNodeUpdate):
    """Update an existing managed node. Regenerates YAML + reloads Prometheus."""
    async with get_session() as session:
        node = await session.get(ManagedNode, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail='Managed node not found')

        if body.ip_address is not None:
            node.ip_address = body.ip_address
        if body.label is not None:
            node.label = body.label
        if body.ssh_username is not None:
            node.ssh_username = body.ssh_username
        if body.node_exporter_port is not None:
            node.node_exporter_port = body.node_exporter_port
        if body.enabled is not None:
            node.enabled = body.enabled

        await session.commit()
        await session.refresh(node)

        all_nodes = (await session.execute(select(ManagedNode))).scalars().all()
        await sync_prometheus(all_nodes)

        return _node_to_dict(node)


@router.delete('/nodes/managed/{node_id}', status_code=200)
async def delete_managed_node(node_id: int):
    """Remove a managed node. Regenerates YAML + reloads Prometheus."""
    async with get_session() as session:
        node = await session.get(ManagedNode, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail='Managed node not found')

        ip = node.ip_address
        await session.delete(node)
        await session.commit()

        all_nodes = (await session.execute(select(ManagedNode))).scalars().all()
        await sync_prometheus(all_nodes)

        return {'status': 'deleted', 'ip_address': ip}


@router.post('/nodes/managed/{node_id}/validate')
async def validate_managed_node(node_id: int):
    """Run connectivity checks (ping + port + SSH) against a managed node."""
    async with get_session() as session:
        node = await session.get(ManagedNode, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail='Managed node not found')

        result = await validate_node(
            ip=node.ip_address,
            port=node.node_exporter_port,
            ssh_username=node.ssh_username,
        )

        node.validation_status = result['overall']
        node.validation_detail = result['summary']
        node.last_validated_at = datetime.utcnow()
        await session.commit()

        return {**result, 'node_id': node_id, 'ip_address': node.ip_address}


@router.post('/nodes/managed/validate-new')
async def validate_new_node(body: ManagedNodeCreate):
    """Validate connectivity for a node that has NOT been saved yet (pre-save check)."""
    result = await validate_node(
        ip=body.ip_address,
        port=body.node_exporter_port,
        ssh_username=body.ssh_username,
    )
    return result


@router.post('/nodes/managed/reload-prometheus')
async def trigger_prometheus_reload():
    """Manually regenerate nodes.yaml from DB and reload Prometheus."""
    async with get_session() as session:
        all_nodes = (await session.execute(select(ManagedNode))).scalars().all()
    result = await sync_prometheus(all_nodes)
    return result


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
