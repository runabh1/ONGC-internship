"""
Infrastructure Health Checker for ONGC AI Cluster Monitor.

Background asyncio task that checks each node's monitoring pipeline health:

  1. ping          — ICMP reachability  (subprocess)
  2. node_exporter — HTTP GET :9100/metrics → 200 + valid Prometheus format
  3. prometheus    — /api/v1/targets confirms node target is up
  4. ssh           — asyncssh lightweight connect (skipped if SSH_KEY_PATH is empty)

Status derivation:
  all pass                        → online
  ssh only fails                  → warning
  node_exporter / prometheus fail → critical
  ping fails                      → offline

Results are persisted to InfraCheck table. Node.status is updated after each cycle.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys
from datetime import datetime
from typing import Optional

import aiohttp
from sqlalchemy import select

from backend.db import AsyncSessionLocal
from backend.models import InfraCheck, Node

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HEALTH_CHECK_INTERVAL = int(os.getenv('HEALTH_CHECK_INTERVAL', '30'))
NODE_EXPORTER_PORT    = int(os.getenv('NODE_EXPORTER_PORT',    '9100'))
PROMETHEUS_URL        = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')
SSH_KEY_PATH          = os.getenv('SSH_KEY_PATH', '')
SSH_USERNAME          = os.getenv('SSH_USERNAME', 'ubuntu')

IS_WINDOWS = platform.system() == 'Windows'

# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

async def _check_ping(ip: str) -> tuple[bool, str]:
    """ICMP ping — cross-platform. Uses asyncio.to_thread + subprocess.run
    to avoid NotImplementedError on Windows with uvicorn --reload."""
    import subprocess

    def _do_ping() -> int:
        if IS_WINDOWS:
            cmd = ['ping', '-n', '1', '-w', '1000', ip]
        else:
            cmd = ['ping', '-c', '1', '-W', '1', ip]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return result.returncode
        except subprocess.TimeoutExpired:
            return -1
        except Exception:
            return -2

    try:
        returncode = await asyncio.wait_for(
            asyncio.to_thread(_do_ping), timeout=8.0
        )
        if returncode == 0:
            return True, 'reachable'
        elif returncode == -1:
            return False, 'ping timed out'
        elif returncode == -2:
            return False, 'ping subprocess error'
        else:
            return False, 'ping timed out / unreachable'
    except asyncio.TimeoutError:
        return False, 'ping timed out'
    except Exception as exc:
        return False, f'ping error: {repr(exc)}'


async def _check_node_exporter(ip: str, session: aiohttp.ClientSession) -> tuple[bool, str]:
    """GET http://<ip>:9100/metrics → 200 and contains Prometheus exposition."""
    url = f'http://{ip}:{NODE_EXPORTER_PORT}/metrics'
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status != 200:
                return False, f'HTTP {resp.status}'
            text = await resp.text()
            if 'node_cpu_seconds_total' not in text:
                return False, 'response does not contain node_cpu_seconds_total'
            return True, f'HTTP 200, {len(text)} bytes'
    except aiohttp.ClientConnectorError:
        return False, 'connection refused'
    except asyncio.TimeoutError:
        return False, 'connection timed out'
    except Exception as exc:
        return False, f'error: {exc}'


async def _check_prometheus_scrape(ip: str, session: aiohttp.ClientSession) -> tuple[bool, str]:
    """Query Prometheus /api/v1/targets to confirm node target is being scraped."""
    url = f'{PROMETHEUS_URL}/api/v1/targets'
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                return False, f'Prometheus API returned HTTP {resp.status}'
            data = await resp.json()
            targets = data.get('data', {}).get('activeTargets', [])
            for t in targets:
                labels = t.get('labels', {})
                instance = labels.get('instance', '')
                if ip in instance:
                    health = t.get('health', 'unknown')
                    last_err = t.get('lastError', '')
                    if health == 'up':
                        return True, f'target health=up, last scrape: {t.get("lastScrape","")}'
                    else:
                        return False, f'target health={health} — {last_err}'
            return False, f'no Prometheus target found for {ip}'
    except asyncio.TimeoutError:
        return False, 'Prometheus API timed out'
    except Exception as exc:
        return False, f'error: {exc}'


