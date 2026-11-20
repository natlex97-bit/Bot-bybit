# Bot de trading — Bybit Demo, desde tu teléfono (GitHub)

El mismo bot que corre en la simulación, con los mismos parámetros v6
(`strategy.md`), pero abriendo y cerrando órdenes de verdad en tu **cuenta
demo de Bybit** (fondos simulados, sin dinero real). Todo se hace desde el
navegador de tu teléfono. No necesitas computadora ni a Frijol para que corra.

## Antes de empezar: dos cosas que tienes que saber

**1. Cada 1 minuto NO se puede en GitHub.** GitHub Actions no permite
programar más seguido que **cada 5 minutos** ("The shortest interval you can
run scheduled workflows is once every 5 minutes"). Un cron de `* * * * *` se
acepta pero simplemente no corre cada minuto. El mínimo real es `*/5`.

Esto no es un problema para los cierres: cada futuro se abre con su
**take-profit y stop-loss puestos EN Bybit**, así que el exchange cierra la
posición al instante cuando el precio toca el nivel, aunque el bot revise cada
5 minutos. Lo único que se revisa cada 5 minutos son las aperturas nuevas.

Si de verdad quieres cada 1 minuto, la única opción es un servidor barato
(VPS de ~$4-5 USD/mes) corriendo el bot con cron. GitHub gratis no lo da.

**2. Cada 5 minutos solo sale gratis si el repositorio es PÚBLICO.** En
repositorio privado GitHub Free solo da 2,000 minutos al mes, y cada 5 minutos
son ~8,640 minutos al mes: no alcanza y se detendría. En repositorio público
los minutos son ilimitados y gratis. Por eso el repo debe ser **público**.

Que sea público NO expone tus claves: van en **Secrets** y GitHub nunca las
muestra ni las publica. Lo que sí será visible es el código del bot y el
historial de la demo (que es dinero ficticio).

## Paso 1: cuenta de Bybit y clave API para la demo (desde el navegador del teléfono)

1. En **Chrome de tu teléfono** (no en la app de Bybit), entra a
   `bybit.com` e inicia sesión o crea tu cuenta.
2. Activa la verificación en dos pasos (Google Authenticator). Sin 2FA no te
   deja crear claves API.
3. **Si tu cuenta es nueva, espera 48 horas**: Bybit no deja crear claves API
   en las primeras 48 horas después del registro.
4. Cambia a **Demo Trading**: menú Trade → Demo Trading (o toca tu foto de
   perfil → Demo Trading). Verás que arriba dice "Demo Trading". La demo no
   pide verificación de identidad (KYC) y trae 50,000 USDT de fondos de
   prueba.
5. Con Demo Trading activo, toca tu foto de perfil → **API** → **Create New
   Key** → **System-generated API Keys**.
6. Ponle nombre, por ejemplo `bot-demo-github`, y marca:
   - **API Key Usage:** API Transaction
   - **Permissions:** Read-Write
   - Activa **Unified Trading** (órdenes y posiciones).
   - **NO marques nada de retiros ni transferencias.** La clave solo debe
     poder operar.
   - **IP:** déjalo sin amarrar a ninguna IP (GitHub cambia de IP en cada
     corrida, no se puede amarrar). Ojo: una clave sin IP amarrada **caduca a
     los 90 días**; cuando deje de funcionar, crea otra igual y actualiza el
     Secret en GitHub.
7. Confirma con tu código de Google Authenticator y **copia la API Key y el
   API Secret en ese momento** (el Secret solo se muestra una vez). Guárdalos
   en un lugar seguro. Nunca los pegues en el código ni en el chat.

## Paso 2: repositorio en GitHub (desde el navegador del teléfono)

1. En Chrome entra a `github.com` e inicia sesión o crea tu cuenta (gratis).
2. Crea un repositorio **PÚBLICO** (por lo explicado arriba), por ejemplo
   `bot-bybit-demo`. No marques "Add a README" (ya viene uno en el paquete).
3. Sube los archivos de este paquete: **Add file → Upload files** y elige
   todos los archivos (`trade_demo.py`, `bybit_client.py`, `analyze.py`,
   `fetch_and_run.py`, `strategy.md`, `state.json`, `README.md` y la carpeta
   `data/`).
4. La carpeta `.github/workflows/` es oculta y el selector de archivos del
   teléfono puede no mostrarla. Si no se sube, créala a mano: **Add file →
   Create new file**, escribe como nombre `.github/workflows/bot.yml` y pega
   el contenido del archivo `bot.yml` que viene en este paquete. El
   contenido completo también está al final de este README.

## Paso 3: guardar las claves en GitHub Secrets

1. En tu repositorio: **Settings → Secrets and variables → Actions →
   New repository secret**.
2. Crea `BYBIT_API_KEY` con el valor de tu clave.
3. Crea `BYBIT_API_SECRET` con el valor de tu secreto.
4. En la misma pantalla, pestaña **Variables**, crea la variable `DRY_RUN`
   con valor `true` (modo de prueba, ver paso 4).

## Paso 4: primera prueba en modo DRY_RUN (no abre nada)

1. Ve a la pestaña **Actions** de tu repositorio. Si te pide activar los
   workflows, actívalos.
2. Abre el workflow **Bot Bybit Demo → Run workflow → Run workflow**.
3. Espera a que termine (1-2 minutos) y abre el log. Debes ver líneas como
   `[DRY-RUN] abriría ...` y al final un resumen JSON. **En este modo no se
   envió ninguna orden a Bybit.**
4. Si el log marca error, léelo: lo más común es un Secret mal copiado o la
   clave creada fuera de Demo Trading.

## Paso 5: encender la demo de verdad

1. En **Settings → Secrets and variables → Actions → Variables**, cambia
   `DRY_RUN` a `false`.
2. Vuelve a **Actions → Run workflow** y corre el bot a mano una vez.
3. Revisa en Bybit (Demo Trading → Positions) si apareció alguna posición, y
   en tu repositorio si `state.json` y `dashboard.html` se actualizaron con
   la corrida.
4. Si todo se ve bien, ya no hagas nada: el cron corre solo **cada 5
   minutos** y guarda el estado en el repositorio después de cada corrida.
5. Para ver cómo va, abre el archivo `dashboard.html` en tu repositorio (o
   `equity.csv` y `ledger.csv`).

## Cómo apagarlo (interruptor de emergencia)

- **Opción rápida:** en Bybit, Demo Trading → perfil → API → borra la clave.
  El bot dejará de poder operar al instante.
- **Opción GitHub:** Actions → Bot Bybit Demo → **Disable workflow**.

## Avisos importantes

- **Apaga la simulación de Frijol** cuando enciendas este bot, para no mezclar
  dos historiales. Dile a Frijol que detenga sus crons.
- **No todas las monedas del top 100 están en Bybit.** Si el bot detecta una
  señal en una moneda sin par de futuros en Bybit, omite esa apertura y lo
  anota en el log. Es normal.
- El bot usa una base de capital de **$10,000** para el tamaño de las
  posiciones (igual que la simulación), aunque la demo traiga 50,000 USDT de
  fondos de prueba. Así los resultados son comparables con la simulación.
- El spot long no lleva TP/SL en el exchange (Bybit spot no lo permite igual
  que en futuros): esas salidas las vigila el bot en cada revisión de 5 min.
- La clave API caduca a los 90 días si no tiene IP amarrada. Cuando el bot
  empiece a fallar con error de autenticación, crea una clave nueva y
  actualiza los Secrets.
- GitHub a veces retrasa las corridas programadas cuando hay mucha carga.
  Aunque una corrida se retrase, tus posiciones siguen protegidas por el
  take-profit y stop-loss puestos en Bybit.
- Esto sigue siendo una prueba. La demo no cobra comisiones ni tiene
  deslizamiento real, así que los resultados no son lo que pasaría con dinero
  real.

---

## Contenido de `.github/workflows/bot.yml` (por si lo creas a mano)

```yaml
name: Bot Bybit Demo

on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: bot-bybit-demo
  cancel-in-progress: false

jobs:
  trade:
    runs-on: ubuntu-latest
    timeout-minutes: 4
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Correr bot (Bybit Demo)
        env:
          BYBIT_API_KEY: ${{ secrets.BYBIT_API_KEY }}
          BYBIT_API_SECRET: ${{ secrets.BYBIT_API_SECRET }}
          DRY_RUN: ${{ vars.DRY_RUN }}
          CAPITAL_BASE: '10000'
        run: python trade_demo.py
      - name: Guardar estado
        run: |
          git config user.name "bot-bybit-demo"
          git config user.email "bot@github.local"
          git add state.json ledger.csv equity.csv dashboard.html data/ || true
          git diff --cached --quiet || git commit -m "bot: $(date -u '+%Y-%m-%d %H:%M UTC')"
          git push || true
```
