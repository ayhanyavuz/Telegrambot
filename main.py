import os
import logging
import asyncio
import json
import io
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from aiohttp import web

import yfinance as yf
import mplfinance as mpf
import pandas as pd
import indicators
from keep_alive import keep_alive

# Load environment variables explicitly
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Global Application object for Webhook access
bot_app = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"Merhaba {user.first_name}! 👋\n\n"
        "Ben Matriks Bot. BIST 30 hisselerini analiz etmene yardımcı olabilirim.\n\n"
        "Kullanabileceğin komutlar:\n"
        "/fiyat <HISSE> - Anlık fiyat sorgula\n"
        "/t3 <HISSE> - 5dk T3 grafiği (TradingView stili)\n"
        "/teknik <HISSE> - Detaylı teknik analiz özeti\n"
        "/tarama <TİP> - BIST 30 taraması yap (t3, rsi, macd)\n"
        "/bist - Popüler hisseleri listele\n"
        "/yardim - Tüm komutları gör\n\n"
        "Örnek: /t3 THYAO"
    )
    await update.message.reply_text(welcome_text)



async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Bilinmeyen komut: {update.message.text}\nMenü için /menu yazın.")

async def get_stock_price(symbol):
    try:
        loop = asyncio.get_running_loop()
        ticker = f"{symbol.upper()}.IS"
        
        def fetch_data():
            stock = yf.Ticker(ticker)
            return stock.history(period="1d")
            
        data = await loop.run_in_executor(None, fetch_data)
        
        if not data.empty:
            price = data['Close'].iloc[-1]
            return f"{symbol.upper()}: {price:.2f} TL"
        else:
            return f"{symbol.upper()} bulunamadı veya veri yok."
    except Exception as e:
        logging.error(f"Error fetching {symbol}: {e}")
        return f"Hata oluştu: {str(e)}"

async def generate_t3_chart(symbol):
    try:
        loop = asyncio.get_running_loop()
        ticker = f"{symbol.upper()}.IS"
        
        def fetch_calc_plot():
            stock = yf.Ticker(ticker)
            # Get 5-minute data (5 days)
            df = stock.history(period="5d", interval="5m")
            if df.empty:
                return None, None
            
            # Manual Tilson T3 Calculation
            length = 6
            a = 0.9
            
            # Calculate EMA function
            def ema(series, period):
                return series.ewm(span=period, adjust=False).mean()

            v = a
            e1 = ema(df['Close'], length)
            e2 = ema(e1, length)
            e3 = ema(e2, length)
            e4 = ema(e3, length)
            e5 = ema(e4, length)
            e6 = ema(e5, length)
            
            c1 = -v**3
            c2 = 3*v**2 + 3*v**3
            c3 = -6*v**2 - 3*v - 3*v**3
            c4 = 1 + 3*v + v**3 + 3*v**2
            
            t3 = c1*e6 + c2*e5 + c3*e4 + c4*e3
            df['T3'] = t3
            
            # Slice for plotting (Show only the last day)
            # We fetch 5d for calculation, but only plot the last day's data
            last_date = df.index[-1].date()
            df_plot = df[df.index.date == last_date].copy()
            
            # Re-create conditional series for the sliced data
            t3_up = df_plot['T3'].copy()
            t3_down = df_plot['T3'].copy()
            
            mask_up = df_plot['Close'] >= df_plot['T3']
            mask_down = df_plot['Close'] < df_plot['T3']
            
            t3_up[~mask_up] = float('nan')
            t3_down[~mask_down] = float('nan')
            
            # --- Custom Style (TradingView Dark Look) ---
            mc = mpf.make_marketcolors(up='#089981', down='#F23645', inherit=True)
            s = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc, gridstyle=':', y_on_right=True)
            
            # Create Plots
            ap_up = mpf.make_addplot(t3_up, color='#089981', width=2) # Green
            ap_down = mpf.make_addplot(t3_down, color='#F23645', width=2) # Red
            
            buf = io.BytesIO()
            mpf.plot(df_plot, type='candle', style=s, 
                     addplot=[ap_up, ap_down],
                     title=f'{symbol.upper()} - 5m T3({length}, {a})',
                     ylabel='Fiyat',
                     volume=False,
                     savefig=dict(fname=buf, format='png', bbox_inches='tight', dpi=150))
            buf.seek(0)
            
            last_price = df['Close'].iloc[-1]
            last_t3 = df['T3'].iloc[-1]
            trend = "YÜKSELİŞ 🟢" if last_price > last_t3 else "DÜŞÜŞ 🔴"
            
            info_text = (f"📊 **{symbol.upper()} Analizi (5dk)**\n"
                         f"Fiyat: {last_price:.2f}\n"
                         f"T3 (6, 0.9): {last_t3:.2f}\n"
                         f"Trend: {trend}")
            
            return buf, info_text
            
        buf, info_text = await loop.run_in_executor(None, fetch_calc_plot)
        return buf, info_text
                
    except Exception as e:
        logging.error(f"Chart Error {symbol}: {e}")
        return None, f"Hata: {str(e)}"