async def _check_ssh(ip: str, username: str) -> tuple[bool, str]:
    """Lightweight SSH connectivity check using asyncssh (skipped if no key configured)."""
    if not SSH_KEY_PATH:
        return True, 'SSH check skipped (SSH_KEY_PATH not configured)'
    try:
        import asyncssh  # type: ignore
        async with asyncssh.connect(
            ip, username=username,
            client_keys=[SSH_KEY_PATH],
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            result = await conn.run('echo ok', timeout=3)
            return result.exit_status == 0, 'SSH ok'
    except ImportError:
        return True, 'SSH check skipped (asyncssh not installed)'
    except Exception as exc:
        return False, f'SSH error: {exc}'


# ---------------------------------------------------------------------------
# Per-node health check
# ---------------------------------------------------------------------------

async def check_node(node: Node, http_session: aiohttp.ClientSession, target_user: str) -> dict:
    """Run all 4 checks for a node. Returns dict of results."""
    ip = node.ip_address or node.hostname

    ping_ok,   ping_detail   = await _check_ping(ip)
    ne_ok,     ne_detail     = await _check_node_exporter(ip, http_session)
    prom_ok,   prom_detail   = await _check_prometheus_scrape(ip, http_session)
    ssh_ok,    ssh_detail    = await _check_ssh(ip, target_user)

    results = {
        'ping':          (ping_ok,  ping_detail),
        'node_exporter': (ne_ok,    ne_detail),
        'prometheus':    (prom_ok,  prom_detail),
        'ssh':           (ssh_ok,   ssh_detail),
    }

    # Derive overall status
    # Some Docker and WSL environments do not allow ICMP ping from containers.
    # If ping fails but Prometheus and SSH are both healthy, keep node status unchanged.
    if not ping_ok and not (ne_ok and prom_ok and ssh_ok):
        status = 'offline'
    elif not ne_ok or not prom_ok:
        status = 'critical'
    elif not ssh_ok:
        status = 'warning'
    else:
        status = None  # do not override collector status (it's finer-grained by CPU)

    return {'results': results, 'derived_status': status}


# ---------------------------------------------------------------------------
# Main health checker loop
# ---------------------------------------------------------------------------

async def run_health_checker() -> None:
    """Background task — runs every HEALTH_CHECK_INTERVAL seconds."""
    logger.info('Health checker started — interval: %ds', HEALTH_CHECK_INTERVAL)
    while True:
        try:
            await _health_check_cycle()
        except Exception as exc:
            logger.error('Health check cycle error: %s', exc)
        await asyncio.sleep(HEALTH_CHECK_INTERVAL)


async def _health_check_cycle() -> None:
    now = datetime.utcnow()

    from backend.models import ManagedNode

    async with AsyncSessionLocal() as session:
        nodes = (await session.execute(select(Node))).scalars().all()
        if not nodes:
            return

        # Build IP → ssh_username map; fall back to global SSH_USERNAME per node
        managed_rows = (await session.execute(select(ManagedNode))).scalars().all()
        ssh_user_map = {
            mn.ip_address: (mn.ssh_username or SSH_USERNAME)
            for mn in managed_rows
        }

        connector = aiohttp.TCPConnector(limit=20, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as http_session:
            tasks = [
                check_node(
                    node,
                    http_session,
                    ssh_user_map.get(node.ip_address or node.hostname, SSH_USERNAME) or SSH_USERNAME,
                )
                for node in nodes
            ]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)

        for node, res in zip(nodes, results_list):
            if isinstance(res, Exception):
                logger.warning('Health check exception for %s: %s', node.hostname, res)
                continue

            check_results = res['results']
            derived_status = res['derived_status']

            # Persist InfraCheck rows
            for check_type, (passed, detail) in check_results.items():
                session.add(InfraCheck(
                    node_id=node.id,
                    check_type=check_type,
                    passed=passed,
                    detail=detail[:1000] if detail else None,
                    checked_at=now,
                ))
                if not passed:
                    logger.info(
                        'HEALTH [%s] %s — %s FAILED: %s',
                        check_type.upper(), node.hostname, check_type, detail
                    )

            # Only override node status with offline/critical/warning from health checker
            # (online/warmup status is managed by the metric collector)
            if derived_status is not None:
                if node.status not in ('warmup',) or derived_status == 'offline':
                    node.status = derived_status
                    logger.info(
                        'HEALTH STATUS %s → %s', node.hostname, derived_status
                    )

        await session.commit()
