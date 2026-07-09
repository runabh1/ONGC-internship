"""
Incident lifecycle management for the ONGC AI Monitoring dashboard.

This module handles:
1. Incident creation and tracking
2. Incident status lifecycle (Active → Recovered)
3. Incident metadata (ID, start time, peak values, affected nodes)
4. Recovery detection and classification
5. Startup event detection to reduce false positives

Production monitoring systems track incidents as first-class entities with
complete lifecycle management. This module implements that pattern.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class Incident:
    """Represents a complete incident lifecycle in the monitoring system."""
    
    incident_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    peak_value: float = 0.0
    affected_nodes: List[str] = None
    detectors: List[str] = None
    status: str = 'Active'  # Active, Recovered, Acknowledged
    description: str = ''
    anomaly_samples: int = 0
    confidence_score: float = 0.0
    is_startup_event: bool = False
    recovery_time: Optional[datetime] = None
    
    def __post_init__(self):
        if self.affected_nodes is None:
            self.affected_nodes = []
        if self.detectors is None:
            self.detectors = []
    
    @property
    def duration_seconds(self) -> float:
        """Duration of the incident in seconds."""
        end = self.end_time or datetime.now(timezone.utc)
        return (end - self.start_time).total_seconds()
    
    @property
    def duration_str(self) -> str:
        """Human-readable duration."""
        seconds = int(self.duration_seconds)
        minutes = seconds // 60
        secs = seconds % 60
        if minutes == 0:
            return f"{secs}s"
        return f"{minutes}m {secs}s"
    
    @property
    def is_active(self) -> bool:
        """Whether the incident is currently active."""
        return self.status == 'Active'
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert incident to dictionary, handling datetime serialization."""
        data = asdict(self)
        data['start_time'] = self.start_time.isoformat() if self.start_time else None
        data['end_time'] = self.end_time.isoformat() if self.end_time else None
        data['recovery_time'] = self.recovery_time.isoformat() if self.recovery_time else None
        return data


