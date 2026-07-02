from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ml.utils import AnomalyResult, ensure_df_index


@dataclass
class LSTMAutoencoderDetector:
    metric: str
    sequence_length: int = 10
    epochs: int = 20
    batch_size: int = 16
    model: Optional[keras.Model] = None
    threshold: Optional[float] = None

    def _import_keras(self):
        try:
            from tensorflow import keras
        except ImportError as exc:
            raise ImportError(
                'TensorFlow is required for LSTMAutoencoderDetector. Install tensorflow or tensorflow-cpu.'
            ) from exc
        return keras

    def _build_model(self, input_shape: tuple[int, ...]):
        keras = self._import_keras()
        model = keras.Sequential(
            [
                keras.layers.InputLayer(input_shape=input_shape),
                keras.layers.LSTM(64, activation='tanh', return_sequences=True),
                keras.layers.LSTM(32, activation='tanh', return_sequences=False),
                keras.layers.RepeatVector(input_shape[0]),
                keras.layers.LSTM(32, activation='tanh', return_sequences=True),
                keras.layers.LSTM(64, activation='tanh', return_sequences=True),
                keras.layers.TimeDistributed(keras.layers.Dense(input_shape[1])),
            ]
        )
        model.compile(optimizer='adam', loss='mse')
        return model

    def _create_sequences(self, values: np.ndarray) -> np.ndarray:
        sequences = []
        for i in range(len(values) - self.sequence_length + 1):
            sequences.append(values[i : i + self.sequence_length])
        return np.array(sequences)

    def fit(self, data: pd.DataFrame) -> None:
        df = ensure_df_index(data, 'timestamp')
        values = df['value'].astype(float).to_numpy().reshape(-1, 1)
        sequences = self._create_sequences(values)
        if len(sequences) == 0:
            raise ValueError('Not enough data to train LSTM autoencoder')
        self.model = self._build_model((self.sequence_length, 1))
        self.model.fit(sequences, sequences, epochs=self.epochs, batch_size=self.batch_size, verbose=0)
        reconstructions = self.model.predict(sequences, verbose=0)
        mse = np.mean(np.power(sequences - reconstructions, 2), axis=(1, 2))
        self.threshold = float(np.mean(mse) + 2 * np.std(mse))

    def predict(self, data: pd.DataFrame) -> List[AnomalyResult]:
        if self.model is None or self.threshold is None:
            raise RuntimeError('Model not fitted yet')
        df = ensure_df_index(data, 'timestamp')
        values = df['value'].astype(float).to_numpy().reshape(-1, 1)
        sequences = self._create_sequences(values)
        if len(sequences) == 0:
            return []
        reconstructions = self.model.predict(sequences, verbose=0)
        mse = np.mean(np.power(sequences - reconstructions, 2), axis=(1, 2))
        results: List[AnomalyResult] = []
        for index_offset, score in enumerate(mse):
            ts = df.index[index_offset + self.sequence_length - 1]
            value = float(df['value'].iloc[index_offset + self.sequence_length - 1])
            instance = str(df['instance'].iloc[index_offset + self.sequence_length - 1])
            is_anomaly = score > self.threshold
            results.append(
                AnomalyResult(
                    timestamp=ts,
                    instance=instance,
                    metric=self.metric,
                    score=float(score),
                    is_anomaly=is_anomaly,
                    reason='LSTM reconstruction error' if is_anomaly else 'normal',
                    details={'value': value, 'threshold': self.threshold},
                )
            )
        return results

    def score(self, data: pd.DataFrame) -> float:
        results = self.predict(data)
        return float(sum(r.is_anomaly for r in results) / max(len(results), 1))

    def explain(self, data: pd.DataFrame) -> Dict[str, Any]:
        return {
            'metric': self.metric,
            'sequence_length': self.sequence_length,
            'epochs': self.epochs,
            'threshold': self.threshold,
            'rule': 'LSTM autoencoder reconstruction error',
        }