async def fiyat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Received /fiyat command from {update.effective_user.first_name}")
    if not context.args:
        await update.message.reply_text("Lütfen bir hisse kodu girin. Örn: /fiyat THYAO")
        return
    
    symbol = context.args[0]
    message = await update.message.reply_text(f"{symbol.upper()} fiyatı getiriliyor... (Lütfen bekleyin)")
    price_info = await get_stock_price(symbol)
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message.message_id, text=price_info)

async def t3_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Received /t3 command from {update.effective_user.first_name}")
    if not context.args:
        await update.message.reply_text("Lütfen bir hisse kodu girin. Örn: /t3 THYAO")
        return

    symbol = context.args[0]
    message = await update.message.reply_text(f"{symbol.upper()} grafiği hazırlanıyor... (Biraz sürebilir)")
    
    img_buf, result_text = await generate_t3_chart(symbol)
    
    if img_buf:
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=img_buf, caption=result_text, parse_mode='Markdown')
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=message.message_id)
    else:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message.message_id, text=result_text)

async def bist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Received /bist command from {update.effective_user.first_name}")
    message = await update.message.reply_text("BIST verileri getiriliyor... (Biraz sürebilir)")
    symbols = ["THYAO", "GARAN", "ASELS", "AKBNK", "EREGL"]
    
    tasks = [get_stock_price(sym) for sym in symbols]
    results = await asyncio.gather(*tasks)
    
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message.message_id, text="\n".join(results))

# --- Webhook Server ---
async def handle_webhook(request):
    try:
        data = await request.json()
        logging.info(f"Webhook received: {data}")
        
        # Expected format: {"symbol": "THYAO", "message": "AL Sinyali", "price": 123.45}
        symbol = data.get('symbol', 'UNKNOWN')
        msg_text = data.get('message', 'Sinyal Geldi')
        price = data.get('price', 0)
        
        alert_message = (f"🚨 **TRADINGVIEW ALARMI** 🚨\n\n"
                         f"Hisse: {symbol}\n"
                         f"Mesaj: {msg_text}\n"
                         f"Fiyat: {price}")
        
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if chat_id and bot_app:
            await bot_app.bot.send_message(chat_id=chat_id, text=alert_message)
            return web.Response(text="Alert Sent")
        else:
            logging.warning("No CHAT_ID found in .env or bot not ready.")
            return web.Response(text="No Chat ID configured", status=500)
            
    except Exception as e:
        logging.error(f"Webhook Error: {e}")
        return web.Response(text=str(e), status=500)

# Import Indicators
import indicators

# ... (previous code) ...

async def teknik_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Received /teknik command from {update.effective_user.first_name}")
    if not context.args:
        await update.message.reply_text("Lütfen bir hisse kodu girin. Örn: /teknik THYAO")
        return
    
    symbol = context.args[0]
    message = await update.message.reply_text(f"{symbol.upper()} teknik analizi yapılıyor... (Veriler indiriliyor)")
    
    # Run in executor to avoid blocking
    loop = asyncio.get_running_loop()
    summary = await loop.run_in_executor(None, indicators.get_technical_summary, symbol)
    
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message.message_id, text=summary, parse_mode='Markdown')

