from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json
import os
from pathlib import Path

import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import joblib

from ml.utils import AnomalyResult, ensure_df_index
from ml.feature_engineering import add_rolling_features


@dataclass
class IsolationForestDetector:
    """
    Production-oriented Isolation Forest detector.

    Behavior and guarantees:
    - Trains once and persists model + scaler to disk (joblib).
    - Uses engineered features (rolling mean, ewma, zscore, diffs, roc).
    - Standardizes inputs with `StandardScaler` and persists the scaler.
    - Predicts per-sample but exposes the latest-sample decision as the "current" result.
    - Retrains periodically according to configurable thresholds.
    - Avoids training on obvious anomalous samples via simple filtering heuristics.

    This class keeps the public API compatible: `fit`, `predict`, `score`, `explain`.
    The Streamlit app may call `fit` on every refresh; this `fit` is idempotent and
    will only retrain when needed.
    """

    metric: str
    contamination: float = 0.03
    random_state: int = 42
    model: Optional[IsolationForest] = None
    scaler: Optional[StandardScaler] = None
    raw_score_threshold: Optional[float] = None
    persist_dir: Path = Path('ml') / 'models'
    retrain_samples: int = 1000
    retrain_hours: float = 1.0
    min_train_samples: int = 30

    # canonical feature column order used for training and prediction
    FEATURE_COLS = ['value', 'value_rolling_mean', 'value_ewma', 'value_zscore', 'diff_rm', 'diff_ewma', 'roc']

    def _model_paths(self) -> Dict[str, Path]:
        key = self.metric.replace(' ', '_').replace('%', '').lower()
        return {
            'model': self.persist_dir / f'isolation_forest_{key}.joblib',
            'scaler': self.persist_dir / f'isolation_scaler_{key}.joblib',
            'meta': self.persist_dir / f'isolation_meta_{key}.json',
        }

    def _ensure_persist_dir(self) -> None:
        os.makedirs(self.persist_dir, exist_ok=True)

    def _feature_matrix(self, df: pd.DataFrame) -> pd.DataFrame:
        """Build engineered feature matrix from raw dataframe.

        Features:
        - value (CPU)
        - value_rolling_mean
        - value_ewma
        - value_zscore
        - diff_rm (value - rolling_mean)
        - diff_ewma (value - ewma)
        - roc (absolute difference to previous sample)
        """
        dff = add_rolling_features(df, value_column='value', window=5)
        # compute diffs and rate-of-change
        dff['diff_rm'] = dff['value'] - dff.get('value_rolling_mean', 0.0)
        dff['diff_ewma'] = dff['value'] - dff.get('value_ewma', 0.0)
        dff['roc'] = dff['value'].diff().fillna(0.0)
        # Keep a consistent column order
        feat_cols = ['value', 'value_rolling_mean', 'value_ewma', 'value_zscore', 'diff_rm', 'diff_ewma', 'roc']
        for c in feat_cols:
            if c not in dff.columns:
                dff[c] = 0.0
        return dff.reset_index(drop=True)[['timestamp', 'instance'] + feat_cols]

    def _load_persisted(self) -> Dict[str, Any]:
        paths = self._model_paths()
        if not paths['model'].exists() or not paths['scaler'].exists() or not paths['meta'].exists():
            return {}
        try:
            self.model = joblib.load(paths['model'])
            self.scaler = joblib.load(paths['scaler'])
            with open(paths['meta'], 'r') as fh:
                meta = json.load(fh)
            self.raw_score_threshold = meta.get('raw_score_threshold')
            return meta
        except Exception:
            return {}

    def _persist(self, meta: Dict[str, Any]) -> None:
        paths = self._model_paths()
        joblib.dump(self.model, paths['model'])
        joblib.dump(self.scaler, paths['scaler'])
        with open(paths['meta'], 'w') as fh:
            json.dump(meta, fh)

    def fit(self, data: pd.DataFrame, force_retrain: bool = False) -> None:
        """
        Fit or load the Isolation Forest model.

        This method is safe to call repeatedly; it will load an existing persisted
        model when available and only retrain when `force_retrain` is True or when
        retraining criteria are met (enough new samples or time elapsed).
        """
        self._ensure_persist_dir()
        df = ensure_df_index(data, 'timestamp')
        if df.empty:
            return

        meta = self._load_persisted()

        # Prepare feature matrix for potential training
        feats = self._feature_matrix(df.reset_index())

        # Heuristic: exclude obvious anomalies from training set (avoid poisoning)
        # Keep samples with value < 90% and moderate zscore
        clean_mask = (feats['value'] < 90.0) & (feats['value_zscore'].abs() < 2.5)
        X_clean = feats.loc[clean_mask, self.FEATURE_COLS]

        need_retrain = force_retrain or self.model is None or self.scaler is None or not meta

        # Check retrain conditions: time-based or sample-based
        if not need_retrain and meta:
            try:
                last_trained = pd.to_datetime(meta.get('last_trained'))
                hours_since = (pd.Timestamp.now(tz='UTC') - last_trained).total_seconds() / 3600.0
                if hours_since >= float(self.retrain_hours):
                    need_retrain = True
            except Exception:
                need_retrain = True
            if not need_retrain and len(X_clean) >= int(self.retrain_samples):
                need_retrain = True

        if not need_retrain:
            # model loaded above by _load_persisted
            return

        # If cleaning yields degenerate training data, fall back to using all samples.
        if X_clean.empty:
            X_train = feats[self.FEATURE_COLS]
        else:
            unique_counts = X_clean.nunique(dropna=False)
            if unique_counts.eq(1).all() or np.allclose(X_clean.std(ddof=0).values, 0.0):
                X_train = feats[self.FEATURE_COLS]
            else:
                X_train = X_clean

        if X_train.empty:
            # nothing to train on
            return

        # Ensure the training matrix uses the canonical feature order.
        X_train = X_train[self.FEATURE_COLS]

        # Require a minimum number of training samples to avoid poisoning/overfitting
        if len(X_train) < int(self.min_train_samples):
            return

        # Standardize
        self.scaler = StandardScaler()
        # Impute/clean numeric issues before scaling
        X_train = X_train.ffill().bfill().fillna(0.0)
        Xs = self.scaler.fit_transform(X_train.values)

        # Train Isolation Forest
        self.model = IsolationForest(
            contamination=float(self.contamination), random_state=int(self.random_state)
        )
        self.model.fit(Xs)

        # Persist model/scaler with metadata
        train_scores = self.model.decision_function(Xs)
        self.raw_score_threshold = float(np.percentile(train_scores, max(1.0, float(self.contamination) * 100.0)))

        meta = {
            'metric': self.metric,
            'contamination': self.contamination,
            'random_state': self.random_state,
            'trained_samples': int(len(X_train)),
            'last_trained': pd.Timestamp.now(tz='UTC').isoformat(),
            'raw_score_threshold': self.raw_score_threshold,
        }
        self._persist(meta)

    def predict(self, data: pd.DataFrame) -> List[AnomalyResult]:
        """
        Predict anomalies for each sample in `data` and return a list of
        `AnomalyResult`. The method computes per-sample decision_function and
        label, but the monitoring UI should treat only the *latest* sample as
        the current decision.
        """
        # Ensure model is available (fit will load or train if needed)
        if self.model is None or self.scaler is None:
            # Attempt to load or fit using provided data
            try:
                self.fit(data)
            except Exception:
                pass

        df = ensure_df_index(data, 'timestamp')
        if df.empty:
            return []

        feats = self._feature_matrix(df.reset_index())
        feature_cols = list(self.FEATURE_COLS)
        # Ensure feature matrix columns exist and are ordered consistently
        for c in feature_cols:
            if c not in feats.columns:
                feats[c] = 0.0
        X = feats[feature_cols].values

        if self.scaler is None or self.model is None:
            # Cannot score without a trained model/scaler; return neutral results
            results: List[AnomalyResult] = []
            for ts, inst, val in zip(feats['timestamp'], feats['instance'], feats['value']):
                results.append(AnomalyResult(timestamp=ts, instance=str(inst), metric=self.metric, score=0.0, is_anomaly=False, reason='model unavailable', details={'value': float(val)}))
            return results

        # Impute/clean before scaling
        X = pd.DataFrame(X, columns=feature_cols).ffill().bfill().fillna(0.0).values
        Xs = self.scaler.transform(X)
        raw_scores = self.model.decision_function(Xs)
        labels = self.model.predict(Xs)
        # anomaly score: higher means more anomalous -> use -decision_function
        anomaly_scores = (-raw_scores).tolist()

        results = []
        # build a mapping from column name to index for features
        feat_list = list(feature_cols)
        for row_idx, (ts, inst, val, lab, raw, asc) in enumerate(zip(feats['timestamp'], feats['instance'], feats['value'], labels, raw_scores, anomaly_scores)):
            if self.raw_score_threshold is not None:
                is_anom = raw < float(self.raw_score_threshold)
            else:
                is_anom = int(lab) == -1
            details: Dict[str, Any] = {
                'value': float(val),
                'decision_function': float(raw),
                'anomaly_score': float(asc),
            }
            # include feature values for explainability
            for i, col in enumerate(feat_list):
                try:
                    details[f'feat_{col}'] = float(X[row_idx, i])
                except Exception:
                    details[f'feat_{col}'] = None

            results.append(
                AnomalyResult(
                    timestamp=ts,
                    instance=str(inst),
                    metric=self.metric,
                    score=float(asc),
                    is_anomaly=bool(is_anom),
                    reason='isolation forest outlier' if is_anom else 'normal',
                    details=details,
                )
            )

        return results

    def score(self, data: pd.DataFrame) -> float:
        """
        Return a concise score representing the latest sample only.

        To remain compatible with the dashboard's usage, this returns 1.0 when
        the latest sample is anomalous according to the model, otherwise 0.0.
        """
        preds = self.predict(data)
        if not preds:
            return 0.0
        latest = preds[-1]
        return 1.0 if latest.is_anomaly else 0.0

    def explain(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Return model metadata and latest-sample explanation when possible."""
        meta = {
            'metric': self.metric,
            'contamination': self.contamination,
            'random_state': self.random_state,
            'features': ['value', 'value_rolling_mean', 'value_ewma', 'value_zscore', 'diff_rm', 'diff_ewma', 'roc'],
        }
        try:
            preds = self.predict(data)
            if preds:
                latest = preds[-1]
                meta['latest'] = {
                    'timestamp': latest.timestamp.isoformat() if hasattr(latest.timestamp, 'isoformat') else str(latest.timestamp),
                    'instance': latest.instance,
                    'score': latest.score,
                    'is_anomaly': latest.is_anomaly,
                    'details': latest.details,
                }
        except Exception:
            pass
        return meta
