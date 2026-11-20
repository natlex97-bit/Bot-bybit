#!/usr/bin/env python3
"""Bot de trading conectado a la CUENTA DEMO de Bybit (fondos simulados).

Misma estrategia v6 que la simulación ficticia (analyze.py): señales desde
CoinGecko top-100, spot long 1x, futuros long/short 3x, TP/SL y enfriamiento
de 2h tras take-profit. La diferencia es que las órdenes se EJECUTAN en
Bybit Demo y cada futuro lleva su take-profit y stop-loss puestos EN EL
EXCHANGE, así que el cierre ocurre al instante aunque el bot revise cada
5 minutos.

Flujo por corrida:
  1. Descarga foto de CoinGecko (top 100 + sparklines).
  2. Concilia con Bybit: posiciones que el exchange ya cerró por TP/SL se
     registran con su precio y PnL reales (si no es DRY_RUN).
  3. Ejecuta analyze.py (decide aperturas/cierres con las reglas v6).
  4. Ejecuta en Bybit lo que analyze decidió: abre con TP/SL en el
     exchange, cierra por mercado lo que analyze cerró y Bybit aún tiene.
  5. Regenera dashboard.html.

Variables de entorno:
  BYBIT_API_KEY, BYBIT_API_SECRET : clave DEMO creada dentro de Demo Trading
  DRY_RUN    : "true" (por defecto) = solo simula, no envía órdenes a Bybit
  CAPITAL_BASE : base de capital para el tamaño de las posiciones (def. 10000)

La clave NUNCA va en el código: en GitHub va en Secrets.
"""
import csv
import json
import os
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
MARKETS = os.path.join(DATA, "markets_latest.json")
SPARKS = os.path.join(DATA, "sparks_latest.json")
STATE = os.path.join(BASE, "state.json")
LEDGER = os.path.join(BASE, "ledger.csv")
EQUITY = os.path.join(BASE, "equity.csv")

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("1", "true", "yes")
CAPITAL_BASE = float(os.environ.get("CAPITAL_BASE", "10000"))

LEDGER_HEADER = ["opened_at", "closed_at", "coin_id", "symbol", "side", "market",
                 "leverage", "margin_usd", "notional_usd", "entry_price",
                 "exit_price", "pnl_usd", "pnl_pct_on_margin", "price_move_pct",
                 "duration_min", "result", "exit_reason"]

sys.path.insert(0, BASE)
import fetch_and_run as fr  # noqa: E402  (reutiliza descarga CoinGecko y dashboard)
from bybit_client import BybitDemoClient, BybitDemoError, round_down_to_step, format_qty  # noqa: E402


def log(*a):
    print("[trade_demo]", *a, flush=True)


def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def parse_time(s):
    try:
        if s.endswith(" UTC"):
            return datetime.strptime(s, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def bybit_symbol(coin_symbol):
    return coin_symbol.upper() + "USDT"


# ---------------- descarga CoinGecko (igual que la simulación) ----------------
def fetch_snapshot():
    url = ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
           "&order=market_cap_desc&per_page=100&page=1&price_change_percentage=7d,30d")
    raw = fr.get_json(url)
    markets = []
    for c in raw:
        markets.append({
            "id": c.get("id"), "symbol": c.get("symbol"), "price": c.get("current_price"),
            "rank": c.get("market_cap_rank"),
            "pct24": c.get("price_change_percentage_24h"),
            "pct7": c.get("price_change_percentage_7d_in_currency"),
            "pct30": c.get("price_change_percentage_30d_in_currency"),
        })
    os.makedirs(DATA, exist_ok=True)
    json.dump(markets, open(MARKETS, "w"), indent=2, ensure_ascii=False)

    state = json.load(open(STATE))
    held = {p["id"] for p in state.get("open_positions", [])}
    watch = set(held)
    for c in markets:
        if fr.is_stablecoin(c):
            continue
        pct7, pct30, pct24 = c.get("pct7"), c.get("pct30"), c.get("pct24")
        if pct7 is None or pct30 is None or pct24 is None:
            continue
        if (pct7 > 2 and pct30 > 0 and 0 < pct24 < 25) or (pct7 < -5 and pct30 < 0 and -25 < pct24 < 0):
            watch.add(c["id"])
    watch = sorted(watch)[:60]
    sparks = {}
    if watch:
        ids = ",".join(watch)
        url2 = (f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
                f"&ids={urllib.parse.quote(ids)}&sparkline=true")
        raw2 = fr.get_json(url2)
        for c in raw2:
            sp = (c.get("sparkline_in_7d") or {}).get("price") or []
            sparks[c["id"]] = sp
    json.dump(sparks, open(SPARKS, "w"), indent=2, ensure_ascii=False)
    return markets


