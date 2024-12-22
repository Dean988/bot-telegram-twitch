import logging
import asyncio

# 1) IMPORTA nest_asyncio
import nest_asyncio
nest_asyncio.apply()

import os
from datetime import datetime

# Libreria python-dotenv
from dotenv import load_dotenv

# Librerie Telegram
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    JobQueue,
    ContextTypes
)
from telegram import Update
from telegram.constants import ChatMemberStatus

# Libreria Twitch
from twitchAPI.twitch import Twitch
from twitchAPI.types import AuthScope

# ---------------------------------------------------------------------------
# CARICA LE VARIABILI D'AMBIENTE DAL FILE .env (o criptato.env)
# ---------------------------------------------------------------------------
# Se si chiama "criptato.env" ed è in /home/test2.gamesite.it/public_html/, 
# passiamo il path completo a load_dotenv():
load_dotenv("/home/test2.gamesite.it/public_html/criptato.env")

# ---------------------------------------------------------------------------
# CONFIGURA IL LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LEGGI LE VARIABILI D'AMBIENTE
# ---------------------------------------------------------------------------
TWITCH_CLIENT_ID = os.getenv('TWITCH_CLIENT_ID')
TWITCH_CLIENT_SECRET = os.getenv('TWITCH_CLIENT_SECRET')
BROADCASTER_ID = os.getenv('BROADCASTER_ID')

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = int(os.getenv('TELEGRAM_CHAT_ID', '0'))
BOT_OWNER_ID = int(os.getenv('BOT_OWNER_ID', '0'))

# STAMPE DI DEBUG (rimuovile se non servono più)
print(f"DEBUG: TWITCH_CLIENT_ID = {TWITCH_CLIENT_ID}")
print(f"DEBUG: TWITCH_CLIENT_SECRET = {TWITCH_CLIENT_SECRET}")
print(f"DEBUG: BROADCASTER_ID = {BROADCASTER_ID}")
print(f"DEBUG: TELEGRAM_BOT_TOKEN = {TELEGRAM_BOT_TOKEN}")
print(f"DEBUG: TELEGRAM_CHAT_ID = {TELEGRAM_CHAT_ID}")
print(f"DEBUG: BOT_OWNER_ID = {BOT_OWNER_ID}")

# ---------------------------------------------------------------------------
# DIZIONARIO PER TRACCIARE GLI ABBONAMENTI TWITCH
# ---------------------------------------------------------------------------
subscribers = {}
last_live_status = False  # Per tracciare lo stato della live

