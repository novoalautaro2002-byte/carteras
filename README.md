# Portfolio Builder — Portfolio Investment

Arma carteras de inversión a partir de un monto en USD, combinando:
- **Universo**: 52 acciones de EEUU (verificadas contra el listado oficial de
  CEDEARs de BYMA) + 20 ADRs argentinos (72 tickers en total).
- **Scoring fundamental + técnico**: value, calidad, crecimiento estimado a
  futuro (vía analyst estimates de FMP) y **momentum residual vs SPY** (ajustado
  por beta, para aislar la fuerza propia del activo del ruido de mercado).
- **Perfil de riesgo**: conservador / moderado / agresivo, define caps duros
  de exposición por activo, sector y Argentina.
- **Optimización Black-Litterman + Markowitz**: retornos de equilibrio de
  mercado ajustados por el scoring, optimizados por riesgo-retorno respetando
  los caps del perfil.
- **Métricas vs mercado**: beta, alpha de Jensen anual y tracking error de la
  cartera final contra SPY.

## Demo en vivo

Hay una demo estática en **GitHub Pages** que muestra la interfaz con una
cartera de ejemplo (salida real del motor, congelada). No necesita backend.

> La demo es solo la UI: el cálculo real corre en el backend local (abajo). Una
> página en Pages (HTTPS) no puede pegarle a un backend en `localhost`, así que
> para armar carteras de verdad hay que correr el backend en tu máquina.

## Uso rápido (Windows)

La forma más simple, sin terminal:

1. **Primera vez**: doble-click en **`setup.bat`** (crea el entorno virtual e
   instala todo). Después abrí `backend\.env` y pegá tu `FMP_API_KEY`.
2. **Cada vez que quieras usarlo**: doble-click en **`run.bat`** — levanta el
   backend y abre la app en el navegador. Para apagarlo, cerrá la ventana negra
   del backend.

Si preferís hacerlo a mano, o no estás en Windows, seguí los pasos de abajo.

## Setup local (manual)

### 1. Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Configurá tu API key de Financial Modeling Prep:
copy .env.example .env         # y pegá tu FMP_API_KEY en .env
# (.env está en .gitignore — la key nunca se sube al repo)

uvicorn app.main:app --reload --port 8000
```

Confirmá que funciona entrando a `http://127.0.0.1:8000/docs` (Swagger UI).

> **Nota sobre yfinance**: se usa `yfinance>=1.5.1`. Versiones viejas (0.2.x)
> devuelven 0 filas porque la API de Yahoo cambió. Es una dependencia no
> oficial: si en el futuro deja de traer precios, probá actualizarla.

### 2. Precalentar el caché (recomendado, una vez por día)

Con el free tier de FMP (250 calls/día) **no conviene** pedir fundamentals
en vivo cada vez que armás una cartera — 72 tickers x ~6 endpoints c/u te
come el rate limit rápido. Corré esto 1 vez por día:

```bash
cd backend
python -m scripts.warmup_cache
```

Esto deja todo en `backend/cache/cache.sqlite3` con TTL de 24hs.

> **Cobertura del free tier**: los endpoints premium de FMP (ratios, growth,
> analyst estimates) devuelven `402` para varios símbolos (ADRs argentinos y
> algunos US); `profile` (market cap, beta) sí funciona para todos. Con la key
> gratis el scoring queda parcial y se apoya más en momentum + market cap. Para
> scoring fundamental completo hace falta un plan FMP pago.

### 3. Frontend

Es un único HTML standalone, sin build:

```bash
cd frontend
start index.html       # Windows (o doble-click)
```

Necesita el backend corriendo en `127.0.0.1:8000` — el header te avisa si no lo
detecta y, si no hay backend, cae a **modo demo** con datos de ejemplo.

## Arquitectura

```
index.html                    # redirect a frontend/ (solo para GitHub Pages)
backend/
  app/
    config.py          # universo (tickers + sectores), perfiles de riesgo, MARKET_PROXY_TICKER
    data/
      cache.py          # caché SQLite con TTL
      prices.py          # yfinance: precios, retornos, covarianza, momentum residual vs SPY
      fundamentals.py    # FMP: ratios, growth, analyst estimates/targets, ROE (key-metrics-ttm)
    models/
      scoring.py          # factor model: value/quality/growth-futuro/momentum -> score 0-100
      optimization.py     # Black-Litterman (views = scoring) + Markowitz con caps + métricas vs mercado
    api/
      routes.py            # POST /api/portfolio/build, GET /api/universe
    schemas.py             # pydantic request/response
  scripts/
    warmup_cache.py        # precalienta fundamentals 1 vez/día
frontend/
  index.html                # UI standalone (Bloomberg dark) + modo demo
```

## Decisiones de diseño que vale la pena que sepas

- **Momentum residual, no absoluto**: el momentum de cada activo se ajusta por
  su beta (`momentum_activo - beta*momentum_SPY`). Restar el mercado "plano"
  sería un shift uniforme que no cambia el ranking cross-sectional (el z-score
  es invariante a restar una constante); descontar `beta*mercado` sí aísla la
  fuerza propia del activo del componente de mercado.
- **`sum(pesos) <= 1`, no `= 1`**: si los caps de sector/Argentina del perfil
  son muy restrictivos para el universo disponible, puede ser imposible invertir
  el 100% sin violar un cap. En ese caso el optimizador deja el resto en cash
  (`cash_leftover_usd`) en vez de fallar.
- **Black-Litterman simplificado**: views absolutas (una por activo, picking
  matrix = identidad) en vez de la formulación matricial completa. Es la
  simplificación estándar cuando cada activo tiene una única view del scoring.
- **yfinance es no-oficial**: si un ticker falla la descarga de precio, se
  descarta (lo vas a ver en `warnings` de la respuesta) en vez de tirar abajo
  todo el request.
- **No ejecuta nada**: es 100% una herramienta de research/sugerencia. No se
  conecta a ningún broker — el output es "comprarías esto, en estas cantidades",
  vos decidís y ejecutás.

## Pendiente / próximos pasos posibles

- Guardar carteras generadas (historial) — hoy es stateless.
- Endpoint de rebalanceo (dada una cartera actual + nuevo monto, sugerir ajustes).
- Si FMP free tier queda corto, hay margen para mezclar con datos de IBKR vía su
  API oficial — pero es una integración aparte.
