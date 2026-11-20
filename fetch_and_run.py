#!/usr/bin/env python3
"""Descarga foto fresca de CoinGecko (top100 + sparklines de candidatas/abiertas),
ejecuta el motor y regenera el dashboard HTML."""
import json, os, urllib.request, urllib.parse, subprocess, sys, csv
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
MARKETS = os.path.join(DATA, "markets_latest.json")
SPARKS = os.path.join(DATA, "sparks_latest.json")
STATE = os.path.join(BASE, "state.json")
LEDGER = os.path.join(BASE, "ledger.csv")
DASH = os.path.join(BASE, "dashboard.html")

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

def is_stablecoin(c):
    p = c.get("price") or 0
    return 0.99 < p < 1.01 and abs(c.get("pct7") or 0) < 2

def main():
    # 1. top 100
    url = ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
           "&order=market_cap_desc&per_page=100&page=1&price_change_percentage=7d,30d")
    raw = get_json(url)
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

    # 2. ids a vigilar
    state = json.load(open(STATE))
    held = {p["id"] for p in state.get("open_positions", [])}
    watch = set(held)
    for c in markets:
        if is_stablecoin(c):
            continue
        pct7, pct30, pct24 = c.get("pct7"), c.get("pct30"), c.get("pct24")
        if pct7 is None or pct30 is None or pct24 is None:
            continue
        if (pct7 > 2 and pct30 > 0 and 0 < pct24 < 25) or (pct7 < -5 and pct30 < 0 and -25 < pct24 < 0):
            watch.add(c["id"])
    watch = sorted(watch)[:60]  # límite razonable por corrida (sin máximo de posiciones)
    sparks = {}
    if watch:
        ids = ",".join(watch)
        url2 = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={urllib.parse.quote(ids)}&sparkline=true"
        raw2 = get_json(url2)
        for c in raw2:
            sp = (c.get("sparkline_in_7d") or {}).get("price") or []
            sparks[c["id"]] = sp
    json.dump(sparks, open(SPARKS, "w"), indent=2, ensure_ascii=False)

    # 3. motor
    out = subprocess.run([sys.executable, os.path.join(BASE, "analyze.py")],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        print("ERROR motor:", out.stderr[-2000:])
        sys.exit(1)
    print(out.stdout)

    # 4. dashboard
    build_dashboard()
    print("dashboard actualizado:", DASH)

def build_dashboard():
    state = json.load(open(STATE))
    markets = json.load(open(MARKETS)) if os.path.exists(MARKETS) else []
    prices = {c["id"]: c["price"] for c in markets if c.get("price")}
    # equity
    unreal = 0.0
    rows_open = ""
    for p in state.get("open_positions", []):
        price = prices.get(p["id"])
        u = 0.0
        if price:
            if p.get("side","long") == "long":
                u = p["notional_usd"] * (price - p["entry_price"]) / p["entry_price"]
            else:
                u = p["margin_usd"] * p.get("leverage",3) * (p["entry_price"] - price) / p["entry_price"]
            unreal += u
        if p.get("side","long") == "long":
            side_label = "LONG futuros 3x" if p.get("market")=="futures" else "LONG spot"
        else:
            side_label = f"SHORT futuros {p.get('leverage',3)}x"
        rows_open += (f"<tr data-id=\"{p['id']}\"><td>{p['symbol'].upper()}</td><td>{side_label}</td>"
                      f"<td>${p['entry_price']:.6g}</td><td class=\"price-cell\">${price:.6g}</td>" if price else
                      f"<tr data-id=\"{p['id']}\"><td>{p['symbol'].upper()}</td><td>{side_label}</td><td>${p['entry_price']:.6g}</td><td class=\"price-cell\">—</td>")
        rows_open += f"<td>${p['margin_usd']:,.2f}</td><td class=\"pnl-cell\">${u:+,.2f}</td><td>{p['entry_time']}</td></tr>\n"
    if not rows_open:
        rows_open = "<tr><td colspan='7'>Sin posiciones abiertas</td></tr>"
    # ledger
    rows_closed = ""
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            r = list(csv.DictReader(f))
            for row in reversed(r):
                if not row.get("coin_id"):
                    continue
                rows_closed += (f"<tr><td>{row['symbol'].upper()} {row['side']}/{row['market']}</td>"
                                f"<td>${row['entry_price']}</td><td>${row['exit_price']}</td>"
                                f"<td>${row['pnl_usd']}</td><td>{row['pnl_pct_on_margin']}%</td>"
                                f"<td>{row.get('duration_min','')} min</td><td>{row['exit_reason']}</td>"
                                f"<td>{row['opened_at']} → {row['closed_at']}</td></tr>\n")
    if not rows_closed:
        rows_closed = "<tr><td colspan='8'>Aún no hay operaciones cerradas</td></tr>"
    cap0 = state["capital_start"]
    realized = state["realized_pnl"]
    equity = cap0 + realized + unreal
    gain_pct = (equity - cap0) / cap0 * 100
    s = state["stats"]
    wr = f"{s['wins']/s['closed']*100:.1f}%" if s["closed"] else "—"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    positions_json = json.dumps([{"id":p["id"],"symbol":p["symbol"],"side":p.get("side","long"),"market":p.get("market","spot"),"leverage":p.get("leverage",1),"margin_usd":p.get("margin_usd",0),"notional_usd":p.get("notional_usd",0),"entry_price":p["entry_price"]} for p in state.get("open_positions",[])])
    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>Bot de Trading — Bybit Demo</title>
<style>body{{font-family:system-ui,Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}}
h1{{font-size:1.4rem}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.card{{background:#1e293b;border-radius:12px;padding:14px}} .card b{{font-size:1.3rem;display:block;margin-top:4px}}
table{{width:100%;border-collapse:collapse;margin-top:12px;font-size:.85rem}} th,td{{border-bottom:1px solid #334155;padding:6px;text-align:left}}
.pos{{color:#4ade80}} .neg{{color:#f87171}} small{{color:#94a3b8}}</style></head><body>
<h1>📈 Bot de Trading — Bybit Demo <small>(cuenta demo, sin dinero real)</small></h1>
<p><small>Foto base: {now} · Precios en vivo actualizándose cada 30 s · La página se recarga sola cada 60 segundos para mostrar aperturas y cierres nuevos · El bot revisa cada 5 minutos · Cada futuro lleva su take-profit y stop-loss puestos en Bybit, así que los cierres ocurren al instante aunque el bot revise cada 5 minutos</small></p>
<p><small id="live-status">Conectando precios en vivo…</small></p>
<div class="cards">
<div class="card">Saldo inicial<b>${cap0:,.2f}</b></div>
<div class="card">Saldo actual<b id="equity-val">${equity:,.2f}</b></div>
<div class="card">Ganancia %<b id="gain-val" class="{'pos' if gain_pct>=0 else 'neg'}">{gain_pct:+.2f}%</b></div>
<div class="card">P&amp;L realizado<b class="{'pos' if realized>=0 else 'neg'}">${realized:+,.2f}</b></div>
<div class="card">P&amp;L no realizado<b id="unreal-val" class="{'pos' if unreal>=0 else 'neg'}">${unreal:+,.2f}</b></div>
<div class="card">Operaciones cerradas<b>{s['closed']}</b></div>
<div class="card">Aciertos<b>{wr}</b> <small>({s['wins']}G / {s['losses']}P)</small></div>
</div>
<h2>Posiciones abiertas</h2>
<table><tr><th>Moneda</th><th>Lado</th><th>Entrada</th><th>Precio actual</th><th>Margen</th><th>P&amp;L no realizado</th><th>Desde</th></tr>{rows_open}</table>
<h2>Operaciones cerradas</h2>
<table><tr><th>Moneda</th><th>Entrada</th><th>Salida</th><th>P&amp;L $</th><th>P&amp;L % margen</th><th>Duración</th><th>Motivo</th><th>Horario</th></tr>{rows_closed}</table>
<p><small>Spot long 1x y futuros long/short 3x en la cuenta demo de Bybit. No es consejo financiero. Los precios de posiciones abiertas se actualizan en vivo en tu navegador; la tabla de cerradas se actualiza cuando el bot regenera esta página (cada 5 minutos).</small></p>
<script>
const POSITIONS={positions_json};
const CAP0={cap0};
const REALIZED={realized};
function fmt(n){{return '$'+n.toLocaleString('en-US',{{minimumFractionDigits:2,maximumFractionDigits:2}})}}
function fmtSigned(n){{return (n>=0?'+':'')+fmt(n)}}
async function refreshLive(){{
  if(!POSITIONS.length) return;
  const ids=POSITIONS.map(p=>p.id).join(',');
  try{{
    const r=await fetch('https://api.coingecko.com/api/v3/simple/price?ids='+encodeURIComponent(ids)+'&vs_currencies=usd');
    const data=await r.json();
    let unreal=0;
    POSITIONS.forEach(p=>{{
      const price=data[p.id]&&data[p.id].usd;
      if(!price) return;
      let u;
      if(p.side==='long') u=p.notional_usd*(price-p.entry_price)/p.entry_price;
      else u=p.margin_usd*p.leverage*(p.entry_price-price)/p.entry_price;
      unreal+=u;
      const row=document.querySelector('tr[data-id="'+p.id+'"]');
      if(row){{row.querySelector('.price-cell').textContent='$'+price; row.querySelector('.pnl-cell').textContent=fmtSigned(u);}}
    }});
    const equity=CAP0+REALIZED+unreal;
    const gain=(equity-CAP0)/CAP0*100;
    document.getElementById('equity-val').textContent=fmt(equity);
    const gv=document.getElementById('gain-val'); gv.textContent=(gain>=0?'+':'')+gain.toFixed(2)+'%'; gv.className=gain>=0?'pos':'neg';
    const uv=document.getElementById('unreal-val'); uv.textContent=fmtSigned(unreal); uv.className=unreal>=0?'pos':'neg';
    document.getElementById('live-status').textContent='Precios en vivo actualizados: '+new Date().toLocaleTimeString('es-MX');
  }}catch(e){{document.getElementById('live-status').textContent='No se pudieron actualizar los precios en vivo (revisa tu conexión).';}}
}}
refreshLive(); setInterval(refreshLive,30000);
// recarga sin caché cada minuto para mostrar aperturas/cierres nuevos
setInterval(function(){{location.replace(location.pathname+'?t='+Date.now());}},60000);
</script>
</body></html>"""
    open(DASH, "w", encoding="utf-8").write(html)

if __name__ == "__main__":
    main()
