"""
Backtest walk-forward del pipeline REAL de la app (scoring + BL + optimizador)
para responder una sola pregunta: ¿la herramienta construye carteras que valen
la pena vs alternativas naive?

Qué testea:
  - La señal de momentum residual (unico factor reconstruible historicamente;
    los fundamentals historicos no estan disponibles en FMP free).
  - La maquinaria de construccion: seleccion top-N + Black-Litterman +
    Markowitz con los caps del perfil "moderado".

Variantes comparadas (rebalanceo mensual, mismo universo y fechas):
  1. SPY buy & hold                  (benchmark de mercado)
  2. Equal-weight de todo el universo (benchmark naive)
  3. Top-N momentum, equal-weight     (señal sola, sin optimizador)
  4. Top-N momentum + BL + optimizador (el pipeline completo de la app)

Ademas: IC (Spearman) score->retorno del mes siguiente, para medir si la
señal ordena retornos futuros.

Limitaciones (leer antes de creer):
  - Sesgo de supervivencia: el universo es el ACTUAL (se excluyeron delisted).
    Infla los retornos absolutos de 2,3,4; afecta menos las comparaciones
    relativas entre ellas.
  - Costos: 10 bps por lado sobre el turnover. Sin slippage.
  - El cash sobrante rinde 0.
  - ~4 años de datos = ~45 rebalanceos. Indicativo, no prueba estadistica.

Uso:  cd backend && python -m scripts.backtest_validation
"""
import sys
import warnings
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from app.config import full_universe, RISK_PROFILES, MARKET_PROXY_TICKER
from app.data import prices as P
from app.models import scoring, optimization as O

COST_PER_SIDE = 0.001   # 10 bps por lado
WARMUP_DAYS = 252
YEARS = 5
PROFILE = RISK_PROFILES["moderado"]


def max_drawdown(cum: pd.Series) -> float:
    peak = cum.cummax()
    return float(((cum / peak) - 1).min())


def perf_stats(monthly: pd.Series) -> dict:
    cum = (1 + monthly).cumprod()
    n = len(monthly)
    years = n / 12
    cagr = cum.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    vol = monthly.std(ddof=1) * np.sqrt(12)
    sharpe = (monthly.mean() * 12) / vol if vol > 0 else np.nan
    return {
        "total": cum.iloc[-1] - 1, "cagr": cagr, "vol": vol,
        "sharpe": sharpe, "maxdd": max_drawdown(cum), "n": n,
    }


def turnover_cost(w_new: pd.Series, w_prev: pd.Series | None) -> float:
    if w_prev is None:
        return float(w_new.sum()) * COST_PER_SIDE  # compra inicial
    all_idx = w_new.index.union(w_prev.index)
    delta = w_new.reindex(all_idx, fill_value=0.0) - w_prev.reindex(all_idx, fill_value=0.0)
    return float(delta.abs().sum()) * COST_PER_SIDE