# ---------------- conciliación con Bybit ----------------
def record_close(state, pos, exit_price, pnl, reason, now_dt):
    """Registra un cierre en state/ledger igual que lo hace analyze.py."""
    now = now_dt.strftime("%Y-%m-%d %H:%M UTC")
    entry = pos["entry_price"]
    price_move_pct = (exit_price - entry) / entry * 100
    pnl_pct_margin = (pnl / pos["margin_usd"] * 100) if pos["margin_usd"] else 0
    t0 = parse_time(pos.get("entry_time_iso") or pos.get("entry_time") or "")
    duration_min = round((now_dt - t0).total_seconds() / 60, 1) if t0 else None
    if reason.startswith("take-profit"):
        state.setdefault("cooldowns", {})[pos["id"]] = (
            now_dt + timedelta(hours=2)).isoformat()
    state["realized_pnl"] += pnl
    state["stats"]["closed"] += 1
    state["stats"]["wins" if pnl > 0 else "losses"] += 1
    if not os.path.exists(LEDGER) or os.path.getsize(LEDGER) == 0:
        with open(LEDGER, "w", newline="") as f:
            csv.writer(f).writerow(LEDGER_HEADER)
    with open(LEDGER, "a", newline="") as f:
        csv.writer(f).writerow([
            pos.get("entry_time"), now, pos["id"], pos["symbol"],
            pos.get("side", "long"), pos.get("market", "spot"),
            pos.get("leverage", 1), pos.get("margin_usd", pos.get("size_usd", 0)),
            pos.get("notional_usd", pos.get("size_usd", 0)), entry,
            round(exit_price, 8), round(pnl, 2), round(pnl_pct_margin, 2),
            round(price_move_pct, 2), duration_min,
            "win" if pnl > 0 else "loss", reason])
    return {"id": pos["id"], "symbol": pos["symbol"], "exit_price": exit_price,
            "pnl_usd": round(pnl, 2), "reason": reason}


def infer_close_reason(pos, exit_price, pnl):
    side = pos.get("side", "long")
    target, stop = pos.get("target"), pos.get("stop")
    if side == "long":
        if target and exit_price >= target * 0.999:
            return "take-profit +10%" if pos.get("market") == "spot" else "take-profit +3% a favor = +9% margen (futuros 3x)"
        if stop and exit_price <= stop * 1.001:
            return "stop-loss -6%" if pos.get("market") == "spot" else "stop-loss -4% en contra (futuros 3x)"
        if pnl <= -pos["margin_usd"] * 0.99:
            return "liquidación simulada -30% en contra"
        return "corte de tendencia (7d y 24h negativas)"
    else:
        if target and exit_price <= target * 1.001:
            return "take-profit -3% a favor = +9% margen (futuros 3x)"
        if stop and exit_price >= stop * 0.999:
            return "stop-loss +4% en contra (futuros 3x)"
        if pnl <= -pos["margin_usd"] * 0.99:
            return "liquidación simulada +30% en contra"
        return "corte de tendencia (7d y 24h positivas)"


def sync_with_bybit(client, state, now_dt):
    """Detecta posiciones que Bybit ya cerró (TP/SL del exchange) y las
    registra con precio/PnL reales antes de que analyze.py las vuelva a ver."""
    synced = []
    try:
        bybit_pos = client.get_linear_positions()
    except BybitDemoError as e:
        log("AVISO: no se pudo leer posiciones de Bybit, se sigue sin conciliar:", e)
        return synced
    remaining = []
    for pos in state.get("open_positions", []):
        if pos.get("market") != "futures":
            remaining.append(pos)
            continue
        sym = bybit_symbol(pos["symbol"])
        bp = bybit_pos.get(sym)
        if bp is not None:
            remaining.append(pos)
            continue
        # Ya no está en Bybit: la cerró el TP/SL del exchange (o liquidación).
        entry_dt = parse_time(pos.get("entry_time_iso") or pos.get("entry_time") or "")
        entry_ms = int(entry_dt.timestamp() * 1000) if entry_dt else 0
        exit_price, pnl, found = None, None, False
        try:
            for rec in client.get_closed_pnl(sym, limit=10):
                try:
                    ct = int(rec.get("createdTime", 0))
                except Exception:
                    ct = 0
                if ct >= entry_ms:
                    exit_price = float(rec["avgExitPrice"])
                    pnl = float(rec["closedPnl"])
                    found = True
                    break
        except BybitDemoError as e:
            log("AVISO: no se pudo leer PnL cerrado de", sym, ":", e)
        if not found:
            log("AVISO: posición", sym, "ya no está en Bybit y no se encontró su cierre; se marca al último precio conocido")
            continue
        reason = infer_close_reason(pos, exit_price, pnl)
        rec = record_close(state, pos, exit_price, pnl, reason, now_dt)
        rec["bybit_closed"] = True
        synced.append(rec)
        log(f"Conciliado cierre en Bybit: {pos['symbol'].upper()} {reason} PnL ${pnl:+.2f} @ ${exit_price}")
    state["open_positions"] = remaining
    # Posiciones en Bybit que el bot no conoce: avisar, no tocarlas.
    known = {bybit_symbol(p["symbol"]) for p in state.get("open_positions", [])
             if p.get("market") == "futures"}
    for sym in bybit_pos:
        if sym not in known:
            log("AVISO: Bybit tiene una posición que el bot no conoce:", sym,
                "revísala a mano en Demo Trading.")
    return synced


