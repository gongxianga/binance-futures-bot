#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安合约交易软件 v3.0  —  混合策略扫描版
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import time
import json
import os
import sys
import math
import urllib.request
from datetime import datetime

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MPL_OK = True
except ImportError:
    MPL_OK = False

try:
    from binance.client import Client
    BINANCE_OK = True
except ImportError:
    BINANCE_OK = False


# ─────────────────────────────────────────────
#  系统主题检测
# ─────────────────────────────────────────────

def _is_dark():
    if sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return v == 0
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            import subprocess
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                               capture_output=True, text=True)
            return "Dark" in r.stdout
        except Exception:
            pass
    return False

DARK = _is_dark()

C = dict(
    bg="#1c1c1e",   bg2="#2c2c2e",  bg3="#3a3a3c",
    text="#f2f2f7", muted="#8e8e93", border="#48484a",
    accent="#0a84ff", green="#30d158", red="#ff453a", yellow="#ffd60a",
    chart_bg="#1c1c1e", chart_grid="#3a3a3c",
) if DARK else dict(
    bg="#f2f2f7",   bg2="#ffffff",  bg3="#e5e5ea",
    text="#1c1c1e", muted="#8e8e93", border="#c7c7cc",
    accent="#007aff", green="#34c759", red="#ff3b30", yellow="#ff9500",
    chart_bg="#f9f9fb", chart_grid="#d1d1d6",
)

SCORE_COLORS = ["", C["yellow"], C["yellow"], "#ff8c00", C["red"]]  # 1~4分颜色


def _btn(parent, text, cmd, color=None, **kw):
    bg = color or C["accent"]
    return tk.Button(parent, text=text, command=cmd, bg=bg, fg="white",
                     relief="flat", activebackground=bg, activeforeground="white",
                     font=("Segoe UI", 10), cursor="hand2", **kw)


def _entry(parent, var, w=12, state="normal"):
    return tk.Entry(parent, textvariable=var, width=w, state=state,
                    bg=C["bg3"], fg=C["text"], insertbackground=C["text"],
                    relief="flat", highlightthickness=1,
                    highlightbackground=C["border"], highlightcolor=C["accent"],
                    font=("Segoe UI", 9))


def _lbl(parent, text="", var=None, fg=None, size=9, bold=False):
    f = ("Segoe UI", size, "bold") if bold else ("Segoe UI", size)
    kw = dict(bg=C["bg2"], fg=fg or C["text"], font=f)
    if var:
        return tk.Label(parent, textvariable=var, **kw)
    return tk.Label(parent, text=text, **kw)


# ─────────────────────────────────────────────
#  交易引擎
# ─────────────────────────────────────────────

class FuturesEngine:
    CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

    def __init__(self):
        self.client: "Client | None" = None

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
        """下单并同时挂止损/止盈"""
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=lev)
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=qty)

            # 获取成交均价
            price = float(order.get("avgPrice") or 0)
            if price == 0:
                price = self.get_price(symbol) or 0

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

    def save_config(self, key, secret, testnet):
        with open(self.CONFIG, "w") as f:
            json.dump({"api_key": key, "api_secret": secret, "testnet": testnet}, f)

    def load_config(self):
        if os.path.exists(self.CONFIG):
            with open(self.CONFIG) as f:
                return json.load(f)
        return {}

    @staticmethod
    def fetch_tickers():
        try:
            req = urllib.request.Request("https://fapi.binance.com/fapi/v1/ticker/24hr")
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            return sorted([d for d in data if d["symbol"].endswith("USDT")],
                          key=lambda x: float(x["quoteVolume"]), reverse=True)
        except Exception:
            return []


# ─────────────────────────────────────────────
#  信号引擎（4策略混合）
# ─────────────────────────────────────────────

