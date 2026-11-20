# Simulación de trading cripto (paper trading) — v6

Simulación ficticia pedida por Nathanael el 2026-11-17: leer las top 100 criptos, abrir operaciones ficticias de trading spot y futuros, registrar entradas/salidas/ganancias y medir el porcentaje de aciertos. No es dinero real ni consejo financiero.

## Capital y límites
- Capital ficticio inicial: $10,000 USD.
- Sin máximo de posiciones abiertas y sin máximo de entradas por corrida (regla quitada por Nathanael el 2026-11-17): se abre una operación por cada moneda que cumpla el análisis, siempre que haya saldo disponible suficiente para el margen/tamaño de esa posición.
- Saldo disponible = equity total (capital inicial + P&L realizado + P&L no realizado) menos el margen comprometido en posiciones abiertas (spot: tamaño completo; futuros: solo el margen, no el nocional). Si el margen necesario supera el saldo disponible, esa operación se salta.
- Tamaño spot (long): 10% del capital actual por posición, sin apalancamiento (1x).
- Tamaño futuros (long y short): margen del 5% del capital actual, apalancamiento 3x. Nocional = margen × 3. El P&L se calcula sobre el margen, amplificado por el apalancamiento.

## Universo
- Top 100 por capitalización de mercado (CoinGecko, endpoint /coins/markets, orden market_cap_desc).
- Se excluyen stablecoins: precio entre 0.99 y 1.01 con cambio 7d menor a 2%.

## Reglas de entrada LONG SPOT (sin apalancamiento)
Una moneda entra en long si cumple TODO:
- Cambio 7d > +2%
- Cambio 30d > 0%
- Cambio 24h entre 0% y +25% (evita perseguir pumps extremos)
- Tendencia de la gráfica (sparkline 7d, datos por hora): promedio de las últimas 24 horas mayor que el promedio de las 24 anteriores
- RSI(14) sobre la sparkline < 75 (no sobrecomprada)
- No tener ya una posición abierta en esa moneda

## Reglas de entrada LONG FUTUROS (apalancado 3x)
Una moneda entra en long apalancado si cumple TODO:
- Cambio 7d > +5%
- Cambio 30d > 0%
- Cambio 24h entre 0% y +25%
- Tendencia de la gráfica (sparkline 7d): promedio de las últimas 24 horas mayor que el promedio de las 24 anteriores
- RSI(14) sobre la sparkline entre 50 y 75 (alcista pero no sobrecomprada extrema)
- No tener ya una posición abierta en esa moneda

## Reglas de entrada SHORT FUTUROS (apalancado 3x)
Una moneda entra en short si cumple TODO:
- Cambio 7d < -5%
- Cambio 30d < 0%
- Cambio 24h entre -25% y 0% (evita perseguir caídas extremas)
- Tendencia de la gráfica (sparkline 7d): promedio de las últimas 24 horas menor que el promedio de las 24 anteriores
- RSI(14) sobre la sparkline entre 25 y 50 (bajista pero no sobrevendida extrema)
- No tener ya una posición abierta en esa moneda

Si hay varias candidatas, se ordenan por magnitud del movimiento (longs por 7d descendente, shorts por 7d ascendente, es decir, las más negativas primero) y se abren todas las que cumplan el análisis y tengan saldo disponible, sin límite por corrida. Prioridad por corrida: primero longs spot, luego longs futuros, luego shorts futuros.

## Reglas de salida (se revisan en cada corrida al precio actual)
### Long spot
- Stop-loss: precio ≤ entrada × 0.94 (−6%) → cerrar.
- Take-profit: precio ≥ entrada × 1.10 (+10%) → cerrar.
- Corte de tendencia: cambio 7d < 0% Y cambio 24h < 0% → cerrar al precio actual.
- P&L = tamaño × (salida − entrada) / entrada.

