"""
Datos fundamentales, ratios y estimates a futuro vía Financial Modeling Prep (FMP).
Esta es la pieza que cubre el componente "futuro" del análisis: forward P/E,
price targets de analistas y crecimiento de EPS estimado.

Requiere FMP_API_KEY en variables de entorno (ver config.py). Con el free tier
(250 calls/día) hay que ser cuidadoso: se cachea agresivamente (TTL 24hs) y
se recomienda correr un "warmup" una vez por día en vez de pedir on-demand
para 70+ tickers en cada request del usuario (ver scripts/warmup_cache.py).
"""
from __future__ import annotations
import logging
from typing import Optional

import httpx

from app.config import FMP_API_KEY, FMP_STABLE_URL, FUNDAMENTALS_CACHE_TTL_HOURS
from app.data import cache

logger = logging.getLogger(__name__)


class FMPError(Exception):
    pass


def _get(endpoint: str, params: Optional[dict] = None) -> list | dict:
    if not FMP_API_KEY:
        raise FMPError(
            "Falta FMP_API_KEY. Conseguila gratis en financialmodelingprep.com "
            "y seteala como variable de entorno antes de levantar el backend."
        )
    params = dict(params or {})
    params["apikey"] = FMP_API_KEY
    url = f"{FMP_STABLE_URL}/{endpoint}"
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(url, params=params)
        if resp.status_code == 429:
            raise FMPError("Rate limit de FMP alcanzado (free tier: 250 calls/día).")
        resp.raise_for_status()
        return resp.json()


def get_fundamentals(ticker: str) -> dict:
    """
    Devuelve un dict normalizado con fundamentals + forward estimates para
    un ticker. Cachea por 24hs (configurable) para no quemar el rate limit.

    Usa la API "stable" de FMP (query params), que es la vigente — la v3
    vieja (path params tipo /ratios-ttm/{symbol}) está deprecada.
    """
    key = f"fundamentals:{ticker}"
    cached = cache.get(key, FUNDAMENTALS_CACHE_TTL_HOURS)
    if cached is not None:
        return cached

    result = {
        "ticker": ticker,
        "pe_ratio": None,
        "forward_pe": None,
        "peg_ratio": None,
        "price_to_book": None,
        "debt_to_equity": None,
        "roe": None,
        "revenue_growth_3y": None,
        "eps_growth_estimate_next_y": None,
        "analyst_target_price": None,
        "analyst_recommendation": None,
        "dividend_yield": None,
        "beta": None,
        "market_cap": None,
        "_partial": False,
    }

    try:
        profile = _get("profile", params={"symbol": ticker})
        if profile:
            p = profile[0]
            result["beta"] = p.get("beta")
            result["market_cap"] = p.get("marketCap") or p.get("mktCap")
            result["dividend_yield"] = p.get("lastDividend")
    except (FMPError, httpx.HTTPError, IndexError, KeyError) as e:
        logger.warning("profile falló para %s: %s", ticker, e)
        result["_partial"] = True

    try:
        ratios = _get("ratios-ttm", params={"symbol": ticker})
        if ratios:
            r = ratios[0]
            result["pe_ratio"] = r.get("priceToEarningsRatioTTM") or r.get("peRatioTTM")
            result["price_to_book"] = r.get("priceToBookRatioTTM")
            result["debt_to_equity"] = r.get("debtToEquityRatioTTM") or r.get("debtEquityRatioTTM")
            result["roe"] = r.get("returnOnEquityTTM")
    except (FMPError, httpx.HTTPError, IndexError, KeyError) as e:
        logger.warning("ratios falló para %s: %s", ticker, e)
        result["_partial"] = True

    try:
        growth = _get("financial-growth", params={"symbol": ticker, "limit": 3})
        if growth:
            revs = [g.get("revenueGrowth") for g in growth if g.get("revenueGrowth") is not None]
            if revs:
                result["revenue_growth_3y"] = sum(revs) / len(revs)
    except (FMPError, httpx.HTTPError, IndexError, KeyError) as e:
        logger.warning("growth falló para %s: %s", ticker, e)
        result["_partial"] = True

    try:
        estimates = _get("analyst-estimates", params={"symbol": ticker, "period": "annual", "page": 0, "limit": 1})
        if estimates:
            est = estimates[0]
            # campos típicos del endpoint stable: estimatedEpsAvg, estimatedRevenueAvg
            result["eps_growth_estimate_next_y"] = est.get("estimatedEpsAvg")
    except (FMPError, httpx.HTTPError, IndexError, KeyError) as e:
        logger.warning("estimates falló para %s: %s", ticker, e)
        result["_partial"] = True

    try:
        target = _get("price-target-consensus", params={"symbol": ticker})
        if target:
            t = target[0] if isinstance(target, list) else target
            result["analyst_target_price"] = t.get("targetConsensus") or t.get("targetMedian")
    except (FMPError, httpx.HTTPError, IndexError, KeyError) as e:
        logger.warning("price target falló para %s: %s", ticker, e)
        result["_partial"] = True

    cache.set(key, result)
    return result


def get_fundamentals_bulk(tickers: list[str]) -> dict[str, dict]:
    """Trae fundamentals para una lista de tickers, ticker por ticker (FMP free
    tier no tiene bulk endpoint utilizable sin plan pago)."""
    out = {}
    for t in tickers:
        try:
            out[t] = get_fundamentals(t)
        except FMPError as e:
            logger.error("No se pudieron obtener fundamentals de %s: %s", t, e)
            out[t] = {"ticker": t, "_partial": True, "_error": str(e)}
    return out