class IncidentManager:
    """Manages incident lifecycle for anomaly detection."""
    
    def __init__(self, recovery_threshold_minutes: int = 2):
        """
        Initialize the incident manager.
        
        Args:
            recovery_threshold_minutes: Time (in minutes) that CPU must remain
                normal before marking incident as Recovered
        """
        self.recovery_threshold = timedelta(minutes=recovery_threshold_minutes)
        self.active_incidents: Dict[str, Incident] = {}
        self.recovered_incidents: List[Incident] = []
    
    @staticmethod
    def _generate_incident_id(timestamp: datetime, nodes: List[str]) -> str:
        """Generate a unique incident ID based on timestamp and affected nodes."""
        node_str = ','.join(sorted(nodes))
        data = f"{timestamp.isoformat()}|{node_str}"
        hash_digest = hashlib.md5(data.encode()).hexdigest()[:8]
        unix_timestamp = int(timestamp.timestamp())
        return f"INC-{unix_timestamp}-{hash_digest}"
    
    def detect_startup_event(
        self,
        df: pd.DataFrame,
        threshold_percentage: float = 90.0,
        max_duration_minutes: int = 5,
    ) -> bool:
        """
        Detect if anomaly pattern resembles startup initialization.
        
        Characteristics of startup events:
        1. All nodes spike simultaneously
        2. All nodes recover together (synchronized pattern)
        3. Happens very early in monitoring (first few samples)
        4. Spikes are consistent across all nodes
        
        Production events typically show:
        - Single or subset of nodes affected
        - Uneven recovery times
        - Can happen at any time
        
        Args:
            df: DataFrame with anomalies (timestamp, instance, value, etc.)
            threshold_percentage: CPU threshold for spike detection
            max_duration_minutes: Max duration for startup vs real incident
        
        Returns:
            True if pattern matches startup initialization
        """
        if df.empty or 'value' not in df.columns or 'instance' not in df.columns:
            return False
        
        # Get unique nodes with anomalies
        affected_nodes = df['instance'].unique()
        total_nodes_monitoring = df['instance'].nunique()
        
        # If not ALL nodes are affected, less likely to be startup
        if len(affected_nodes) < total_nodes_monitoring:
            return False
        
        # Check if most values are above threshold
        high_values = (df['value'] > threshold_percentage).sum()
        if high_values < len(df) * 0.7:  # At least 70% above threshold
            return False
        
        # Check if duration is short (typical for startup)
        if 'timestamp' in df.columns:
            try:
                df_copy = df.copy()
                if not pd.api.types.is_datetime64_any_dtype(df_copy['timestamp']):
                    df_copy['timestamp'] = pd.to_datetime(df_copy['timestamp'], unit='s', utc=True)
                
                time_span = df_copy['timestamp'].max() - df_copy['timestamp'].min()
                max_duration = pd.Timedelta(minutes=max_duration_minutes)
                
                if time_span > max_duration:
                    return False
            except Exception:
                pass
        
        return True
    
    def create_incident(
        self,
        anomalies_df: pd.DataFrame,
        detectors: List[str],
        confidence: float,
        current_time: Optional[datetime] = None,
    ) -> Optional[Incident]:
        """
        Create a new incident from anomaly data.
        
        Args:
            anomalies_df: DataFrame with detected anomalies
            detectors: List of detector names that triggered
            confidence: Consensus confidence score (0-1)
            current_time: Current timestamp (default: now)
        
        Returns:
            Incident object or None if invalid input
        """
        if anomalies_df.empty or 'value' not in anomalies_df.columns:
            return None
        
        current_time = current_time or datetime.now(timezone.utc)
        affected_nodes = sorted(anomalies_df['instance'].unique().tolist())
        peak_value = float(anomalies_df['value'].max())
        
        # Determine start time
        if 'timestamp' in anomalies_df.columns:
            try:
                df_copy = anomalies_df.copy()
                if not pd.api.types.is_datetime64_any_dtype(df_copy['timestamp']):
                    df_copy['timestamp'] = pd.to_datetime(df_copy['timestamp'], unit='s', utc=True)
                start_time = df_copy['timestamp'].min()
            except Exception:
                start_time = current_time
        else:
            start_time = current_time
        
        incident_id = self._generate_incident_id(start_time, affected_nodes)
        
        # Check if this is a startup event
        is_startup = self.detect_startup_event(anomalies_df)
        
        incident = Incident(
            incident_id=incident_id,
            start_time=start_time,
            peak_value=peak_value,
            affected_nodes=affected_nodes,
            detectors=detectors,
            anomaly_samples=len(anomalies_df),
            confidence_score=confidence,
            is_startup_event=is_startup,
            status='Active',
            description='',
        )
        
        self.active_incidents[incident_id] = incident
        return incident
    
    def update_incident_recovery(
        self,
        current_values: Dict[str, float],
        normal_threshold: float = 50.0,
        current_time: Optional[datetime] = None,
    ) -> List[Incident]:
        """
        Check if active incidents should be marked as recovered.
        
        An incident is marked as Recovered when:
        1. All affected nodes return to normal (below threshold)
        2. Have remained normal for recovery_threshold duration
        
        Args:
            current_values: Latest CPU values per instance {instance: value}
            normal_threshold: CPU percentage considered normal
            current_time: Current timestamp (default: now)
        
        Returns:
            List of incidents marked as recovered in this call
        """
        current_time = current_time or datetime.now(timezone.utc)
        recovered = []
        
        for incident_id, incident in list(self.active_incidents.items()):
            if incident.status != 'Active':
                continue
            
            # Check if all affected nodes are below threshold
            all_normal = True
            for node in incident.affected_nodes:
                current_val = current_values.get(node, 0.0)
                if current_val >= normal_threshold:
                    all_normal = False
                    break
            
            if all_normal:
                # Check if enough time has passed
                if not incident.recovery_time:
                    # First time we see it normal
                    incident.recovery_time = current_time
                elif current_time - incident.recovery_time >= self.recovery_threshold:
                    # Enough time passed - mark as recovered
                    incident.status = 'Recovered'
                    incident.end_time = current_time
                    recovered.append(incident)
                    self.recovered_incidents.append(incident)
                    del self.active_incidents[incident_id]
            else:
                # Reset recovery timer if spike is detected again
                incident.recovery_time = None
        
        return recovered
    
    def get_incident_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics of incident management.
        
        Returns:
            Dict with active_count, recovered_count, last_incident_time, etc.
        """
        active = len(self.active_incidents)
        recovered_24h = sum(
            1 for inc in self.recovered_incidents
            if inc.end_time and (datetime.now(timezone.utc) - inc.end_time) < timedelta(hours=24)
        )
        
        last_incident_time = None
        if self.active_incidents:
            last_incident_time = max(inc.start_time for inc in self.active_incidents.values())
        elif self.recovered_incidents:
            last_incident_time = max(inc.end_time for inc in self.recovered_incidents if inc.end_time)
        
        return {
            'active_incidents': active,
            'resolved_24h': recovered_24h,
            'last_incident': last_incident_time,
            'total_tracked': active + len(self.recovered_incidents),
        }
    
    def classify_severity(
        self,
        confidence: float,
        peak_value: float,
        duration_seconds: float,
        num_nodes: int,
    ) -> str:
        """
        Classify incident severity based on multiple factors.
        
        Args:
            confidence: Model consensus confidence (0-1)
            peak_value: Peak CPU percentage
            duration_seconds: Incident duration in seconds
            num_nodes: Number of affected nodes
        
        Returns:
            Severity level: 'Healthy', 'Low', 'Medium', 'High', 'Critical'
        """
        # Startup events are lower severity
        if confidence > 0.75 and peak_value >= 90:
            if duration_seconds < 300:  # Less than 5 minutes
                return 'Medium'
            return 'High' if peak_value >= 95 else 'Medium'
        elif confidence > 0.5 and peak_value >= 70:
            return 'Medium'
        elif confidence > 0.25 or peak_value >= 50:
            return 'Low'
        
        return 'Healthy'
    
    def generate_explanation(
        self,
        incident: Incident,
        all_metrics: Dict[str, List[float]],
    ) -> str:
        """
        Generate operator-friendly explanation of incident cause.
        
        Args:
            incident: Incident object
            all_metrics: Dict of metric_name -> list of values
        
        Returns:
            Human-readable explanation string
        """
        if incident.is_startup_event:
            return (
                "**Startup Initialization Event**\n\n"
                "All monitored nodes spiked simultaneously immediately after monitoring started. "
                "Pattern matches system initialization (rate() calculation, cache warming, I/O initialization). "
                "Likely transient, not production workload issue."
            )
        
        # Multi-node simultaneous spike
        if len(incident.affected_nodes) >= 3:
            return (
                f"**Cluster-wide Resource Spike**\n\n"
                f"CPU spike observed on {len(incident.affected_nodes)} nodes simultaneously. "
                f"Peak: {incident.peak_value:.0f}%. "
                f"Pattern suggests cluster-level event (batch job, periodic task, or initialization). "
                f"Recommend checking: cron jobs, batch schedulers, maintenance tasks."
            )
        
        # Single or pair of nodes
        if len(incident.affected_nodes) <= 2:
            return (
                f"**Single Node High CPU**\n\n"
                f"High CPU detected on {', '.join(incident.affected_nodes)}. "
                f"Peak: {incident.peak_value:.0f}%. "
                f"Duration: {incident.duration_str}. "
                f"Recommend: Check top CPU processes, verify containerized workload limits, inspect logs."
            )
        
        return (
            f"**Multi-node Anomaly**\n\n"
            f"{len(incident.affected_nodes)} nodes affected. Peak: {incident.peak_value:.0f}%. "
            f"Review individual node details below."
        )


def calculate_recovery_percentage(current_value: float, peak_value: float) -> float:
    """Calculate recovery percentage from peak to current."""
    if peak_value == 0:
        return 0.0
    return max(0.0, (peak_value - current_value) / peak_value * 100)
