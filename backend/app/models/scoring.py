"""
Modelo de scoring: combina factores fundamentales (value, calidad, growth
estimado a futuro) y técnicos (momentum) en un score compuesto 0-100 por
ticker. Este score se usa para:
  1) filtrar el universo a un subconjunto candidato
  2) alimentar las "views" del modelo Black-Litterman (ver optimization.py)

Todos los factores se calculan por z-score relativo (cross-sectional) dentro
del universo disponible en el momento del request, no contra un benchmark
externo — así el ranking es siempre comparable internamente aunque falten
datos de algún ticker puntual.
"""
from __future__ import annotations
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Pesos del score compuesto. Suman 1.0. Ajustables.
FACTOR_WEIGHTS = {
    "value": 0.25,        # P/E y P/B bajos = mejor
    "quality": 0.20,      # ROE alto, deuda/equity bajo = mejor
    "growth_future": 0.30,  # crecimiento esperado de EPS/revenue + upside del price target = mejor
    "momentum": 0.25,     # momentum de precio 6m = mejor
}


def _zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    mu, sigma = s.mean(skipna=True), s.std(skipna=True)
    if sigma == 0 or np.isnan(sigma):
        return pd.Series(0.0, index=s.index)
    z = (s - mu) / sigma
    return z.fillna(0.0)


def build_factor_table(fundamentals: dict[str, dict], momentum: pd.Series) -> pd.DataFrame:
    """
    fundamentals: {ticker: dict devuelto por fundamentals.get_fundamentals}
    momentum: Series con retorno de momentum por ticker (de prices.momentum_score)

    Devuelve un DataFrame index=ticker con columnas de cada factor y el score final.
    """
    rows = []
    for ticker, f in fundamentals.items():
        rows.append({
            "ticker": ticker,
            "pe_ratio": f.get("pe_ratio"),
            "price_to_book": f.get("price_to_book"),
            "roe": f.get("roe"),
            "debt_to_equity": f.get("debt_to_equity"),
            "revenue_growth_3y": f.get("revenue_growth_3y"),
            "eps_growth_estimate_next_y": f.get("eps_growth_estimate_next_y"),
            "analyst_target_price": f.get("analyst_target_price"),
            "market_cap": f.get("market_cap"),
            "beta": f.get("beta"),
        })
    df = pd.DataFrame(rows).set_index("ticker")
    df["momentum_6m"] = momentum.reindex(df.index)

    # --- Value: P/E y P/B bajos puntúan mejor -> invertimos el z-score ---
    value_z = -_zscore(df["pe_ratio"]) * 0.6 + -_zscore(df["price_to_book"]) * 0.4

    # --- Quality: ROE alto puntúa mejor, deuda/equity alta puntúa peor ---
    quality_z = _zscore(df["roe"]) * 0.6 + -_zscore(df["debt_to_equity"]) * 0.4

    # --- Growth futuro: combina crecimiento estimado + upside implícito del
    #     price target de analistas (esto es lo que mete la mirada "a futuro") ---
    growth_z = _zscore(df["revenue_growth_3y"]) * 0.4 + _zscore(df["eps_growth_estimate_next_y"]) * 0.6

    # --- Momentum técnico ---
    momentum_z = _zscore(df["momentum_6m"])

    df["score_value"] = value_z
    df["score_quality"] = quality_z
    df["score_growth_future"] = growth_z
    df["score_momentum"] = momentum_z

    composite = (
        FACTOR_WEIGHTS["value"] * value_z
        + FACTOR_WEIGHTS["quality"] * quality_z
        + FACTOR_WEIGHTS["growth_future"] * growth_z
        + FACTOR_WEIGHTS["momentum"] * momentum_z
    )

    # normalizamos a 0-100 para que sea legible en el frontend
    rank_pct = composite.rank(pct=True)
    df["composite_score"] = (rank_pct * 100).round(1)
    df["composite_z"] = composite

    return df.sort_values("composite_score", ascending=False)


def select_candidates(factor_table: pd.DataFrame, max_assets: int, min_assets: int) -> list[str]:
    """Toma el top-N del ranking, respetando min/max del perfil de riesgo."""
    n = min(max_assets, max(min_assets, max_assets))
    top = factor_table.head(max(min_assets, n)).index.tolist()
    return top
