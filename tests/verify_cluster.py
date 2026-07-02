"""Infrastructure verification module for cluster health checks."""

from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass
from typing import List, Optional

import requests

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False


@dataclass
class CheckResult:
    """Result of infrastructure check for a single node."""
    name: str
    ssh: bool
    exporter: bool
    prometheus: bool


def load_nodes() -> List[str]:
    """Load node addresses from environment or default list."""
    nodes_str = os.getenv('CLUSTER_NODES', '192.168.56.101:9100,192.168.56.102:9100,192.168.56.103:9100')
    return [node.strip() for node in nodes_str.split(',') if node.strip()]


def check_node_exporter(node: str) -> bool:
    """Check if Node Exporter is reachable on node (port 9100)."""
    try:
        # Extract host from "host:port" format
        host = node.split(':')[0] if ':' in node else node
        response = requests.get(f'http://{host}:9100/metrics', timeout=2)
        return response.status_code == 200
    except (requests.RequestException, socket.error):
        return False


def check_ssh(node: str, password: Optional[str] = None, username: str = 'root') -> bool:
    """
    Check if SSH is accessible on node (port 22).
    
    If password is provided and paramiko is available, attempts authentication.
    Otherwise, just checks port connectivity.
    """
    try:
        host = node.split(':')[0] if ':' in node else node
        
        # If password provided and paramiko available, do authentication check
        if password and HAS_PARAMIKO:
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(host, username=username, password=password, timeout=3)
                ssh.close()
                return True
            except (paramiko.AuthenticationException, paramiko.SSHException):
                return False
        
        # Otherwise just check port connectivity
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, 22))
        sock.close()
        return result == 0
    except (socket.error, OSError):
        return False


def check_prometheus_scrape(node: str, prometheus_url: str = None) -> bool:
    """Check if node is being scraped by Prometheus."""
    if prometheus_url is None:
        prometheus_url = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')
    
    try:
        # Get the instance identifier (host:port format)
        instance_id = node if ':' in node else f"{node}:9100"
        
        # Query Prometheus targets endpoint to check if this node is being scraped
        response = requests.get(
            f'{prometheus_url}/api/v1/targets',
            timeout=3
        )
        
        if response.status_code != 200:
            return False
        
        data = response.json()
        active_targets = data.get('data', {}).get('activeTargets', [])
        
        # Check if any active target matches this node with job=node_exporter
        for target in active_targets:
            labels = target.get('labels', {})
            if (labels.get('job') == 'node_exporter' and 
                labels.get('instance') == instance_id and
                target.get('health') == 'up'):
                return True
        
        return False
    except (requests.RequestException, ValueError):
        return False


def run_all_checks(password: Optional[str] = None, username: str = 'root') -> List[CheckResult]:
    """Run all infrastructure checks on all nodes."""
    nodes = load_nodes()
    prometheus_url = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')
    results: List[CheckResult] = []
    
    for node in nodes:
        ssh_ok = check_ssh(node, password=password, username=username)
        exporter_ok = check_node_exporter(node)
        prometheus_ok = check_prometheus_scrape(node, prometheus_url)
        
        results.append(
            CheckResult(
                name=node,
                ssh=ssh_ok,
                exporter=exporter_ok,
                prometheus=prometheus_ok,
            )
        )
    
    return results
