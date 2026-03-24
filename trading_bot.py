#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ULTIMATE TRADING BOT - WITH TELEGRAM & GOOGLE AI
"""

import os
import sys
import time
import sqlite3
import threading
import requests
import json
import logging
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION - KEYS ADDED
# ============================================================================

# Telegram Keys
TELEGRAM_TOKEN = "8789790689:AAGfkoE1lcjn-97IxXMbiM4fL15IsA0niTo"
TELEGRAM_ADMIN_ID = 7890333339

# Google AI Key
GOOGLE_AI_KEY = "AIzaSyBvUNwz2YaJ1LNAO84OCoQlGYiK2JDIyXI"

# Trading Settings
PAPER_MODE = True
INITIAL_CAPITAL = 30000
RISK_PER_TRADE = 1.0
MAX_OPEN_TRADES = 2
ACTIVE_SYMBOLS = ["NIFTY", "BANKNIFTY"]
MIN_CONFIDENCE = 70
PREDICTION_INTERVAL = 60
PROFIT_TARGET_PCT = 2.5
INITIAL_STOP_ATR = 1.5
TRAILING_STOP_ATR = 2.0

# Database
DB_FILE = "trading_data.db"

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# IMPORTS
# ============================================================================

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
except ImportError:
    logger.info("Installing required libraries...")
    os.system("pip install yfinance pandas numpy requests")
    import numpy as np
    import pandas as pd
    import yfinance as yf

# ============================================================================
# GOOGLE AI FUNCTIONS
# ============================================================================

class GoogleAI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"
    
    def analyze_market(self, symbol, price, rsi, volume, sentiment):
        """Get AI analysis for market"""
        try:
            prompt = f"""
            Analyze this market data:
            Symbol: {symbol}
            Price: ₹{price}
            RSI: {rsi}
            Volume: {volume}
            Market Sentiment: {sentiment}
            
            Give trading recommendation (BUY/SELL/HOLD) with confidence score.
            Also give brief reason.
            """
            
            url = f"{self.base_url}?key={self.api_key}"
            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }]
            }
            
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if 'candidates' in data:
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    return text
            return None
        except Exception as e:
            logger.error(f"Google AI error: {e}")
            return None
    
    def get_signal(self, symbol, price, rsi, volume):
        """Get trading signal from AI"""
        try:
            prompt = f"""
            Based on technical indicators:
            - {symbol} current price: ₹{price}
            - RSI: {rsi} (30-70 is normal)
            - Volume: {volume}
            
            Should I BUY, SELL, or HOLD? Respond with just one word: BUY, SELL, or HOLD.
            """
            
            url = f"{self.base_url}?key={self.api_key}"
            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }]
            }
            
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if 'candidates' in data:
                    text = data['candidates'][0]['content']['parts'][0]['text'].strip().upper()
                    if "BUY" in text:
                        return "BUY"
                    elif "SELL" in text:
                        return "SELL"
            return "HOLD"
        except:
            return "HOLD"

# Initialize Google AI
google_ai = GoogleAI(GOOGLE_AI_KEY) if GOOGLE_AI_KEY != "YOUR_GOOGLE_KEY_HERE" else None

# ============================================================================
# DATABASE
# ============================================================================

DB_LOCK = threading.Lock()

def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DB_FILE)
        conn.execute('''CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT, symbol TEXT, direction TEXT, strategy TEXT,
            entry_price REAL, exit_price REAL, quantity INTEGER, lot_size INTEGER,
            entry_time TIMESTAMP, exit_time TIMESTAMP, pnl REAL, pnl_pct REAL,
            status TEXT, exit_reason TEXT, confidence REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS capital_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            capital REAL, change REAL, reason TEXT
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS ai_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, analysis TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized")

init_db()

# ============================================================================
# TRADING STATE
# ============================================================================

class TradingState:
    def __init__(self):
        self.auto_trading = True
        self.paper_mode = PAPER_MODE
        self.capital = INITIAL_CAPITAL
        self.open_trades = []
        self.trade_counter = 0
        self.active_symbols = ACTIVE_SYMBOLS.copy()
        self.risk_per_trade = RISK_PER_TRADE
        self.min_confidence = MIN_CONFIDENCE
        self.start_time = datetime.now()
        self.running = True
        self.consecutive_losses = 0
        self._load_capital_history()
    
    def _load_capital_history(self):
        try:
            with DB_LOCK:
                conn = sqlite3.connect(DB_FILE)
                cur = conn.cursor()
                cur.execute("SELECT capital FROM capital_history ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                if row:
                    self.capital = row[0]
                    logger.info(f"💰 Loaded capital: ₹{self.capital:,.2f}")
                conn.close()
        except:
            pass
    
    def update_capital(self, amount, reason=""):
        old = self.capital
        self.capital += amount
        try:
            with DB_LOCK:
                conn = sqlite3.connect(DB_FILE)
                conn.execute("INSERT INTO capital_history (capital, change, reason) VALUES (?, ?, ?)",
                            (self.capital, amount, reason))
                conn.commit()
                conn.close()
        except:
            pass
        logger.info(f"💰 Capital: ₹{old:,.2f} → ₹{self.capital:,.2f} ({amount:+,.2f})")
        return self.capital

state = TradingState()

# ============================================================================
# MARKET FUNCTIONS
# ============================================================================

_price_cache = {}
_cache_time = {}

def get_live_price(symbol):
    now = time.time()
    if symbol in _price_cache and now - _cache_time.get(symbol, 0) < 5:
        return _price_cache[symbol]
    try:
        tickers = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
        ticker = tickers.get(symbol, symbol)
        df = yf.download(ticker, period="1d", interval="1m", progress=False)
        if df is not None and len(df) > 0:
            price = float(df['Close'].iloc[-1])
            _price_cache[symbol] = price
            _cache_time[symbol] = now
            return price
    except:
        pass
    return None

def get_historical_data(symbol, days=2):
    try:
        tickers = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
        ticker = tickers.get(symbol, symbol)
        df = yf.download(ticker, period=f"{days}d", interval="5m", progress=False)
        if df is None or len(df) < 20:
            return None
        df = df.copy()
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))
        high_low = df['High'] - df['Low']
        high_close = (df['High'] - df['Close'].shift()).abs()
        low_close = (df['Low'] - df['Close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(window=14).mean()
        return df.dropna()
    except:
        return None

def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0)
    market_close = now.replace(hour=15, minute=30, second=0)
    return market_open <= now <= market_close

# ============================================================================
# TRADING STRATEGIES
# ============================================================================

class Strategies:
    @staticmethod
    def momentum_buy(df, last, price):
        if last.get('MA5', 0) > last.get('MA20', 0) and last.get('RSI', 50) > 50:
            return ("BUY", 70)
        return None
    
    @staticmethod
    def momentum_sell(df, last, price):
        if last.get('MA5', 0) < last.get('MA20', 0) and last.get('RSI', 50) < 50:
            return ("SELL", 70)
        return None
    
    @staticmethod
    def breakout_buy(df, last, price):
        high_20 = df['High'].iloc[-20:].max() if len(df) >= 20 else price
        if price > high_20 * 1.005:
            return ("BUY", 75)
        return None
    
    @staticmethod
    def breakout_sell(df, last, price):
        low_20 = df['Low'].iloc[-20:].min() if len(df) >= 20 else price
        if price < low_20 * 0.995:
            return ("SELL", 75)
        return None
    
    @staticmethod
    def rsi_oversold(df, last, price):
        if last.get('RSI', 50) < 30:
            return ("BUY", 72)
        return None
    
    @staticmethod
    def rsi_overbought(df, last, price):
        if last.get('RSI', 50) > 70:
            return ("SELL", 72)
        return None
    
    @staticmethod
    def moving_average_cross(df, last, price):
        if len(df) >= 2:
            if last.get('MA5', 0) > last.get('MA20', 0) and df['MA5'].iloc[-2] <= df['MA20'].iloc[-2]:
                return ("BUY", 80)
            if last.get('MA5', 0) < last.get('MA20', 0) and df['MA5'].iloc[-2] >= df['MA20'].iloc[-2]:
                return ("SELL", 80)
        return None
    
    @staticmethod
    def google_ai_signal(df, last, price):
        if google_ai and 'RSI' in last:
            try:
                signal = google_ai.get_signal(
                    symbol=last.get('symbol', 'NIFTY'),
                    price=price,
                    rsi=last.get('RSI', 50),
                    volume=last.get('volume', 1000000)
                )
                if signal == "BUY":
                    return ("BUY", 75)
                elif signal == "SELL":
                    return ("SELL", 75)
            except:
                pass
        return None

# ============================================================================
# PREDICTION ENGINE
# ============================================================================

class PredictionEngine:
    def predict(self, symbol, df):
        if df is None or len(df) < 20:
            return None
        
        try:
            last = df.iloc[-1]
            price = float(last['Close'])
            atr = float(last['ATR']) if 'ATR' in last else price * 0.005
            
            strategies = [
                Strategies.momentum_buy, Strategies.momentum_sell,
                Strategies.breakout_buy, Strategies.breakout_sell,
                Strategies.rsi_oversold, Strategies.rsi_overbought,
                Strategies.moving_average_cross,
                Strategies.google_ai_signal,
            ]
            
            buy_signals = sell_signals = 0
            buy_weights = sell_weights = 0
            
            for strategy in strategies:
                try:
                    result = strategy(df, last, price)
                    if result:
                        direction, confidence = result
                        if direction == "BUY":
                            buy_signals += 1
                            buy_weights += confidence
                        elif direction == "SELL":
                            sell_signals += 1
                            sell_weights += confidence
                except:
                    continue
            
            if buy_signals > sell_signals:
                direction = "BUY"
                confidence = min(95, (buy_weights / max(buy_signals, 1)) + (buy_signals * 2))
            elif sell_signals > buy_signals:
                direction = "SELL"
                confidence = min(95, (sell_weights / max(sell_signals, 1)) + (sell_signals * 2))
            else:
                direction = "HOLD"
                confidence = 50
            
            return {
                'symbol': symbol, 'direction': direction, 'confidence': confidence,
                'price': price, 'atr': atr,
                'buy_signals': buy_signals, 'sell_signals': sell_signals
            }
        except:
            return None

predictor = PredictionEngine()

# ============================================================================
# TRADE CLASS
# ============================================================================

class Trade:
    def __init__(self, trade_id, symbol, direction, strategy, entry_price, quantity, confidence, atr):
        self.trade_id = trade_id
        self.symbol = symbol
        self.direction = direction
        self.strategy = strategy
        self.entry_price = entry_price
        self.quantity = quantity
        self.lot_size = 75 if symbol == "NIFTY" else 15
        self.entry_time = datetime.now()
        self.confidence = confidence
        self.status = "OPEN"
        self.exit_reason = None
        self.exit_price = None
        self.pnl = None
        self.highest = entry_price
        self.lowest = entry_price
        self.stop = entry_price - (atr * INITIAL_STOP_ATR) if direction == "BUY" else entry_price + (atr * INITIAL_STOP_ATR)
    
    def update_stop(self, current, atr):
        if self.direction == "BUY":
            if current > self.highest:
                self.highest = current
                self.stop = self.highest - (atr * TRAILING_STOP_ATR)
            if current <= self.stop:
                self.exit_reason = "STOP LOSS"
                return True
        else:
            if current < self.lowest:
                self.lowest = current
                self.stop = self.lowest + (atr * TRAILING_STOP_ATR)
            if current >= self.stop:
                self.exit_reason = "STOP LOSS"
                return True
        
        pnl_pct = ((current - self.entry_price) / self.entry_price) * 100
        if self.direction == "SELL":
            pnl_pct = -pnl_pct
        if pnl_pct >= PROFIT_TARGET_PCT:
            self.exit_reason = "TARGET HIT"
            return True
        return False
    
    def close(self, exit_price, pnl_pct):
        self.exit_price = exit_price
        self.exit_time = datetime.now()
        self.status = "CLOSED"
        if self.direction == "BUY":
            self.pnl = (exit_price - self.entry_price) * self.lot_size * self.quantity
        else:
            self.pnl = (self.entry_price - exit_price) * self.lot_size * self.quantity
        return self.pnl

# ============================================================================
# TELEGRAM BOT
# ============================================================================

class TelegramBot:
    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.admin_id = TELEGRAM_ADMIN_ID
        self.offset = 0
        self.session = requests.Session()
    
    def send(self, chat_id, text):
        if not self.token or self.token == "YOUR_BOT_TOKEN_HERE":
            logger.info(f"📱 Would send: {text[:100]}")
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            self.session.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=3)
        except Exception as e:
            logger.error(f"Send error: {e}")
    
    def get_updates(self):
        if not self.token or self.token == "YOUR_BOT_TOKEN_HERE":
            return []
        try:
            url = f"https://api.telegram.org/bot{self.token}/getUpdates"
            params = {"offset": self.offset, "timeout": 5}
            resp = self.session.get(url, params=params, timeout=8)
            data = resp.json()
            if data.get("ok"):
                updates = []
                for update in data.get("result", []):
                    self.offset = update["update_id"] + 1
                    updates.append(update)
                return updates
            return []
        except:
            return []
    
    def handle_command(self, chat_id, cmd):
        cmd_lower = cmd.lower()
        
        if cmd_lower == "/start":
            self.send(chat_id, self.start_msg())
        elif cmd_lower == "/status":
            self.send(chat_id, self.status_msg())
        elif cmd_lower == "/balance":
            self.send(chat_id, self.balance_msg())
        elif cmd_lower == "/pnl":
            self.send(chat_id, self.pnl_msg())
        elif cmd_lower == "/positions":
            self.send(chat_id, self.positions_msg())
        elif cmd_lower == "/market":
            self.send(chat_id, self.market_msg())
        elif cmd_lower == "/signals":
            self.send(chat_id, self.signals_msg())
        elif cmd_lower == "/ai":
            self.send(chat_id, self.ai_analysis_msg())
        elif cmd_lower == "/auto_on":
            state.auto_trading = True
            self.send(chat_id, "✅ Auto trading ON")
        elif cmd_lower == "/auto_off":
            state.auto_trading = False
            self.send(chat_id, "⏹️ Auto trading OFF")
        elif cmd_lower == "/mode":
            state.paper_mode = not state.paper_mode
            self.send(chat_id, f"✅ Mode: {'PAPER' if state.paper_mode else 'LIVE'}")
        elif cmd_lower == "/help":
            self.send(chat_id, self.help_msg())
        elif cmd_lower.startswith("/buy"):
            parts = cmd.split()
            if len(parts) >= 2:
                self.manual_trade(chat_id, parts[1].upper(), int(parts[2]) if len(parts) > 2 else 1, "BUY")
        elif cmd_lower.startswith("/sell"):
            parts = cmd.split()
            if len(parts) >= 2:
                self.manual_trade(chat_id, parts[1].upper(), int(parts[2]) if len(parts) > 2 else 1, "SELL")
        else:
            self.send(chat_id, f"❌ Unknown: {cmd}\nType /help")
    
    def manual_trade(self, chat_id, symbol, quantity, direction):
        if not is_market_open():
            self.send(chat_id, "❌ Market closed")
            return
        price = get_live_price(symbol)
        if not price:
            self.send(chat_id, f"❌ No price for {symbol}")
            return
        if len(state.open_trades) >= MAX_OPEN_TRADES:
            self.send(chat_id, "❌ Max trades reached")
            return
        
        qty = min(quantity, 2)
        state.trade_counter += 1
        trade = Trade(f"T{state.trade_counter}", symbol, direction, "MANUAL", price, qty, 100, price * 0.005)
        state.open_trades.append(trade)
        
        with DB_LOCK:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("INSERT INTO trades (trade_id, symbol, direction, strategy, entry_price, quantity, lot_size, entry_time, status, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (trade.trade_id, trade.symbol, trade.direction, trade.strategy, trade.entry_price, trade.quantity, trade.lot_size, trade.entry_time, "OPEN", trade.confidence))
            conn.commit()
            conn.close()
        
        self.send(chat_id, f"✅ {direction} {symbol} @ ₹{price:.0f}")
    
    def start_msg(self):
        return f"""🤖 <b>TRADING BOT</b>