# ---------------------------------------------------------------------------
# FUNZIONI DI SUPPORTO PER TWITCH
# ---------------------------------------------------------------------------
async def initialize_twitch():
    """Inizializza il client Twitch."""
    twitch = Twitch(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
    twitch.authenticate_app([AuthScope.CHANNEL_READ_SUBSCRIPTIONS])
    return twitch

async def is_live(twitch: Twitch, broadcaster_id: str):
    """Verifica se la live è attualmente in corso."""
    response = await twitch.get_streams(user_id=[broadcaster_id])
    streams = response.get('data', [])
    return len(streams) > 0

async def get_all_subscriptions(twitch: Twitch, broadcaster_id: str):
    """Restituisce la lista di tutti gli abbonati al canale Twitch."""
    all_subs = []
    cursor = None
    while True:
        response = await twitch.get_broadcaster_subscriptions(
            broadcaster_id=broadcaster_id, after=cursor
        )
        data = response.get('data', [])
        all_subs.extend(data)
        pagination = response.get('pagination', {})
        cursor = pagination.get('cursor')
        if not cursor:
            break
    return all_subs

async def notify_new_subscription(username, chat_id, context):
    """Notifica l'arrivo di un nuovo abbonato."""
    message = f"Grazie per esserti abbonato, @{username}! Benvenuto nel nostro gruppo!"
    await context.bot.send_message(chat_id=chat_id, text=message)

async def notify_expired_subscription(username, chat_id, context):
    """Notifica la scadenza di un abbonamento."""
    message = (
        f"Ciao @{username}, il tuo abbonamento è scaduto. "
        f"Rinnovalo per continuare a supportarci!"
    )
    await context.bot.send_message(chat_id=chat_id, text=message)

async def notify_live(context):
    """Notifica l'inizio della live."""
    message = (
        "🎉 La live è iniziata! Unisciti a noi su Twitch: "
        f"https://www.twitch.tv/{BROADCASTER_ID}"
    )
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

# ---------------------------------------------------------------------------
# FUNZIONI DI SUPPORTO PER TELEGRAM
# ---------------------------------------------------------------------------
async def is_user_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Controlla se l'utente che invoca il comando è l'owner del bot o un admin del gruppo."""
    if update.effective_user.id == BOT_OWNER_ID:
        return True
    chat_member = await context.bot.get_chat_member(
        update.effective_chat.id, update.effective_user.id
    )
    return chat_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start: attiva il bot e conferma il corretto funzionamento."""
    if not await is_user_allowed(update, context):
        await update.message.reply_text(
            "Questo comando è riservato agli amministratori e al proprietario del bot."
        )
        return
    await update.message.reply_text("Il bot è attivo e monitorerà abbonamenti e live!")

async def live_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /live_notification: invia il messaggio di live attiva."""
    if not await is_user_allowed(update, context):
        await update.message.reply_text(
            "Questo comando è riservato agli amministratori e al proprietario del bot."
        )
        return
    await update.message.reply_text(
        f"Live attiva! Unisciti a noi su Twitch: https://www.twitch.tv/{BROADCASTER_ID}"
    )

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /chatid: mostra l'ID della chat corrente."""
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Chat ID: {chat_id}")

# ---------------------------------------------------------------------------
# JOB CHE CONTROLLA ABBONATI E LIVE
# ---------------------------------------------------------------------------
async def check_subscriptions(context):
    """Controlla i nuovi abbonati e quelli che hanno perso l'abbonamento."""
    twitch = context.application.twitch
    current_sub_data = await get_all_subscriptions(twitch, BROADCASTER_ID)
    current_subscribers = {sub['user_name']: sub['user_id'] for sub in current_sub_data}

    # Notifica nuovi abbonati
    for username in current_subscribers:
        if username not in subscribers:
            subscribers[username] = {
                'user_id': current_subscribers[username],
                'sub_date': datetime.now(),
            }
            await notify_new_subscription(username, TELEGRAM_CHAT_ID, context)

    # Notifica abbonamenti scaduti
    for username in list(subscribers.keys()):
        if username not in current_subscribers:
            await notify_expired_subscription(username, TELEGRAM_CHAT_ID, context)
            del subscribers[username]

async def periodic_check(context):
    """Controllo periodico: verifica se la live è iniziata e aggiorna le subscription."""
    global last_live_status
    try:
        twitch = context.application.twitch
        # Controlla se la live è attiva
        is_currently_live = await is_live(twitch, BROADCASTER_ID)
        if is_currently_live and not last_live_status:
            await notify_live(context)
        last_live_status = is_currently_live

        # Controlla le subscription
        await check_subscriptions(context)
    except Exception as e:
        logger.error(f"Errore durante il controllo periodico: {e}")

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
async def main():
    try:
        # Costruisce l'applicazione Telegram
        application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

        # Inizializza Twitch
        twitch = await initialize_twitch()
        application.twitch = twitch

        # Registra i comandi
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("live_notification", live_notification))
        application.add_handler(CommandHandler("chatid", get_chat_id))

        # Imposta JobQueue per controlli periodici (una volta ogni ora)
        job_queue = application.job_queue
        job_queue.run_repeating(periodic_check, interval=3600, first=0)

        # Avvia il bot con run_polling
        logger.info("Bot avviato con successo!")
        await application.run_polling()
    except telegram.error.InvalidToken as e:
        logger.error(f"ERRORE: Token Telegram non valido. Dettagli: {e}")
    except Exception as e:
        logger.error(f"Errore durante l'esecuzione del bot: {e}")
if __name__ == "__main__":
    asyncio.run(main())