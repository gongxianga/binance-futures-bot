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

    def place_order(self, symbol, side, otype, qty, price=None, lev=10):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=lev)
            p = dict(symbol=symbol, side=side, type=otype, quantity=qty)
            if otype == "LIMIT" and price:
                p["price"] = price
                p["timeInForce"] = "GTC"
            return True, self.client.futures_create_order(**p)
        except Exception as e:
            return False, str(e)

    def place_with_sltp(self, symbol, side, qty, lev, sl_pct, tp_pct):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=lev)
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=qty)
            price = float(order.get("avgPrice") or 0) or self.get_price(symbol) or 0
            close_side = "SELL" if side == "BUY" else "BUY"
            if side == "BUY":
                sl_px = round(price * (1 - sl_pct / 100), 6)
                tp_px = round(price * (1 + tp_pct / 100), 6)
            else:
                sl_px = round(price * (1 + sl_pct / 100), 6)
                tp_px = round(price * (1 - tp_pct / 100), 6)
            if price > 0:
                self.client.futures_create_order(
                    symbol=symbol, side=close_side, type="STOP_MARKET",
                    stopPrice=sl_px, closePosition=True)
                self.client.futures_create_order(
                    symbol=symbol, side=close_side, type="TAKE_PROFIT_MARKET",
                    stopPrice=tp_px, closePosition=True)
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


def get_tickers():
    # 优先用已认证的客户端（绕过地理限制）
    if engine and state["connected"]:
        try:
            data = engine.client.futures_ticker()
            return sorted([d for d in data if d["symbol"].endswith("USDT")],
                          key=lambda x: float(x["quoteVolume"]), reverse=True)
        except Exception as e:
            add_log(f"客户端行情失败: {e}", "warn")

    # 回退：直接 HTTP
    for url in [
        "https://fapi.binance.com/fapi/v1/ticker/24hr",
        "https://fapi1.binance.com/fapi/v1/ticker/24hr",
        "https://fapi2.binance.com/fapi/v1/ticker/24hr",
    ]:
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return sorted([d for d in data if d["symbol"].endswith("USDT")],
                          key=lambda x: float(x["quoteVolume"]), reverse=True)
        except Exception:
            continue
    return []


# ─────────────────────────────────────────────
#  信号引擎
# ─────────────────────────────────────────────

class SignalEngine:
    KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"

    def _klines(self, symbol, interval="1h", limit=50):
        # 优先用已认证客户端
        if state["connected"]:
            try:
                return self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            except Exception:
                pass
        # 回退直接 HTTP
        try:
            url = f"{self.KLINE_URL}?symbol={symbol}&interval={interval}&limit={limit}"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        except Exception as e:
            add_log(f"K线获取失败 {symbol}: {e}", "warn")
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

    def analyze(self, symbol, ticker, all_tickers):
        klines = self._klines(symbol)
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

        # 量价突破
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0
        if vol_ratio >= 2.5:
            if chg_pct > 0: long_s += 1;  details["vol"] = {"text": "多", "cls": "green"}
            else:            short_s += 1; details["vol"] = {"text": "空", "cls": "red"}
        else:
            details["vol"] = {"text": "--", "cls": "muted"}

        # RSI
        rsi = self._rsi(closes)
        if rsi < 30:   long_s += 1;  details["rsi"] = {"text": f"{rsi:.0f}↑", "cls": "green"}
        elif rsi > 70: short_s += 1; details["rsi"] = {"text": f"{rsi:.0f}↓", "cls": "red"}
        else:                         details["rsi"] = {"text": f"{rsi:.0f}",  "cls": "muted"}

        # 动量排名
        by_chg = sorted(all_tickers, key=lambda x: float(x["priceChangePercent"]), reverse=True)
        by_vol = sorted(all_tickers, key=lambda x: float(x["quoteVolume"]), reverse=True)
        rank_c = next((i for i, t in enumerate(by_chg) if t["symbol"] == symbol), 999)
        rank_v = next((i for i, t in enumerate(by_vol) if t["symbol"] == symbol), 999)
        n = len(all_tickers)
        if rank_c < 5 and rank_v < 20:   long_s += 1;  details["mom"] = {"text": "强多", "cls": "green"}
        elif rank_c > n-6 and rank_v < 20: short_s += 1; details["mom"] = {"text": "强空", "cls": "red"}
        else:                               details["mom"] = {"text": "--",   "cls": "muted"}

        # 布林带
        upper, _, lower = self._bollinger(closes)
        if upper and lower:
            if cur_price > upper:   long_s += 1;  details["bb"] = {"text": "突破上轨", "cls": "green"}
            elif cur_price < lower: short_s += 1; details["bb"] = {"text": "跌破下轨", "cls": "red"}
            else:                                  details["bb"] = {"text": "--", "cls": "muted"}
        else:
            details["bb"] = {"text": "--", "cls": "muted"}

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

        with ThreadPoolExecutor(max_workers=20) as executor:
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

    eng = FuturesEngine()
    ok, msg = eng.connect(key, secret, testnet)
    if ok:
        engine = eng
        state["connected"] = True
        state["testnet"] = testnet
        # 加密保存 API 密钥
        encrypted_key = api_encryptor.encrypt(key)
        encrypted_secret = api_encryptor.encrypt(secret)
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "api_key": encrypted_key,
                "api_secret": encrypted_secret,
                "testnet": testnet
            }, f)
        add_log(f"连接成功 ({'测试网' if testnet else '正式网'})", "ok")
    else:
        add_log(f"连接失败: {msg}", "err")
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
    result = []
    for p in positions:
        amt = float(p["positionAmt"])
        result.append({
            "symbol":     p["symbol"],
            "side":       "多" if amt > 0 else "空",
            "amt":        abs(amt),
            "entry":      float(p["entryPrice"]),
            "mark":       float(p["markPrice"]),
            "pnl":        float(p["unRealizedProfit"]),
            "roe":        float(p.get("percentage", 0)),
            "leverage":   p.get("leverage", "--"),
            "positionAmt": p["positionAmt"],
        })
    return jsonify({"ok": True, "positions": result})