async def ind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Received /ind command from {update.effective_user.first_name}")
    if len(context.args) < 2:
        await update.message.reply_text("Kullanım: /ind <HISSE> <INDIKATOR>\nÖrn: /ind THYAO rsi\nÖrn: /ind GARAN macd")
        return
    
    symbol = context.args[0]
    ind_code = context.args[1]
    
    message = await update.message.reply_text(f"{symbol.upper()} - {ind_code.upper()} grafiği hazırlanıyor...")
    
    loop = asyncio.get_running_loop()
    img_buf, caption = await loop.run_in_executor(None, indicators.get_indicator_chart, symbol, ind_code)
    
    if img_buf:
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=img_buf, caption=caption, parse_mode='Markdown')
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=message.message_id)
    else:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message.message_id, text=caption)

# ... (Imports)

# Subscription Manager
class SubscriptionManager:
    def __init__(self, filename='subscribers.json'):
        self.filename = filename
        self.subscribers = self.load_subscribers()

    def load_subscribers(self):
        if not os.path.exists(self.filename):
            return set()
        try:
            with open(self.filename, 'r') as f:
                return set(json.load(f))
        except:
            return set()

    def save_subscribers(self):
        with open(self.filename, 'w') as f:
            json.dump(list(self.subscribers), f)

    def add_subscriber(self, chat_id):
        if chat_id not in self.subscribers:
            self.subscribers.add(chat_id)
            self.save_subscribers()
            return True
        return False

    def remove_subscriber(self, chat_id):
        if chat_id in self.subscribers:
            self.subscribers.remove(chat_id)
            self.save_subscribers()
            return True
        return False

    def get_subscribers(self):
        return list(self.subscribers)

# Global Objects
bot_app = None
sub_manager = SubscriptionManager()

# ... (Existing Commands: start, echo, etc.)

async def abone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if sub_manager.add_subscriber(chat_id):
        await update.message.reply_text("✅ Başarıyla abone oldunuz! Artık TradingView sinyallerini alacaksınız.")
        logging.info(f"New subscriber: {chat_id}")
    else:
        await update.message.reply_text("ℹ️ Zaten abonesiniz.")

async def cikis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if sub_manager.remove_subscriber(chat_id):
        await update.message.reply_text("❌ Abonelikten çıktınız. Artık sinyal almayacaksınız.")
        logging.info(f"Subscriber removed: {chat_id}")
    else:
        await update.message.reply_text("ℹ️ Zaten abone değilsiniz.")

async def tarama_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Received /tarama command from {update.effective_user.first_name}")
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ Lütfen bir indikatör belirtin.\n\n"
            "Kullanım:\n"
            "• `/tarama till` -> T3 Taraması (Eski T3)\n"
            "• `/tarama rsi` -> RSI Taraması\n"
            "• `/tarama macd` -> MACD Taraması\n"
            "• `/tarama stoch` -> Stokastik Taraması",
            parse_mode='Markdown'
        )
        return

    ind_code = context.args[0].lower()
    
    # Map 'till' to 't3'
    if ind_code == 'till':
        ind_code = 't3'
        
    message = await update.message.reply_text(f"🔎 BIST 30 taranıyor... ({ind_code.upper()})\nBu işlem 10-15 saniye sürebilir.")
    
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, indicators.scan_bist30, ind_code)
    
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message.message_id, text=result, parse_mode='Markdown')

# ... (Existing Technical Analysis Commands)

async def yardim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **Matriks Bot Yardım Menüsü**\n\n"
        "**Abonelik:**\n"
        "/abone - Sinyal aboneliğini başlat\n"
        "/cikis - Sinyal aboneliğini iptal et\n\n"
        "**Tarama:**\n"
        "/tarama - Varsayılan T3 Taraması\n"
        "/tarama <KOD> - Özel İndikatör Taraması\n"
        "Örn: `/tarama rsi`, `/tarama macd`, `/tarama sma50`\n\n"
        "**Temel Komutlar:**\n"
        "/fiyat <HISSE> - Anlık fiyat (Yahoo)\n"
        "/bist - Popüler hisseler\n"
        "/teknik <HISSE> - Detaylı Teknik Analiz Özeti\n\n"
        "**Grafik ve İndikatörler:**\n"
        "/t3 <HISSE> - T3 (Tilson) Grafiği\n"
        "/ind <HISSE> <KOD> - Özel İndikatör Grafiği\n\n"
        "**İndikatör Kodları:**\n"
        "• `rsi` - Relative Strength Index\n"
        "• `macd` - MACD Trend\n"
        "• `bb` - Bollinger Bantları\n"
        "• `stoch` - Stochastic Oscillator\n"
        "• `adx` - Trend Gücü\n"
        "• `cci` - Commodity Channel Index\n"
        "• `sma50`, `sma200` - Basit Ortalamalar\n"
        "• `ema20`, `ema50` - Üstel Ortalamalar\n"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