━━━━━━━━━━━━━━━━━━━━━━━━━
⚙️ Auto: {'ON' if state.auto_trading else 'OFF'}
📝 Mode: {'PAPER' if state.paper_mode else 'LIVE'}
💰 Capital: ₹{state.capital:,.2f}
📊 Open: {len(state.open_trades)}
🤖 AI: {'Active' if google_ai else 'Inactive'}

<b>Commands:</b>
/status - System status
/balance - Balance
/positions - Open trades
/market - Market data
/signals - Trading signals
/ai - AI Analysis
/auto_on - Auto ON
/auto_off - Auto OFF
/help - All commands"""
    
    def help_msg(self):
        return """🤖 <b>COMMANDS</b>
━━━━━━━━━━━━━━━━━━━━━━━━━

📊 <b>Info</b>
/start - Menu
/status - System status
/balance - Balance
/pnl - P&L summary

📈 <b>Trading</b>
/positions - Open trades
/market - Market data
/signals - Trading signals
/ai - AI market analysis

⚙️ <b>Control</b>
/auto_on - Auto trading ON
/auto_off - Auto trading OFF
/mode - Toggle Paper/Live

💼 <b>Manual</b>
/buy SYM QTY - Buy
/sell SYM QTY - Sell

❓ <b>Help</b>
/help - This help"""
    
    def status_msg(self):
        return f"""📊 STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━
