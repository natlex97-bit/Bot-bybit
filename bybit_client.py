#!/usr/bin/env python3
"""Cliente mínimo (solo librería estándar) para la API v5 de Bybit,
apuntando al entorno DEMO: https://api-demo.bybit.com

No mueve dinero real: la demo usa fondos simulados. La clave API debe
crearse DENTRO de Demo Trading (perfil -> API) y tener solo permisos de
operar (Unified Trading: órdenes y posiciones), NUNCA retiros.

Autenticación v5: HMAC-SHA256 sobre timestamp + apiKey + recvWindow +
queryString (GET) o cuerpo JSON (POST).
"""
import hashlib
import hmac
import json
import time
import urllib.request
import urllib.parse
import urllib.error

BASE_URL = "https://api-demo.bybit.com"
RECV_WINDOW = "5000"


class BybitDemoError(Exception):
    pass


def _sign(secret, timestamp, api_key, recv_window, payload):
    """Firma HMAC-SHA256 según la documentación v5 de Bybit."""
    param_str = str(timestamp) + api_key + str(recv_window) + payload
    return hmac.new(secret.encode("utf-8"), param_str.encode("utf-8"),
                    hashlib.sha256).hexdigest()


class BybitDemoClient:
    def __init__(self, api_key, api_secret, dry_run=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.dry_run = dry_run

    # ---- bajo nivel ----
    def _request(self, method, path, params=None, body=None, auth=True):
        params = params or {}
        if method == "GET":
            qs = urllib.parse.urlencode(params)
            url = BASE_URL + path + ("?" + qs if qs else "")
            payload = qs
            data = None
        else:
            url = BASE_URL + path
            payload = json.dumps(body or {}, separators=(",", ":"))
            data = payload.encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if auth:
            ts = str(int(time.time() * 1000))
            sign = _sign(self.api_secret, ts, self.api_key, RECV_WINDOW, payload)
            headers.update({
                "X-BAPI-API-KEY": self.api_key,
                "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": RECV_WINDOW,
                "X-BAPI-SIGN": sign,
            })
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                resp = json.load(e)
            except Exception:
                raise BybitDemoError(f"HTTP {e.code} en {path}")
            raise BybitDemoError(f"Bybit {path}: {resp.get('retMsg')} (retCode {resp.get('retCode')})")
        except Exception as e:
            raise BybitDemoError(f"Error de red en {path}: {e}")
        if resp.get("retCode") != 0:
            raise BybitDemoError(f"Bybit {path}: {resp.get('retMsg')} (retCode {resp.get('retCode')})")
        return resp.get("result", {})

    # ---- públicos (no necesitan clave) ----
    def get_ticker(self, symbol):
        """Precio actual de un símbolo lineal, ej. BTCUSDT."""
        r = self._request("GET", "/v5/market/tickers",
                          {"category": "linear", "symbol": symbol}, auth=False)
        lst = r.get("list", [])
        return float(lst[0]["lastPrice"]) if lst else None

    def get_instruments_info(self, category, symbol):
        r = self._request("GET", "/v5/market/instruments-info",
                          {"category": category, "symbol": symbol}, auth=False)
        lst = r.get("list", [])
        return lst[0] if lst else None

    # ---- cuenta / posiciones ----
    def get_wallet_balance_usdt(self):
        r = self._request("GET", "/v5/account/wallet-balance",
                          {"accountType": "UNIFIED", "coin": "USDT"})
        lst = r.get("list", [])
        if not lst:
            return 0.0
        for c in lst[0].get("coin", []):
            if c.get("coin") == "USDT":
                return float(c.get("walletBalance", 0))
        return 0.0

    def get_coin_balance(self, coin):
        r = self._request("GET", "/v5/account/wallet-balance",
                          {"accountType": "UNIFIED", "coin": coin})
        lst = r.get("list", [])
        if not lst:
            return 0.0
        for c in lst[0].get("coin", []):
            if c.get("coin") == coin:
                return float(c.get("walletBalance", 0))
        return 0.0

    def get_linear_positions(self):
        """Posiciones abiertas de futuros. Devuelve dict símbolo -> posición (size != 0)."""
        r = self._request("GET", "/v5/position/list", {"category": "linear", "limit": 200})
        out = {}
        for p in r.get("list", []):
            try:
                size = float(p.get("size", 0))
            except Exception:
                size = 0.0
            if size != 0:
                out[p["symbol"]] = p
        return out

    def get_closed_pnl(self, symbol, limit=10):
        """Historial de PnL cerrado de un símbolo (para conciliar cierres por TP/SL)."""
        r = self._request("GET", "/v5/position/closed-pnl",
                          {"category": "linear", "symbol": symbol, "limit": limit})
        return r.get("list", [])

    def get_executions(self, order_id, symbol=None):
        params = {"category": "linear", "orderId": order_id, "limit": 10}
        if symbol:
            params["symbol"] = symbol
        r = self._request("GET", "/v5/execution/list", params)
        return r.get("list", [])

    # ---- configuración por símbolo ----
    def set_isolated_margin(self, symbol):
        """Margen aislado para el símbolo (si ya está así, Bybit devuelve error y se ignora)."""
        try:
            return self._request("POST", "/v5/account/set-margin-mode",
                                 body={"category": "linear", "symbol": symbol,
                                       "setMarginMode": "ISOLATED_MARGIN"})
        except BybitDemoError as e:
            if "already" in str(e).lower() or "same" in str(e).lower():
                return {"ignored": str(e)}
            raise

    def set_leverage(self, symbol, leverage):
        try:
            return self._request("POST", "/v5/position/set-leverage",
                                 body={"category": "linear", "symbol": symbol,
                                       "buyLeverage": str(leverage),
                                       "sellLeverage": str(leverage)})
        except BybitDemoError as e:
            if "already" in str(e).lower() or "same" in str(e).lower() or "110043" in str(e):
                return {"ignored": str(e)}
            raise

    # ---- órdenes ----
    def place_linear_order(self, symbol, side, qty, take_profit=None, stop_loss=None):
        """Orden de mercado en futuros lineales con TP/SL puestos en el exchange.
        side: 'Buy' o 'Sell'. qty en unidades de la moneda base."""
        if self.dry_run:
            return {"dry_run": True, "symbol": symbol, "side": side, "qty": qty,
                    "takeProfit": take_profit, "stopLoss": stop_loss}
        body = {"category": "linear", "symbol": symbol, "side": side,
                "orderType": "Market", "qty": str(qty),
                "timeInForce": "GTC", "positionIdx": 0,
                "takeProfit": str(take_profit) if take_profit else None,
                "stopLoss": str(stop_loss) if stop_loss else None,
                "tpTriggerBy": "LastPrice", "slTriggerBy": "LastPrice"}
        body = {k: v for k, v in body.items() if v is not None}
        return self._request("POST", "/v5/order/create", body=body)

    def place_spot_order(self, symbol, side, qty):
        """Orden de mercado spot. qty en moneda base. Sin TP/SL (los vigila el bot)."""
        if self.dry_run:
            return {"dry_run": True, "symbol": symbol, "side": side, "qty": qty}
        body = {"category": "spot", "symbol": symbol, "side": side,
                "orderType": "Market", "qty": str(qty), "timeInForce": "GTC"}
        return self._request("POST", "/v5/order/create", body=body)

    def close_linear_position(self, symbol, side, qty):
        """Cierre por mercado (reduceOnly) de una posición de futuros.
        side es el lado del cierre: 'Buy' cierra un short, 'Sell' cierra un long."""
        if self.dry_run:
            return {"dry_run": True, "symbol": symbol, "side": side, "qty": qty}
        body = {"category": "linear", "symbol": symbol, "side": side,
                "orderType": "Market", "qty": str(qty),
                "timeInForce": "GTC", "positionIdx": 0, "reduceOnly": True}
        return self._request("POST", "/v5/order/create", body=body)

    def cancel_all_orders(self, symbol, category="linear"):
        if self.dry_run:
            return {"dry_run": True, "symbol": symbol}
        return self._request("POST", "/v5/order/cancel-all",
                             body={"category": category, "symbol": symbol})


def round_down_to_step(qty, step):
    """Redondea qty hacia abajo al múltiplo de step (evita rechazos por lotaje)."""
    if step <= 0:
        return qty
    import math
    return math.floor(qty / step) * step


def format_qty(qty, step):
    """Formatea qty con los decimales que permite el step."""
    if step <= 0:
        return str(qty)
    decimals = 0
    s = ("%f" % step).rstrip("0")
    if "." in s:
        decimals = len(s.split(".")[1])
    return f"{qty:.{decimals}f}"
