from __future__ import annotations

from typing import List
import pandas as pd
import plotly.graph_objects as go


def plot_metric_with_anomalies(df: pd.DataFrame, anomalies: List[dict], metric_col: str = 'value') -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df[metric_col], mode='lines', name='value'))
    if anomalies:
        ann_df = pd.DataFrame(anomalies)
        fig.add_trace(
            go.Scatter(
                x=ann_df['timestamp'], y=ann_df.get('details', {}).apply(lambda d: d.get('value') if isinstance(d, dict) else None),
                mode='markers', marker=dict(color='red', size=8), name='anomaly'
            )
        )
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10))
    return fig