Auto: {'ON' if state.auto_trading else 'OFF'}
Mode: {'PAPER' if state.paper_mode else 'LIVE'}
Capital: ₹{state.capital:,.2f}
Risk: {state.risk_per_trade}%
Open: {len(state.open_trades)}
AI: {'Active' if google_ai else 'Inactive'}"""
    
    def balance_msg(self):
        pnl = state.capital - INITIAL_CAPITAL
        return f"""💰 BALANCE
━━━━━━━━━━━━━━━━━━━━━━━━━
Balance: ₹{state.capital:,.2f}
P&L: ₹{pnl:+,.2f}
Return: {(pnl/INITIAL_CAPITAL*100):+.2f}%"""
    
    def pnl_msg(self):
        pnl = state.capital - INITIAL_CAPITAL
        return f"""📊 P&L
━━━━━━━━━━━━━━━━━━━━━━━━━
Total: ₹{pnl:+,.2f}
Return: {(pnl/INITIAL_CAPITAL*100):+.2f}%
Trades: {state.trade_counter}"""
    
    def positions_msg(self):
        if not state.open_trades:
            return "📭 No open positions"
        msg = f"📈 OPEN ({len(state.open_trades)})\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for i, t in enumerate(state.open_trades, 1):
            current = get_live_price(t.symbol)
            msg += f"\n{i}. {t.trade_id} | {t.symbol} - {t.direction}"
            msg += f"\n   Entry: ₹{t.entry_price:.0f} | Qty: {t.quantity} lot"
            if current:
                pnl_pct = ((current - t.entry_price) / t.entry_price * 100) if t.direction == "BUY" else ((t.entry_price - current) / t.entry_price * 100)
                msg += f"\n   Current: ₹{current:.0f} | P&L: {pnl_pct:+.1f}%"
        return msg
    
    def market_msg(self):
        msg = f"📈 MARKET\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for sym in ACTIVE_SYMBOLS:
            price = get_live_price(sym)
            if price:
                msg += f"\n{sym}: ₹{price:,.0f}"
            else:
                msg += f"\n{sym}: N/A"
        return msg
    
    def signals_msg(self):
        if not is_market_open():
            return "🎯 SIGNALS\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\nMarket Closed"
        msg = "🎯 SIGNALS\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        found = False
        for sym in ACTIVE_SYMBOLS:
            df = get_historical_data(sym)
            if df:
                pred = predictor.predict(sym, df)
                if pred and pred['confidence'] >= state.min_confidence:
                    found = True
                    emoji = "🟢" if pred['direction'] == "BUY" else "🔴"
                    msg += f"\n{emoji} {sym}: {pred['direction']} ({pred['confidence']:.0f}%)"
        if not found:
            msg += "\nNo strong signals"
        return msg
    
    def ai_analysis_msg(self):
        if not google_ai:
            return "🤖 GOOGLE AI\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\nAI not configured"
        
        msg = "🤖 GOOGLE AI ANALYSIS\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for sym in ACTIVE_SYMBOLS:
            price = get_live_price(sym)
            if price:
                df = get_historical_data(sym)
                rsi = df['RSI'].iloc[-1] if df is not None else 50
                analysis = google_ai.analyze_market(sym, price, rsi, 1000000, "Neutral")
                if analysis:
                    msg += f"\n{sym}:\n{analysis[:200]}...\n"
        return msg
    
    def run(self):
        logger.info(f"🤖 Bot Started | Capital: ₹{state.capital:,.2f} | AI: {'Active' if google_ai else 'Inactive'}")
        while state.running:
            try:
                updates = self.get_updates()
                for update in updates:
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        if msg["from"]["id"] != self.admin_id:
                            self.send(chat_id, "❌ Unauthorized")
                            continue
                        text = msg.get("text", "")
                        if text.startswith("/"):
                            self.handle_command(chat_id, text)
                time.sleep(0.3)
            except KeyboardInterrupt:
                state.running = False
                break
            except Exception as e:
                logger.error(f"Bot error: {e}")
                time.sleep(1)

# ============================================================================
# AUTO TRADING ENGINE
# ============================================================================

class AutoEngine:
    def __init__(self):
        self.last_trade = {}
    
    def start(self):
        logger.info("🚀 Auto Trading Started")
        threading.Thread(target=self._prediction_loop, daemon=True).start()
        threading.Thread(target=self._monitor_loop, daemon=True).start()
    
    def _prediction_loop(self):
        while state.running:
            try:
                if state.auto_trading and is_market_open():
                    for sym in ACTIVE_SYMBOLS:
                        df = get_historical_data(sym)
                        if df:
                            pred = predictor.predict(sym, df)
                            if pred and pred['confidence'] >= state.min_confidence:
                                self._maybe_trade(pred)
                time.sleep(PREDICTION_INTERVAL)
            except Exception as e:
                logger.error(f"Pred error: {e}")
                time.sleep(5)
    
    def _maybe_trade(self, pred):
        sym = pred['symbol']
        if sym in self.last_trade and (time.time() - self.last_trade[sym]) < 300:
            return
        if any(t.symbol == sym for t in state.open_trades):
            return
        if len(state.open_trades) >= MAX_OPEN_TRADES:
            return
        
        price = get_live_price(sym) or pred['price']
        qty = max(1, min(2, int(state.capital * state.risk_per_trade / 100 / 375)))
        
        state.trade_counter += 1
        trade = Trade(f"T{state.trade_counter}", sym, pred['direction'], "AUTO", price, qty, pred['confidence'], pred['atr'])
        state.open_trades.append(trade)
        self.last_trade[sym] = time.time()
        
        with DB_LOCK:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("INSERT INTO trades (trade_id, symbol, direction, strategy, entry_price, quantity, lot_size, entry_time, status, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (trade.trade_id, trade.symbol, trade.direction, trade.strategy, trade.entry_price, trade.quantity, trade.lot_size, trade.entry_time, "OPEN", trade.confidence))
            conn.commit()
            conn.close()
        
        logger.info(f"🎯 AUTO: {pred['direction']} {sym} @ ₹{price:.0f}")
    
    def _monitor_loop(self):
        while state.running:
            try:
                for trade in state.open_trades[:]:
                    current = get_live_price(trade.symbol)
                    if current:
                        df = get_historical_data(trade.symbol)
                        atr = df['ATR'].iloc[-1] if df is not None else current * 0.005
                        if trade.update_stop(current, atr):
                            if trade.direction == "BUY":
                                pnl_pct = ((current - trade.entry_price) / trade.entry_price) * 100
                            else:
                                pnl_pct = -((current - trade.entry_price) / trade.entry_price) * 100
                            pnl = trade.close(current, pnl_pct)
                            
                            state.update_capital(pnl, f"Auto {trade.exit_reason}")
                            state.open_trades.remove(trade)
                            
                            with DB_LOCK:
                                conn = sqlite3.connect(DB_FILE)
                                conn.execute("UPDATE trades SET exit_price=?, exit_time=?, pnl=?, pnl_pct=?, status='CLOSED', exit_reason=? WHERE trade_id=?",
                                            (trade.exit_price, trade.exit_time, trade.pnl, pnl_pct, trade.exit_reason, trade.trade_id))
                                conn.commit()
                                conn.close()
                            
                            logger.info(f"📍 EXIT: {trade.symbol} - {trade.exit_reason} - ₹{trade.pnl:+,.0f}")
                time.sleep(2)
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                time.sleep(1)

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("""
╔═══════════════════════════════════════════════════════════════════╗
║     TRADING BOT - WITH TELEGRAM & GOOGLE AI                       ║
║     =====================================                        ║
║     ✅ Telegram Commands                                         ║
║     ✅ Google AI Integration                                     ║
║     ✅ Auto Trading                                              ║
║     ✅ Capital Auto-Update                                       ║
╚═══════════════════════════════════════════════════════════════════╝
    """)
    
    logger.info(f"💰 Capital: ₹{state.capital:,.2f}")
    logger.info(f"🤖 Google AI: {'Active' if google_ai else 'Inactive'}")
    logger.info(f"📡 Telegram Bot: {'Active' if TELEGRAM_TOKEN != 'YOUR_BOT_TOKEN_HERE' else 'Inactive'}")
    
    # Start auto engine
    engine = AutoEngine()
    threading.Thread(target=engine.start, daemon=True).start()
    
    # Start Telegram bot
    bot = TelegramBot()
    
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()