# ---------------- ejecución de aperturas ----------------
def execute_open(client, pos, state):
    """Abre en Bybit lo que analyze.py decidió. Devuelve True si quedó abierta."""
    sym = bybit_symbol(pos["symbol"])
    market = pos.get("market", "spot")
    entry = pos["entry_price"]
    if DRY_RUN:
        if market == "futures":
            log(f"[DRY-RUN] abriría {sym} {pos['side']} {pos['leverage']}x margen ${pos['margin_usd']} "
                f"@ ~${entry} TP ${pos.get('target')} SL ${pos.get('stop')}")
        else:
            log(f"[DRY-RUN] compraría spot {sym} margen ${pos['margin_usd']} @ ~${entry}")
        return True
    try:
        if market == "futures":
            info = client.get_instruments_info("linear", sym)
            if not info:
                log(f"Sin par de futuros en Bybit para {sym}: se omite la apertura")
                return False
            lot = info.get("lotSizeFilter", {})
            qty_step = float(lot.get("qtyStep", 0) or 0)
            min_qty = float(lot.get("minOrderQty", 0) or 0)
            min_amt = float(lot.get("minOrderAmt", 0) or 0)
            qty = round_down_to_step(pos["notional_usd"] / entry, qty_step)
            if qty <= 0 or qty < min_qty or qty * entry < min_amt:
                log(f"Orden muy pequeña para {sym} (qty {qty}, mín {min_qty} / ${min_amt}): se omite")
                return False
            try:
                client.set_leverage(sym, pos.get("leverage", 3))
            except BybitDemoError as e:
                log("AVISO apalancamiento", sym, ":", e)
            try:
                client.set_isolated_margin(sym)
            except BybitDemoError as e:
                log("AVISO margen aislado", sym, ":", e)
            side = "Buy" if pos["side"] == "long" else "Sell"
            res = client.place_linear_order(sym, side, format_qty(qty, qty_step),
                                            take_profit=pos.get("target"),
                                            stop_loss=pos.get("stop"))
            pos["bybit_symbol"] = sym
            pos["bybit_qty"] = qty
            pos["bybit_order_id"] = (res.get("orderId") if isinstance(res, dict) else None)
            pos["executed_on"] = "bybit-demo"
            log(f"Abierta en Bybit Demo: {sym} {side} {qty} @ ~${entry} "
                f"TP ${pos.get('target')} SL ${pos.get('stop')} (orden {pos['bybit_order_id']})")
            return True
        else:  # spot
            info = client.get_instruments_info("spot", sym)
            if not info:
                log(f"Sin par spot en Bybit para {sym}: se omite la apertura")
                return False
            lot = info.get("lotSizeFilter", {})
            base_prec = int(lot.get("basePrecision", 0) or 0)
            min_qty = float(lot.get("minOrderQty", 0) or 0)
            min_amt = float(lot.get("minOrderAmt", 0) or 0)
            qty = pos["margin_usd"] / entry
            qty = int(qty * (10 ** base_prec)) / (10 ** base_prec)
            if qty <= 0 or qty < min_qty or qty * entry < min_amt:
                log(f"Compra spot muy pequeña para {sym}: se omite")
                return False
            res = client.place_spot_order(sym, "Buy", f"{qty:.{base_prec}f}")
            pos["bybit_symbol"] = sym
            pos["bybit_qty"] = qty
            pos["bybit_order_id"] = (res.get("orderId") if isinstance(res, dict) else None)
            pos["executed_on"] = "bybit-demo"
            log(f"Comprado spot en Bybit Demo: {sym} {qty} @ ~${entry} (orden {pos['bybit_order_id']})")
            return True
    except BybitDemoError as e:
        log(f"ERROR al abrir {sym} en Bybit:", e)
        return False


