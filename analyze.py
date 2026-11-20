#!/usr/bin/env python3
"""Motor v2 de la simulación de trading cripto.
Spot long sin apalancamiento + futuros short 3x.
Aplica las reglas de strategy.md y actualiza state.json, ledger.csv y equity.csv."""
import json, csv, os
from datetime import datetime, timezone, timedelta

COOLDOWN_AFTER_TP_HOURS = 2  # v6: no reabrir una moneda justo después de un take-profit

BASE = os.path.dirname(os.path.abspath(__file__))
MARKETS = os.path.join(BASE, "data", "markets_latest.json")
SPARKS = os.path.join(BASE, "data", "sparks_latest.json")
STATE = os.path.join(BASE, "state.json")
LEDGER = os.path.join(BASE, "ledger.csv")
EQUITY = os.path.join(BASE, "equity.csv")

LEDGER_HEADER = ["opened_at","closed_at","coin_id","symbol","side","market","leverage",
                 "margin_usd","notional_usd","entry_price","exit_price","pnl_usd",
                 "pnl_pct_on_margin","price_move_pct","duration_min","result","exit_reason"]

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)

def trend_up(spark):
    if len(spark) < 48:
        return None
    recent = sum(spark[-24:]) / 24
    prior = sum(spark[-48:-24]) / 24
    return recent > prior

def is_stablecoin(coin):
    p = coin.get("price") or 0
    return 0.99 < p < 1.01 and abs(coin.get("pct7") or 0) < 2

