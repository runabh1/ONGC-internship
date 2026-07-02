from __future__ import annotations

import pandas as pd

from ml.utils import ensure_df_index, ewma, rolling_mean, z_score


def add_rolling_features(df: pd.DataFrame, value_column: str = 'value', window: int = 5) -> pd.DataFrame:
    df = ensure_df_index(df, 'timestamp')
    df[f'{value_column}_rolling_mean'] = rolling_mean(df[value_column], window=window)
    df[f'{value_column}_ewma'] = ewma(df[value_column], span=window)
    df[f'{value_column}_zscore'] = z_score(df[value_column])
    return df.reset_index()


def aggregate_by_instance(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby('instance', as_index=False).agg(
        value_mean=pd.NamedAgg(column='value', aggfunc='mean'),
        value_std=pd.NamedAgg(column='value', aggfunc='std'),
        value_max=pd.NamedAgg(column='value', aggfunc='max'),
        value_min=pd.NamedAgg(column='value', aggfunc='min'),
    )
