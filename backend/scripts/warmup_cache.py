"""
Corré esto 1 vez por día (manualmente, o con el Task Scheduler de Windows)
para precalentar el caché de fundamentals de todo el universo SIN pegarle
a FMP en vivo durante el uso normal de la herramienta.

Uso:
    cd backend
    python -m scripts.warmup_cache
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import full_universe
from app.data import fundamentals as fundamentals_mod


def main():
    tickers = sorted(full_universe().keys())
    print(f"Precalentando caché de fundamentals para {len(tickers)} tickers...")
    ok, fail = 0, 0
    for i, t in enumerate(tickers, 1):
        try:
            fundamentals_mod.get_fundamentals(t)
            ok += 1
            print(f"  [{i}/{len(tickers)}] {t} OK")
        except Exception as e:
            fail += 1
            print(f"  [{i}/{len(tickers)}] {t} FALLÓ: {e}")
        time.sleep(0.3)  # no saturar el rate limit del free tier
    print(f"\nListo. OK={ok} FALLÓ={fail}")
    if fail:
        print("Tip: con el free tier de FMP (250 calls/día) y ~74 tickers x 5 "
              "endpoints c/u, podés estar cerca del límite. Si falla por rate "
              "limit, corré de nuevo más tarde o subí de plan.")


if __name__ == "__main__":
    main()
