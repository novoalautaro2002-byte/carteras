from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field


class PortfolioRequest(BaseModel):
    amount_usd: float = Field(..., gt=0, description="Monto a invertir en USD")
    risk_profile: Literal["conservador", "moderado", "agresivo"] = "moderado"
    exclude_tickers: list[str] = Field(default_factory=list, description="Tickers a excluir del universo")
    only_argentina: bool = False
    only_us: bool = False


class AssetAllocation(BaseModel):
    ticker: str
    sector: str
    weight: float
    amount_usd: float
    shares_approx: float
    last_price: float
    composite_score: float
    rationale: str


class PortfolioResponse(BaseModel):
    amount_usd: float
    risk_profile: str
    allocations: list[AssetAllocation]
    expected_return_annual: float
    expected_volatility_annual: float
    sharpe_approx: Optional[float]
    cash_leftover_usd: float
    # Métricas vs mercado (históricas, sobre el lookback). None si no hay
    # serie de mercado disponible.
    market_proxy: Optional[str] = None
    beta_vs_market: Optional[float] = None
    alpha_annual_vs_market: Optional[float] = None
    tracking_error_annual: Optional[float] = None
    warnings: list[str] = Field(default_factory=list)


class UniverseTicker(BaseModel):
    ticker: str
    sector: str
    composite_score: Optional[float] = None
    last_price: Optional[float] = None


class RefreshResponse(BaseModel):
    tickers_refreshed: int
    errors: list[str] = Field(default_factory=list)


class UniverseScanRow(BaseModel):
    """Una fila del panel de mercado: todos los indicadores de un ticker."""
    ticker: str
    sector: str
    last_price: Optional[float] = None
    # Scoring (composite 0-100 + z-scores por factor para recomputar en el front)
    composite_score: Optional[float] = None
    score_value: Optional[float] = None
    score_quality: Optional[float] = None
    score_growth: Optional[float] = None
    score_momentum: Optional[float] = None
    # Fundamentales
    pe_ratio: Optional[float] = None
    price_to_book: Optional[float] = None
    roe: Optional[float] = None
    debt_to_equity: Optional[float] = None
    dividend_yield: Optional[float] = None
    market_cap: Optional[float] = None
    revenue_growth_3y: Optional[float] = None
    eps_growth_estimate_next_y: Optional[float] = None
    # Técnicos / riesgo
    momentum_6m: Optional[float] = None
    beta: Optional[float] = None
    annual_return: Optional[float] = None
    annual_vol: Optional[float] = None
    # Analistas
    analyst_target_price: Optional[float] = None
    upside_pct: Optional[float] = None
    # Flag: fundamentals incompletos (típico del free tier de FMP)
    partial: bool = False


class UniverseScanResponse(BaseModel):
    generated_at: str
    market_proxy: Optional[str] = None
    coverage_full: int          # cuántos tickers con fundamentals completos
    coverage_total: int
    rows: list[UniverseScanRow]