# ---------------- ejecución de cierres ----------------
def correct_ledger_with_actual(state, pos, exit_price, pnl, now_dt):
    """Corrige la fila que analyze.py escribió con el precio de CoinGecko por
    el precio/PnL reales de Bybit, ajustando realized_pnl."""
    if not os.path.exists(LEDGER):
        return
    rows = list(csv.DictReader(open(LEDGER)))
    for r in reversed(rows):
        if r.get("coin_id") == pos["id"] and r.get("opened_at") == pos.get("entry_time"):
            try:
                old_pnl = float(r.get("pnl_usd") or 0)
            except Exception:
                old_pnl = 0.0
            state["realized_pnl"] += (pnl - old_pnl)
            r["exit_price"] = str(round(exit_price, 8))
            r["pnl_usd"] = str(round(pnl, 2))
            r["pnl_pct_on_margin"] = str(round(pnl / pos["margin_usd"] * 100, 2)) if pos["margin_usd"] else r["pnl_pct_on_margin"]
            r["price_move_pct"] = str(round((exit_price - pos["entry_price"]) / pos["entry_price"] * 100, 2))
            r["result"] = "win" if pnl > 0 else "loss"
            with open(LEDGER, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=LEDGER_HEADER)
                w.writeheader()
                w.writerows(rows)
            log(f"Ledger corregido con precio real de Bybit: {pos['symbol'].upper()} salida ${exit_price} PnL ${pnl:+.2f}")
            return True
    return False


def execute_close(client, pos, state, bybit_pos, now_dt):
    """Cierra en Bybit lo que analyze.py cerró y el exchange aún tiene abierto."""
    sym = bybit_symbol(pos["symbol"])
    market = pos.get("market", "spot")
    if DRY_RUN:
        log(f"[DRY-RUN] cerraría {sym} ({pos.get('reason', '')})")
        return
    try:
        if market == "futures":
            bp = bybit_pos.get(sym)
            if bp is None:
                # El exchange ya la cerró entre la conciliación y analyze: buscar su PnL real.
                entry_dt = parse_time(pos.get("entry_time_iso") or pos.get("entry_time") or "")
                entry_ms = int(entry_dt.timestamp() * 1000) if entry_dt else 0
                for rec in client.get_closed_pnl(sym, limit=5):
                    if int(rec.get("createdTime", 0)) >= entry_ms:
                        correct_ledger_with_actual(state, pos, float(rec["avgExitPrice"]),
                                                   float(rec["closedPnl"]), now_dt)
                        return
                return
            size = float(bp.get("size", 0))
            side = bp.get("side", "")
            close_side = "Buy" if side == "Sell" else "Sell"
            try:
                client.cancel_all_orders(sym, "linear")
            except BybitDemoError as e:
                log("AVISO cancelar órdenes", sym, ":", e)
            res = client.close_linear_position(sym, close_side, str(size))
            order_id = res.get("orderId") if isinstance(res, dict) else None
            log(f"Cerrada en Bybit Demo: {sym} {close_side} {size} (orden {order_id})")
            if order_id:
                for ex in client.get_executions(order_id, sym):
                    try:
                        fill = float(ex.get("execPrice"))
                        qty = float(ex.get("execQty", 0))
                    except Exception:
                        continue
                    if qty > 0:
                        lev = pos.get("leverage", 3)
                        pnl = pos["margin_usd"] * lev * ((fill - pos["entry_price"]) / pos["entry_price"]
                                                         if pos["side"] == "long"
                                                         else (pos["entry_price"] - fill) / pos["entry_price"])
                        correct_ledger_with_actual(state, pos, fill, pnl, now_dt)
                        break
        else:  # spot
            qty = pos.get("bybit_qty")
            if not qty:
                return
            bal = client.get_coin_balance(pos["symbol"].upper())
            if bal < qty * 0.5:
                log(f"Spot {sym}: saldo {bal} menor que lo comprado ({qty}), ya no está; no se vende")
                return
            info = client.get_instruments_info("spot", sym)
            base_prec = int((info or {}).get("lotSizeFilter", {}).get("basePrecision", 0) or 0)
            sell_qty = min(bal, qty)
            sell_qty = int(sell_qty * (10 ** base_prec)) / (10 ** base_prec)
            res = client.place_spot_order(sym, "Sell", f"{sell_qty:.{base_prec}f}")
            log(f"Vendido spot en Bybit Demo: {sym} {sell_qty} (orden {(res or {}).get('orderId') if isinstance(res, dict) else None})")
    except BybitDemoError as e:
        log(f"ERROR al cerrar {sym} en Bybit:", e)