class SignalEngine:
    KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"

    def _fetch_klines(self, symbol, interval="1h", limit=50):
        try:
            url = f"{self.KLINE_URL}?symbol={symbol}&interval={interval}&limit={limit}"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read())
        except Exception:
            return []

    @staticmethod
    def _rsi(closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        if al == 0:
            return 100.0
        return 100 - (100 / (1 + ag / al))

    @staticmethod
    def _bollinger(closes, period=20):
        if len(closes) < period:
            return None, None, None
        d = closes[-period:]
        sma = sum(d) / period
        std = (sum((x - sma) ** 2 for x in d) / period) ** 0.5
        return sma + 2 * std, sma, sma - 2 * std

    def analyze(self, symbol, ticker, all_tickers):
        klines = self._fetch_klines(symbol)
        if not klines:
            return None

        closes  = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        cur_price = closes[-1]
        cur_vol   = volumes[-1]
        avg_vol   = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else (sum(volumes) / max(len(volumes), 1))
        chg_pct   = float(ticker["priceChangePercent"])

        long_s = short_s = 0
        details = {}

        # ── 策略1：量价突破 ──
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0
        if vol_ratio >= 2.5:
            if chg_pct > 0:
                long_s += 1;  details["vol"] = ("多", C["green"])
            else:
                short_s += 1; details["vol"] = ("空", C["red"])
        else:
            details["vol"] = ("--", C["muted"])

        # ── 策略2：RSI ──
        rsi = self._rsi(closes)
        if rsi < 30:
            long_s += 1;  details["rsi"] = (f"{rsi:.0f}↑", C["green"])
        elif rsi > 70:
            short_s += 1; details["rsi"] = (f"{rsi:.0f}↓", C["red"])
        else:
            details["rsi"] = (f"{rsi:.0f}", C["muted"])

        # ── 策略3：动量排名 ──
        by_chg = sorted(all_tickers, key=lambda x: float(x["priceChangePercent"]), reverse=True)
        by_vol = sorted(all_tickers, key=lambda x: float(x["quoteVolume"]), reverse=True)
        rank_chg = next((i for i, t in enumerate(by_chg) if t["symbol"] == symbol), 999)
        rank_vol = next((i for i, t in enumerate(by_vol) if t["symbol"] == symbol), 999)
        n = len(all_tickers)
        if rank_chg < 5 and rank_vol < 20:
            long_s += 1;  details["mom"] = ("强多", C["green"])
        elif rank_chg > n - 6 and rank_vol < 20:
            short_s += 1; details["mom"] = ("强空", C["red"])
        else:
            details["mom"] = ("--", C["muted"])

        # ── 策略4：布林带突破 ──
        upper, _, lower = self._bollinger(closes)
        if upper and lower:
            if cur_price > upper:
                long_s += 1;  details["bb"] = ("突破上轨", C["green"])
            elif cur_price < lower:
                short_s += 1; details["bb"] = ("跌破下轨", C["red"])
            else:
                details["bb"] = ("--", C["muted"])
        else:
            details["bb"] = ("--", C["muted"])

        score = max(long_s, short_s)
        if score == 0:
            return None

        direction = "LONG" if long_s >= short_s else "SHORT"
        return {
            "symbol":    symbol,
            "score":     score,
            "direction": direction,
            "price":     cur_price,
            "chg":       chg_pct,
            "details":   details,
        }

    def scan(self, tickers, top_n=30, progress_cb=None):
        results = []
        top = tickers[:top_n]
        for i, t in enumerate(top):
            if progress_cb:
                progress_cb(i + 1, len(top))
            r = self.analyze(t["symbol"], t, tickers)
            if r:
                results.append(r)
        results.sort(key=lambda x: x["score"], reverse=True)
        return results


# ─────────────────────────────────────────────
#  雷达图面板
# ─────────────────────────────────────────────

class RadarPanel:
    def __init__(self, parent, on_select):
        self.on_select = on_select
        self._syms, self._xs, self._ys = [], [], []
        self._signal_map: dict = {}

        outer = tk.Frame(parent, bg=C["bg2"])
        outer.pack(fill=tk.BOTH, expand=True)

        hdr = tk.Frame(outer, bg=C["bg2"])
        hdr.pack(fill=tk.X, padx=10, pady=(10, 4))
        tk.Label(hdr, text="市场雷达", bg=C["bg2"], fg=C["muted"],
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.lbl_ts = tk.Label(hdr, text="", bg=C["bg2"], fg=C["muted"],
                               font=("Segoe UI", 8))
        self.lbl_ts.pack(side=tk.RIGHT)
        _btn(hdr, "刷新", self.refresh, color=C["bg3"],
             fg=C["text"], padx=8, pady=2).pack(side=tk.RIGHT, padx=(0, 6))

        if MPL_OK:
            self.fig = Figure(figsize=(5.2, 4.8), dpi=92, tight_layout=True)
            self.fig.patch.set_facecolor(C["chart_bg"])
            self.ax = self.fig.add_subplot(111)
            self._style_ax()
            self.canvas = FigureCanvasTkAgg(self.fig, master=outer)
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
            self.canvas.mpl_connect("button_press_event", self._on_click)
            threading.Thread(target=self._load, daemon=True).start()
        else:
            tk.Label(outer, text="需安装 matplotlib\npip install matplotlib",
                     bg=C["bg2"], fg=C["muted"], font=("Segoe UI", 10)).pack(expand=True)

    def _style_ax(self):
        ax = self.ax
        ax.set_facecolor(C["chart_bg"])
        ax.tick_params(colors=C["muted"], labelsize=7.5)
        for sp in ax.spines.values():
            sp.set_color(C["border"])
        ax.set_xlabel("24h 涨跌幅 %", color=C["muted"], fontsize=8.5)
        ax.set_ylabel("成交额 (亿USDT)", color=C["muted"], fontsize=8.5)
        ax.axvline(0, color=C["border"], lw=0.8, ls="--", alpha=0.5)
        ax.set_title("点击选择交易对  |  ★=有信号", color=C["muted"], fontsize=8, pad=4)
        ax.grid(True, color=C["chart_grid"], lw=0.5, alpha=0.4)

    def _load(self):
        tickers = FuturesEngine.fetch_tickers()
        if tickers:
            self.fig.canvas.get_tk_widget().after(0, lambda: self._render(tickers))

    def refresh(self):
        if MPL_OK:
            threading.Thread(target=self._load, daemon=True).start()

    def update_signals(self, signal_map: dict):
        """由扫描器调用，传入 {symbol: score} 以在雷达上标注"""
        self._signal_map = signal_map
        if MPL_OK:
            threading.Thread(target=self._load, daemon=True).start()

    def _render(self, tickers):
        top = tickers[:60]
        self._syms, self._xs, self._ys = [], [], []
        xs, ys, sizes, colors = [], [], [], []
        max_vol = max(float(t["quoteVolume"]) for t in top) or 1

        for t in top:
            chg = float(t["priceChangePercent"])
            vol = float(t["quoteVolume"]) / 1e8
            self._syms.append(t["symbol"])
            xs.append(chg); ys.append(vol)
            self._xs.append(chg); self._ys.append(vol)
            sizes.append(max(20, min(600, float(t["quoteVolume"]) / max_vol * 600)))
            r = max(0, 1 - chg / 20) if chg > 0 else 1.0
            g = 1.0 if chg > 0 else max(0, 1 + chg / 20)
            colors.append((r, g, 0.2, 0.75))

        self.ax.clear()
        self._style_ax()
        self.ax.scatter(xs, ys, s=sizes, c=colors, edgecolors="none", zorder=3)

        # 标注前20成交量的币名
        for i, t in enumerate(top[:20]):
            sym = t["symbol"].replace("USDT", "")
            score = self._signal_map.get(t["symbol"], 0)
            label = f"★{sym}" if score >= 2 else sym
            color = SCORE_COLORS[min(score, 4)] if score > 0 else C["text"]
            self.ax.annotate(label, (xs[i], ys[i]),
                             fontsize=7.5 if score == 0 else 8.5,
                             color=color, alpha=0.9 if score > 0 else 0.7,
                             fontweight="bold" if score >= 2 else "normal",
                             textcoords="offset points", xytext=(3, 3))

        self.canvas.draw()
        self.lbl_ts.config(text=f"更新: {datetime.now().strftime('%H:%M:%S')}")

    def _on_click(self, event):
        if event.inaxes != self.ax or not self._syms:
            return
        dists = [(math.hypot(event.xdata - x, (event.ydata - y) * 0.05), i)
                 for i, (x, y) in enumerate(zip(self._xs, self._ys))]
        nearest = min(dists)[1]
        self.on_select(self._syms[nearest])


# ─────────────────────────────────────────────
#  主界面
# ─────────────────────────────────────────────

class App:
    SCAN_INTERVAL = 30 * 60  # 30分钟

    def __init__(self):
        self.engine  = FuturesEngine()
        self.scanner = SignalEngine()
        self.connected = False
        self._stop = threading.Event()
        self._scan_running = False
        self._next_scan_at = 0

        self.root = tk.Tk()
        self.root.title("币安合约交易软件 v3.0")
        self.root.geometry("1400x880")
        self.root.minsize(1200, 750)
        self.root.configure(bg=C["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        self._setup_theme()
        self._build_header()
        self._build_body()
        self._load_config()

    # ── Theme ────────────────────────────────

    def _setup_theme(self):
        try:
            import sv_ttk
            sv_ttk.use_dark_theme() if DARK else sv_ttk.use_light_theme()
            return
        except ImportError:
            pass
        s = ttk.Style()
        if sys.platform == "win32":    s.theme_use("vista")
        elif sys.platform == "darwin": s.theme_use("aqua")
        else:                          s.theme_use("clam")
        s.configure("Treeview", rowheight=26, font=("Segoe UI", 9))
        s.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        s.configure("TNotebook.Tab", padding=[14, 6], font=("Segoe UI", 10))

    # ── Header ──────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=C["bg2"],
                       highlightthickness=1, highlightbackground=C["border"])
        hdr.pack(fill=tk.X)

        tk.Label(hdr, text="  BINANCE FUTURES BOT  v3.0",
                 bg=C["bg2"], fg=C["accent"],
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT, padx=12, pady=10)

        cf = tk.Frame(hdr, bg=C["bg2"])
        cf.pack(side=tk.RIGHT, padx=12, pady=8)

        self.v_key    = tk.StringVar()
        self.v_secret = tk.StringVar()
        self.v_test   = tk.BooleanVar(value=True)

        for col, (ltext, var, w) in enumerate([("API Key", self.v_key, 18),
                                                ("Secret",  self.v_secret, 18)]):
            tk.Label(cf, text=ltext, bg=C["bg2"], fg=C["muted"],
                     font=("Segoe UI", 9)).grid(row=0, column=col*2, padx=(0, 4))
            e = tk.Entry(cf, textvariable=var, width=w, show="*",
                         bg=C["bg3"], fg=C["text"], insertbackground=C["text"],
                         relief="flat", highlightthickness=1,
                         highlightbackground=C["border"], highlightcolor=C["accent"],
                         font=("Segoe UI", 9))
            e.grid(row=0, column=col*2+1, padx=(0, 10))

        tk.Checkbutton(cf, text="测试网", variable=self.v_test,
                       bg=C["bg2"], fg=C["text"], selectcolor=C["bg3"],
                       activebackground=C["bg2"],
                       font=("Segoe UI", 9)).grid(row=0, column=4, padx=(0, 10))

        self.btn_conn = _btn(cf, "连  接", self._connect, padx=18, pady=5)
        self.btn_conn.grid(row=0, column=5, padx=(0, 10))
        self.lbl_status = tk.Label(cf, text="● 未连接", bg=C["bg2"],
                                   fg=C["red"], font=("Segoe UI", 9))
        self.lbl_status.grid(row=0, column=6)

    # ── Body ────────────────────────────────

    def _build_body(self):
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 左侧雷达
        left = tk.Frame(body, bg=C["bg2"],
                        highlightthickness=1, highlightbackground=C["border"],
                        width=530)
        left.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 6))
        left.pack_propagate(False)
        self.radar = RadarPanel(left, on_select=self._on_radar_select)

        # 右侧
        right = tk.Frame(body, bg=C["bg"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Notebook（交易 / 策略扫描）
        nb = ttk.Notebook(right)
        nb.pack(fill=tk.BOTH, expand=False)

        trade_tab   = tk.Frame(nb, bg=C["bg2"])
        scanner_tab = tk.Frame(nb, bg=C["bg2"])
        nb.add(trade_tab,   text="  交 易  ")
        nb.add(scanner_tab, text="  策略扫描  ")

        self._build_trade_tab(trade_tab)
        self._build_scanner_tab(scanner_tab)

        self._build_positions_panel(right)
        self._build_log_panel(right)

    # ── 交易 Tab ─────────────────────────────

    def _build_trade_tab(self, parent):
        # 账户
        acc = tk.Frame(parent, bg=C["bg2"])
        acc.pack(fill=tk.X, padx=14, pady=(12, 0))

        tk.Label(acc, text="账户总览", bg=C["bg2"], fg=C["muted"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        row_f = tk.Frame(acc, bg=C["bg2"])
        row_f.pack(fill=tk.X)
        self.v_avail  = tk.StringVar(value="-- USDT")
        self.v_wallet = tk.StringVar(value="-- USDT")
        self.v_pnl    = tk.StringVar(value="-- USDT")
        for i, (ltext, var) in enumerate([("可用余额", self.v_avail),
                                           ("钱包余额", self.v_wallet),
                                           ("未实现盈亏", self.v_pnl)]):
            col = tk.Frame(row_f, bg=C["bg2"])
            col.grid(row=0, column=i, padx=(0, 22))
            tk.Label(col, text=ltext, bg=C["bg2"], fg=C["muted"],
                     font=("Segoe UI", 8)).pack(anchor="w")
            lv = tk.Label(col, textvariable=var, bg=C["bg2"], fg=C["green"],
                          font=("Segoe UI", 11, "bold"))
            lv.pack(anchor="w")
            if ltext == "未实现盈亏":
                self.lbl_pnl = lv

        _btn(acc, "刷新账户", self._refresh_account, color=C["bg3"],
             fg=C["text"], padx=10, pady=3).pack(anchor="e", pady=(6, 0))

        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, padx=14, pady=8)

        # 下单
        f = tk.Frame(parent, bg=C["bg2"])
        f.pack(fill=tk.X, padx=14)

        tk.Label(f, text="手动下单", bg=C["bg2"], fg=C["muted"],
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w",
                                                     columnspan=4, pady=(0, 8))

        self.v_sym   = tk.StringVar(value="BTCUSDT")
        self.v_qty   = tk.StringVar(value="0.001")
        self.v_lev   = tk.StringVar(value="10")
        self.v_otype = tk.StringVar(value="MARKET")
        self.v_lpx   = tk.StringVar()
        self.v_cpx   = tk.StringVar(value="--")

        def frow(ltext, widget, r):
            tk.Label(f, text=ltext, bg=C["bg2"], fg=C["muted"],
                     font=("Segoe UI", 9)).grid(row=r, column=0, sticky="w", pady=4)
            widget.grid(row=r, column=1, sticky="w", padx=(10, 0), pady=4)

        # 交易对 + 价格
        sym_row = tk.Frame(f, bg=C["bg2"])
        frow("交易对", sym_row, 1)
        _entry(sym_row, self.v_sym, w=10).pack(side=tk.LEFT)
        tk.Label(sym_row, textvariable=self.v_cpx, bg=C["bg2"],
                 fg=C["yellow"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(8, 0))

        cb = ttk.Combobox(f, textvariable=self.v_otype,
                          values=["MARKET", "LIMIT"], width=11, state="readonly")
        cb.bind("<<ComboboxSelected>>", self._on_otype_change)
        frow("订单类型", cb, 2)
        frow("杠 杆 倍 数", _entry(f, self.v_lev), 3)
        frow("数    量",    _entry(f, self.v_qty), 4)
        self.ent_lp = _entry(f, self.v_lpx, state="disabled")
        frow("限    价", self.ent_lp, 5)

        _btn(parent, "获取当前价格", self._get_price, color=C["bg3"],
             fg=C["text"], padx=10, pady=3).pack(anchor="e", padx=14, pady=(4, 0))

        brow = tk.Frame(parent, bg=C["bg2"])
        brow.pack(fill=tk.X, padx=14, pady=10)
        _btn(brow, "做  多  (买入)", lambda: self._manual_order("BUY"),
             color=C["green"], padx=10, pady=10,
             font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        _btn(brow, "做  空  (卖出)", lambda: self._manual_order("SELL"),
             color=C["red"], padx=10, pady=10,
             font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT, expand=True, fill=tk.X)

        _btn(parent, "撤销当前交易对所有挂单", self._cancel_orders,
             color=C["bg3"], fg=C["yellow"], padx=10, pady=4).pack(pady=(0, 10))

    # ── 策略扫描 Tab ─────────────────────────

    def _build_scanner_tab(self, parent):
        # ─ 设置区 ─
        settings = tk.Frame(parent, bg=C["bg2"])
        settings.pack(fill=tk.X, padx=14, pady=12)

        tk.Label(settings, text="扫描设置", bg=C["bg2"], fg=C["muted"],
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, columnspan=6,
                                                     sticky="w", pady=(0, 8))

        self.v_min_score  = tk.StringVar(value="2")
        self.v_auto_trade = tk.BooleanVar(value=False)
        self.v_trade_usdt = tk.StringVar(value="100")
        self.v_scan_lev   = tk.StringVar(value="10")
        self.v_sl_pct     = tk.StringVar(value="2.0")
        self.v_tp_pct     = tk.StringVar(value="4.0")

        def sf(ltext, var, r, c, w=7):
            tk.Label(settings, text=ltext, bg=C["bg2"], fg=C["muted"],
                     font=("Segoe UI", 9)).grid(row=r, column=c, sticky="e",
                                                 padx=(12, 4), pady=4)
            _entry(settings, var, w=w).grid(row=r, column=c+1, sticky="w", pady=4)

        sf("最低信号分(1-4)", self.v_min_score,  1, 0, 4)
        sf("每笔金额(USDT)", self.v_trade_usdt, 1, 2)
        sf("杠    杆",       self.v_scan_lev,   1, 4, 4)
        sf("止 损 %",        self.v_sl_pct,      2, 0, 4)
        sf("止 盈 %",        self.v_tp_pct,      2, 2)

        # 执行模式
        mode_f = tk.Frame(settings, bg=C["bg2"])
        mode_f.grid(row=2, column=4, columnspan=2, sticky="w", padx=(12, 0))
        tk.Label(mode_f, text="执行模式:", bg=C["bg2"], fg=C["muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        tk.Radiobutton(mode_f, text="手动确认", variable=self.v_auto_trade, value=False,
                       bg=C["bg2"], fg=C["text"], selectcolor=C["bg3"],
                       activebackground=C["bg2"],
                       font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(6, 0))
        tk.Radiobutton(mode_f, text="全自动", variable=self.v_auto_trade, value=True,
                       bg=C["bg2"], fg=C["text"], selectcolor=C["bg3"],
                       activebackground=C["bg2"],
                       font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 0))

        # 控制按钮行
        ctrl = tk.Frame(settings, bg=C["bg2"])
        ctrl.grid(row=3, column=0, columnspan=6, sticky="w", pady=(8, 0))

        self.btn_scan_start = _btn(ctrl, "▶  开始扫描", self._start_scan,
                                   color=C["green"], padx=14, pady=5)
        self.btn_scan_start.pack(side=tk.LEFT)
        self.btn_scan_stop = _btn(ctrl, "■  停止", self._stop_scan,
                                  color=C["red"], padx=14, pady=5, state="disabled")
        self.btn_scan_stop.pack(side=tk.LEFT, padx=(8, 0))
        self.lbl_scan_status = tk.Label(ctrl, text="  状态: 未启动",
                                        bg=C["bg2"], fg=C["muted"],
                                        font=("Segoe UI", 9))
        self.lbl_scan_status.pack(side=tk.LEFT, padx=(14, 0))

        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, padx=14, pady=(0, 8))

        # ─ 结果表格 ─
        res_hdr = tk.Frame(parent, bg=C["bg2"])
        res_hdr.pack(fill=tk.X, padx=14)
        tk.Label(res_hdr, text="扫描结果", bg=C["bg2"], fg=C["muted"],
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.lbl_scan_ts = tk.Label(res_hdr, text="", bg=C["bg2"],
                                    fg=C["muted"], font=("Segoe UI", 8))
        self.lbl_scan_ts.pack(side=tk.RIGHT)

        cols = ("symbol", "score", "dir", "vol", "rsi", "mom", "bb", "price")
        self.scan_tree = ttk.Treeview(parent, columns=cols, show="headings",
                                      height=7, selectmode="browse")
        for col, head, w in [
            ("symbol","交易对",90), ("score","评分",50), ("dir","方向",55),
            ("vol","量价突破",75), ("rsi","RSI",65), ("mom","动量排名",75),
            ("bb","布林带",75), ("price","当前价",90),
        ]:
            self.scan_tree.heading(col, text=head)
            self.scan_tree.column(col, width=w, anchor="center", minwidth=40)

        vsb = ttk.Scrollbar(parent, orient="vertical", command=self.scan_tree.yview)
        self.scan_tree.configure(yscrollcommand=vsb.set)
        self.scan_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                            padx=(14, 0), pady=(4, 6))
        vsb.pack(side=tk.LEFT, fill=tk.Y, pady=(4, 6), padx=(0, 6))

        _btn(parent, "交易选中信号", self._trade_signal,
             color=C["accent"], padx=14, pady=6).pack(anchor="e", padx=14, pady=(0, 10))

    # ── 持仓面板 ─────────────────────────────

    def _build_positions_panel(self, parent):
        card = tk.Frame(parent, bg=C["bg2"],
                        highlightthickness=1, highlightbackground=C["border"])
        card.pack(fill=tk.BOTH, expand=True, pady=6)

        hdr = tk.Frame(card, bg=C["bg2"])
        hdr.pack(fill=tk.X, padx=14, pady=(10, 4))
        tk.Label(hdr, text="当前持仓", bg=C["bg2"], fg=C["muted"],
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        _btn(hdr, "刷新", self._refresh_positions, color=C["bg3"],
             fg=C["text"], padx=8, pady=2).pack(side=tk.RIGHT)

        cols = ("symbol","side","qty","entry","mark","pnl","roe","lev")
        self.pos_tree = ttk.Treeview(card, columns=cols, show="headings",
                                     height=4, selectmode="browse")
        for col, head, w in [
            ("symbol","交易对",88), ("side","方向",50), ("qty","数量",75),
            ("entry","开仓价",90), ("mark","标记价",90),
            ("pnl","未实现盈亏",105), ("roe","收益%",70), ("lev","杠杆",50),
        ]:
            self.pos_tree.heading(col, text=head)
            self.pos_tree.column(col, width=w, anchor="center", minwidth=40)

        vsb = ttk.Scrollbar(card, orient="vertical", command=self.pos_tree.yview)
        self.pos_tree.configure(yscrollcommand=vsb.set)
        self.pos_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                           padx=(14, 0), pady=(0, 6))
        vsb.pack(side=tk.LEFT, fill=tk.Y, pady=(0, 6), padx=(0, 6))

        _btn(card, "平仓 (选中)", self._close_pos, color=C["red"],
             padx=12, pady=5).pack(anchor="e", padx=14, pady=(0, 10))

    # ── 日志面板 ─────────────────────────────

    def _build_log_panel(self, parent):
        card = tk.Frame(parent, bg=C["bg2"],
                        highlightthickness=1, highlightbackground=C["border"])
        card.pack(fill=tk.X)

        hdr = tk.Frame(card, bg=C["bg2"])
        hdr.pack(fill=tk.X, padx=14, pady=(8, 2))
        tk.Label(hdr, text="操作日志", bg=C["bg2"], fg=C["muted"],
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        _btn(hdr, "清除", self._clear_log, color=C["bg3"],
             fg=C["muted"], padx=8, pady=2).pack(side=tk.RIGHT)

        log_bg = "#0f0f0f" if DARK else "#fafafa"
        self.log_box = scrolledtext.ScrolledText(
            card, height=5, bg=log_bg, fg=C["muted"],
            font=("Consolas", 9), relief="flat", padx=8, pady=6, state="disabled")
        self.log_box.pack(fill=tk.X, padx=14, pady=(0, 10))
        for tag, color in [("ok", C["green"]), ("err", C["red"]),
                            ("warn", C["yellow"]), ("info", C["muted"])]:
            self.log_box.tag_configure(tag, foreground=color)

    # ── 日志辅助 ─────────────────────────────

    def log(self, msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, f"[{ts}]  ", "info")
        self.log_box.insert(tk.END, msg + "\n", level)
        self.log_box.see(tk.END)
        self.log_box.config(state="disabled")

    def _clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state="disabled")

    # ── 雷达回调 ─────────────────────────────

    def _on_radar_select(self, symbol):
        self.v_sym.set(symbol)
        self.log(f"已选择: {symbol}", "info")
        if self.connected:
            self._get_price()

    # ── 连接 ─────────────────────────────────

    def _connect(self):
        key = self.v_key.get().strip()
        sec = self.v_secret.get().strip()
        if not key or not sec:
            messagebox.showerror("错误", "请填写 API Key 和 Secret")
            return
        self.btn_conn.config(state="disabled")
        self.log("正在连接...", "info")

        def _do():
            ok, msg = self.engine.connect(key, sec, self.v_test.get())
            def _ui():
                if ok:
                    self.connected = True
                    self.lbl_status.config(text="● 已连接", fg=C["green"])
                    self.log(f"连接成功 ({'测试网' if self.v_test.get() else '正式网'})", "ok")
                    self.engine.save_config(key, sec, self.v_test.get())
                    self._refresh_account()
                    self._refresh_positions()
                    self._start_auto_refresh()
                else:
                    self.lbl_status.config(text="● 连接失败", fg=C["red"])
                    self.log(f"连接失败: {msg}", "err")
                    messagebox.showerror("连接失败", msg)
                self.btn_conn.config(state="normal")
            self.root.after(0, _ui)
        threading.Thread(target=_do, daemon=True).start()

    # ── 账户 ─────────────────────────────────

    def _refresh_account(self):
        if not self.connected:
            return
        def _do():
            bal = self.engine.get_balance()
            acc = self.engine.get_account()
            def _ui():
                if bal:
                    self.v_avail.set(f"{float(bal.get('availableBalance',0)):.2f} USDT")
                    self.v_wallet.set(f"{float(bal.get('balance',0)):.2f} USDT")
                if acc:
                    pnl = float(acc.get("totalUnrealizedProfit", 0))
                    self.v_pnl.set(f"{pnl:+.2f} USDT")
                    self.lbl_pnl.config(fg=C["green"] if pnl >= 0 else C["red"])
            self.root.after(0, _ui)
        threading.Thread(target=_do, daemon=True).start()

    # ── 持仓 ─────────────────────────────────

    def _refresh_positions(self):
        if not self.connected:
            return
        def _do():
            positions = self.engine.get_positions()
            def _ui():
                for item in self.pos_tree.get_children():
                    self.pos_tree.delete(item)
                if not positions:
                    self.pos_tree.insert("", tk.END, values=("暂无持仓","","","","","","",""))
                    return
                for p in positions:
                    amt = float(p["positionAmt"])
                    pnl = float(p["unRealizedProfit"])
                    self.pos_tree.insert("", tk.END,
                                         tags=("up" if pnl >= 0 else "dn",),
                                         values=(
                                             p["symbol"], "多" if amt > 0 else "空",
                                             f"{abs(amt):.4f}",
                                             f"{float(p['entryPrice']):.4f}",
                                             f"{float(p['markPrice']):.4f}",
                                             f"{pnl:+.2f}",
                                             f"{float(p.get('percentage',0)):+.2f}%",
                                             f"{p.get('leverage','--')}x",
                                         ))
                self.pos_tree.tag_configure("up", foreground=C["green"])
                self.pos_tree.tag_configure("dn", foreground=C["red"])
            self.root.after(0, _ui)
        threading.Thread(target=_do, daemon=True).start()

    # ── 价格 ─────────────────────────────────

    def _get_price(self):
        if not self.connected:
            self.log("请先连接", "warn")
            return
        sym = self.v_sym.get().strip().upper()
        def _do():
            p = self.engine.get_price(sym)
            self.root.after(0, lambda: (
                self.v_cpx.set(f"{p:.4f}") if p else self.log(f"获取 {sym} 价格失败", "err")))
        threading.Thread(target=_do, daemon=True).start()

    # ── 手动下单 ─────────────────────────────

    def _on_otype_change(self, _=None):
        self.ent_lp.config(state="normal" if self.v_otype.get() == "LIMIT" else "disabled")

    def _manual_order(self, side):
        if not self.connected:
            messagebox.showwarning("提示", "请先连接到币安")
            return
        sym = self.v_sym.get().strip().upper()
        try:
            qty = float(self.v_qty.get())
            lev = int(self.v_lev.get())
        except ValueError:
            messagebox.showerror("错误", "数量/杠杆格式有误")
            return
        price = None
        if self.v_otype.get() == "LIMIT":
            try:
                price = float(self.v_lpx.get())
            except ValueError:
                messagebox.showerror("错误", "请输入有效限价")
                return
        cn = "做多" if side == "BUY" else "做空"
        if not messagebox.askyesno("确认下单",
                f"交易对: {sym}\n方向: {cn}\n类型: {self.v_otype.get()}\n数量: {qty}\n杠杆: {lev}x"):
            return
        def _do():
            ok, res = self.engine.place_order(sym, side, self.v_otype.get(), qty, price, lev)
            def _ui():
                if ok:
                    self.log(f"下单成功  {cn} {sym}  ID:{res.get('orderId','N/A')}", "ok")
                    self._refresh_account()
                    self._refresh_positions()
                else:
                    self.log(f"下单失败: {res}", "err")
                    messagebox.showerror("下单失败", str(res))
            self.root.after(0, _ui)
        threading.Thread(target=_do, daemon=True).start()

    # ── 平仓 ─────────────────────────────────

    def _close_pos(self):
        if not self.connected:
            messagebox.showwarning("提示", "请先连接")
            return
        sel = self.pos_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请选择要平仓的持仓")
            return
        sym = self.pos_tree.item(sel[0])["values"][0]
        if sym == "暂无持仓":
            return
        if not messagebox.askyesno("确认平仓", f"确认平仓 {sym}？"):
            return
        def _do():
            positions = self.engine.get_positions()
            pos = next((p for p in positions if p["symbol"] == sym), None)
            if not pos:
                self.root.after(0, lambda: self.log(f"未找到 {sym} 持仓", "warn"))
                return
            ok, res = self.engine.close_position(sym, pos["positionAmt"])
            def _ui():
                if ok:
                    self.log(f"平仓成功: {sym}", "ok")
                    self._refresh_account(); self._refresh_positions()
                else:
                    self.log(f"平仓失败: {res}", "err")
                    messagebox.showerror("平仓失败", str(res))
            self.root.after(0, _ui)
        threading.Thread(target=_do, daemon=True).start()

    # ── 撤单 ─────────────────────────────────

    def _cancel_orders(self):
        if not self.connected:
            self.log("请先连接", "warn"); return
        sym = self.v_sym.get().strip().upper()
        if not messagebox.askyesno("确认撤单", f"撤销 {sym} 所有挂单？"):
            return
        def _do():
            ok, msg = self.engine.cancel_all(sym)
            self.root.after(0, lambda: self.log(msg, "ok" if ok else "err"))
        threading.Thread(target=_do, daemon=True).start()

    # ── 策略扫描 ─────────────────────────────

    def _start_scan(self):
        if self._scan_running:
            return
        self._scan_running = True
        self._stop.clear()
        self.btn_scan_start.config(state="disabled")
        self.btn_scan_stop.config(state="normal")
        self.log("策略扫描已启动，间隔 30 分钟", "ok")
        threading.Thread(target=self._scan_loop, daemon=True).start()
        threading.Thread(target=self._countdown_loop, daemon=True).start()

    def _stop_scan(self):
        self._scan_running = False
        self.btn_scan_start.config(state="normal")
        self.btn_scan_stop.config(state="disabled")
        self.lbl_scan_status.config(text="  状态: 已停止")
        self.log("策略扫描已停止", "warn")

    def _scan_loop(self):
        while self._scan_running:
            self._next_scan_at = time.time() + self.SCAN_INTERVAL
            self.root.after(0, lambda: self.lbl_scan_status.config(
                text="  状态: 扫描中..."))
            self._do_scan()
            # 等待下次
            if self._scan_running:
                self._next_scan_at = time.time() + self.SCAN_INTERVAL

    def _countdown_loop(self):
        while self._scan_running:
            remaining = max(0, int(self._next_scan_at - time.time()))
            m, s = divmod(remaining, 60)
            txt = f"  状态: 下次扫描 {m:02d}:{s:02d}"
            self.root.after(0, lambda t=txt: self.lbl_scan_status.config(text=t))
            time.sleep(1)

    def _do_scan(self):
        self.root.after(0, lambda: self.log("开始扫描市场信号...", "info"))

        def progress(cur, total):
            pct = int(cur / total * 100)
            self.root.after(0, lambda p=pct: self.lbl_scan_status.config(
                text=f"  状态: 扫描中 {p}%"))

        tickers = FuturesEngine.fetch_tickers()
        if not tickers:
            self.root.after(0, lambda: self.log("获取行情数据失败", "err"))
            return

        results = self.scanner.scan(tickers, top_n=30, progress_cb=progress)

        try:
            min_score = int(self.v_min_score.get())
        except ValueError:
            min_score = 2

        qualified = [r for r in results if r["score"] >= min_score]
        signal_map = {r["symbol"]: r["score"] for r in results}

        def _ui():
            # 更新扫描结果表
            for item in self.scan_tree.get_children():
                self.scan_tree.delete(item)

            if not qualified:
                self.scan_tree.insert("", tk.END,
                    values=("暂无符合条件的信号", "", "", "", "", "", "", ""))
            else:
                for r in qualified:
                    d = r["details"]
                    self.scan_tree.insert("", tk.END,
                        tags=(f"s{r['score']}",),
                        values=(
                            r["symbol"],
                            "★" * r["score"],
                            "多▲" if r["direction"] == "LONG" else "空▼",
                            d["vol"][0], d["rsi"][0],
                            d["mom"][0], d["bb"][0],
                            f"{r['price']:.4f}",
                        ))

            for s, color in [(1, C["yellow"]), (2, C["yellow"]),
                              (3, "#ff8c00"), (4, C["red"])]:
                self.scan_tree.tag_configure(f"s{s}", foreground=color)

            self.lbl_scan_ts.config(
                text=f"最近扫描: {datetime.now().strftime('%H:%M:%S')}  共 {len(qualified)} 个信号")
            self.log(f"扫描完成: 找到 {len(qualified)} 个 ≥{min_score}分 信号", "ok")

            # 更新雷达图高亮
            self.radar.update_signals(signal_map)

            # 自动交易
            if self.v_auto_trade.get() and qualified and self.connected:
                for r in qualified[:3]:  # 最多3个
                    self._auto_trade(r)

        self.root.after(0, _ui)

    def _auto_trade(self, signal: dict):
        """全自动下单（含止损/止盈）"""
        try:
            usdt   = float(self.v_trade_usdt.get())
            lev    = int(self.v_scan_lev.get())
            sl_pct = float(self.v_sl_pct.get())
            tp_pct = float(self.v_tp_pct.get())
        except ValueError:
            self.root.after(0, lambda: self.log("参数格式有误，请检查设置", "err"))
            return

        sym   = signal["symbol"]
        side  = "BUY" if signal["direction"] == "LONG" else "SELL"
        price = signal["price"]
        qty   = round(usdt / price, 3)
        if qty <= 0:
            return

        def _do():
            ok, res, sl, tp = self.engine.place_with_sltp(sym, side, qty, lev, sl_pct, tp_pct)
            def _ui():
                cn = "做多" if side == "BUY" else "做空"
                if ok:
                    self.log(
                        f"[自动] {cn} {sym}  评分:{signal['score']}  "
                        f"数量:{qty}  止损:{sl:.4f}  止盈:{tp:.4f}", "ok")
                    self._refresh_account()
                    self._refresh_positions()
                else:
                    self.log(f"[自动] 下单失败 {sym}: {res}", "err")
            self.root.after(0, _ui)
        threading.Thread(target=_do, daemon=True).start()

    def _trade_signal(self):
        """手动交易扫描结果中选中的信号"""
        if not self.connected:
            messagebox.showwarning("提示", "请先连接到币安")
            return
        sel = self.scan_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个信号")
            return
        vals = self.scan_tree.item(sel[0])["values"]
        if not vals or vals[0] in ("暂无符合条件的信号",):
            return

        sym  = vals[0]
        dirn = vals[2]
        side = "BUY" if "多" in str(dirn) else "SELL"
        cn   = "做多" if side == "BUY" else "做空"

        try:
            usdt   = float(self.v_trade_usdt.get())
            lev    = int(self.v_scan_lev.get())
            sl_pct = float(self.v_sl_pct.get())
            tp_pct = float(self.v_tp_pct.get())
        except ValueError:
            messagebox.showerror("错误", "请检查金额/杠杆/止损/止盈格式")
            return

        price = self.engine.get_price(sym) or 0
        qty   = round(usdt / price, 3) if price > 0 else 0

        if not messagebox.askyesno("确认交易",
                f"交易对: {sym}\n方向: {cn}\n金额: {usdt} USDT\n"
                f"杠杆: {lev}x\n止损: {sl_pct}%\n止盈: {tp_pct}%"):
            return

        def _do():
            ok, res, sl, tp = self.engine.place_with_sltp(sym, side, qty, lev, sl_pct, tp_pct)
            def _ui():
                if ok:
                    self.log(f"下单成功  {cn} {sym}  止损:{sl:.4f}  止盈:{tp:.4f}", "ok")
                    self._refresh_account(); self._refresh_positions()
                else:
                    self.log(f"下单失败: {res}", "err")
                    messagebox.showerror("下单失败", str(res))
            self.root.after(0, _ui)
        threading.Thread(target=_do, daemon=True).start()

    # ── 自动刷新 ─────────────────────────────

    def _start_auto_refresh(self):
        def _loop():
            while not self._stop.wait(15):
                if self.connected:
                    self._refresh_account()
                    self._refresh_positions()
        def _radar_loop():
            while not self._stop.wait(60):
                self.radar.refresh()
        threading.Thread(target=_loop,       daemon=True).start()
        threading.Thread(target=_radar_loop, daemon=True).start()

    # ── 配置 ─────────────────────────────────

    def _load_config(self):
        cfg = self.engine.load_config()
        if cfg:
            self.v_key.set(cfg.get("api_key", ""))
            self.v_secret.set(cfg.get("api_secret", ""))
            self.v_test.set(cfg.get("testnet", True))
            self.log("已加载保存的 API 配置", "info")
        else:
            self.log("欢迎使用 币安合约交易软件 v3.0", "info")
            self.log("提示: 建议先勾选「测试网」验证功能", "warn")

    def _quit(self):
        self._stop.set()
        self._scan_running = False
        self.connected = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ─────────────────────────────────────────────

def main():
    missing = []
    if not BINANCE_OK: missing.append("python-binance")
    if not MPL_OK:     missing.append("matplotlib numpy")
    if missing:
        import subprocess
        print(f"正在安装依赖: {' '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)
        print("安装完成，请重新运行")
        return
    App().run()


if __name__ == "__main__":
    main()
