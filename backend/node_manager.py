"""
node_manager.py - Node lifecycle helpers for ONGC AI Cluster Monitor.

Responsibilities:
  1. write_nodes_yaml(nodes)   - regenerate config/nodes.yaml from a list of ManagedNode rows
  2. reload_prometheus()        - POST {PROMETHEUS_URL}/-/reload (requires --web.enable-lifecycle)
  3. validate_node(ip, port)   - connectivity checks: ping -> port -> SSH
  4. sync_prometheus()          - convenience: write YAML + reload in one call
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import socket
from datetime import datetime
from pathlib import Path

import httpx
import yaml

logger = logging.getLogger(__name__)

PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://prometheus:9090')
SSH_KEY_PATH   = os.getenv('SSH_KEY_PATH', '')
SSH_USERNAME   = os.getenv('SSH_USERNAME', 'ubuntu')
IS_WINDOWS     = platform.system() == 'Windows'

NODES_YAML_PATH = Path(
    os.getenv('NODES_YAML',
              str(Path(__file__).parent.parent / 'config' / 'nodes.yaml'))
)


# ---------------------------------------------------------------------------
# YAML writer
# ---------------------------------------------------------------------------

def write_nodes_yaml(managed_nodes) -> None:
    """Regenerate config/nodes.yaml from the given list of ManagedNode objects.
    Only enabled nodes are written. Prometheus reads this file at startup and
    after a /-/reload call.
    """
    enabled = [n for n in managed_nodes if n.enabled]
    targets = [f"{n.ip_address}:{n.node_exporter_port}" for n in enabled]

    data = [
        {
            'labels': {'job': 'node_exporter'},
            'targets': targets,
        }
    ]

    NODES_YAML_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(NODES_YAML_PATH, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    logger.info('nodes.yaml written with %d target(s): %s', len(targets), targets)


# ---------------------------------------------------------------------------
# Prometheus reload
# ---------------------------------------------------------------------------

async def reload_prometheus() -> dict:
    """POST to Prometheus /-/reload (requires --web.enable-lifecycle flag).
    Returns a dict with success bool and detail string.
    """
    url = f"{PROMETHEUS_URL.rstrip('/')}/-/reload"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url)
        if resp.status_code == 200:
            logger.info('Prometheus reloaded successfully')
            return {'success': True, 'detail': 'Prometheus reloaded'}
        else:
            logger.warning('Prometheus reload returned HTTP %d: %s', resp.status_code, resp.text)
            return {'success': False, 'detail': f'HTTP {resp.status_code}: {resp.text}'}
    except Exception as exc:
        logger.warning('Prometheus reload failed: %s', exc)
        return {'success': False, 'detail': str(exc)}


# ---------------------------------------------------------------------------
# Full sync: write YAML + reload
# ---------------------------------------------------------------------------

async def sync_prometheus(managed_nodes) -> dict:
    """Write nodes.yaml from DB list, then reload Prometheus."""
    write_nodes_yaml(managed_nodes)
    return await reload_prometheus()


# ---------------------------------------------------------------------------
# Connectivity validator
# ---------------------------------------------------------------------------

async def validate_node(ip: str, port: int = 9100, ssh_username: str = None) -> dict:
    """Run three checks against a node:
      1. ping  - ICMP reachability
      2. port  - TCP connect to node_exporter port
      3. ssh   - asyncssh lightweight connect (skipped if SSH_KEY_PATH is empty)

    Returns a dict with overall status and per-check results.
    """
    username = ssh_username or SSH_USERNAME

    # 1. Ping
    ping_ok, ping_detail = await _check_ping(ip)

    # 2. Port (TCP connect)
    port_ok, port_detail = await _check_port(ip, port)

    # 3. SSH
    if SSH_KEY_PATH:
        ssh_ok, ssh_detail = await _check_ssh(ip, username)
    else:
        ssh_ok, ssh_detail = False, 'SSH key not configured (SSH_KEY_PATH is empty)'

    # Overall: ok if ping and port both pass; SSH is informational
    overall = 'ok' if (ping_ok and port_ok) else 'failed'

    parts = []
    if not ping_ok:
        parts.append(f'ping failed: {ping_detail}')
    if not port_ok:
        parts.append(f'port {port} unreachable: {port_detail}')
    if not ssh_ok:
        parts.append(f'SSH: {ssh_detail}')
    summary = '; '.join(parts) if parts else 'All checks passed'

    return {
        'overall': overall,
        'ping': {'passed': ping_ok, 'detail': ping_detail},
        'port': {'passed': port_ok, 'detail': port_detail},
        'ssh':  {'passed': ssh_ok,  'detail': ssh_detail},
        'summary': summary,
    }


# ---------------------------------------------------------------------------
# Internal check helpers
# ---------------------------------------------------------------------------

async def _check_ping(ip: str):
    import subprocess

    def _do_ping():
        if IS_WINDOWS:
            cmd = ['ping', '-n', '1', '-w', '1000', ip]
        else:
            cmd = ['ping', '-c', '1', '-W', '2', ip]
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=6)
            return r.returncode
        except subprocess.TimeoutExpired:
            return -1
        except Exception:
            return -2

    try:
        rc = await asyncio.wait_for(asyncio.to_thread(_do_ping), timeout=10.0)
        if rc == 0:
            return True, 'reachable'
        return False, f'ping returned code {rc}'
    except asyncio.TimeoutError:
        return False, 'ping timed out'
    except Exception as exc:
        return False, str(exc)


async def _check_port(ip: str, port: int):
    def _do_connect():
        try:
            s = socket.create_connection((ip, port), timeout=5)
            s.close()
            return True, f'port {port} open'
        except ConnectionRefusedError:
            return False, f'port {port} refused'
        except socket.timeout:
            return False, f'port {port} timed out'
        except Exception as exc:
            return False, str(exc)

    try:
        ok, detail = await asyncio.wait_for(asyncio.to_thread(_do_connect), timeout=8.0)
        return ok, detail
    except asyncio.TimeoutError:
        return False, f'port {port} check timed out'
    except Exception as exc:
        return False, str(exc)


async def _check_ssh(ip: str, username: str):
    if not SSH_KEY_PATH:
        return False, 'SSH_KEY_PATH not configured'
    try:
        import asyncssh
        conn = await asyncio.wait_for(
            asyncssh.connect(
                ip,
                username=username,
                client_keys=[SSH_KEY_PATH],
                known_hosts=None,
                connect_timeout=8,
            ),
            timeout=12.0,
        )
        await conn.close()
        return True, f'connected as {username}'
    except asyncio.TimeoutError:
        return False, 'SSH timed out'
    except Exception as exc:
        return False, str(exc)[:120]
