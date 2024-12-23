import logging
import asyncio

# 1) IMPORTA nest_asyncio
import nest_asyncio
nest_asyncio.apply()

import os
from datetime import datetime, timedelta

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

# Libreria Twitchio
from twitchio.ext.commands import Bot

# ---------------------------------------------------------------------------
# CARICA LE VARIABILI D'AMBIENTE DAL FILE .env
# ---------------------------------------------------------------------------
load_dotenv()
print(f"TELEGRAM_BOT_TOKEN: {os.getenv('TELEGRAM_BOT_TOKEN')}")

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
TWITCH_ACCESS_TOKEN = os.getenv('TWITCH_ACCESS_TOKEN')
BROADCASTER_ID = os.getenv('BROADCASTER_ID')

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = int(os.getenv('TELEGRAM_CHAT_ID', '0'))
BOT_OWNER_ID = int(os.getenv('BOT_OWNER_ID', '0'))

# ---------------------------------------------------------------------------
# DIZIONARIO PER TRACCIARE GLI ABBONAMENTI TWITCH
# ---------------------------------------------------------------------------
subscribers = {}
verified_users = {}  # Utenti Telegram verificati
expired_users = {}   # Utenti con abbonamenti scaduti
last_live_status = False  # Per tracciare lo stato della live

# ---------------------------------------------------------------------------
# CLASS TWITCH BOT
# ---------------------------------------------------------------------------
class TwitchBot(Bot):
    def __init__(self):
        super().__init__(token=TWITCH_ACCESS_TOKEN, prefix="!", initial_channels=[BROADCASTER_ID])

    async def event_ready(self):
        logger.info(f"Bot connesso a Twitch come | {self.nick}")

    async def send_subscription_message(self, username):
        channel = self.get_channel(BROADCASTER_ID)
        if channel:
            message = (
                f"🎉 Grazie @{username} per esserti abbonato al canale! "
                f"Usa il comando /verify su Telegram per accedere al gruppo esclusivo."
            )
            await channel.send(message)

# ---------------------------------------------------------------------------
# FUNZIONI DI SUPPORTO PER TWITCH
# ---------------------------------------------------------------------------
async def notify_new_subscription(username):
    """Invia un messaggio nella chat di Twitch per un nuovo abbonato."""
    bot = TwitchBot()
    await bot.connect()
    await bot.send_subscription_message(username)
    await bot.close()

async def check_live_status(context):
    """Controlla se la live è attiva o terminata e notifica su Telegram."""
    global last_live_status
    bot = TwitchBot()
    await bot.connect()
    try:
        channel = bot.get_channel(BROADCASTER_ID)
        streams = await bot.fetch_streams(users=[BROADCASTER_ID])
        is_live = any(streams)
        
        if is_live and not last_live_status:
            # La live è iniziata, invia una notifica su Telegram
            message = (
                f"🎥 La live è iniziata! Unisciti ora: https://www.twitch.tv/{BROADCASTER_ID}"
            )
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        elif not is_live and last_live_status:
            # La live è terminata, invia una notifica su Telegram
            message = (
                f"📴 La live è terminata. Grazie per averci seguito!"
            )
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        last_live_status = is_live
    except Exception as e:
        logger.error(f"Errore durante il controllo dello stato live: {e}")
    finally:
        await bot.close()

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

async def verify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando per verificare un utente Telegram con il suo nickname Twitch."""
    if len(context.args) != 1:
        await update.message.reply_text("Usa il comando così: /verify <Twitch_Nickname>")
        return

    twitch_username = context.args[0]
    if twitch_username in subscribers:
        user_id = update.effective_user.id
        verified_users[user_id] = twitch_username
        await update.message.reply_text(f"✅ Verifica completata! Benvenuto, {twitch_username}.")
    else:
        await update.message.reply_text("❌ Nickname Twitch non trovato tra gli abbonati. Assicurati di essere abbonato!")

async def restrict_unverified_members(context: ContextTypes.DEFAULT_TYPE):
    """Limita l'accesso ai messaggi del gruppo per utenti non verificati."""
    bot = context.bot
    chat_id = TELEGRAM_CHAT_ID
    try:
        async for member in bot.get_chat_administrators(chat_id):
            if member.user.id not in verified_users:
                await bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=member.user.id,
                    permissions={}
                )
    except Exception as e:
        logger.error(f"Errore durante la restrizione degli utenti non verificati: {e}")

