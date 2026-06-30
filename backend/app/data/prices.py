"""
Datos de precios históricos vía yfinance. Calcula retornos diarios,
retorno anualizado y matriz de covarianza anualizada para el optimizador.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Iterable

import numpy as np
import pandas as pd
import yfinance as yf

from app.config import PRICES_CACHE_TTL_HOURS, LOOKBACK_YEARS_PRICES
from app.data import cache

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252


def _cache_key(tickers: tuple, period_days: int) -> str:
    return f"prices:{','.join(sorted(tickers))}:{period_days}"


def fetch_price_history(tickers: list[str], years: int = LOOKBACK_YEARS_PRICES) -> pd.DataFrame:
    """
    Descarga (o sirve de caché) el historial de precios de cierre ajustado
    para una lista de tickers. Devuelve un DataFrame (fecha x ticker).

    Nota: yfinance puede fallar para algunos ADRs de baja liquidez (ej. IRCP,
    DESP) — esos tickers se descartan silenciosamente con un warning en vez
    de tirar abajo todo el request.
    """
    period_days = years * 365
    key = _cache_key(tuple(tickers), period_days)
    cached = cache.get(key, PRICES_CACHE_TTL_HOURS)
    if cached is not None:
        df = pd.read_json(cached, orient="split")
        df.index = pd.to_datetime(df.index)
        return df

    end = datetime.today()
    start = end - timedelta(days=period_days)

    raw = yf.download(
        tickers,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        auto_adjust=True,   # precios ya ajustados por dividendos/splits
        progress=False,
        group_by="ticker",
        threads=True,
    )

    closes = {}
    for t in tickers:
        try:
            if len(tickers) == 1:
                series = raw["Close"]
            else:
                series = raw[t]["Close"]
            if series.dropna().shape[0] < 60:
                logger.warning("Ticker %s con muy poca historia, se descarta", t)
                continue
            closes[t] = series
        except (KeyError, TypeError):
            logger.warning("No se pudo descargar precio para %s, se descarta", t)
            continue

    df = pd.DataFrame(closes).dropna(how="all")
    df = df.ffill().dropna()  # alinea fechas, forward-fill feriados puntuales

    cache.set(key, df.to_json(orient="split"))
    return df


def compute_returns_and_covariance(price_df: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame, pd.Series]:
    """
    A partir de un DataFrame de precios devuelve:
      - retorno_anualizado_historico (Series, por ticker)
      - matriz_covarianza_anualizada (DataFrame, ticker x ticker)
      - volatilidad_anualizada (Series, por ticker)
    """
    daily_returns = price_df.pct_change().dropna()
    mean_daily = daily_returns.mean()
    cov_daily = daily_returns.cov()

    annual_return = (1 + mean_daily) ** TRADING_DAYS_PER_YEAR - 1
    annual_cov = cov_daily * TRADING_DAYS_PER_YEAR
    annual_vol = np.sqrt(np.diag(annual_cov))
    annual_vol = pd.Series(annual_vol, index=annual_cov.index)

    return annual_return, annual_cov, annual_vol


def latest_prices(price_df: pd.DataFrame) -> pd.Series:
    return price_df.iloc[-1]


def momentum_score(price_df: pd.DataFrame, lookback_days: int = 126) -> pd.Series:
    """
    Momentum simple: retorno de los últimos `lookback_days` (default ~6 meses),
    excluyendo el último mes para evitar el efecto de reversión de corto plazo.
    """
    skip_days = 21
    if len(price_df) <= lookback_days + skip_days:
        lookback_days = max(20, len(price_df) - skip_days - 1)
    end_idx = -skip_days if skip_days < len(price_df) else -1
    start_idx = -(lookback_days + skip_days)
    p_start = price_df.iloc[start_idx]
    p_end = price_df.iloc[end_idx]
    return (p_end / p_start) - 1


def relative_momentum_score(
    price_df: pd.DataFrame,
    market_series: pd.Series | None = None,
    lookback_days: int = 126,
) -> pd.Series:
    """
    Momentum RESIDUAL (ajustado por beta) vs el mercado: para cada activo, su
    momentum menos la parte explicada por su exposición al mercado, es decir
    `momentum_activo − beta · momentum_mercado` en la misma ventana.

    Por qué beta y no un simple (activo − mercado): restar el MISMO retorno de
    mercado a todos es un shift uniforme que NO cambia el ranking cross-sectional
    (el z-score es invariante a restar una constante). Para aislar de verdad la
    fuerza propia hay que descontar la parte explicada por el beta de CADA activo,
    que es distinta para cada uno. Así una acción high-beta que subió "a remolque"
    del índice puntúa menos que una low-beta que subió por mérito propio. Si no
    hay serie de mercado utilizable, cae al momentum absoluto.

    Beta se estima con los retornos diarios de todo el historial disponible
    (más estable que estimarlo en la ventana corta de momentum).
    """
    abs_mom = momentum_score(price_df, lookback_days)
    if market_series is None or market_series.dropna().shape[0] < 60:
        return abs_mom

    mkt_mom = float(momentum_score(market_series.to_frame("__MKT__"), lookback_days).iloc[0])

    daily = price_df.pct_change()
    mkt_daily = market_series.pct_change().reindex(daily.index)
    aligned = daily.join(mkt_daily.rename("__MKT__")).dropna()
    mkt_var = aligned["__MKT__"].var() if aligned.shape[0] >= 30 else 0.0
    if not mkt_var:
        # sin datos para estimar beta: caemos a excess return simple
        return abs_mom - mkt_mom

    betas = aligned.drop(columns="__MKT__").apply(
        lambda col: col.cov(aligned["__MKT__"]) / mkt_var
    )
    betas = betas.reindex(abs_mom.index).fillna(1.0)
    return abs_mom - betas * mkt_mom
