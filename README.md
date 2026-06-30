# Portfolio Builder — Portfolio Investment

Arma carteras de inversión a partir de un monto en USD, combinando:
- **Universo**: 52 acciones de EEUU (verificadas contra el listado oficial de
  CEDEARs de BYMA) + 22 ADRs argentinos.
- **Scoring fundamental + técnico**: value, calidad, crecimiento estimado a
  futuro (vía analyst estimates de FMP) y momentum de precio.
- **Perfil de riesgo**: conservador / moderado / agresivo, define caps duros
  de exposición por activo, sector y Argentina.
- **Optimización Black-Litterman + Markowitz**: retornos de equilibrio de
  mercado ajustados por el scoring, optimizados por riesgo-retorno respetando
  los caps del perfil.

## Subir esto a tu repo `carteras`

Ya está todo commiteado localmente (`git log` te va a mostrar el commit
inicial). En tu Lenovo, descomprimí el zip y corré:

```bash
cd portfolio-builder
git remote add origin https://github.com/TU_USUARIO/carteras.git
git branch -M main
git push -u origin main
```

No lo pude pushear yo directamente — crear/autenticar contra tu cuenta de
GitHub requeriría tus credenciales, y eso no lo hago. Pero el repo ya está
inicializado con el commit hecho, así que es solo `remote add` + `push`.

**Importante sobre la API key**: te dejé un `backend/.env` con tu
`FMP_API_KEY` ya cargada para que funcione apenas lo bajes — **pero ese
archivo está en `.gitignore` y no se va a subir a GitHub** (las keys nunca
deberían vivir en un repo, ni siquiera privado). Si en algún momento ves que
`git status` lo lista como "untracked" o (peor) "to be committed", pará y
avisame — algo se rompió.

## Setup (local, en tu Lenovo)

### 1. Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# El .env con tu FMP_API_KEY ya está armado, no hace falta tocar nada.
# Si lo borraste o cambiaste de máquina: copy .env.example .env y pegá tu key ahí.

uvicorn app.main:app --reload --port 8000
```

Confirmá que funciona entrando a `http://127.0.0.1:8000/docs` (Swagger UI).

### 2. Precalentar el caché (recomendado, una vez por día)

Con el free tier de FMP (250 calls/día) **no conviene** pedir fundamentals
en vivo cada vez que armás una cartera — 74 tickers x ~5 endpoints c/u te
come el rate limit rápido. Corré esto 1 vez por día (a la mañana, antes de
laburar):

```bash
cd backend
python -m scripts.warmup_cache
```

Esto deja todo en `backend/cache/cache.sqlite3` con TTL de 24hs. Las
consultas del día siguen usando ese caché, no pegan a FMP en vivo.

### 3. Frontend

Es un único HTML standalone, sin build. Simplemente abrilo en el navegador:

```bash
cd frontend
start index.html       # Windows
```

(o doble-click en `index.html`). Necesita el backend corriendo en
`127.0.0.1:8000` — el header del frontend te avisa si no lo detecta.

## Arquitectura

```
backend/
  app/
    config.py          # universo (tickers + sectores), perfiles de riesgo
    data/
      cache.py          # caché SQLite con TTL
      prices.py          # yfinance: precios, retornos, covarianza, momentum
      fundamentals.py    # FMP: ratios, growth, analyst estimates/targets
    models/
      scoring.py          # factor model: value/quality/growth-futuro/momentum -> score 0-100
      optimization.py     # Black-Litterman (views = scoring) + Markowitz con caps
    api/
      routes.py            # POST /api/portfolio/build, GET /api/universe
    schemas.py             # pydantic request/response
  scripts/
    warmup_cache.py        # precalienta fundamentals 1 vez/día
frontend/
  index.html                # UI standalone (Bloomberg dark, Inter + JetBrains Mono)
```

## Decisiones de diseño que vale la pena que sepas

- **`sum(pesos) <= 1`, no `= 1`**: si los caps de sector/Argentina del
  perfil de riesgo son muy restrictivos para el universo disponible (ej.
  activaste "Solo ADRs argentinos" con perfil conservador, cap Argentina
  10%), puede ser matemáticamente imposible invertir el 100% sin violar un
  cap. En ese caso el optimizador deja el resto en cash
  (`cash_leftover_usd`) en vez de fallar o violar la restricción.
- **Black-Litterman simplificado**: uso views absolutas (una por activo,
  picking matrix = identidad) en vez de la formulación matricial completa
  con P/Q/Omega arbitrarios. Es la simplificación estándar cuando cada
  activo tiene exactamente una view del modelo de scoring.
- **yfinance es no-oficial**: puede romperse sin aviso si Yahoo cambia algo.
  Si un ticker falla la descarga de precio, se descarta silenciosamente (vas
  a verlo en `warnings` de la respuesta) en vez de tirar abajo todo el
  request.
- **No ejecuta nada**: esto es 100% una herramienta de research/sugerencia.
  No se conecta a IBKR ni a ningún broker para ejecutar — el output es
  "comprarías esto, en estas cantidades", vos decidís y ejecutás.

## Pendiente / próximos pasos posibles

- Guardar carteras generadas (historial) — hoy es stateless, cada request
  recalcula todo.
- Endpoint de rebalanceo (dada una cartera actual + nuevo monto, sugerir
  ajustes en vez de armar desde cero).
- Reemplazar el caché SQLite por algo compartido si en algún momento esto
  deja de correr "solo local".
- Si FMP free tier queda corto, hay margen para mezclar con datos de IBKR
  vía su API oficial (no el MCP de este chat) ya que tenés cuenta — pero es
  una integración aparte.
