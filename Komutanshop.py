#!/usr/bin/env python3
"""
Pasarguard Node Manager Bot - SENİN PANELİN İÇİN ÖZEL!
Panel: https://crc.fastline-tm-belet-film.ru:8000
"""

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
from datetime import datetime
import threading
import time
import json
import os
import logging

# =====================================================================
# ✅ BURASI SENİN PANELİN İÇİN AYARLANDI - DOKUNMA!
# =====================================================================

API_TOKEN = '8850038202:AAGkr_QlbJJxSAvHR20-zLpWFYUSrYcwa0U'
PANEL_URL = "https://crc.fastline-tm-belet-film.ru:8000"
MASTER_PANEL_API = f"{PANEL_URL}/api"
MASTER_ADMIN_USERNAME = "komutan31"
MASTER_ADMIN_PASSWORD = "KomutanPanel_13"

ALLOWED_TELEGRAM_IDS = [8359722718, 7115611768]

# Node'lar
NODES = [
    {"id": 1, "name": "Node-1", "ip": "11.22.33.44", "location": "İstanbul"},
    {"id": 2, "name": "Node-2", "ip": "55.66.77.88", "location": "Ankara"},
]

CHECK_INTERVAL = 30
MIN_TRAFFIC = 1000
MIN_USERS = 1
FAIL_COUNT = 3

# =====================================================================

STATE_FILE = "state.json"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(API_TOKEN)
user_data = {}

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "active_node": NODES[0]["id"],
            "auto_enabled": True,
            "bad_count": 0,
            "total_switches": 0,
            "uptime_start": int(time.time())
        }
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