async def notify_expired_users(context: ContextTypes.DEFAULT_TYPE):
    """Notifica agli utenti verificati con abbonamenti scaduti che hanno 3 giorni per rinnovare."""
    bot = context.bot
    chat_id = TELEGRAM_CHAT_ID
    now = datetime.now()
    to_remove = []
    try:
        for user_id, info in expired_users.items():
            days_since_expired = (now - info['expired_date']).days
            if days_since_expired >= 3:
                # Espelli l'utente
                await bot.ban_chat_member(chat_id, user_id)
                to_remove.append(user_id)
                logger.info(f"Utente {user_id} espulso per abbonamento scaduto.")
            elif days_since_expired % 1 == 0:  # Notifica ogni 24 ore
                # Notifica l'utente
                await bot.send_message(
                    chat_id=user_id,
                    text="⚠️ Il tuo abbonamento Twitch è scaduto. Hai 3 giorni per rinnovarlo o sarai rimosso dal gruppo."
                )
    except Exception as e:
        logger.error(f"Errore durante la notifica degli utenti scaduti: {e}")
    for user_id in to_remove:
        del expired_users[user_id]

# ---------------------------------------------------------------------------
# JOB CHE CONTROLLA ABBONATI E LIVE
# ---------------------------------------------------------------------------
async def check_subscriptions(context):
    """Controlla i nuovi abbonati e quelli che hanno perso l'abbonamento."""
    twitch = TwitchBot()
    await twitch.connect()
    try:
        # Simula la verifica degli abbonamenti
        current_subs = {"utente_demo": datetime.now()}  # Simula un utente abbonato
        expired = []
        
        for user, info in subscribers.items():
            if user not in current_subs:
                expired.append(user)

        # Aggiorna i dizionari
        for user in expired:
            if user in verified_users:
                user_id = [k for k, v in verified_users.items() if v == user][0]
                expired_users[user_id] = {"expired_date": datetime.now()}
                del verified_users[user_id]
                logger.info(f"Utente {user} aggiunto a expired_users.")

        # Aggiungi nuovi abbonati
        for user in current_subs:
            if user not in subscribers:
                subscribers[user] = {"sub_date": datetime.now()}
                await notify_new_subscription(user)
    except Exception as e:
        logger.error(f"Errore durante il controllo degli abbonamenti: {e}")
    finally:
        await twitch.close()

    # Notifica gli utenti con abbonamenti scaduti
    await notify_expired_users(context)

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
async def main():
    try:
        # Costruisce l'applicazione Telegram utilizzando il token direttamente
        application = ApplicationBuilder().token("7541852048:AAG0FXErC8JE25pFD7Cq8LUkFWkfa0CX4xc").build()

        # Registra i comandi
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("verify", verify_user))

        # Imposta JobQueue per controlli periodici
        job_queue = application.job_queue
        job_queue.run_repeating(check_subscriptions, interval=86400, first=0)  # Controlla gli abbonamenti ogni 24 ore
        job_queue.run_repeating(check_live_status, interval=300, first=0)  # Controlla lo stato live ogni 5 minuti

        # Avvia il bot con run_polling
        logger.info("Bot avviato con successo!")
        await application.run_polling()
    except Exception as e:
        logger.error(f"Errore durante l'esecuzione del bot: {e}")


if __name__ == "__main__":
    asyncio.run(main())