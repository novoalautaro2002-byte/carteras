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
from datetime import datetime
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
    except (FMPError, httpx.HTTPError, IndexError, KeyError) as e:
        logger.warning("ratios falló para %s: %s", ticker, e)
        result["_partial"] = True

    try:
        # ROE no está en ratios-ttm (ese endpoint no expone ningún return*);
        # el campo real returnOnEquityTTM vive en key-metrics-ttm.
        key_metrics = _get("key-metrics-ttm", params={"symbol": ticker})
        if key_metrics:
            km = key_metrics[0]
            result["roe"] = km.get("returnOnEquityTTM")
    except (FMPError, httpx.HTTPError, IndexError, KeyError) as e:
        logger.warning("key-metrics falló para %s: %s", ticker, e)
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
        # El campo real es epsAvg (no estimatedEpsAvg). El endpoint devuelve
        # filas anuales ordenadas por fecha desc, incluyendo años futuros, así
        # que en vez de tomar un nivel de EPS suelto calculamos el crecimiento
        # de EPS estimado del próximo año fiscal vs el año base (el más reciente
        # <= hoy) — que es lo que el nombre del campo realmente describe.
        estimates = _get("analyst-estimates", params={"symbol": ticker, "period": "annual", "page": 0, "limit": 8})
        if estimates:
            eps_by_year = {}
            for e in estimates:
                d = str(e.get("date") or "")
                eps = e.get("epsAvg")
                if len(d) >= 4 and d[:4].isdigit() and eps is not None:
                    eps_by_year[int(d[:4])] = float(eps)
            if eps_by_year:
                cur_year = datetime.now().year
                base_year = max((y for y in eps_by_year if y <= cur_year), default=min(eps_by_year))
                base, nxt = eps_by_year.get(base_year), eps_by_year.get(base_year + 1)
                if base and base > 0 and nxt is not None:
                    result["eps_growth_estimate_next_y"] = nxt / base - 1
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
