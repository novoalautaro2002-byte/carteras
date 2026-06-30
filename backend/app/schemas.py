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
    warnings: list[str] = Field(default_factory=list)


class UniverseTicker(BaseModel):
    ticker: str
    sector: str
    composite_score: Optional[float] = None
    last_price: Optional[float] = None


class RefreshResponse(BaseModel):
    tickers_refreshed: int
    errors: list[str] = Field(default_factory=list)