class PasarguardAPI:
    def __init__(self):
        self.token = None
        self.session = requests.Session()
        self.session.verify = False
        self.session.timeout = 15

    def get_token(self):
        if self.token:
            return self.token
        
        # SENİN PANELİN İÇİN TÜM OLASI ENDPOINT'LER
        endpoints = [
            f"{MASTER_PANEL_API}/auth/token",
            f"{MASTER_PANEL_API}/auth/login",
            f"{MASTER_PANEL_API}/login",
            f"{MASTER_PANEL_API}/token",
            f"{MASTER_PANEL_API}/admin/token",
            f"{MASTER_PANEL_API}/admins/token",
        ]
        
        for url in endpoints:
            try:
                logger.info(f"🔑 Token deneniyor: {url}")
                response = self.session.post(
                    url,
                    json={"username": MASTER_ADMIN_USERNAME, "password": MASTER_ADMIN_PASSWORD},
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    self.token = data.get("access_token") or data.get("token")
                    if self.token:
                        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                        logger.info(f"✅ Token alındı: {url}")
                        return self.token
            except:
                continue
        
        # ALTERNATİF: Form-data ile dene
        try:
            url = f"{MASTER_PANEL_API}/auth/token"
            response = self.session.post(
                url,
                data={"username": MASTER_ADMIN_USERNAME, "password": MASTER_ADMIN_PASSWORD},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access_token") or data.get("token")
                if self.token:
                    self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                    logger.info(f"✅ Token alındı (form-data): {url}")
                    return self.token
        except:
            pass
        
        logger.error("❌ Token alınamadı!")
        return None

    def _request(self, method, endpoint, data=None):
        if not self.get_token():
            return {}
        try:
            url = f"{MASTER_PANEL_API}/{endpoint.lstrip('/')}"
            response = self.session.request(method, url, json=data, timeout=10)
            if response.status_code == 200:
                return response.json()
            return {}
        except:
            return {}

    def get_hosts(self):
        return self._request("GET", "hosts")

    def update_host_ip(self, ip):
        return self._request("POST", "hosts/update", {"ip": ip})

    def get_current_ip(self):
        hosts = self.get_hosts()
        return hosts.get("ip") or hosts.get("address")

    def get_nodes(self):
        return self._request("GET", "nodes")

    def get_node_stats(self, node_id):
        return self._request("GET", f"nodes/{node_id}/stats")

    def get_admins(self):
        return self._request("GET", "admins")

    def create_admin(self, username, password, role="admin"):
        return self._request("POST", "admins", {"username": username, "password": password, "role": role})

    def delete_admin(self, username):
        return self._request("DELETE", f"admins/{username}")

api = PasarguardAPI()

def format_traffic(bps):
    if bps < 1024:
        return f"{bps} B/s"
    elif bps < 1024*1024:
        return f"{bps/1024:.1f} KB/s"
    else:
        return f"{bps/(1024*1024):.1f} MB/s"

def format_bytes(bytes_value):
    if bytes_value < 1024:
        return f"{bytes_value} B"
    elif bytes_value < 1024*1024:
        return f"{bytes_value/1024:.1f} KB"
    elif bytes_value < 1024*1024*1024:
        return f"{bytes_value/(1024*1024):.1f} MB"
    else:
        return f"{bytes_value/(1024*1024*1024):.2f} GB"

def check_node_health(node_id):
    stats = api.get_node_stats(node_id)
    bps = stats.get("current_bps", 0)
    users = stats.get("online_users", 0)
    healthy = bps >= MIN_TRAFFIC and users >= MIN_USERS
    return healthy, stats

def get_next_node(state):
    active = state.get("active_node")
    for i, n in enumerate(NODES):
        if n["id"] == active:
            return NODES[(i + 1) % len(NODES)]
    return NODES[0]

def switch_to_node(node_id, state, force=False):
    target = None
    for n in NODES:
        if n["id"] == node_id:
            target = n
            break
    if not target:
        return False, "❌ Node bulunamadı!"
    
    if not force:
        healthy, _ = check_node_health(node_id)
        if not healthy:
            return False, "⚠️ Node sağlıksız!"
    
    if api.update_host_ip(target["ip"]):
        state["active_node"] = node_id
        state["bad_count"] = 0
        state["total_switches"] = state.get("total_switches", 0) + 1
        save_state(state)
        return True, f"✅ {target['name']} ({target['ip']}) geçildi!"
    return False, "❌ API hatası!"

def is_authorized(message_or_call):
    if isinstance(message_or_call, telebot.types.CallbackQuery):
        user_id = message_or_call.from_user.id
    else:
        user_id = message_or_call.chat.id
    return user_id in ALLOWED_TELEGRAM_IDS

def main_menu():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("👥 Adminler", callback_data="list_admins"),
        InlineKeyboardButton("➕ Admin Ekle", callback_data="add_admin")
    )
    markup.row(
        InlineKeyboardButton("🌐 Host & IP", callback_data="show_hosts"),
        InlineKeyboardButton("🖥️ Node'lar", callback_data="list_nodes")
    )
    markup.row(
        InlineKeyboardButton("🔄 Sonraki Node", callback_data="switch_next"),
        InlineKeyboardButton("📊 Durum", callback_data="show_status")
    )
    markup.row(
        InlineKeyboardButton("⚙️ Otomatik", callback_data="toggle_auto")
    )
    return markup

def admin_menu():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("➕ Admin Ekle", callback_data="add_admin"),
        InlineKeyboardButton("🗑️ Admin Sil", callback_data="delete_admin")
    )
    markup.row(InlineKeyboardButton("⬅️ Geri", callback_data="back_home"))
    return markup

