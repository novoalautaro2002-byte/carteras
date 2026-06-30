"""Test rápido con datos sintéticos: valida que scoring + optimización no
rompan y que las restricciones del perfil de riesgo se respeten."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from app.models import scoring, optimization
from app.config import RISK_PROFILES

np.random.seed(42)
tickers = [f"T{i}" for i in range(15)]
sectors = pd.Series({t: ["Tech", "Energy", "Argentina"][i % 3] for i, t in enumerate(tickers)})

# fundamentals sintéticos
fundamentals = {}
for t in tickers:
    fundamentals[t] = {
        "pe_ratio": np.random.uniform(8, 35),
        "price_to_book": np.random.uniform(1, 8),
        "roe": np.random.uniform(0.05, 0.35),
        "debt_to_equity": np.random.uniform(0.1, 2.5),
        "revenue_growth_3y": np.random.uniform(-0.05, 0.25),
        "eps_growth_estimate_next_y": np.random.uniform(-0.1, 0.3),
        "analyst_target_price": None,
        "market_cap": np.random.uniform(5e9, 500e9),
        "beta": np.random.uniform(0.7, 1.5),
    }

# precios sintéticos: random walk geométrico para 15 activos, 750 días
days = 750
returns = np.random.normal(0.0005, 0.018, size=(days, len(tickers)))
prices = 100 * np.exp(np.cumsum(returns, axis=0))
price_df = pd.DataFrame(prices, columns=tickers)

momentum = (price_df.iloc[-1] / price_df.iloc[-150]) - 1

factor_table = scoring.build_factor_table(fundamentals, momentum)
print("=== Factor table (top 5) ===")
print(factor_table[["composite_score", "score_value", "score_quality", "score_growth_future", "score_momentum"]].head())

profile = RISK_PROFILES["moderado"]
candidates = scoring.select_candidates(factor_table, profile["max_assets"], profile["min_assets"])
print(f"\nCandidatos seleccionados ({len(candidates)}): {candidates}")

daily_ret = price_df[candidates].pct_change().dropna()
annual_cov = daily_ret.cov() * 252
sub_factors = factor_table.loc[candidates]
sub_sectors = sectors.loc[candidates]

w_mkt = optimization.market_cap_weights(sub_factors["market_cap"])
pi = optimization.implied_equilibrium_returns(annual_cov, w_mkt, profile["risk_aversion_lambda"])
posterior = optimization.black_litterman_posterior(annual_cov, pi, sub_factors["composite_z"])

weights = optimization.optimize_weights(
    expected_returns=posterior,
    cov=annual_cov,
    sectors=sub_sectors,
    risk_aversion_lambda=profile["risk_aversion_lambda"],
    max_weight_per_asset=profile["max_weight_per_asset"],
    max_weight_per_sector=profile["max_weight_per_sector"],
    max_weight_argentina=profile["max_weight_argentina"],
)

print("\n=== Pesos optimizados ===")
print(weights)
print(f"\nSuma de pesos: {weights.sum():.6f} (puede ser <= 1.0; el resto queda en cash)")
print(f"Max peso individual: {weights.max():.4f} (cap perfil: {profile['max_weight_per_asset']})")

for sector in sub_sectors.unique():
    sw = weights[sub_sectors[sub_sectors == sector].index].sum()
    cap = profile["max_weight_argentina"] if sector == "Argentina" else profile["max_weight_per_sector"]
    status = "OK" if sw <= cap + 1e-6 else "VIOLACION"
    print(f"Sector {sector}: peso={sw:.4f} cap={cap} [{status}]")

stats = optimization.portfolio_stats(weights, posterior, annual_cov)
print(f"\nStats: {stats}")
print("\n✅ Smoke test completo sin excepciones.")