def parse_time(s):
    # formatos: "2026-11-17 06:05 UTC" o ISO
    try:
        if s.endswith(" UTC"):
            return datetime.strptime(s, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def main():
    markets = json.load(open(MARKETS))
    sparks = json.load(open(SPARKS)) if os.path.exists(SPARKS) else {}
    state = json.load(open(STATE))
    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime("%Y-%m-%d %H:%M UTC")
    # v6: enfriamiento tras take-profit — limpiar los ya vencidos
    cooldowns = state.setdefault("cooldowns", {})
    for cid in [k for k, v in cooldowns.items()
                if (parse_time(v) or now_dt) <= now_dt]:
        del cooldowns[cid]
    prices = {c["id"]: c for c in markets}
    opened, closed = [], []

    # --- salidas ---
    remaining = []
    for pos in state["open_positions"]:
        # migración de posiciones viejas (v1) a v2
        pos.setdefault("side", "long")
        pos.setdefault("market", "spot")
        pos.setdefault("leverage", 1)
        pos.setdefault("margin_usd", pos.get("size_usd", 0))
        pos.setdefault("notional_usd", pos.get("size_usd", 0))
        pos.setdefault("entry_time_iso", pos.get("entry_time"))
        coin = prices.get(pos["id"])
        price = coin["price"] if coin else (sparks[pos["id"]][-1] if sparks.get(pos["id"]) else None)
        if price is None:
            remaining.append(pos)
            continue
        entry = pos["entry_price"]
        side = pos["side"]
        reason = None
        pnl = None
        if side == "long":
            lev = pos.get("leverage", 1)
            is_fut = pos.get("market") == "futures" and lev > 1
            if is_fut and price <= entry * 0.70:
                reason = "liquidación simulada -30% en contra"
                pnl = -pos["margin_usd"]
                price = entry * 0.70
            elif is_fut and price <= entry * 0.96:
                reason = "stop-loss -4% en contra (futuros 3x)"
            elif is_fut and price >= entry * 1.03:
                reason = "take-profit +3% a favor = +9% margen (futuros 3x)"
            elif not is_fut and price <= entry * 0.94:
                reason = "stop-loss -6%"
            elif not is_fut and price >= entry * 1.10:
                reason = "take-profit +10%"
            elif coin and (coin.get("pct7") or 0) < 0 and (coin.get("pct24") or 0) < 0:
                reason = "corte de tendencia (7d y 24h negativas)"
            if reason and pnl is None:
                pnl = pos["margin_usd"] * lev * (price - entry) / entry if is_fut else pos["notional_usd"] * (price - entry) / entry
        else:  # short futuros
            lev = pos.get("leverage", 3)
            if price >= entry * 1.30:
                reason = "liquidación simulada +30% en contra"
                pnl = -pos["margin_usd"]
                price = entry * 1.30
            elif price >= entry * 1.04:
                reason = "stop-loss +4% en contra (futuros 3x)"
            elif price <= entry * 0.97:
                reason = "take-profit -3% a favor = +9% margen (futuros 3x)"
            elif coin and (coin.get("pct7") or 0) > 0 and (coin.get("pct24") or 0) > 0:
                reason = "corte de tendencia (7d y 24h positivas)"
            if reason and pnl is None:
                pnl = pos["margin_usd"] * lev * (entry - price) / entry
        if reason:
            # v6: tras un take-profit, la moneda queda en enfriamiento 2 horas
            # (no se puede reabrir en esta corrida ni en las siguientes hasta que venza)
            if reason.startswith("take-profit"):
                cooldowns[pos["id"]] = (now_dt + timedelta(hours=COOLDOWN_AFTER_TP_HOURS)).isoformat()
            price_move_pct = (price - entry) / entry * 100
            pnl_pct_margin = (pnl / pos["margin_usd"] * 100) if pos["margin_usd"] else 0
            t0 = parse_time(pos.get("entry_time_iso") or pos.get("entry_time") or "")
            duration_min = round((now_dt - t0).total_seconds() / 60, 1) if t0 else None
            rec = {**pos, "exit_price": price, "exit_time": now,
                   "pnl_usd": round(pnl, 2), "pnl_pct": round(pnl_pct_margin, 2),
                   "price_move_pct": round(price_move_pct, 2),
                   "duration_min": duration_min,
                   "reason": reason,
                   "result": "win" if pnl > 0 else "loss"}
            closed.append(rec)
            state["realized_pnl"] += pnl
            state["stats"]["closed"] += 1
            state["stats"]["wins" if pnl > 0 else "losses"] += 1
        else:
            remaining.append(pos)
    state["open_positions"] = remaining

    # --- entradas ---
    held = {p["id"] for p in state["open_positions"]}
    capital = state["capital_start"] + state["realized_pnl"]
    # saldo disponible: equity menos margen comprometido (sin máximo de posiciones)
    unreal_pre = 0.0
    for pos in state["open_positions"]:
        coin_pre = prices.get(pos["id"])
        price_pre = coin_pre["price"] if coin_pre else (sparks[pos["id"]][-1] if sparks.get(pos["id"]) else None)
        if price_pre is None:
            continue
        if pos.get("side", "long") == "long":
            unreal_pre += pos["notional_usd"] * (price_pre - pos["entry_price"]) / pos["entry_price"]
        else:
            unreal_pre += pos["margin_usd"] * pos.get("leverage", 3) * (pos["entry_price"] - price_pre) / pos["entry_price"]
    equity_pre = state["capital_start"] + state["realized_pnl"] + unreal_pre
    committed = sum(p.get("margin_usd", p.get("size_usd", 0)) for p in state["open_positions"])
    available = equity_pre - committed
    if True:
        spot_longs, fut_longs, shorts = [], [], []
        for c in markets:
            if c["id"] in held or is_stablecoin(c):
                continue
            # v6: en enfriamiento tras take-profit, no se evalúa para reabrir
            cd = cooldowns.get(c["id"])
            if cd and (parse_time(cd) or now_dt) > now_dt:
                continue
            pct7, pct30, pct24 = c.get("pct7"), c.get("pct30"), c.get("pct24")
            if pct7 is None or pct30 is None or pct24 is None:
                continue
            if pct7 > 2 and pct30 > 0 and 0 < pct24 < 25:
                spot_longs.append(c)
            if pct7 > 5 and pct30 > 0 and 0 < pct24 < 25:
                fut_longs.append(c)
            if pct7 < -5 and pct30 < 0 and -25 < pct24 < 0:
                shorts.append(c)
        spot_longs.sort(key=lambda x: x["pct7"], reverse=True)
        fut_longs.sort(key=lambda x: x["pct7"], reverse=True)
        shorts.sort(key=lambda x: x["pct7"])  # más negativas primero
        # prioridad: longs spot, luego longs futuros, luego shorts futuros
        queue = ([("spot_long", c) for c in spot_longs]
                 + [("fut_long", c) for c in fut_longs]
                 + [("short", c) for c in shorts])
        for kind, c in queue:
            if c["id"] in held:
                continue
            spark = sparks.get(c["id"])
            if not spark:
                continue
            r = rsi(spark)
            t = trend_up(spark)
            if kind == "spot_long":
                side = "long"
                if r is not None and r >= 75:
                    continue
                if t is False:
                    continue
                margin = round(0.10 * capital, 2)
                lev = 1
                stop = round(c["price"] * 0.94, 8)
                target = round(c["price"] * 1.10, 8)
                liq = None
                market = "spot"
            elif kind == "fut_long":
                side = "long"
                if r is not None and not (50 <= r <= 75):
                    continue
                if t is False:
                    continue
                margin = round(0.05 * capital, 2)
                lev = 3
                stop = round(c["price"] * 0.96, 8)
                target = round(c["price"] * 1.03, 8)
                liq = round(c["price"] * 0.70, 8)
                market = "futures"
            else:  # short
                side = "short"
                if r is not None and not (25 <= r <= 50):
                    continue
                if t is True:
                    continue
                margin = round(0.05 * capital, 2)
                lev = 3
                stop = round(c["price"] * 1.04, 8)
                target = round(c["price"] * 0.97, 8)
                liq = round(c["price"] * 1.30, 8)
                market = "futures"
            if margin > available:
                continue
            notional = round(margin * lev, 2)
            pos = {"id": c["id"], "symbol": c["symbol"], "side": side,
                   "market": market,
                   "leverage": lev, "margin_usd": margin, "notional_usd": notional,
                   "size_usd": margin,  # compatibilidad
                   "entry_price": c["price"], "entry_time": now,
                   "entry_time_iso": now_dt.isoformat(),
                   "stop": stop, "target": target, "liquidation_price": liq,
                   "rsi_at_entry": round(r, 1) if r is not None else None,
                   "entry_reason": (f"{side} {market} {lev}x 7d {c['pct7']:+.1f}%, 30d {c['pct30']:+.1f}%, "
                                    f"24h {c['pct24']:+.1f}%, RSI {r:.0f}, "
                                    f"{'tendencia al alza' if side=='long' else 'tendencia a la baja'}")
                   if r is not None else "momentum"}
            state["open_positions"].append(pos)
            opened.append(pos)
            held.add(c["id"])
            available -= margin

    # --- equity snapshot ---
    unreal = 0.0
    for pos in state["open_positions"]:
        coin = prices.get(pos["id"])
        price = coin["price"] if coin else (sparks[pos["id"]][-1] if sparks.get(pos["id"]) else None)
        if price is None:
            continue
        if pos.get("side", "long") == "long":
            unreal += pos["notional_usd"] * (price - pos["entry_price"]) / pos["entry_price"]
        else:
            unreal += pos["margin_usd"] * pos.get("leverage", 3) * (pos["entry_price"] - price) / pos["entry_price"]
    equity_total = state["capital_start"] + state["realized_pnl"] + unreal
    if not os.path.exists(EQUITY):
        with open(EQUITY, "w", newline="") as f:
            csv.writer(f).writerow(["timestamp","equity_total","realized_pnl","unrealized_pnl","open_positions"])
    with open(EQUITY, "a", newline="") as f:
        csv.writer(f).writerow([now, round(equity_total,2), round(state["realized_pnl"],2),
                                round(unreal,2), len(state["open_positions"])])

    # --- registro ledger ---
    if not os.path.exists(LEDGER) or os.path.getsize(LEDGER) == 0:
        with open(LEDGER, "w", newline="") as f:
            csv.writer(f).writerow(LEDGER_HEADER)
    with open(LEDGER, "a", newline="") as f:
        w = csv.writer(f)
        for c in closed:
            w.writerow([c["entry_time"], c["exit_time"], c["id"], c["symbol"], c.get("side","long"),
                        c.get("market","spot"), c.get("leverage",1), c.get("margin_usd", c.get("size_usd",0)),
                        c.get("notional_usd", c.get("size_usd",0)), c["entry_price"], c["exit_price"],
                        c["pnl_usd"], c["pnl_pct"], c.get("price_move_pct",""),
                        c.get("duration_min",""), c["result"], c["reason"]])

    json.dump(state, open(STATE, "w"), indent=2, ensure_ascii=False)
    s = state["stats"]
    wr = (s["wins"] / s["closed"] * 100) if s["closed"] else None
    print(json.dumps({
        "opened": opened, "closed": closed,
        "open_positions": state["open_positions"],
        "realized_pnl": round(state["realized_pnl"], 2),
        "unrealized_pnl": round(unreal, 2),
        "equity_total": round(equity_total, 2),
        "closed_trades": s["closed"], "wins": s["wins"], "losses": s["losses"],
        "win_rate": round(wr, 1) if wr is not None else None,
    }, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
