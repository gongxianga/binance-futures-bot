#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安合约交易软件 v4.0  —  Web 版
运行: python app.py  然后浏览器打开 http://localhost:5000
"""

import json
import os
import sys
import time
import threading
import urllib.request
import webbrowser
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, jsonify, request, render_template, session, redirect, url_for, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

try:
    from binance.client import Client
    BINANCE_OK = True
except ImportError:
    BINANCE_OK = False

from auth import (UserManager, LoginRateLimiter, CaptchaGenerator,
                  APIKeyEncryptor)

app = Flask(__name__)
app.secret_key = os.urandom(24)  # 生成随机密钥

# Flask-Login 配置
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

# 安全组件初始化
user_manager = UserManager(os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json"))
rate_limiter = LoginRateLimiter(max_attempts=5, lockout_duration=300)
captcha_gen = CaptchaGenerator()
api_encryptor = APIKeyEncryptor(os.path.join(os.path.dirname(os.path.abspath(__file__)), "secret.key"))

@login_manager.user_loader
def load_user(user_id):
    return user_manager.get_user(user_id)

# ─────────────────────────────────────────────
#  全局状态
# ─────────────────────────────────────────────

state = {
    "connected": False,
    "testnet": True,
    "scan_running": False,
    "next_scan_at": 0,
    "scan_results": [],
    "scan_ts": "",
    "prev_scan_results": [],
    "prev_scan_ts": "",
    "scan_progress": {"current": 0, "total": 0, "symbol": ""},
    "log": [],
}

engine = None
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_lock = threading.Lock()


def add_log(msg, level="info"):
    entry = {"ts": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level}
    with _lock:
        state["log"].insert(0, entry)
        if len(state["log"]) > 200:
            state["log"] = state["log"][:200]


# ─────────────────────────────────────────────
#  交易引擎
# ─────────────────────────────────────────────

class FuturesEngine:
    def __init__(self):
        self.client = None

    def connect(self, key, secret, testnet=False):
        try:
            self.client = Client(key, secret, testnet=testnet)
            if testnet:
                self.client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
            self.client.futures_account()
            return True, "连接成功"
        except Exception as e:
            self.client = None
            return False, str(e)

    def get_balance(self):
        try:
            return next((b for b in self.client.futures_account_balance()
                         if b["asset"] == "USDT"), None)
        except Exception:
            return None

    def get_account(self):
        try:
            return self.client.futures_account()
        except Exception:
            return None

    def get_positions(self):
        try:
            return [p for p in self.client.futures_position_information()
                    if float(p["positionAmt"]) != 0]
        except Exception:
            return []

    def get_price(self, symbol):
        try:
            return float(self.client.futures_symbol_ticker(symbol=symbol)["price"])
        except Exception:
            pass
        # 回退直接 HTTP
        try:
            url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}"
            with urllib.request.urlopen(url, timeout=5) as r:
                return float(json.loads(r.read())["price"])
        except Exception:
            return None

    def get_symbol_filters(self, symbol):
        """返回 (step_size, tick_size, max_qty, is_trading)，从 exchange_info 缓存中读取"""
        if not hasattr(self, "_exinfo_cache"):
            self._exinfo_cache = {}
        if symbol not in self._exinfo_cache:
            try:
                info = self.client.futures_exchange_info()
                for s in info.get("symbols", []):
                    step = "1"
                    tick = "0.01"
                    max_qty = "1000000"  # 默认大值
                    for f in s.get("filters", []):
                        if f["filterType"] == "LOT_SIZE":
                            step = f["stepSize"]
                            max_qty = f.get("maxQty", "1000000")
                        elif f["filterType"] == "PRICE_FILTER":
                            tick = f["tickSize"]
                    self._exinfo_cache[s["symbol"]] = {
                        "step": step,
                        "tick": tick,
                        "max_qty": max_qty,
                        "status": s.get("status", "TRADING")
                    }
            except Exception:
                return "1", "0.01", "1000000", True
        d = self._exinfo_cache.get(symbol, {"step": "1", "tick": "0.01", "max_qty": "1000000", "status": "TRADING"})
        return d["step"], d["tick"], d["max_qty"], d["status"] == "TRADING"

    def _fmt(self, value, size_str):
        """按 stepSize/tickSize 格式化数值为字符串，避免浮点精度问题"""
        import math
        size_f = float(size_str)
        if size_f <= 0:
            return str(int(value))
        precision = max(0, -int(math.floor(math.log10(size_f))))
        rounded = math.floor(value / size_f) * size_f
        return f"{rounded:.{precision}f}"

    def _safe_set_leverage(self, symbol, lev):
        """设置杠杆，超出上限时自动降为该合约允许的最大值"""
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=lev)
        except Exception:
            try:
                brackets = self.client.futures_leverage_bracket(symbol=symbol)
                max_lev = brackets[0]["brackets"][0]["initialLeverage"]
                use_lev = min(lev, max_lev)
                self.client.futures_change_leverage(symbol=symbol, leverage=use_lev)
                add_log(f"{symbol} 最大杠杆{max_lev}x，已自动调整", "warn")
            except Exception as e:
                add_log(f"{symbol} 杠杆设置失败: {e}", "warn")

    def place_order(self, symbol, side, otype, qty, price=None, lev=10):
        step, tick, max_qty, is_trading = self.get_symbol_filters(symbol)
        if not is_trading:
            return False, f"{symbol} 交易对已关闭，无法下单"
        qty = self._fmt(float(qty), step)
        # 检查是否超过最大数量
        if float(qty) > float(max_qty):
            qty = self._fmt(float(max_qty) * 0.99, step)  # 使用最大值的99%
            add_log(f"{symbol} 数量超限，已调整为 {qty}", "warn")
        if float(qty) <= 0:
            return False, f"{symbol} 计算数量为0，请增加交易金额"
        try:
            self._safe_set_leverage(symbol, lev)
            p = dict(symbol=symbol, side=side, type=otype, quantity=qty)
            if otype == "LIMIT" and price:
                p["price"] = self._fmt(float(price), tick)
                p["timeInForce"] = "GTC"
            return True, self.client.futures_create_order(**p)
        except Exception as e:
            return False, str(e)

    def place_with_sltp(self, symbol, side, qty, lev, sl_pct, tp_pct):
        step, tick, max_qty, is_trading = self.get_symbol_filters(symbol)
        if not is_trading:
            return False, f"{symbol} 交易对已关闭，无法下单", 0, 0
        qty = self._fmt(float(qty), step)
        # 检查是否超过最大数量
        if float(qty) > float(max_qty):
            qty = self._fmt(float(max_qty) * 0.99, step)  # 使用最大值的99%
            add_log(f"{symbol} 数量超限，已调整为 {qty}", "warn")
        if float(qty) <= 0:
            return False, f"{symbol} 计算数量为0，请增加交易金额", 0, 0
        try:
            self._safe_set_leverage(symbol, lev)
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=qty)
            price = float(order.get("avgPrice") or 0) or self.get_price(symbol) or 0
            close_side = "SELL" if side == "BUY" else "BUY"
            # 止损/止盈基于本金百分比：价格移动幅度 = 百分比 / 杠杆倍数
            lev_f = float(lev) if lev else 1.0
            if side == "BUY":
                sl_px = price * (1 - sl_pct / (100 * lev_f))
                tp_px = price * (1 + tp_pct / (100 * lev_f))
            else:
                sl_px = price * (1 + sl_pct / (100 * lev_f))
                tp_px = price * (1 - tp_pct / (100 * lev_f))
            sl_px_str = self._fmt(sl_px, tick)
            tp_px_str = self._fmt(tp_px, tick)
            sl_px = float(sl_px_str)
            tp_px = float(tp_px_str)
            if price > 0:
                self.client.futures_create_order(
                    symbol=symbol, side=close_side, type="STOP_MARKET",
                    stopPrice=sl_px_str, closePosition=True)
                self.client.futures_create_order(
                    symbol=symbol, side=close_side, type="TAKE_PROFIT_MARKET",
                    stopPrice=tp_px_str, closePosition=True)
            return True, order, sl_px, tp_px
        except Exception as e:
            return False, str(e), 0, 0

    def close_position(self, symbol, amt_str):
        try:
            amt = float(amt_str)
            return True, self.client.futures_create_order(
                symbol=symbol, side="SELL" if amt > 0 else "BUY",
                type="MARKET", quantity=abs(amt), reduceOnly=True)
        except Exception as e:
            return False, str(e)

    def cancel_all(self, symbol):
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            return True, f"已撤销 {symbol} 所有挂单"
        except Exception as e:
            return False, str(e)


def _symbol_to_okx(symbol):
    """BTCUSDT -> BTC-USDT-SWAP"""
    if symbol.endswith("USDT"):
        return symbol[:-4] + "-USDT-SWAP"
    return symbol


def get_tickers():
    """优先从 OKX 拉取行情（无地理限制），格式归一化为币安风格"""
    try:
        url = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "python-requests/2.28")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        result = []
        for d in resp.get("data", []):
            if not d["instId"].endswith("-USDT-SWAP"):
                continue
            last    = float(d.get("last") or 0)
            open24h = float(d.get("open24h") or 0)
            if last == 0 or open24h == 0:
                continue
            chg_pct  = (last - open24h) / open24h * 100
            vol_usdt = float(d.get("volCcy24h") or 0)
            symbol   = d["instId"].replace("-USDT-SWAP", "") + "USDT"
            result.append({
                "symbol":             symbol,
                "priceChangePercent": str(round(chg_pct, 4)),
                "quoteVolume":        str(vol_usdt),
                "lastPrice":          str(last),
            })
        if result:
            return sorted(result, key=lambda x: float(x["quoteVolume"]), reverse=True)
    except Exception as e:
        add_log(f"OKX行情获取失败: {e}", "warn")

    # 回退：已连接的币安客户端
    if engine and state["connected"]:
        try:
            data = engine.client.futures_ticker()
            return sorted([d for d in data if d["symbol"].endswith("USDT")],
                          key=lambda x: float(x["quoteVolume"]), reverse=True)
        except Exception as e:
            add_log(f"币安行情也失败: {e}", "warn")
    return []


# ─────────────────────────────────────────────
#  信号引擎
# ─────────────────────────────────────────────

class SignalEngine:
    KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"

    _OKX_BAR = {"1h": "1H", "4h": "4H", "1d": "1D", "15m": "15m", "1m": "1m"}

    def _klines(self, symbol, interval="1h", limit=50):
        """只走 OKX K线；OKX 没有的币直接返回空（跳过扫描）"""
        try:
            inst_id = _symbol_to_okx(symbol)
            bar     = self._OKX_BAR.get(interval, "1H")
            url     = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "python-requests/2.28")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=8) as r:
                resp = json.loads(r.read())
            rows = resp.get("data", [])
            if rows:
                return list(reversed(rows))   # OKX 返回最新在前，翻转为旧→新
        except Exception:
            pass
        return []

    @staticmethod
    def _rsi(closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0)); losses.append(max(-d, 0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))

    @staticmethod
    def _bollinger(closes, period=20):
        if len(closes) < period:
            return None, None, None
        d = closes[-period:]
        sma = sum(d) / period
        std = (sum((x - sma) ** 2 for x in d) / period) ** 0.5
        return sma + 2 * std, sma, sma - 2 * std

    @staticmethod
    def _ema(closes, period):
        if len(closes) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    @staticmethod
    def _atr(klines, period=14):
        if len(klines) < period + 1:
            return None
        trs = []
        for i in range(1, len(klines)):
            h = float(klines[i][2]); l = float(klines[i][3]); pc = float(klines[i-1][4])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs[-period:]) / period

    def _funding_rate(self, symbol):
        """获取OKX资金费率，失败返回None"""
        try:
            url = f"https://www.okx.com/api/v5/public/funding-rate?instId={_symbol_to_okx(symbol)}"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "python-requests/2.28")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read()).get("data", [])
            if data:
                return float(data[0].get("fundingRate", 0))
        except Exception:
            pass
        return None

    def analyze(self, symbol, ticker, all_tickers):
        klines = self._klines(symbol, limit=60)
        if not klines:
            return None
        closes  = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        cur_price = closes[-1]
        cur_vol   = volumes[-1]
        avg_vol   = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else sum(volumes) / max(len(volumes), 1)
        chg_pct   = float(ticker["priceChangePercent"])

        long_s = short_s = 0
        details = {}

        # 1. 量价突破
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0
        if vol_ratio >= 2.5:
            if chg_pct > 0: long_s += 1;  details["vol"] = {"text": "多", "cls": "green"}
            else:            short_s += 1; details["vol"] = {"text": "空", "cls": "red"}
        else:
            details["vol"] = {"text": "--", "cls": "muted"}

        # 2. RSI
        rsi = self._rsi(closes)
        if rsi < 30:   long_s += 1;  details["rsi"] = {"text": f"{rsi:.0f}↑", "cls": "green"}
        elif rsi > 70: short_s += 1; details["rsi"] = {"text": f"{rsi:.0f}↓", "cls": "red"}
        else:                         details["rsi"] = {"text": f"{rsi:.0f}",  "cls": "muted"}

        # 3. 动量排名
        by_chg = sorted(all_tickers, key=lambda x: float(x["priceChangePercent"]), reverse=True)
        by_vol = sorted(all_tickers, key=lambda x: float(x["quoteVolume"]), reverse=True)
        rank_c = next((i for i, t in enumerate(by_chg) if t["symbol"] == symbol), 999)
        rank_v = next((i for i, t in enumerate(by_vol) if t["symbol"] == symbol), 999)
        n = len(all_tickers)
        if rank_c < 5 and rank_v < 20:    long_s += 1;  details["mom"] = {"text": "强多", "cls": "green"}
        elif rank_c > n-6 and rank_v < 20: short_s += 1; details["mom"] = {"text": "强空", "cls": "red"}
        else:                               details["mom"] = {"text": "--",   "cls": "muted"}

        # 4. 布林带
        upper, mid_bb, lower = self._bollinger(closes)
        if upper and lower:
            if cur_price > upper:   long_s += 1;  details["bb"] = {"text": "突破上轨", "cls": "green"}
            elif cur_price < lower: short_s += 1; details["bb"] = {"text": "跌破下轨", "cls": "red"}
            else:                                  details["bb"] = {"text": "--", "cls": "muted"}
        else:
            details["bb"] = {"text": "--", "cls": "muted"}

        # 5. EMA 金叉/死叉 (EMA20 vs EMA50)
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        if ema20 and ema50 and len(closes) > 51:
            ema20_p = self._ema(closes[:-1], 20)
            ema50_p = self._ema(closes[:-1], 50)
            if ema20_p and ema50_p:
                if ema20_p <= ema50_p and ema20 > ema50:
                    long_s += 1; details["ema"] = {"text": "金叉↑", "cls": "green"}
                elif ema20_p >= ema50_p and ema20 < ema50:
                    short_s += 1; details["ema"] = {"text": "死叉↓", "cls": "red"}
                else:
                    trend = "多头" if ema20 > ema50 else "空头"
                    details["ema"] = {"text": trend, "cls": "green" if ema20 > ema50 else "red"}
            else:
                details["ema"] = {"text": "--", "cls": "muted"}
        else:
            details["ema"] = {"text": "--", "cls": "muted"}

        # 6. 挤压动量 (Bollinger Bands vs Keltner Channel)
        atr = self._atr(klines)
        if atr and ema20 and upper and lower:
            kc_upper = ema20 + 1.5 * atr
            kc_lower = ema20 - 1.5 * atr
            in_sqz = upper < kc_upper and lower > kc_lower
            if len(closes) > 1 and len(klines) > 1:
                up_p, _, lo_p = self._bollinger(closes[:-1])
                ema20_p2 = self._ema(closes[:-1], 20)
                atr_p    = self._atr(klines[:-1])
                if up_p and lo_p and ema20_p2 and atr_p:
                    kcu_p   = ema20_p2 + 1.5 * atr_p
                    kcl_p   = ema20_p2 - 1.5 * atr_p
                    was_sqz = up_p < kcu_p and lo_p > kcl_p
                    if was_sqz and not in_sqz:
                        if cur_price > ema20:
                            long_s += 1; details["sqz"] = {"text": "挤压↑", "cls": "green"}
                        else:
                            short_s += 1; details["sqz"] = {"text": "挤压↓", "cls": "red"}
                    elif in_sqz:
                        details["sqz"] = {"text": "蓄力中", "cls": "muted"}
                    else:
                        details["sqz"] = {"text": "--", "cls": "muted"}
                else:
                    details["sqz"] = {"text": "--", "cls": "muted"}
            else:
                details["sqz"] = {"text": "--", "cls": "muted"}
        else:
            details["sqz"] = {"text": "--", "cls": "muted"}

        # 7. 资金费率极端值
        fr = self._funding_rate(symbol)
        if fr is not None:
            fr_pct = fr * 100
            if fr > 0.001:
                short_s += 1; details["fr"] = {"text": f"{fr_pct:.3f}%↓", "cls": "red"}
            elif fr < -0.0005:
                long_s += 1;  details["fr"] = {"text": f"{fr_pct:.3f}%↑", "cls": "green"}
            else:
                details["fr"] = {"text": f"{fr_pct:.3f}%", "cls": "muted"}
        else:
            details["fr"] = {"text": "--", "cls": "muted"}

        score = max(long_s, short_s)
        if score == 0:
            return None
        return {
            "symbol":    symbol,
            "score":     score,
            "direction": "LONG" if long_s >= short_s else "SHORT",
            "price":     cur_price,
            "chg":       chg_pct,
            "details":   details,
        }

    def scan(self, tickers, top_n=30):
        results = []
        top = tickers[:top_n]
        total = len(top)
        completed = 0
        lock = threading.Lock()

        def _analyze_one(item):
            i, t = item
            r = self.analyze(t["symbol"], t, tickers)
            return i, t["symbol"], r

        state["scan_progress"] = {"current": 0, "total": total, "symbol": ""}
        add_log(f"开始并发扫描 {total} 个交易对...", "info")

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_analyze_one, (i, t)): i for i, t in enumerate(top)}
            for future in as_completed(futures):
                i, symbol, r = future.result()
                with lock:
                    completed += 1
                    state["scan_progress"] = {"current": completed, "total": total, "symbol": symbol}
                if r:
                    with lock:
                        results.append(r)

        state["scan_progress"] = {"current": 0, "total": 0, "symbol": ""}
        results.sort(key=lambda x: x["score"], reverse=True)
        add_log(f"扫描完成，共找到 {len(results)} 个有效信号", "info")
        return results


signal_engine = SignalEngine()


# ─────────────────────────────────────────────
#  辅助函数
# ─────────────────────────────────────────────

def get_client_ip():
    """获取客户端真实IP"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