@bot.message_handler(commands=['start', 'panel'])
def send_welcome(message):
    if not is_authorized(message):
        bot.send_message(message.chat.id, "❌ **YETKİSİZ ERİŞİM!**", parse_mode="Markdown")
        return
    
    state = load_state()
    current_ip = api.get_current_ip() or "Bilinmiyor"
    
    text = (
        f"🛡️ **PASARGUARD KONTROL PANELİ**\n\n"
        f"📍 Aktif IP: `{current_ip}`\n"
        f"🔄 Otomatik: {'AÇIK' if state.get('auto_enabled', True) else 'KAPALI'}\n"
        f"⚠️ Hata: {state.get('bad_count', 0)}/{FAIL_COUNT}\n"
        f"📊 Geçiş: {state.get('total_switches', 0)}\n\n"
        f"📡 Panel: {PANEL_URL}"
    )
    
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=main_menu())

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if not is_authorized(call):
        bot.answer_callback_query(call.id, "❌ Yetkiniz yok!")
        return
    
    data = call.data
    state = load_state()
    
    if data == "list_admins":
        admins = api.get_admins()
        if not admins:
            text = "👥 **Admin Listesi**\n\nHiç admin bulunamadı!"
        else:
            text = "👥 **Admin Listesi**\n\n"
            for admin in admins:
                username = admin.get("username", "Bilinmiyor")
                role = admin.get("role", "user")
                text += f"• `{username}` ({role})\n"
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=admin_menu()
        )
    
    elif data == "add_admin":
        bot.edit_message_text(
            "➕ **Admin Ekle**\n\n"
            "Yeni admin eklemek için şu formatı kullan:\n"
            "`adminekle KULLANICIADI SIFRE`\n\n"
            "Örnek: `adminekle yeniadmin 123456`",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=admin_menu()
        )
        user_data[call.from_user.id] = {"action": "add_admin"}
    
    elif data == "delete_admin":
        bot.edit_message_text(
            "🗑️ **Admin Sil**\n\n"
            "Admin silmek için şu formatı kullan:\n"
            "`adminsil KULLANICIADI`\n\n"
            "Örnek: `adminsil eskiadmin`",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=admin_menu()
        )
        user_data[call.from_user.id] = {"action": "delete_admin"}
    
    elif data == "show_hosts":
        current_ip = api.get_current_ip() or "Bilinmiyor"
        hosts = api.get_hosts()
        
        text = f"🌐 **Host & IP Bilgileri**\n\n"
        text += f"📍 Mevcut IP: `{current_ip}`\n\n"
        text += f"📋 Host Detayları:\n"
        for key, value in hosts.items():
            if key != "ip":
                text += f"• {key}: `{value}`\n"
        
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("🔄 IP Güncelle", callback_data="update_ip"))
        markup.row(InlineKeyboardButton("⬅️ Geri", callback_data="back_home"))
        
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
    
    elif data == "update_ip":
        node = get_next_node(state)
        success, msg = switch_to_node(node["id"], state, force=True)
        bot.edit_message_text(
            msg,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_menu()
        )
    
    elif data == "list_nodes":
        text = "🖥️ **Node Listesi**\n\n"
        for node in NODES:
            active = "▶️ " if node["id"] == state.get("active_node") else "   "
            healthy, stats = check_node_health(node["id"])
            status = "✅" if healthy else "❌"
            bps = format_traffic(stats.get("current_bps", 0))
            users = stats.get("online_users", 0)
            total = format_bytes(stats.get("total_bytes", 0))
            text += f"{active}{status} **{node['name']}**\n"
            text += f"   📍 {node.get('location', 'N/A')} | IP: `{node['ip']}`\n"
            text += f"   📊 {bps} | 👥 {users} kullanıcı\n"
            text += f"   📦 Toplam: {total}\n\n"
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("🔄 Sonraki Node", callback_data="switch_next"),
            InlineKeyboardButton("✋ Node Seç", callback_data="pick_node")
        )
        markup.row(InlineKeyboardButton("⬅️ Geri", callback_data="back_home"))
        
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
    
    elif data == "switch_next":
        node = get_next_node(state)
        success, msg = switch_to_node(node["id"], state, force=True)
        bot.edit_message_text(
            msg,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_menu()
        )
    
    elif data == "pick_node":
        markup = InlineKeyboardMarkup()
        for node in NODES:
            healthy, _ = check_node_health(node["id"])
            status = "✅" if healthy else "❌"
            active = "▶️" if node["id"] == state.get("active_node") else ""
            markup.row(InlineKeyboardButton(
                f"{active} {status} {node['name']}",
                callback_data=f"select_node_{node['id']}"
            ))
        markup.row(InlineKeyboardButton("⬅️ Geri", callback_data="back_home"))
        
        bot.edit_message_text(
            "**Node seçin:**",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
    
    elif data.startswith("select_node_"):
        node_id = int(data.split("_")[2])
        success, msg = switch_to_node(node_id, state, force=True)
        bot.edit_message_text(
            msg,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_menu()
        )
    
    elif data == "show_status":
        current_ip = api.get_current_ip() or "Bilinmiyor"
        active_node = next((n for n in NODES if n["id"] == state.get("active_node")), None)
        healthy, stats = check_node_health(state.get("active_node"))
        
        text = (
            f"📊 **Sistem Durumu**\n\n"
            f"📍 Aktif IP: `{current_ip}`\n"
            f"🖥️ Aktif Node: {active_node['name'] if active_node else 'Yok'}\n"
            f"📊 Trafik: {format_traffic(stats.get('current_bps', 0))}\n"
            f"👥 Kullanıcı: {stats.get('online_users', 0)}/{stats.get('total_users', 0)}\n"
            f"📦 Aylık: {format_bytes(stats.get('monthly_bytes', 0))}\n"
            f"🔄 Otomatik: {'AÇIK' if state.get('auto_enabled', True) else 'KAPALI'}\n"
            f"⚠️ Hata: {state.get('bad_count', 0)}/{FAIL_COUNT}\n"
            f"📊 Geçiş: {state.get('total_switches', 0)}\n"
            f"📈 Durum: {'✅ Sağlıklı' if healthy else '❌ Sağlıksız'}\n"
            f"⏱ Çalışma: {time.strftime('%H:%M:%S', time.gmtime(time.time() - state.get('uptime_start', time.time())))}"
        )
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=main_menu()
        )
    
    elif data == "toggle_auto":
        state["auto_enabled"] = not state.get("auto_enabled", True)
        save_state(state)
        durum = "AÇIK" if state["auto_enabled"] else "KAPALI"
        bot.edit_message_text(
            f"✅ Otomatik mod: {durum}",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_menu()
        )
    
    elif data == "back_home":
        state = load_state()
        current_ip = api.get_current_ip() or "Bilinmiyor"
        text = (
            f"🛡️ **PASARGUARD KONTROL PANELİ**\n\n"
            f"📍 Aktif IP: `{current_ip}`\n"
            f"🔄 Otomatik: {'AÇIK' if state.get('auto_enabled', True) else 'KAPALI'}\n"
            f"⚠️ Hata: {state.get('bad_count', 0)}/{FAIL_COUNT}\n"
            f"📊 Geçiş: {state.get('total_switches', 0)}"
        )
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=main_menu()
        )

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    if not is_authorized(message):
        return
    
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text.startswith("adminekle"):
        parts = text.split()
        if len(parts) == 3:
            username = parts[1]
            password = parts[2]
            result = api.create_admin(username, password)
            if result:
                bot.reply_to(message, f"✅ Admin `{username}` eklendi!", parse_mode="Markdown")
            else:
                bot.reply_to(message, "❌ Admin eklenemedi!")
        else:
            bot.reply_to(message, "Kullanım: `adminekle KULLANICIADI SIFRE`", parse_mode="Markdown")
    
    elif text.startswith("adminsil"):
        parts = text.split()
        if len(parts) == 2:
            username = parts[1]
            result = api.delete_admin(username)
            if result:
                bot.reply_to(message, f"✅ Admin `{username}` silindi!", parse_mode="Markdown")
            else:
                bot.reply_to(message, "❌ Admin silinemedi!")
        else:
            bot.reply_to(message, "Kullanım: `adminsil KULLANICIADI`", parse_mode="Markdown")
    
    else:
        bot.reply_to(message, "Bilmediğim bir komut. /start yaz.")