def _public_price(symbol):
    """无需认证，直接调用币安合约公开行情接口"""
    try:
        url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol.upper()}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            return float(data["price"])
    except Exception:
        try:
            url = f"https://testnet.binancefuture.com/fapi/v1/ticker/price?symbol={symbol.upper()}"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                return float(data["price"])
        except Exception:
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
    add_log("策略扫描已启动（实时模式，前300个交易对）", "ok")

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
                state["scan_results"] = qualified
                state["scan_ts"] = datetime.now().strftime("%H:%M:%S")
                add_log(f"第{cycle}轮完成: 扫描{min(300,len(tickers))}个，找到 {len(qualified)} 个 ≥{min_score}分 信号", "ok")

                # 全自动交易
                if settings.get("auto_trade") and qualified and state["connected"] and engine:
                    for r in qualified[:3]:
                        side = "BUY" if r["direction"] == "LONG" else "SELL"
                        usdt = float(settings.get("trade_usdt", 100))
                        lev  = int(settings.get("leverage", 10))
                        sl   = float(settings.get("sl_pct", 2.0))
                        tp   = float(settings.get("tp_pct", 4.0))
                        qty  = round(usdt / r["price"], 3)
                        ok, res, sl_px, tp_px = engine.place_with_sltp(
                            r["symbol"], side, qty, lev, sl, tp)
                        cn = "做多" if side == "BUY" else "做空"
                        if ok:
                            add_log(f"[自动] {cn} {r['symbol']} 评分:{r['score']} 止损:{sl_px:.4f} 止盈:{tp_px:.4f}", "ok")
                        else:
                            add_log(f"[自动] 下单失败 {r['symbol']}: {res}", "err")
            elif not tickers:
                add_log("获取行情失败，5秒后重试...", "warn")
                time.sleep(5)
                continue

            # 每轮扫描完立即开始下一轮，短暂休息3秒避免触发限频
            state["next_scan_at"] = time.time() + 3
            time.sleep(3)

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
        "results":   state["scan_results"],
        "ts":        state["scan_ts"],
        "progress":  state["scan_progress"],
    })


@app.route("/api/log")
@login_required
def api_log():
    with _lock:
        return jsonify({"log": state["log"][:50]})


@app.route("/api/status")
@login_required
def api_status():
    return jsonify({
        "connected": state["connected"],
        "testnet":   state["testnet"],
    })


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
