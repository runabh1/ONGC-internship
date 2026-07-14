"""
Ensemble Anomaly Detection for ONGC AI Cluster Monitor.

Four detectors combined by weighted vote:

  A. StaticThresholdDetector  (weight=0.35) — fast, deterministic, env-configurable thresholds
  B. ZScoreDetector           (weight=0.25) — statistical, seeded from NodeBaseline after warmup
  C. EWMAAnomalyDetector      (weight=0.20) — rolling EWMA deviation
  D. IsolationForestDetector  (weight=0.20) — sklearn IF on rolling feature window (needs ≥30 pts)

Ensemble score = weighted average of normalised component scores (0→1).

Severity mapping:
  ≥ 0.80  → Critical
  ≥ 0.55  → High
  ≥ 0.35  → Medium
  ≥ 0.20  → Low  (emitted but not counted as active anomaly)
  < 0.20  → Normal (no event)

Deduplication: caller is responsible — one open AnomalyEvent per (node_id, metric_name).
Auto-resolve: when ensemble_score < 0.15 for an open event, caller marks resolved=True.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ml.baseline_detector import EWMAAnomalyDetector, RollingMeanDetector, ZScoreDetector
from ml.isolation_forest import IsolationForestDetector
from ml.lstm_detector import LSTMAutoencoderDetector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env-driven thresholds (all overridable via .env)
# ---------------------------------------------------------------------------
_f = float

THRESHOLDS = [
    # (metric_name, threshold, severity, description)
    ('cpu_used_pct',    _f(os.getenv('CPU_CRITICAL_PCT', '95')),   'Critical', 'CPU critically high (>{:.0f}%) — node overloaded'),
    ('cpu_used_pct',    _f(os.getenv('CPU_HIGH_PCT',     '85')),   'High',     'CPU high (>{:.0f}%) — performance degraded'),
    ('cpu_used_pct',    _f(os.getenv('CPU_MEDIUM_PCT',   '75')),   'Medium',   'CPU elevated (>{:.0f}%)'),
    ('memory_used_pct', _f(os.getenv('MEM_CRITICAL_PCT', '95')),   'Critical', 'Memory critically high (>{:.0f}%) — OOM risk'),
    ('memory_used_pct', _f(os.getenv('MEM_HIGH_PCT',     '90')),   'High',     'Memory high (>{:.0f}%)'),
    ('memory_used_pct', _f(os.getenv('MEM_MEDIUM_PCT',   '80')),   'Medium',   'Memory elevated (>{:.0f}%)'),
    ('disk_used_pct',   _f(os.getenv('DISK_CRITICAL_PCT','95')),   'Critical', 'Disk critically high (>{:.0f}%) — disk-full risk'),
    ('disk_used_pct',   _f(os.getenv('DISK_HIGH_PCT',    '85')),   'High',     'Disk high (>{:.0f}%)'),
    ('cpu_iowait_pct',  _f(os.getenv('IOWAIT_HIGH_PCT',  '40')),   'High',     'I/O wait high (>{:.0f}%) — storage bottleneck'),
    ('cpu_iowait_pct',  _f(os.getenv('IOWAIT_MEDIUM_PCT','20')),   'Medium',   'I/O wait elevated (>{:.0f}%)'),
    ('load_one',        _f(os.getenv('LOAD_CRITICAL',    '30')),   'Critical', 'Load average critically high (>{:.0f})'),
    ('load_one',        _f(os.getenv('LOAD_HIGH',        '15')),   'High',     'Load average very high (>{:.0f})'),
    ('load_one',        _f(os.getenv('LOAD_MEDIUM',      '8')),    'Medium',   'Load average elevated (>{:.0f})'),
]

SEVERITY_WEIGHTS = {'Critical': 1.0, 'High': 0.70, 'Medium': 0.40, 'Low': 0.20}

# Detector weights must sum to 1.0
W_THRESHOLD   = 0.30
W_ROLLING     = 0.15
W_EWMA        = 0.15
W_ZSCORE      = 0.15
W_IFOREST     = 0.15
W_LSTM        = 0.10

# Severity thresholds on ensemble_score
SEV_CRITICAL = 0.90
SEV_HIGH     = 0.75
SEV_MEDIUM   = 0.50
SEV_LOW      = 0.30  # advisory only; low anomalies are not persisted by default
SEV_RESOLVE  = 0.15  # auto-resolve threshold

# Minimum samples to enable the IsolationForest detector
IF_MIN_SAMPLES = 30


@dataclass
class EnsembleResult:
    metric_name: str
    metric_value: float
    ensemble_score: float          # 0–1
    severity: Optional[str]        # None → normal; 'Low'/'Medium'/'High'/'Critical'
    description: str
    detector_scores: dict          # component scores for debugging
    is_anomaly: bool               # True if ensemble_score >= SEV_LOW


@dataclass
class MetricEnsemble:
    """
    Per-(node, metric) ensemble. One instance is kept alive in memory
    across collection cycles so the statistical/ML detectors preserve state.
    """
    metric_name: str
    # Persisted baseline from warmup (optional seed)
    baseline_mean: Optional[float] = None
    baseline_std:  Optional[float] = None

    # Internal detector instances
    _rolling_mean: RollingMeanDetector = field(init=False)
    _zscore: ZScoreDetector = field(init=False)
    _ewma:   EWMAAnomalyDetector = field(init=False)
    _if_model: Optional[IsolationForestDetector] = field(init=False, default=None)
    _lstm: Optional[LSTMAutoencoderDetector] = field(init=False, default=None)
    _history:  list = field(init=False, default_factory=list)  # rolling (value, ts) tuples

    def __post_init__(self):
        self._rolling_mean = RollingMeanDetector(metric=self.metric_name, window=15, threshold=0.20)
        self._zscore = ZScoreDetector(metric=self.metric_name, threshold=2.5)
        self._ewma   = EWMAAnomalyDetector(metric=self.metric_name, span=12, threshold=0.20)
        if self.baseline_mean is not None and self.baseline_std is not None:
            self._zscore.fitted_mean = self.baseline_mean
            self._zscore.fitted_std  = max(self.baseline_std, 0.01)

    def _build_df(self) -> pd.DataFrame:
        """Build a tiny DataFrame from the rolling history buffer."""
        if not self._history:
            return pd.DataFrame(columns=['timestamp', 'instance', 'value'])
        return pd.DataFrame(
            [{'timestamp': pd.Timestamp(ts), 'instance': 'node', 'value': float(v)}
             for v, ts in self._history]
        )

    def _threshold_score(self, value: float) -> tuple[float, str, str]:
        """Return (normalised_score 0-1, severity, description) for the threshold detector."""
        for metric, thresh, sev, desc_fmt in THRESHOLDS:
            if metric != self.metric_name:
                continue
            if value > thresh:
                base_w = SEVERITY_WEIGHTS.get(sev, 0.0)
                # Proportionally scale: how far above threshold
                excess = (value - thresh) / max(thresh, 1.0)
                score = min(1.0, base_w + excess * 0.3)
                return score, sev, desc_fmt.format(thresh)
        return 0.0, 'Normal', ''

    def _rolling_mean_score(self, df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        try:
            results = self._rolling_mean.predict(df)
            if not results:
                return 0.0
            latest = results[-1]
            return min(1.0, max(0.0, latest.score / 0.5))
        except Exception as exc:
            logger.debug('Rolling mean predict error for %s: %s', self.metric_name, exc)
            return 0.0

    def _zscore_score(self, df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        try:
            results = self._zscore.predict(df)
            if not results:
                return 0.0
            latest = results[-1]
            # Normalise: zscore threshold=2.5 → score=0.5; 5.0 → score=1.0
            return min(1.0, max(0.0, latest.score / 5.0))
        except Exception as exc:
            logger.debug('ZScore predict error for %s: %s', self.metric_name, exc)
            return 0.0

    def _ewma_score(self, df: pd.DataFrame) -> float:
        if df.empty or len(df) < 3:
            return 0.0
        try:
            results = self._ewma.predict(df)
            if not results:
                return 0.0
            latest = results[-1]
            return min(1.0, max(0.0, latest.score / 0.5))
        except Exception as exc:
            logger.debug('EWMA predict error for %s: %s', self.metric_name, exc)
            return 0.0

    def _iforest_score(self, df: pd.DataFrame) -> float:
        if len(self._history) < IF_MIN_SAMPLES:
            return 0.0
        try:
            if self._if_model is None:
                self._if_model = IsolationForestDetector(
                    metric=self.metric_name,
                    contamination=0.05,
                    min_train_samples=IF_MIN_SAMPLES,
                )
            self._if_model.fit(df)
            results = self._if_model.predict(df)
            if not results:
                return 0.0
            latest = results[-1]
            raw = latest.details.get('anomaly_score', 0.0)
            return min(1.0, max(0.0, float(raw) / 0.5))
        except Exception as exc:
            logger.debug('IsolationForest error for %s: %s', self.metric_name, exc)
            return 0.0

    def _lstm_score(self, df: pd.DataFrame) -> float:
        if df.empty or len(df) < 12:
            return 0.0
        try:
            if self._lstm is None:
                self._lstm = LSTMAutoencoderDetector(metric=self.metric_name, sequence_length=10, epochs=10, batch_size=8)
            if self._lstm.model is None:
                self._lstm.fit(df)
            results = self._lstm.predict(df)
            if not results:
                return 0.0
            latest = results[-1]
            if self._lstm.threshold is None:
                return 1.0 if latest.is_anomaly else 0.0
            return min(1.0, max(0.0, latest.score / max(self._lstm.threshold, 1.0)))
        except Exception as exc:
            logger.debug('LSTM predict error for %s: %s', self.metric_name, exc)
            return 0.0

    def evaluate(self, value: float, timestamp) -> EnsembleResult:
        """
        Add the new data point and run the ensemble.
        Returns an EnsembleResult describing the current anomaly state.
        """
        # Rolling buffer — keep last 200 points to avoid unbounded growth
        self._history.append((value, timestamp))
        if len(self._history) > 200:
            self._history = self._history[-200:]

        df = self._build_df()

        # Component A — threshold
        t_score, t_sev, t_desc = self._threshold_score(value)

        # Existing detector components from your production modules
        r_score = self._rolling_mean_score(df)
        z_score = self._zscore_score(df)
        e_score = self._ewma_score(df)
        if_available = len(self._history) >= IF_MIN_SAMPLES
        i_score = self._iforest_score(df)
        l_score = self._lstm_score(df)

        weight_map = {
            'threshold': W_THRESHOLD,
            'rolling_mean': W_ROLLING,
            'ewma': W_EWMA,
            'zscore': W_ZSCORE,
            'iforest': W_IFOREST if if_available else 0.0,
            'lstm': W_LSTM,
        }
        total_weight = sum(weight_map.values())
        if total_weight <= 0:
            ensemble_score = 0.0
        else:
            ensemble_score = (
                (weight_map['threshold'] / total_weight) * t_score +
                (weight_map['rolling_mean'] / total_weight) * r_score +
                (weight_map['ewma'] / total_weight) * e_score +
                (weight_map['zscore'] / total_weight) * z_score +
                (weight_map['iforest'] / total_weight) * i_score +
                (weight_map['lstm'] / total_weight) * l_score
            )

        detector_names = ['Threshold', 'RollingMean', 'EWMA', 'ZScore', 'IsolationForest']
        if l_score > 0.0:
            detector_names.append('LSTM')
        detectors_used = '+'.join(detector_names)

        ensemble_score = round(float(np.clip(ensemble_score, 0.0, 1.0)), 4)

        # Map score → severity
        if ensemble_score >= SEV_CRITICAL:
            severity = 'Critical'
        elif ensemble_score >= SEV_HIGH:
            severity = 'High'
        elif ensemble_score >= SEV_MEDIUM:
            severity = 'Medium'
        elif ensemble_score >= SEV_LOW:
            severity = 'Low'
        else:
            severity = None  # Normal

        # Description: prefer threshold description, else generic
        if t_desc:
            description = t_desc
        elif severity:
            description = (f'{self.metric_name.replace("_"," ").title()} anomaly detected '
                           f'(value={value:.2f}, score={ensemble_score:.3f})')
        else:
            description = ''

        return EnsembleResult(
            metric_name=self.metric_name,
            metric_value=round(float(value), 3),
            ensemble_score=ensemble_score,
            severity=severity,
            description=description,
            detector_scores={
                'threshold': round(t_score, 4),
                'rolling_mean': round(r_score, 4),
                'zscore':    round(z_score, 4),
                'ewma':      round(e_score, 4),
                'iforest':   round(i_score, 4),
                'lstm':      round(l_score, 4),
                'detectors': detectors_used,
            },
            is_anomaly=severity in ('Medium', 'High', 'Critical'),
        )


# ---------------------------------------------------------------------------
# Per-node ensemble registry  (in-memory, rebuilt on restart)
# ---------------------------------------------------------------------------
# node_id → metric_name → MetricEnsemble
_ensembles: dict[int, dict[str, MetricEnsemble]] = {}


def get_ensemble(node_id: int, metric_name: str,
                 baseline_mean: float | None = None,
                 baseline_std:  float | None = None) -> MetricEnsemble:
    """Return (and lazily create) the MetricEnsemble for a (node, metric) pair."""
    if node_id not in _ensembles:
        _ensembles[node_id] = {}
    if metric_name not in _ensembles[node_id]:
        _ensembles[node_id][metric_name] = MetricEnsemble(
            metric_name=metric_name,
            baseline_mean=baseline_mean,
            baseline_std=baseline_std,
        )
    return _ensembles[node_id][metric_name]


def seed_ensemble_from_baseline(node_id: int, metric_name: str,
                                 mean: float, std: float) -> None:
    """Called after warmup completes to seed the ZScore detector with real baselines."""
    ens = get_ensemble(node_id, metric_name, baseline_mean=mean, baseline_std=std)
    ens._zscore.fitted_mean = mean
    ens._zscore.fitted_std  = max(std, 0.01)
    logger.info('Seeded ensemble for node=%s metric=%s mean=%.3f std=%.3f',
 