# --- Webhook Server ---
async def handle_webhook(request):
    try:
        data = await request.json()
        logging.info(f"Webhook received: {data}")
        
        symbol = data.get('symbol', 'UNKNOWN')
        msg_text = data.get('message', 'Sinyal Geldi')
        price = data.get('price', 0)
        
        alert_message = (f"🚨 **TRADINGVIEW ALARMI** 🚨\n\n"
                         f"Hisse: {symbol}\n"
                         f"Mesaj: {msg_text}\n"
                         f"Fiyat: {price}")
        
        # Broadcast to all subscribers
        subscribers = sub_manager.get_subscribers()
        
        # Also include the admin from .env if not in list (Optional, but good for safety)
        admin_id = os.getenv('TELEGRAM_CHAT_ID')
        if admin_id:
            try:
                admin_id = int(admin_id)
                if admin_id not in subscribers:
                    subscribers.append(admin_id)
            except:
                pass

        if not subscribers:
             logging.warning("No subscribers to send alert to.")
             return web.Response(text="No subscribers", status=200)

        count = 0
        if bot_app:
            for chat_id in subscribers:
                try:
                    await bot_app.bot.send_message(chat_id=chat_id, text=alert_message)
                    count += 1
                except Exception as e:
                    logging.error(f"Failed to send to {chat_id}: {e}")
            
            return web.Response(text=f"Alert sent to {count} subscribers")
        else:
            return web.Response(text="Bot not ready", status=500)
            
    except Exception as e:
        logging.error(f"Webhook Error: {e}")
        return web.Response(text=str(e), status=500)

async def main():
    global bot_app
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env file.")
        return

    # 1. Setup Telegram Bot
    bot_app = ApplicationBuilder().token(token).build()
    
    bot_app.add_handler(CommandHandler('start', start))
    
    bot_app.add_handler(CommandHandler('yardim', yardim_command))
    
    # Subscription Handlers
    bot_app.add_handler(CommandHandler('abone', abone_command))
    bot_app.add_handler(CommandHandler('cikis', cikis_command))
    
    bot_app.add_handler(CommandHandler('tarama', tarama_command))
    
    bot_app.add_handler(CommandHandler('fiyat', fiyat_command))
    bot_app.add_handler(CommandHandler('t3', t3_command))
    bot_app.add_handler(CommandHandler('bist', bist_command))
    
    # New Handlers
    bot_app.add_handler(CommandHandler('teknik', teknik_command))
    bot_app.add_handler(CommandHandler('ind', ind_command))
    
    # Shortcuts
    async def rsi_shortcut(u, c): c.args = [c.args[0], 'rsi'] if c.args else []; await ind_command(u, c)
    async def macd_shortcut(u, c): c.args = [c.args[0], 'macd'] if c.args else []; await ind_command(u, c)
    async def bb_shortcut(u, c): c.args = [c.args[0], 'bb'] if c.args else []; await ind_command(u, c)
    
    bot_app.add_handler(CommandHandler('rsi', rsi_shortcut))
    bot_app.add_handler(CommandHandler('macd', macd_shortcut))
    bot_app.add_handler(CommandHandler('bb', bb_shortcut))

    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), echo))
    
    await bot_app.initialize()
    await bot_app.start()
    
    # Start Flask keep-alive server before polling
    keep_alive()
    # bot.polling() veya bot.infinity_polling() buranın altında kalacak
    
    await bot_app.updater.start_polling()
    
    print("Bot started...")

    # 2. Setup Webhook Server
    app = web.Application()
    app.router.add_post('/webhook', handle_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 80) # Listen on port 80
    await site.start()
    
    print("Webhook server running on port 80...")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        await runner.cleanup()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