# ─────────────────────────────────────────────
#  Flask 路由 - 认证
# ─────────────────────────────────────────────

@app.route("/login")
def login_page():
    """登录页面"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template("login.html")


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    """登录接口"""
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    captcha = data.get("captcha", "").strip()

    client_ip = get_client_ip()

    # 检查是否被锁定
    if rate_limiter.is_locked(client_ip):
        remaining = rate_limiter.get_remaining_time(client_ip)
        return jsonify({
            "ok": False,
            "msg": f"登录失败次数过多，请 {remaining} 秒后重试"
        })

    # 验证验证码
    session_id = session.get('session_id', '')
    if not captcha_gen.verify(session_id, captcha):
        rate_limiter.record_attempt(client_ip)
        return jsonify({"ok": False, "msg": "验证码错误"})

    # 验证用户名密码
    user = user_manager.get_user_by_username(username)
    if not user or not user.check_password(password):
        rate_limiter.record_attempt(client_ip)
        add_log(f"登录失败: {username} from {client_ip}", "warn")
        return jsonify({"ok": False, "msg": "用户名或密码错误"})

    # 检查IP白名单
    if not user.is_ip_allowed(client_ip):
        add_log(f"IP不在白名单: {username} from {client_ip}", "err")
        return jsonify({"ok": False, "msg": "您的IP不在白名单中"})

    # 登录成功
    login_user(user)
    rate_limiter.reset(client_ip)
    add_log(f"用户登录成功: {username} from {client_ip}", "ok")

    return jsonify({"ok": True, "msg": "登录成功"})


@app.route("/api/auth/logout", methods=["POST"])
@login_required
def api_logout():
    """登出接口"""
    username = current_user.username
    logout_user()
    add_log(f"用户登出: {username}", "info")
    return jsonify({"ok": True})


@app.route("/api/auth/captcha")
def api_captcha():
    """获取验证码图片"""
    # 为每个会话生成唯一ID
    if 'session_id' not in session:
        session['session_id'] = os.urandom(16).hex()

    session_id = session['session_id']
    code = captcha_gen.generate(session_id)
    image_buffer = captcha_gen.create_image(code)

    return send_file(image_buffer, mimetype='image/png')


@app.route("/api/auth/status")
def api_auth_status():
    """获取认证状态"""
    return jsonify({
        "authenticated": current_user.is_authenticated,
        "username": current_user.username if current_user.is_authenticated else None
    })


# ─────────────────────────────────────────────
#  Flask 路由 - 主页
# ─────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/connect", methods=["POST"])
@login_required
def api_connect():
    global engine
    data = request.json
    key     = data.get("api_key", "").strip()
    secret  = data.get("api_secret", "").strip()
    testnet = data.get("testnet", True)

    if not key or not secret:
        return jsonify({"ok": False, "msg": "请填写 API Key 和 Secret"})

    # 无论连接是否成功都先保存密钥
    encrypted_key = api_encryptor.encrypt(key)
    encrypted_secret = api_encryptor.encrypt(secret)
    with open(CONFIG_FILE, "w") as f:
        json.dump({
            "api_key": encrypted_key,
            "api_secret": encrypted_secret,
            "testnet": testnet
        }, f)

    eng = FuturesEngine()
    ok, msg = eng.connect(key, secret, testnet)
    if ok:
        engine = eng
        state["connected"] = True
        state["testnet"] = testnet
        add_log(f"连接成功 ({'测试网' if testnet else '正式网'})", "ok")
    else:
        add_log(f"连接失败（密钥已保存）: {msg}", "err")
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/config")
@login_required
def api_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # 解密 API 密钥（但不返回完整内容）
        encrypted_key = cfg.get("api_key", "")
        if encrypted_key:
            decrypted_key = api_encryptor.decrypt(encrypted_key)
            # 只显示前4位和后4位
            if len(decrypted_key) > 8:
                cfg["api_key"] = decrypted_key[:4] + "•" * 10 + decrypted_key[-4:]
            else:
                cfg["api_key"] = "•" * 8
        cfg["api_secret"] = "•" * 8  # 不暴露 secret
        return jsonify(cfg)
    return jsonify({})


@app.route("/api/account")
@login_required
def api_account():
    if not state["connected"] or not engine:
        return jsonify({"ok": False})
    bal = engine.get_balance()
    acc = engine.get_account()
    if not bal:
        return jsonify({"ok": False})
    return jsonify({
        "ok": True,
        "available": float(bal.get("availableBalance", 0)),
        "wallet":    float(bal.get("balance", 0)),
        "pnl":       float(acc.get("totalUnrealizedProfit", 0)) if acc else 0,
    })


@app.route("/api/positions")
@login_required
def api_positions():
    if not state["connected"] or not engine:
        return jsonify({"ok": False, "positions": []})
    positions = engine.get_positions()
    # 从 account 获取杠杆信息作为补充
    lev_map = {}
    try:
        acc = engine.client.futures_account()
        for p in acc.get("positions", []):
            lev = int(p.get("leverage", 0))
            if lev > 0:
                lev_map[p["symbol"]] = lev
    except Exception:
        pass
    result = []
    for p in positions:
        amt = float(p["positionAmt"])

        # 过滤掉已平仓的持仓（数量为0）
        if amt == 0:
            continue

        entry = float(p["entryPrice"])
        mark = float(p["markPrice"])
        pnl = float(p["unRealizedProfit"])
        lev = lev_map.get(p["symbol"]) or int(p.get("leverage") or 0) or 1

        # 正确计算收益率: ROE = (未实现盈亏 / 保证金) * 100
        # 保证金 = 入场价 * 数量 / 杠杆
        if isinstance(lev, int) and lev > 0:
            margin = (entry * abs(amt)) / lev
            roe = (pnl / margin * 100) if margin > 0 else 0
            notional = mark * abs(amt)  # 仓位价值
            # 计算强平价格（近似）
            if amt > 0:  # 多仓
                liq_price = entry * (1 - 0.9 / lev)
            else:  # 空仓
                liq_price = entry * (1 + 0.9 / lev)
        else:
            roe = 0
            margin = 0
            notional = 0
            liq_price = 0
            lev = "--"

        result.append({
            "symbol":       p["symbol"],
            "side":         "多" if amt > 0 else "空",
            "amt":          abs(amt),
            "entry":        entry,
            "mark":         mark,
            "pnl":          pnl,
            "roe":          roe,
            "leverage":     lev,
            "positionAmt":  p["positionAmt"],
            "margin":       margin,
            "notional":     notional,
            "liquidation":  liq_price,
            "updateTime":   p.get("updateTime", 0),
        })

    # 添加排序功能
    sort_by = request.args.get("sort", "pnl")  # 默认按盈亏排序
    sort_order = request.args.get("order", "desc")  # desc 或 asc

    if sort_by in ["pnl", "roe", "amt", "entry"]:
        reverse = (sort_order == "desc")
        result.sort(key=lambda x: x.get(sort_by, 0), reverse=reverse)

    return jsonify({"ok": True, "positions": result})


def _public_price(symbol):
    """无需认证获取实时价格，优先 OKX"""
    try:
        inst_id = _symbol_to_okx(symbol.upper())
        url = f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "python-requests/2.28")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            resp = json.loads(r.read())
            return float(resp["data"][0]["last"])
    except Exception:
        pass
    # 回退币安
    for base in ["https://fapi.binance.com", "https://testnet.binancefuture.com"]:
        try:
            url = f"{base}/fapi/v1/ticker/price?symbol={symbol.upper()}"
            with urllib.request.urlopen(url, timeout=5) as r:
                return float(json.loads(r.read())["price"])
        except Exception:
            continue
    return None


@app.route("/api/price/<symbol>")
@login_required
def api_price(symbol):
    sym = symbol.upper()
    # 已连接时优先用已认证的客户端
    if state["connected"] and engine:
        price = engine.get_price(sym)
        if price:
            return jsonify({"ok": True, "price": price})
    # 回退到公开接口（无需 API Key）
    price = _public_price(sym)
    if price:
        return jsonify({"ok": True, "price": price})
    return jsonify({"ok": False, "msg": "无法获取价格，请检查交易对名称"})


@app.route("/api/order", methods=["POST"])
@login_required
def api_order():
    if not state["connected"] or not engine:
        return jsonify({"ok": False, "msg": "未连接"})
    d = request.json
    ok, res = engine.place_order(
        d["symbol"], d["side"], d["type"],
        d["qty"], d.get("price"), int(d.get("leverage", 10)))
    if ok:
        add_log(f"下单成功 {'做多' if d['side']=='BUY' else '做空'} {d['symbol']} ID:{res.get('orderId','N/A')}", "ok")
        return jsonify({"ok": True, "orderId": res.get("orderId")})
    add_log(f"下单失败: {res}", "err")
    return jsonify({"ok": False, "msg": str(res)})


@app.route("/api/order/sltp", methods=["POST"])
@login_required
def api_order_sltp():
    if not state["connected"] or not engine:
        return jsonify({"ok": False, "msg": "未连接"})
    d = request.json
    ok, res, sl, tp = engine.place_with_sltp(
        d["symbol"], d["side"], float(d["qty"]),
        int(d["leverage"]), float(d["sl_pct"]), float(d["tp_pct"]))
    if ok:
        cn = "做多" if d["side"] == "BUY" else "做空"
        add_log(f"下单成功 {cn} {d['symbol']}  止损:{sl:.4f}  止盈:{tp:.4f}", "ok")
        return jsonify({"ok": True, "sl": sl, "tp": tp})
    add_log(f"下单失败: {res}", "err")
    return jsonify({"ok": False, "msg": str(res)})


@app.route("/api/close", methods=["POST"])
@login_required
def api_close():
    if not state["connected"] or not engine:
        return jsonify({"ok": False, "msg": "未连接"})
    d = request.json
    positions = engine.get_positions()
    pos = next((p for p in positions if p["symbol"] == d["symbol"]), None)
    if not pos:
        return jsonify({"ok": False, "msg": "未找到持仓"})
    ok, res = engine.close_position(d["symbol"], pos["positionAmt"])
    if ok:
        add_log(f"平仓成功: {d['symbol']}", "ok")
    else:
        add_log(f"平仓失败: {res}", "err")
    return jsonify({"ok": ok, "msg": "" if ok else str(res)})


@app.route("/api/batch_close", methods=["POST"])
@login_required
def api_batch_close():
    """批量平仓"""
    if not state["connected"] or not engine:
        return jsonify({"ok": False, "msg": "未连接"})

    data = request.json
    positions = data.get("positions", [])

    if not positions:
        return jsonify({"ok": False, "msg": "未选择持仓"})

    results = []
    success_count = 0

    for pos in positions:
        symbol = pos.get("symbol")
        amt = pos.get("amt")

        if not symbol or not amt:
            results.append({"symbol": symbol or "未知", "ok": False, "msg": "参数错误"})
            continue

        try:
            ok, res = engine.close_position(symbol, amt)
            if ok:
                success_count += 1
                add_log(f"[批量平仓] {symbol} 成功", "ok")
                results.append({"symbol": symbol, "ok": True, "msg": ""})
            else:
                add_log(f"[批量平仓] {symbol} 失败: {res}", "err")
                results.append({"symbol": symbol, "ok": False, "msg": str(res)})
        except Exception as e:
            add_log(f"[批量平仓] {symbol} 异常: {str(e)}", "err")
            results.append({"symbol": symbol, "ok": False, "msg": str(e)})

    return jsonify({
        "ok": True,
        "results": results,
        "success": success_count,
        "total": len(positions)
    })


@app.route("/api/cancel", methods=["POST"])
@login_required
def api_cancel():
    if not state["connected"] or not engine:
        return jsonify({"ok": False, "msg": "未连接"})
    d = request.json
    ok, msg = engine.cancel_all(d["symbol"])
    add_log(msg, "ok" if ok else "err")
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/tickers")
@login_required
def api_tickers():
    tickers = get_tickers()
    result = []
    for t in tickers[:80]:
        result.append({
            "symbol": t["symbol"],
            "chg":    float(t["priceChangePercent"]),
            "vol":    float(t["quoteVolume"]) / 1e8,
            "price":  float(t["lastPrice"]),
        })
    return jsonify({"ok": True, "tickers": result})


@app.route("/api/scan/start", methods=["POST"])
@login_required
def api_scan_start():
    if state["scan_running"]:
        return jsonify({"ok": False, "msg": "扫描已在运行"})
    settings = request.json or {}
    state["scan_running"] = True
    state["next_scan_at"] = time.time()
    add_log("策略扫描已启动（每20分钟一轮，7策略混合，前300个交易对）", "ok")

    def _loop():
        cycle = 0
        while state["scan_running"]:
            cycle += 1
            add_log(f"第 {cycle} 轮扫描开始，获取行情数据...", "info")
            tickers = get_tickers()
            if tickers and state["scan_running"]:
                results = signal_engine.scan(tickers, top_n=300)
                min_score = int(settings.get("min_score", 2))
                qualified = [r for r in results if r["score"] >= min_score]

                # 保存上次结果并计算涨跌表现
                if state["scan_results"]:
                    price_map = {t["symbol"]: float(t["lastPrice"]) for t in tickers}
                    prev = []
                    for r in state["scan_results"]:
                        cur = price_map.get(r["symbol"])
                        if cur:
                            chg = (cur - r["price"]) / r["price"] * 100
                            pnl = chg * (1 if r["direction"] == "LONG" else -1)
                            prev.append({**r, "cur_price": cur, "price_chg": round(chg, 3), "pnl_pct": round(pnl, 3)})
                        else:
                            prev.append({**r, "cur_price": None, "price_chg": None, "pnl_pct": None})
                    state["prev_scan_results"] = prev
                    state["prev_scan_ts"] = state["scan_ts"]

                state["scan_results"] = qualified
                state["scan_ts"] = datetime.now().strftime("%H:%M:%S")
                add_log(f"第{cycle}轮完成: 扫描{min(300,len(tickers))}个，找到 {len(qualified)} 个 ≥{min_score}分 信号", "ok")

                # 全自动交易
                if settings.get("auto_trade"):
                    if not state["connected"] or not engine:
                        add_log("[自动] 未连接交易所，无法自动交易", "warn")
                    elif not qualified:
                        add_log("[自动] 本轮无符合条件的信号", "info")
                    else:
                        add_log(f"[自动] 开始处理 {len(qualified)} 个信号（取前3个）", "info")
                        open_syms = {p["symbol"] for p in engine.get_positions()}
                        traded_count = 0
                        for r in qualified[:3]:
                            if r["symbol"] in open_syms:
                                add_log(f"[自动] 跳过 {r['symbol']}：已有持仓", "warn")
                                continue
                            _, _, _, is_trading = engine.get_symbol_filters(r["symbol"])
                            if not is_trading:
                                add_log(f"[自动] 跳过 {r['symbol']}：交易对已关闭", "warn")
                                continue
                            side = "BUY" if r["direction"] == "LONG" else "SELL"
                            usdt = float(settings.get("trade_usdt", 100))
                            lev  = int(settings.get("leverage", 10))
                            sl   = float(settings.get("sl_pct", 2.0))
                            tp   = float(settings.get("tp_pct", 4.0))
                            qty  = (usdt * lev) / r["price"]
                            ok, res, sl_px, tp_px = engine.place_with_sltp(
                                r["symbol"], side, qty, lev, sl, tp)
                            cn = "做多" if side == "BUY" else "做空"
                            if ok:
                                traded_count += 1
                                add_log(f"[自动] {cn} {r['symbol']} 评分:{r['score']} 止损:{sl_px:.4f} 止盈:{tp_px:.4f}", "ok")
                            else:
                                add_log(f"[自动] 下单失败 {r['symbol']}: {res}", "err")
                        if traded_count == 0 and len([r for r in qualified[:3] if r["symbol"] not in open_syms]) == 0:
                            add_log("[自动] 前3个信号都已有持仓，跳过", "info")
            elif not tickers:
                add_log("获取行情失败，5秒后重试...", "warn")
                time.sleep(5)
                continue

            # 每20分钟扫描一轮
            state["next_scan_at"] = time.time() + 1200
            time.sleep(1200)

    threading.Thread(target=_loop, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/scan/stop", methods=["POST"])
@login_required
def api_scan_stop():
    state["scan_running"] = False
    add_log("策略扫描已停止", "warn")
    return jsonify({"ok": True})


@app.route("/api/scan/status")
@login_required
def api_scan_status():
    remaining = max(0, int(state["next_scan_at"] - time.time()))
    m, s = divmod(remaining, 60)
    return jsonify({
        "running":   state["scan_running"],
        "countdown": f"{m:02d}:{s:02d}",
        "results":      state["scan_results"],
        "ts":           state["scan_ts"],
        "prev_results": state["prev_scan_results"],
        "prev_ts":      state["prev_scan_ts"],
        "progress":  state["scan_progress"],
    })


@app.route("/api/log")
@login_required
def api_log():
    with _lock:
        return jsonify({"log": state["log"][:50]})


@app.route("/api/log/clear", methods=["POST"])
@login_required
def api_log_clear():
    with _lock:
        state["log"] = []
    return jsonify({"ok": True})


@app.route("/api/status")
@login_required
def api_status():
    return jsonify({
        "connected": state["connected"],
        "testnet":   state["testnet"],
    })


@app.route("/api/pnl/history")
@login_required
def api_pnl_history():
    """获取盈亏历史统计"""
    try:
        if not state["connected"]:
            return jsonify({"ok": False, "msg": "未连接"})

        filter_type = request.args.get("filter", "all")
        client = state["client"]

        from datetime import datetime, timedelta

        # 计算时间范围
        now = datetime.now()
        if filter_type == "today":
            start_time = datetime(now.year, now.month, now.day)
        elif filter_type == "week":
            start_time = now - timedelta(days=now.weekday())
            start_time = datetime(start_time.year, start_time.month, start_time.day)
        elif filter_type == "month":
            start_time = datetime(now.year, now.month, 1)
        else:  # all
            start_time = now - timedelta(days=30)  # 最近30天

        start_ms = int(start_time.timestamp() * 1000)

        history = []
        error_msg = None

        try:
            # 方法1：获取收益历史（最准确）
            try:
                income_history = client.futures_income_history(
                    incomeType="REALIZED_PNL",
                    startTime=start_ms,
                    limit=1000
                )

                log_msg("info", f"获取到 {len(income_history)} 条收益记录")

                # 按交易对和时间分组
                pnl_by_trade = {}
                for income in income_history:
                    if float(income["income"]) == 0:
                        continue

                    trade_id = income["tranId"]
                    if trade_id not in pnl_by_trade:
                        pnl_by_trade[trade_id] = {
                            "symbol": income["symbol"],
                            "pnl": 0,
                            "time": income["time"],
                        }
                    pnl_by_trade[trade_id]["pnl"] += float(income["income"])

                # 转换为交易记录
                for trade_id, data in pnl_by_trade.items():
                    if data["pnl"] == 0:
                        continue

                    history.append({
                        "symbol": data["symbol"],
                        "side": "多" if data["pnl"] > 0 else "空",
                        "open_price": 0,
                        "close_price": 0,
                        "qty": 0,
                        "pnl": data["pnl"],
                        "roe": 0,
                        "leverage": 0,
                        "open_time": datetime.fromtimestamp(data["time"] / 1000).strftime("%m-%d %H:%M"),
                        "close_time": datetime.fromtimestamp(data["time"] / 1000).strftime("%m-%d %H:%M"),
                    })

                log_msg("info", f"整理出 {len(history)} 条交易记录")

            except Exception as e:
                error_msg = f"获取收益历史失败: {str(e)}"
                log_msg("err", error_msg)

        except Exception as e:
            error_msg = f"数据处理失败: {str(e)}"
            log_msg("err", error_msg)

        # 计算统计数据
        stats = {
            "today": 0,
            "week": 0,
            "month": 0,
            "total": 0,
            "trade_count": len(history),
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0,
            "max_win": 0,
            "max_loss": 0,
        }

        today_start = datetime(now.year, now.month, now.day)
        week_start = now - timedelta(days=now.weekday())
        week_start = datetime(week_start.year, week_start.month, week_start.day)
        month_start = datetime(now.year, now.month, 1)

        for h in history:
            pnl = h["pnl"]
            stats["total"] += pnl

            if pnl > 0:
                stats["win_count"] += 1
                stats["max_win"] = max(stats["max_win"], pnl)
            elif pnl < 0:
                stats["loss_count"] += 1
                stats["max_loss"] = min(stats["max_loss"], pnl)

            # 按时间统计（简化处理，当前所有记录都算入）
            try:
                close_time = datetime.strptime(h["close_time"], "%m-%d %H:%M")
                close_time = close_time.replace(year=now.year)

                if close_time >= today_start:
                    stats["today"] += pnl
                if close_time >= week_start:
                    stats["week"] += pnl
                if close_time >= month_start:
                    stats["month"] += pnl
            except:
                # 时间解析失败，算入所有统计
                stats["today"] += pnl
                stats["week"] += pnl
                stats["month"] += pnl

        if stats["trade_count"] > 0:
            stats["win_rate"] = (stats["win_count"] / stats["trade_count"]) * 100

        # 按时间倒序排序（最新的在前）
        history.sort(key=lambda x: x["close_time"], reverse=True)

        log_msg("info", f"统计完成: 总盈亏 {stats['total']:.2f}, 交易 {stats['trade_count']} 次, 胜率 {stats['win_rate']:.1f}%")

        return jsonify({
            "ok": True,
            "stats": stats,
            "history": history[:100],  # 最多返回100条
            "error": error_msg,
        })

    except Exception as e:
        error_detail = str(e)
        log_msg("err", f"盈亏API异常: {error_detail}")
        return jsonify({"ok": False, "msg": error_detail})


# ─────────────────────────────────────────────

def main():
    if not BINANCE_OK:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "python-binance", "flask", "flask-login", "cryptography", "pillow"])
        print("安装完成，请重新运行")
        return

    print("=" * 60)
    print("  币安合约交易软件 v4.0  Web 版 (安全增强版)")
    print("  访问: http://localhost:5000")
    print("  默认账户: admin / admin123")
    print("  建议立即修改密码！")
    print("=" * 60)

    # 自动加载加密的配置
    global engine
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            encrypted_key = cfg.get("api_key", "")
            encrypted_secret = cfg.get("api_secret", "")
            testnet = cfg.get("testnet", True)

            if encrypted_key and encrypted_secret:
                key = api_encryptor.decrypt(encrypted_key)
                secret = api_encryptor.decrypt(encrypted_secret)
                if key and secret:
                    eng = FuturesEngine()
                    ok, _ = eng.connect(key, secret, testnet)
                    if ok:
                        engine = eng
                        state["connected"] = True
                        state["testnet"] = testnet
                        print("  ✓ 已自动连接币安API")
        except Exception as e:
            print(f"  警告: 加载配置失败 - {e}")

    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