### Long futuros 3x
- Stop-loss: precio ≤ entrada × 0.96 (−4% en contra; equivale a −12% sobre el margen con 3x) → cerrar.
- Take-profit: precio ≥ entrada × 1.03 (+3% a favor; equivale a +9% sobre el margen con 3x) → cerrar. (Regla cambiada por Nathanael el 2026-11-17: salir rápido con ganancia pequeña sobre el margen para liberar saldo y buscar dinero rápido.)
- Liquidación simulada: precio ≤ entrada × 0.70 (−30% en contra) → cerrar con pérdida del 100% del margen.
- Corte de tendencia: cambio 7d < 0% Y cambio 24h < 0% → cerrar al precio actual.
- P&L = margen × 3 × (salida − entrada) / entrada.

### Short futuros 3x
- Stop-loss: precio ≥ entrada × 1.04 (+4% en contra; equivale a −12% sobre el margen con 3x) → cerrar.
- Take-profit: precio ≤ entrada × 0.97 (−3% a favor; equivale a +9% sobre el margen con 3x) → cerrar. (Regla cambiada por Nathanael el 2026-11-17: salir rápido con ganancia pequeña sobre el margen para liberar saldo y buscar dinero rápido.)
- Liquidación simulada: precio ≥ entrada × 1.30 (+30% en contra) → cerrar con pérdida del 100% del margen. (En la práctica el stop salta antes; la liquidación es respaldo.)
- Corte de tendencia: cambio 7d > 0% Y cambio 24h > 0% → cerrar al precio actual.
- P&L = margen × 3 × (entrada − salida) / entrada.

## Enfriamiento tras take-profit (v6, aplicado 2026-11-18 por Nathanael)
- Cuando una posición cierra por take-profit (futuros ±3% en precio o spot +10%), esa moneda queda bloqueada para reabrir durante **2 horas**, en cualquier lado o mercado. El bloqueo se guarda en `state.json` (`cooldowns: {coin_id: fecha-hora límite}`) y se respeta en la misma corrida del cierre y en las siguientes hasta que vence.
- Los cierres por stop-loss o por corte de tendencia **no** activan el enfriamiento: solo el take-profit, para no perseguir el movimiento ya cobrado.
- Motivo: Bitway perdió -$131.38 reabriendo dos veces seguidas tras el objetivo; PEARL y Pieverse sí siguieron subiendo tras reabrir, así que este cambio puede recortar algunas ganancias de continuación a cambio de evitar recompras en el pico.

## Frecuencia
- Revisión cada 30 minutos mediante script ligero (fetch_and_run.py) que descarga la foto del mercado y las sparklines por API directa, sin pasar por el navegador. Cada corrida hace 2 llamadas a CoinGecko (mercado + sparklines), muy por debajo del límite público.
- Limitación conocida: entre corridas el precio puede tocar stop/target y revertir; se detecta al precio de la siguiente corrida. Es una simulación, no ejecución en tiempo real ni en un exchange.

## Registro
- `ledger.csv`: una fila por operación cerrada (entrada, salida, lado, mercado, apalancamiento, margen, nocional, precio de entrada/salida, P&L en $ y % sobre margen, movimiento del precio %, duración en minutos, resultado, motivo de salida).
- `state.json`: posiciones abiertas, P&L realizado, estadísticas (operaciones cerradas, ganadas, perdidas, % de aciertos).
- `equity.csv`: foto del equity en cada corrida (timestamp, equity realizado, P&L no realizado estimado, equity total, posiciones abiertas).
- `data/markets_latest.json`: última foto de las top 100 (id, símbolo, precio, rango, cambios 24h/7d/30d).
- `data/sparks_latest.json`: sparkline de 7 días de las candidatas y de las posiciones abiertas.
- `analyze.py`: motor que aplica las reglas y actualiza estado y registro.
- `fetch_and_run.py`: descarga datos frescos y ejecuta el motor.
- `dashboard.html`: interfaz generada desde el estado actual (saldo inicial, saldo actual, % ganancia, P&L, posiciones abiertas y cerradas con precios de entrada/salida y duración).

## Reporte
- Sin avisos por cada apertura o cierre (regla quitada por Nathanael el 2026-11-17): las corridas de 30 minutos terminan en silencio.
- Cada 6 horas se le reenvía el acceso a la interfaz con un resumen de progreso (saldo inicial, saldo actual, % ganancia, P&L realizado y no realizado, operaciones cerradas y % de aciertos) para que pueda analizar el avance.
- Nathanael puede pedir el estado completo en cualquier momento.
