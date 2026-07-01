from __future__ import annotations
import logging
import math
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, HTTPException

from app.config import (
    RISK_PROFILES, RISK_FREE_RATE_ANNUAL, MARKET_PROXY_TICKER, full_universe,
)
from app.data import prices as prices_mod
from app.data import fundamentals as fundamentals_mod
from app.models import scoring, optimization
from app.schemas import (
    PortfolioRequest, PortfolioResponse, AssetAllocation,
    UniverseTicker, RefreshResponse, UniverseScanRow, UniverseScanResponse,
)


def _num(x, ndigits: int | None = None):
    """Convierte a float JSON-safe: NaN/inf/None -> None, opcionalmente redondea."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, ndigits) if ndigits is not None else x

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_factor_table(
    tickers: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], pd.Series | None]:
    """Trae precios + fundamentals para `tickers` y devuelve:
    (factor_table, price_df, dropped, market_series).

    Descarga también el proxy de mercado (SPY) en la misma corrida para:
      1) calcular el momentum RELATIVO al mercado (excess return), y
      2) tener la serie alineada para las métricas beta/alpha de la cartera.
    SPY nunca entra como activo invertible (no está en `tickers`)."""
    price_df = prices_mod.fetch_price_history(tickers + [MARKET_PROXY_TICKER])
    market_series = (
        price_df[MARKET_PROXY_TICKER] if MARKET_PROXY_TICKER in price_df.columns else None
    )
    available = [t for t in tickers if t in price_df.columns]
    dropped = sorted(set(tickers) - set(available))
    if dropped:
        logger.warning("Tickers sin datos suficientes de precio, excluidos: %s", dropped)
    if market_series is None:
        logger.warning("No se pudo descargar el proxy de mercado %s; momentum cae a absoluto.", MARKET_PROXY_TICKER)

    momentum = prices_mod.relative_momentum_score(price_df[available], market_series)
    fundamentals = fundamentals_mod.get_fundamentals_bulk(available)
    factor_table = scoring.build_factor_table(fundamentals, momentum)
    return factor_table, price_df[available], dropped, market_series


@router.get("/universe", response_model=list[UniverseTicker])
def get_universe():
    """Lista el universo completo con su sector (sin scoring pesado, rápido)."""
    sectors = full_universe()
    return [UniverseTicker(ticker=t, sector=s) for t, s in sorted(sectors.items())]


@router.post("/portfolio/build", response_model=PortfolioResponse)
def build_portfolio(req: PortfolioRequest):
    universe_sectors = full_universe()
    tickers = list(universe_sectors.keys())

    if req.only_argentina:
        tickers = [t for t in tickers if universe_sectors[t] == "Argentina"]
    if req.only_us:
        tickers = [t for t in tickers if universe_sectors[t] != "Argentina"]
    tickers = [t for t in tickers if t not in set(req.exclude_tickers)]

    if len(tickers) < 5:
        raise HTTPException(400, "Quedan muy pocos tickers en el universo tras los filtros/exclusiones.")

    profile = RISK_PROFILES[req.risk_profile]
    warnings: list[str] = []

    try:
        factor_table, price_df, dropped, market_series = _build_factor_table(tickers)
    except Exception as e:
        logger.exception("Error armando factor table")
        raise HTTPException(502, f"Error obteniendo datos de mercado: {e}")

    if dropped:
        warnings.append(f"Excluidos por falta de datos de precio: {', '.join(dropped)}")

    candidates = scoring.select_candidates(
        factor_table, max_assets=profile["max_assets"], min_assets=profile["min_assets"]
    )
    if len(candidates) < profile["min_assets"]:
        warnings.append(
            f"Solo se pudieron armar {len(candidates)} posiciones "
            f"(mínimo ideal del perfil: {profile['min_assets']})."
        )

    sub_prices = price_df[candidates]
    annual_return_hist, annual_cov, annual_vol = prices_mod.compute_returns_and_covariance(sub_prices)

    sub_factors = factor_table.loc[candidates]
    sectors_series = pd.Series({t: universe_sectors[t] for t in candidates})

    market_caps = sub_factors["market_cap"]
    w_mkt = optimization.market_cap_weights(market_caps)
    pi = optimization.implied_equilibrium_returns(annual_cov, w_mkt, profile["risk_aversion_lambda"])
    posterior_returns = optimization.black_litterman_posterior(
        annual_cov, pi, sub_factors["composite_z"], view_confidence=0.6
    )

    weights = optimization.optimize_weights(
        expected_returns=posterior_returns,
        cov=annual_cov,
        sectors=sectors_series,
        risk_aversion_lambda=profile["risk_aversion_lambda"],
        max_weight_per_asset=profile["max_weight_per_asset"],
        max_weight_per_sector=profile["max_weight_per_sector"],
        max_weight_argentina=profile["max_weight_argentina"],
    )

    # filtramos posiciones residuales casi nulas (ruido numérico del optimizador)
    weights = weights[weights > 0.005]
    weights = weights / weights.sum()

    stats = optimization.portfolio_stats(weights, posterior_returns, annual_cov)
    last_prices = prices_mod.latest_prices(sub_prices)

    # Métricas vs mercado (históricas) de la cartera final
    mkt_stats = optimization.market_relative_stats(
        sub_prices, weights, market_series, RISK_FREE_RATE_ANNUAL
    )

    allocations = []
    invested = 0.0
    for ticker, w in weights.items():
        amount = req.amount_usd * w
        price = float(last_prices[ticker])
        shares = amount / price if price > 0 else 0.0
        invested += amount
        score_row = sub_factors.loc[ticker]
        rationale = _build_rationale(ticker, score_row)
        allocations.append(AssetAllocation(
            ticker=ticker,
            sector=universe_sectors[ticker],
            weight=round(float(w), 4),
            amount_usd=round(amount, 2),
            shares_approx=round(shares, 4),
            last_price=round(price, 2),
            composite_score=float(score_row["composite_score"]),
            rationale=rationale,
        ))

    cash_leftover = round(req.amount_usd - invested, 2)

    return PortfolioResponse(
        amount_usd=req.amount_usd,
        risk_profile=req.risk_profile,
        allocations=sorted(allocations, key=lambda a: a.weight, reverse=True),
        expected_return_annual=round(stats["expected_return_annual"], 4),
        expected_volatility_annual=round(stats["expected_volatility_annual"], 4),
        sharpe_approx=(
            round((stats["expected_return_annual"] - RISK_FREE_RATE_ANNUAL) / stats["expected_volatility_annual"], 3)
            if stats["expected_volatility_annual"] else None
        ),
        cash_leftover_usd=cash_leftover,
        market_proxy=MARKET_PROXY_TICKER if market_series is not None else None,
        beta_vs_market=mkt_stats["beta"],
        alpha_annual_vs_market=mkt_stats["alpha_annual"],
        tracking_error_annual=mkt_stats["tracking_error_annual"],
        warnings=warnings,
    )


def _build_rationale(ticker: str, row: pd.Series) -> str:
    parts = []
    if row.get("score_value", 0) > 0.3:
        parts.append("valuación atractiva (P/E y P/B relativos bajos)")
    elif row.get("score_value", 0) < -0.3:
        parts.append("valuación exigente vs el universo")
    if row.get("score_quality", 0) > 0.3:
        parts.append("buena calidad (ROE alto, deuda controlada)")
    if row.get("score_growth_future", 0) > 0.3:
        parts.append("estimados de crecimiento futuro por encima del promedio")
    if row.get("score_momentum", 0) > 0.3:
        parts.append("momentum superior al mercado (6m, vs SPY)")
    elif row.get("score_momentum", 0) < -0.3:
        parts.append("momentum por debajo del mercado, posición chica por ese motivo")
    if not parts:
        parts.append("score balanceado en todos los factores")
    return f"{ticker}: " + "; ".join(parts) + "."


@router.post("/cache/refresh", response_model=RefreshResponse)
def refresh_cache():
    """Refresca fundamentals para todo el universo (correr 1 vez/día, ej. con
    un cron o el script scripts/warmup_cache.py) para no pegarle a FMP en vivo
    durante el uso normal de la herramienta."""
    tickers = list(full_universe().keys())
    errors = []
    refreshed = 0
    for t in tickers:
        try:
            fundamentals_mod.get_fundamentals(t)
            refreshed += 1
        except Exception as e:
            errors.append(f"{t}: {e}")
    return RefreshResponse(tickers_refreshed=refreshed, errors=errors)


@router.get("/universe/scan", response_model=UniverseScanResponse)
def universe_scan():
    """Panel de mercado: todos los indicadores de todo el universo, en una sola
    respuesta. Reusa el mismo pipeline que el armado de carteras (precios +
    fundamentals + scoring) pero sin optimizar — es para explorar/screenear."""
    universe = full_universe()
    tickers = list(universe.keys())

    try:
        price_df = prices_mod.fetch_price_history(tickers + [MARKET_PROXY_TICKER])
    except Exception as e:
        logger.exception("Error en scan: precios")
        raise HTTPException(502, f"Error obteniendo precios: {e}")

    market_series = (
        price_df[MARKET_PROXY_TICKER] if MARKET_PROXY_TICKER in price_df.columns else None
    )
    available = [t for t in tickers if t in price_df.columns]

    momentum = prices_mod.relative_momentum_score(price_df[available], market_series)
    fundamentals = fundamentals_mod.get_fundamentals_bulk(available)
    factor_table = scoring.build_factor_table(fundamentals, momentum)
    annual_return, _, annual_vol = prices_mod.compute_returns_and_covariance(price_df[available])
    last_prices = prices_mod.latest_prices(price_df[available])

    rows: list[UniverseScanRow] = []
    for t in available:
        ft = factor_table.loc[t]
        f = fundamentals.get(t, {})
        price = _num(last_prices.get(t))
        target = _num(f.get("analyst_target_price"))
        upside = (target / price - 1) if (target and price) else None

        # dividend_yield en fundamentals guarda el último dividendo ($/acción);
        # el yield real ~ dividendo / precio.
        last_div = _num(f.get("dividend_yield"))
        div_yield = (last_div / price) if (last_div and price) else None

        pe = _num(ft.get("pe_ratio"))
        roe = _num(ft.get("roe"))
        partial = bool(f.get("_partial")) or pe is None or roe is None

        rows.append(UniverseScanRow(
            ticker=t,
            sector=universe[t],
            last_price=price,
            composite_score=_num(ft.get("composite_score"), 1),
            score_value=_num(ft.get("score_value"), 3),
            score_quality=_num(ft.get("score_quality"), 3),
            score_growth=_num(ft.get("score_growth_future"), 3),
            score_momentum=_num(ft.get("score_momentum"), 3),
            pe_ratio=_num(pe, 2),
            price_to_book=_num(ft.get("price_to_book"), 2),
            roe=_num(roe, 4),
            debt_to_equity=_num(ft.get("debt_to_equity"), 3),
            dividend_yield=_num(div_yield, 4),
            market_cap=_num(ft.get("market_cap")),
            revenue_growth_3y=_num(ft.get("revenue_growth_3y"), 4),
            eps_growth_estimate_next_y=_num(ft.get("eps_growth_estimate_next_y"), 4),
            momentum_6m=_num(ft.get("momentum_6m"), 4),
            beta=_num(ft.get("beta"), 3),
            annual_return=_num(annual_return.get(t), 4),
            annual_vol=_num(annual_vol.get(t), 4),
            analyst_target_price=_num(target, 2),
            upside_pct=_num(upside, 4),
            partial=partial,
        ))

    rows.sort(key=lambda r: (r.composite_score is None, -(r.composite_score or 0)))
    coverage_full = sum(1 for r in rows if not r.partial)

    return UniverseScanResponse(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        market_proxy=MARKET_PROXY_TICKER if market_series is not None else None,
        coverage_full=coverage_full,
        coverage_total=len(rows),
        rows=rows,
    )
