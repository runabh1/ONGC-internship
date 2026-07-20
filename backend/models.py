from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Column, DateTime, Float, Integer, String, Text,
    Boolean, ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ---------------------------------------------------------------------------
# ManagedNode — operator-controlled node registry (replaces manual nodes.yaml)
# ---------------------------------------------------------------------------
class ManagedNode(Base):
    """Each row represents a node that the operator has added via the UI.
    This is the single source of truth for which nodes are monitored.
    Prometheus config/nodes.yaml is regenerated from this table whenever
    rows are inserted, updated, or deleted.
    """
    __tablename__ = 'managed_nodes'
    id                 = Column(Integer, primary_key=True)
    ip_address         = Column(String(64),  nullable=False, unique=True)
    label              = Column(String(128), nullable=True)   # friendly display name
    ssh_username       = Column(String(128), nullable=True)   # stored for display / future per-node use
    node_exporter_port = Column(Integer,     nullable=False, default=9100)
    enabled            = Column(Boolean,     nullable=False, default=True)
    # validation_status: 'pending' | 'ok' | 'failed' | 'unknown'
    validation_status  = Column(String(32),  nullable=False, default='unknown')
    validation_detail  = Column(Text,        nullable=True)
    last_validated_at  = Column(DateTime,    nullable=True)
    added_at           = Column(DateTime,    nullable=False, default=datetime.utcnow)
    updated_at         = Column(DateTime,    nullable=True,  onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# Cluster — grid → cluster → host hierarchy (Ganglia-style)
# ---------------------------------------------------------------------------
class Cluster(Base):
    __tablename__ = 'clusters'
    id         = Column(Integer, primary_key=True)
    name       = Column(String(128), unique=True, nullable=False, default='ONGC HPC Cluster')
    created_at = Column(DateTime, default=datetime.utcnow)
    nodes      = relationship('Node', back_populates='cluster')


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------
class Node(Base):
    __tablename__ = 'nodes'
    id               = Column(Integer, primary_key=True)
    hostname         = Column(String(255), unique=True, nullable=False)
    ip_address       = Column(String(64),  nullable=True)
    cluster_id       = Column(Integer, ForeignKey('clusters.id'), nullable=True)
    # status: online | warning | critical | offline | warmup | unknown
    status           = Column(String(32), nullable=False, default='unknown')
    os_version       = Column(String(255), nullable=True)
    architecture     = Column(String(64),  nullable=True)
    boot_time        = Column(DateTime,    nullable=True)
    # warmup tracking
    warmup_started_at = Column(DateTime,   nullable=True)
    warmup_ends_at    = Column(DateTime,   nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

    cluster       = relationship('Cluster',          back_populates='nodes')
    metrics       = relationship('MetricHistory',    back_populates='node')
    incidents     = relationship('Incident',         back_populates='node')
    user_sessions = relationship('NodeUserSession',  back_populates='node')
    processes     = relationship('NodeProcess',      back_populates='node')
    anomaly_events= relationship('AnomalyEvent',     back_populates='node')
    infra_checks  = relationship('InfraCheck',       back_populates='node')
    baselines     = relationship('NodeBaseline',     back_populates='node')


# ---------------------------------------------------------------------------
# Metric History  (indexed for time-series queries)
# ---------------------------------------------------------------------------
class MetricHistory(Base):
    __tablename__ = 'metric_history'
    id          = Column(Integer, primary_key=True)
    node_id     = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    metric_name = Column(String(128), nullable=False)
    timestamp   = Column(DateTime, nullable=False)
    value       = Column(Float,    nullable=False)
    labels      = Column(Text,     nullable=True)
    node        = relationship('Node', back_populates='metrics')

    __table_args__ = (
        Index('ix_mh_node_metric_ts', 'node_id', 'metric_name', 'timestamp'),
    )


# ---------------------------------------------------------------------------
# Node Baselines  (computed at end of warmup, seeds statistical detectors)
# ---------------------------------------------------------------------------
class NodeBaseline(Base):
    __tablename__ = 'node_baselines'
    id          = Column(Integer, primary_key=True)
    node_id     = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    metric_name = Column(String(128), nullable=False)
    mean        = Column(Float, nullable=True)
    std         = Column(Float, nullable=True)
    p95         = Column(Float, nullable=True)
    p99         = Column(Float, nullable=True)
    computed_at = Column(DateTime, default=datetime.utcnow)
    node        = relationship('Node', back_populates='baselines')

    __table_args__ = (
        UniqueConstraint('node_id', 'metric_name', name='uq_baseline_node_metric'),
        Index('ix_baseline_node_metric', 'node_id', 'metric_name'),
    )


# ---------------------------------------------------------------------------
# Infrastructure Health Checks
# ---------------------------------------------------------------------------
class InfraCheck(Base):
    """One row per (node, check_type) per run — check_type: ping | node_exporter | prometheus | ssh"""
    __tablename__ = 'infra_checks'
    id         = Column(Integer, primary_key=True)
    node_id    = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    check_type = Column(String(32), nullable=False)
    passed     = Column(Boolean,    nullable=False)
    detail     = Column(Text,       nullable=True)
    checked_at = Column(DateTime,   default=datetime.utcnow)
    node       = relationship('Node', back_populates='infra_checks')

    __table_args__ = (
        Index('ix_infra_checks_node_type_ts', 'node_id', 'check_type', 'checked_at'),
    )


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
class Incident(Base):
    __tablename__ = 'incidents'
    id          = Column(Integer, primary_key=True)
    node_id     = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    start_time  = Column(DateTime, nullable=False)
    end_time    = Column(DateTime, nullable=True)
    peak_value  = Column(Float,    nullable=True)
    status      = Column(String(32), nullable=False, default='Monitoring')
    confidence  = Column(Float,    nullable=True)
    severity    = Column(String(32), nullable=True)
    description = Column(Text,     nullable=True)
    node        = relationship('Node', back_populates='incidents')


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
class Alert(Base):
    __tablename__ = 'alerts'
    id         = Column(Integer, primary_key=True)
    node_id    = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    alert_time = Column(DateTime,  nullable=False)
    severity   = Column(String(32), nullable=False)
    status     = Column(String(32), nullable=False, default='active')
    summary    = Column(Text,      nullable=True)
    node       = relationship('Node')


# ---------------------------------------------------------------------------
# User Session Tracking
# ---------------------------------------------------------------------------
class NodeUserSession(Base):
    __tablename__ = 'node_user_sessions'
    id          = Column(Integer, primary_key=True)
    node_id     = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    username    = Column(String(128), nullable=False)
    terminal    = Column(String(64),  nullable=True)
    remote_host = Column(String(128), nullable=True)
    login_time  = Column(DateTime,    nullable=True)
    collected_at= Column(DateTime,    nullable=False, default=datetime.utcnow)
    node        = relationship('Node', back_populates='user_sessions')


# ---------------------------------------------------------------------------
# Process Tracking
# ---------------------------------------------------------------------------
class NodeProcess(Base):
    __tablename__ = 'node_processes'
    id           = Column(Integer, primary_key=True)
    node_id      = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    pid          = Column(Integer, nullable=False)
    username     = Column(String(128), nullable=True)
    cpu_pct      = Column(Float,       nullable=True)
    mem_pct      = Column(Float,       nullable=True)
    command      = Column(Text,        nullable=True)
    status       = Column(String(32),  nullable=True)
    collected_at = Column(DateTime,    nullable=False, default=datetime.utcnow)
    node         = relationship('Node', back_populates='processes')


# ---------------------------------------------------------------------------
# Anomaly Events
# ---------------------------------------------------------------------------
class AnomalyEvent(Base):
    __tablename__ = 'anomaly_events'
    id             = Column(Integer, primary_key=True)
    node_id        = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    detected_at    = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at    = Column(DateTime, nullable=True)
    metric_name    = Column(String(128), nullable=True)
    anomaly_score  = Column(Float,    nullable=True)   # ensemble 0-1
    metric_value   = Column(Float,    nullable=True)
    severity       = Column(String(32), nullable=True) # Low/Medium/High/Critical
    detector       = Column(String(64), nullable=True) # Ensemble / Threshold / ZScore ...
    description    = Column(Text,     nullable=True)
    resolved       = Column(Boolean,  nullable=False, default=False)
    node           = relationship('Node', back_populates='anomaly_events')

    __table_args__ = (
        Index('ix_anomaly_node_metric_ts', 'node_id', 'metric_name', 'detected_at'),
    )