def main():
    now_dt = datetime.now(timezone.utc)
    api_key = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")
    client = None
    if DRY_RUN:
        log("Modo DRY_RUN: no se enviará ninguna orden a Bybit")
        client = BybitDemoClient(api_key or "dry", api_secret or "dry", dry_run=True)
    else:
        if not api_key or not api_secret:
            print("ERROR: faltan BYBIT_API_KEY / BYBIT_API_SECRET. Ponlas en GitHub Secrets.")
            sys.exit(2)
        client = BybitDemoClient(api_key, api_secret, dry_run=False)

    # 0. capital base (mismo que la simulación: $10,000)
    state = json.load(open(STATE))
    if state.get("capital_start") != CAPITAL_BASE:
        log(f"capital_start en state.json es {state.get('capital_start')}, se usa {CAPITAL_BASE} para el tamaño")
    state["capital_start"] = CAPITAL_BASE
    json.dump(state, open(STATE, "w"), indent=2, ensure_ascii=False)

    # 1. foto CoinGecko
    fetch_snapshot()

    # 2. conciliación con Bybit (solo con claves reales)
    synced = []
    if not DRY_RUN:
        state = json.load(open(STATE))
        synced = sync_with_bybit(client, state, now_dt)
        json.dump(state, open(STATE, "w"), indent=2, ensure_ascii=False)
    else:
        log("DRY_RUN: se omite la conciliación con Bybit")

    # 3. motor de decisiones (reglas v6, igual que la simulación)
    out = subprocess.run([sys.executable, os.path.join(BASE, "analyze.py")],
                         capture_output=True, text=True, timeout=90)
    if out.returncode != 0:
        print("ERROR motor:", out.stderr[-2000:])
        sys.exit(1)
    try:
        result = json.loads(out.stdout)
    except Exception:
        print("ERROR: analyze.py no devolvió JSON:", out.stdout[-2000:])
        sys.exit(1)
    opened = result.get("opened", [])
    closed = result.get("closed", [])
    log(f"Motor: {len(opened)} aperturas, {len(closed)} cierres")

    # 4. ejecutar aperturas en Bybit
    state = json.load(open(STATE))
    executed_opens, failed_opens = [], []
    for pos in opened:
        ok = execute_open(client, pos, state)
        if ok:
            executed_opens.append(pos["id"])
            # guardar campos de Bybit en la posición del state
            for sp in state["open_positions"]:
                if sp["id"] == pos["id"]:
                    sp.update({k: v for k, v in pos.items() if k.startswith("bybit_") or k == "executed_on"})
                    break
        else:
            failed_opens.append(pos["id"])
            state["open_positions"] = [p for p in state["open_positions"] if p["id"] != pos["id"]]
            log(f"Se revierte la apertura de {pos['symbol'].upper()} en el state (no se ejecutó en Bybit)")
    json.dump(state, open(STATE, "w"), indent=2, ensure_ascii=False)

    # 5. ejecutar cierres en Bybit
    bybit_pos = {}
    if not DRY_RUN:
        try:
            bybit_pos = client.get_linear_positions()
        except BybitDemoError as e:
            log("AVISO: no se pudieron leer posiciones para los cierres:", e)
    executed_closes = []
    for pos in closed:
        execute_close(client, pos, state, bybit_pos, now_dt)
        executed_closes.append(pos["id"])
    json.dump(state, open(STATE, "w"), indent=2, ensure_ascii=False)

    # 6. dashboard
    fr.build_dashboard()
    log("dashboard actualizado")

    print(json.dumps({
        "dry_run": DRY_RUN,
        "synced_from_bybit": synced,
        "opened_decided": [p["id"] for p in opened],
        "opened_executed": executed_opens,
        "opened_failed": failed_opens,
        "closed_decided": [p["id"] for p in closed],
        "closed_executed": executed_closes,
        "equity_total": result.get("equity_total"),
        "realized_pnl": round(state["realized_pnl"], 2),
        "open_positions": len(state["open_positions"]),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