def auto_failover():
    while True:
        try:
            state = load_state()
            if not state.get("auto_enabled", True):
                time.sleep(CHECK_INTERVAL)
                continue
            
            active = state.get("active_node")
            healthy, stats = check_node_health(active)
            
            if not healthy:
                state["bad_count"] = state.get("bad_count", 0) + 1
                if state["bad_count"] >= FAIL_COUNT:
                    node = get_next_node(state)
                    success, msg = switch_to_node(node["id"], state)
                    if success:
                        for uid in ALLOWED_TELEGRAM_IDS:
                            try:
                                bot.send_message(
                                    uid,
                                    f"🔄 **Otomatik Failover!**\n\n{msg}\n📊 Trafik: {format_traffic(stats.get('current_bps', 0))}",
                                    parse_mode="Markdown"
                                )
                            except:
                                pass
            else:
                state["bad_count"] = 0
            save_state(state)
        except:
            pass
        time.sleep(CHECK_INTERVAL)

def main():
    print("=" * 60)
    print("🚀 Pasarguard Node Manager Bot")
    print(f"📡 Panel: {PANEL_URL}")
    print(f"👥 Yetkili ID'ler: {ALLOWED_TELEGRAM_IDS}")
    
    if api.get_token():
        print("✅ API bağlantısı başarılı!")
    else:
        print("❌ API bağlantısı başarısız!")
    
    threading.Thread(target=auto_failover, daemon=True).start()
    print("✅ Otomatik failover aktif!")
    print("=" * 60)
    print("✅ Bot çalışıyor!")
    print("📱 Telegram'da /start yaz")
    print("=" * 60)
    
    bot.infinity_polling()

if __name__ == "__main__":
    main()