def main():
    universe = full_universe()
    tickers = list(universe.keys())
    print(f"Bajando {len(tickers)}+SPY, {YEARS} anios de historia...")
    df = P.fetch_price_history(tickers + [MARKET_PROXY_TICKER], years=YEARS)
    if MARKET_PROXY_TICKER not in df.columns:
        print("ERROR: no hay serie de SPY, abortando."); return
    spy = df[MARKET_PROXY_TICKER]
    adf = df[[c for c in df.columns if c != MARKET_PROXY_TICKER]]
    print(f"Historia comun: {adf.index[0].date()} -> {adf.index[-1].date()} "
          f"({len(adf)} ruedas, {adf.shape[1]} tickers)")

    # fechas de rebalanceo: ultimo dia habil de cada mes, tras el warmup
    month_last = adf.groupby(pd.Grouper(freq="ME")).apply(lambda x: x.index[-1] if len(x) else pd.NaT).dropna()
    rebal_dates = [d for d in month_last if adf.index.get_loc(d) >= WARMUP_DAYS]
    if len(rebal_dates) < 13:
        print("ERROR: muy poca historia para backtest."); return

    rows = []
    ics = []
    prev = {"topn_ew": None, "topn_opt": None, "ew_all": None}

    for i in range(len(rebal_dates) - 1):
        t, t2 = rebal_dates[i], rebal_dates[i + 1]
        hist = adf.loc[:t]
        r_next = adf.loc[t2] / adf.loc[t] - 1.0
        spy_r = float(spy.loc[t2] / spy.loc[t] - 1.0)

        # señal: mismo codigo que la app (momentum residual ajustado por beta)
        mom = P.relative_momentum_score(hist, spy.loc[:t])
        fundamentals = {tk: {} for tk in hist.columns}  # sin fundamentals historicos
        ft = scoring.build_factor_table(fundamentals, mom)

        # IC de la señal a 1 mes
        common = ft.index.intersection(r_next.dropna().index)
        ic, _ = spearmanr(ft.loc[common, "composite_z"], r_next.loc[common])
        ics.append(ic)

        cands = scoring.select_candidates(ft, PROFILE["max_assets"], PROFILE["min_assets"])

        # --- variante 3: top-N equal weight ---
        w_ew = pd.Series(1.0 / len(cands), index=cands)

        # --- variante 4: pipeline completo (BL + optimizador con caps) ---
        sub = hist[cands]
        _, cov_a, _ = P.compute_returns_and_covariance(sub)
        w_mkt = pd.Series(1.0 / len(cands), index=cands)  # sin mktcap historico -> equal
        pi = O.implied_equilibrium_returns(cov_a, w_mkt, PROFILE["risk_aversion_lambda"])
        post = O.black_litterman_posterior(cov_a, pi, ft.loc[cands, "composite_z"], view_confidence=0.6)
        sectors = pd.Series({c: universe[c] for c in cands})
        w_opt = O.optimize_weights(
            expected_returns=post, cov=cov_a, sectors=sectors,
            risk_aversion_lambda=PROFILE["risk_aversion_lambda"],
            max_weight_per_asset=PROFILE["max_weight_per_asset"],
            max_weight_per_sector=PROFILE["max_weight_per_sector"],
            max_weight_argentina=PROFILE["max_weight_argentina"],
        )
        w_opt = w_opt[w_opt > 0.005]
        if w_opt.sum() > 0:
            w_opt = w_opt / w_opt.sum()

        # --- variante 2: equal weight de todo el universo ---
        avail = r_next.dropna().index.intersection(hist.columns)
        w_all = pd.Series(1.0 / len(avail), index=avail)

        r_topn_ew = float((w_ew * r_next.reindex(w_ew.index).fillna(0)).sum()) - turnover_cost(w_ew, prev["topn_ew"])
        r_topn_opt = float((w_opt * r_next.reindex(w_opt.index).fillna(0)).sum()) - turnover_cost(w_opt, prev["topn_opt"])
        r_ew_all = float((w_all * r_next.reindex(w_all.index).fillna(0)).sum()) - turnover_cost(w_all, prev["ew_all"])
        prev = {"topn_ew": w_ew, "topn_opt": w_opt, "ew_all": w_all}

        rows.append({"date": t2, "spy": spy_r, "ew_all": r_ew_all,
                     "topn_ew": r_topn_ew, "topn_opt": r_topn_opt})

    res = pd.DataFrame(rows).set_index("date")
    print(f"\nPeriodo evaluado: {res.index[0].date()} -> {res.index[-1].date()} ({len(res)} meses)")
    print(f"IC medio (score -> retorno prox. mes): {np.nanmean(ics):+.4f}  "
          f"(std {np.nanstd(ics):.3f}, %positivo {np.mean([x>0 for x in ics])*100:.0f}%)")

    names = {"spy": "1) SPY buy&hold", "ew_all": "2) EW universo",
             "topn_ew": "3) TopN mom EW", "topn_opt": "4) Pipeline app (BL+opt)"}
    print(f"\n{'Variante':26} {'Total':>8} {'CAGR':>7} {'Vol':>6} {'Sharpe':>7} {'MaxDD':>7}")
    for k, label in names.items():
        s = perf_stats(res[k])
        print(f"{label:26} {s['total']*100:7.1f}% {s['cagr']*100:6.1f}% {s['vol']*100:5.1f}% "
              f"{s['sharpe']:7.2f} {s['maxdd']*100:6.1f}%")

    corr = res["topn_opt"].corr(res["spy"])
    print(f"\nCorrelacion mensual pipeline vs SPY: {corr:.2f}")
    print("\nRecordatorio: sesgo de supervivencia presente; costos 10bps/lado; cash rinde 0.")


if __name__ == "__main__":
    main()
