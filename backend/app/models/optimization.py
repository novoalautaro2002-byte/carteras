"""
Optimizador de cartera: Black-Litterman + Mean-Variance (Markowitz).

Por qué Black-Litterman y no Markowitz puro: con 10-20 activos candidatos,
estimar retornos esperados directamente del promedio histórico es muy
ruidoso y da pesos inestables/extremos. Black-Litterman arranca de un
"equilibrio de mercado" (retornos implícitos por cap-weight) y lo ajusta
con las "views" del modelo de scoring (fundamentals + momentum + estimates
a futuro). Esto fusiona las 3 patas pedidas: scoring, perfil de riesgo
(vía restricciones) y Markowitz (vía la optimización final).

Restricciones duras del perfil de riesgo se aplican como constraints del
optimizador (no son sugerencias): max por activo, max por sector, max
exposición Argentina, cantidad de activos.
"""
from __future__ import annotations
import logging

import numpy as np
import pandas as pd
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


def market_cap_weights(market_caps: pd.Series) -> pd.Series:
    mc = market_caps.fillna(market_caps.median())
    mc = mc.clip(lower=1.0)
    return mc / mc.sum()


def implied_equilibrium_returns(cov: pd.DataFrame, w_mkt: pd.Series, risk_aversion_lambda: float) -> pd.Series:
    """Reverse optimization de Black-Litterman: pi = lambda * Sigma * w_mkt."""
    pi = risk_aversion_lambda * cov.values @ w_mkt.values
    return pd.Series(pi, index=cov.index)


def black_litterman_posterior(
    cov: pd.DataFrame,
    pi: pd.Series,
    composite_z: pd.Series,
    tau: float = 0.05,
    view_confidence: float = 0.5,
) -> pd.Series:
    """
    Versión simplificada de Black-Litterman con "views absolutas" diagonales:
    cada activo tiene una view = pi + ajuste proporcional a su composite_z
    del modelo de scoring, con incertidumbre de la view inversamente
    proporcional a `view_confidence` (0 a 1: 1 = mucha confianza en el score).

    No usamos la formulación matricial completa con P (picking matrix)
    porque cada activo tiene exactamente una view absoluta -> P = identidad,
    lo cual colapsa la fórmula general a un blend ponderado por varianza.
    """
    z = composite_z.reindex(pi.index).fillna(0.0)

    # Escalamos el z-score a un ajuste de retorno esperado: usamos la vol
    # histórica de cada activo como escala, así un "view fuerte" en una
    # acción más volátil mueve el retorno esperado proporcionalmente más
    # (consistente con que esa acción puede moverse más en términos absolutos).
    asset_vol = pd.Series(np.sqrt(np.diag(cov.values)), index=cov.index)
    view_adjustment = z * asset_vol * 0.5  # 0.5 = sensibilidad del view, ajustable

    omega_scale = tau / max(view_confidence, 1e-3)
    blend_weight = 1.0 / (1.0 + omega_scale)  # mayor confianza -> más peso al view

    posterior = pi + blend_weight * view_adjustment
    return posterior


def _portfolio_variance(w: np.ndarray, cov: np.ndarray) -> float:
    return float(w @ cov @ w)


def optimize_weights(
    expected_returns: pd.Series,
    cov: pd.DataFrame,
    sectors: pd.Series,
    risk_aversion_lambda: float,
    max_weight_per_asset: float,
    max_weight_per_sector: float,
    max_weight_argentina: float,
    argentina_sector_name: str = "Argentina",
) -> pd.Series:
    """
    Maximiza utilidad de Markowitz: U(w) = w'mu - (lambda/2) w'Sigma w
    sujeto a:
      sum(w) = 1, w >= 0
      w_i <= max_weight_per_asset
      sum(w en sector s) <= max_weight_per_sector  (para cada sector)
      sum(w en Argentina) <= max_weight_argentina
    """
    tickers = expected_returns.index.tolist()
    n = len(tickers)
    mu = expected_returns.values
    sigma = cov.loc[tickers, tickers].values

    def neg_utility(w):
        ret = w @ mu
        var = _portfolio_variance(w, sigma)
        return -(ret - 0.5 * risk_aversion_lambda * var)

    constraints = [
        {"type": "ineq", "fun": lambda w: 1.0 - np.sum(w)},  # sum(w) <= 1 (permite quedarse en cash)
    ]

    unique_sectors = sectors.unique().tolist()
    for sector in unique_sectors:
        mask = (sectors.values == sector).astype(float)
        cap = max_weight_argentina if sector == argentina_sector_name else max_weight_per_sector
        constraints.append({
            "type": "ineq",
            "fun": (lambda w, mask=mask, cap=cap: cap - np.dot(w, mask)),
        })

    bounds = [(0.0, max_weight_per_asset) for _ in range(n)]
    w0 = _feasible_initial_weights(
        sectors, max_weight_per_asset, max_weight_per_sector, max_weight_argentina, argentina_sector_name
    )

    result = minimize(
        neg_utility,
        w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-9},
    )

    if not result.success:
        logger.warning("Optimizador no convergió (%s), uso fallback feasible respetando caps", result.message)
        w = w0
    else:
        w = result.x
        w = np.clip(w, 0, None)
        if w.sum() > 1.0:
            w = w / w.sum()  # solo recorta si excedió 1 por error numérico chico

    return pd.Series(w, index=tickers).sort_values(ascending=False)


def _feasible_initial_weights(
    sectors: pd.Series, max_w_asset: float, max_w_sector: float, max_w_arg: float, arg_name: str
) -> np.ndarray:
    """
    Punto de partida factible para el optimizador: reparte el peso de forma
    pareja DENTRO de cada sector hasta el cap de ese sector (nunca más).
    Por construcción esto siempre respeta los caps de sector/Argentina y el
    cap por activo, así que también sirve como fallback final si SLSQP no
    converge. La suma total puede dar menor a 1 si los caps de sector son
    restrictivos — eso es correcto: significa que no se puede invertir el
    100% del capital sin violar algún cap, y el resto queda en cash
    (ver `cash_leftover_usd` en la respuesta de la API).
    """
    n = len(sectors)
    sectors_arr = sectors.values
    unique_sectors = pd.unique(sectors_arr)
    w0 = np.zeros(n)

    for s in unique_sectors:
        idx = np.where(sectors_arr == s)[0]
        cap = max_w_arg if s == arg_name else max_w_sector
        k = len(idx)
        per_asset = min(max_w_asset, cap / k)
        w0[idx] = per_asset

    return w0


def portfolio_stats(weights: pd.Series, expected_returns: pd.Series, cov: pd.DataFrame) -> dict:
    w = weights.reindex(expected_returns.index).fillna(0).values
    mu = expected_returns.values
    sigma = cov.loc[expected_returns.index, expected_returns.index].values
    exp_return = float(w @ mu)
    variance = _portfolio_variance(w, sigma)
    vol = float(np.sqrt(variance))
    return {
        "expected_return_annual": exp_return,
        "expected_volatility_annual": vol,
        "sharpe_approx": exp_return / vol if vol > 0 else None,
    }
