"""
Configuración central: universo de inversión, parámetros de modelo y settings.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths / cache
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

CACHE_DB_PATH = CACHE_DIR / "cache.sqlite3"

# Cuánto dura el caché antes de refrescar (en horas). Default 24hs = 1 vez por día.
FUNDAMENTALS_CACHE_TTL_HOURS = int(os.getenv("FUNDAMENTALS_CACHE_TTL_HOURS", "24"))
PRICES_CACHE_TTL_HOURS = int(os.getenv("PRICES_CACHE_TTL_HOURS", "12"))

# ---------------------------------------------------------------------------
# API keys (poné las tuyas en un .env o variables de entorno)
# ---------------------------------------------------------------------------
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
# API "stable" de FMP (query params, ej. /stable/profile?symbol=AAPL).
# La v3 vieja (path params, /v3/profile/AAPL) está deprecada — no la uses.
FMP_STABLE_URL = "https://financialmodelingprep.com/stable"

# ---------------------------------------------------------------------------
# Universo de inversión
# ---------------------------------------------------------------------------
# Acciones de EEUU, todas verificadas contra el listado oficial de CEDEARs de BYMA
# (actualizado 3/2/2026). Organizado por sector GICS.
US_UNIVERSE = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "CRM", "ADBE", "AMD"],
    "Healthcare": ["UNH", "JNJ", "LLY", "ABBV", "PFE"],
    "Financials": ["JPM", "BAC", "GS", "V", "MA", "PYPL", "NU", "SPGI"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS"],
    "Industrials": ["CAT", "BA", "HON", "FDX", "GE", "LMT"],
    "Consumer Staples": ["PG", "KO", "PEP", "WMT", "COST"],
    "Energy": ["XOM", "CVX", "OXY", "SLB", "BP", "SHEL", "TTE"],
    "Utilities": ["VST", "CEG"],
    "Materials": ["NEM", "FCX", "NUE"],
}

# ADRs argentinos (cotizan en NYSE/NASDAQ en USD). Tratados como un "sector"
# propio para que el optimizador pueda respetar un cap de exposición Argentina.
ARGENTINE_ADRS = {
    "Argentina": [
        "GGAL", "YPF", "PAM", "BMA", "BBAR", "CRESY", "CEPU", "EDN", "IRS",
        "LOMA", "SUPV", "TEO", "TGS", "TS", "TX", "CAAP",
        "BIOX", "AGRO", "MELI", "VIST",
        # IRCP y DESP quitados: delisted (IRCP fusionada en IRSA, DESP comprada
        # por Prosus). yfinance no tenía precio -> se descartaban siempre.
    ]
}

# CEDEAR ratio de conversión no se usa para el cálculo (todo opera en USD vía
# precio del subyacente), pero queda documentado para referencia / frontend.


def full_universe() -> dict:
    """Devuelve {ticker: sector} para todo el universo combinado."""
    out = {}
    for sector, tickers in {**US_UNIVERSE, **ARGENTINE_ADRS}.items():
        for t in tickers:
            out[t] = sector
        # nota: si un ticker se repitiera entre sectores, gana el último
    return out


def all_tickers() -> list:
    return sorted(full_universe().keys())


# ---------------------------------------------------------------------------
# Perfiles de riesgo -> restricciones para el optimizador
# ---------------------------------------------------------------------------
RISK_PROFILES = {
    "conservador": {
        "max_weight_per_asset": 0.08,
        "max_weight_per_sector": 0.25,
        "max_weight_argentina": 0.10,   # cap de exposición a ADRs argentinos
        "min_assets": 12,
        "max_assets": 20,
        "target_volatility_annual": 0.12,   # objetivo aproximado, soft constraint
        "risk_aversion_lambda": 6.0,        # mayor = más averso al riesgo (Black-Litterman/MVO)
    },
    "moderado": {
        "max_weight_per_asset": 0.12,
        "max_weight_per_sector": 0.35,
        "max_weight_argentina": 0.20,
        "min_assets": 10,
        "max_assets": 18,
        "target_volatility_annual": 0.18,
        "risk_aversion_lambda": 3.5,
    },
    "agresivo": {
        "max_weight_per_asset": 0.18,
        "max_weight_per_sector": 0.45,
        "max_weight_argentina": 0.35,
        "min_assets": 8,
        "max_assets": 15,
        "target_volatility_annual": 0.28,
        "risk_aversion_lambda": 1.8,
    },
}

# Parámetros generales del modelo
RISK_FREE_RATE_ANNUAL = float(os.getenv("RISK_FREE_RATE_ANNUAL", "0.04"))  # T-Bill ~4% anual, ajustable
LOOKBACK_YEARS_PRICES = 3       # historial de precios para covarianza/retornos
MARKET_PROXY_TICKER = "SPY"     # proxy de mercado para Black-Litterman (no SPY CEDEAR, el subyacente real via yfinance
