#!/usr/bin/env python3
"""
Pasarguard Bot - PANELİN İÇİN ÖZEL!
"""

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import threading
import time
import json
import os
import logging

# ============================================
# KONFIGÜRASYON - ZATEN SENİN İÇİN AYARLANDI!
# ============================================

BOT_TOKEN = '8850038202:AAGkr_QlbJJxSAvHR20-zLpWFYUSrYcwa0U'
PANEL_URL = "https://crc.fastline-tm-belet-film.ru:8000"
ADMIN_USER = "komutan31"
ADMIN_PASS = "KomutanPanel_13"
ALLOWED_IDS = [8359722718, 7115611768]

NODES = [
    {"id": 1, "name": "Node-1", "ip": "11.22.33.44"},
    {"id": 2, "name": "Node-2", "ip": "55.66.77.88"},
]

# ============================================
# KOD - DEĞİŞTİRME!
# ============================================

bot = telebot.TeleBot(BOT_TOKEN)
STATE_FILE = "state.json"
logging.basicConfig(level=logging.INFO)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"active": NODES[0]["id"], "auto": True, "bad": 0, "switches": 0}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)

class API:
    def __init__(self):
        self.token = None
        self.s = requests.Session()
        self.s.verify = False

    def login(self):
        try:
            # Önce cookie ile giriş dene
            self.s.post(f"{PANEL_URL}/login", data={"username": ADMIN_USER, "password": ADMIN_PASS})
            
            # Sonra API token al
            r = self.s.post(f"{PANEL_URL}/api/token", json={"username": ADMIN_USER, "password": ADMIN_PASS})
            if r.status_code == 200:
                self.token = r.json().get("access_token")
                self.s.headers.update({"Authorization": f"Bearer {self.token}"})
                return True
        except:
            pass
        return False

    def get_hosts(self):
        try:
            r = self.s.get(f"{PANEL_URL}/api/hosts")
            return r.json() if r.status_code == 200 else {}
        except:
            return {}

    def update_ip(self, ip):
        try:
            r = self.s.put(f"{PANEL_URL}/api/hosts", json={"ip": ip})
            return r.status_code == 200
        except:
            return False

    def get_stats(self, node_id):
        try:
            r = self.s.get(f"{PANEL_URL}/api/nodes/{node_id}/stats")
            return r.json() if r.status_code == 200 else {}
        except:
            return {}

api = API()

def menu():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("📊 Durum", callback_data="status"),
           InlineKeyboardButton("🔄 Geçiş", callback_data="switch"))
    kb.row(InlineKeyboardButton("📋 Node'lar", callback_data="nodes"),
           InlineKeyboardButton("⚙️ Otomatik", callback_data="auto"))
    return kb

@bot.message_handler(commands=['start'])
def start(msg):
    if msg.chat.id not in ALLOWED_IDS:
        bot.reply_to(msg, "❌ Yetkiniz yok!")
        return
    
    state = load_state()
    hosts = api.get_hosts()
    ip = hosts.get("ip", "Bilinmiyor")
    
    bot.send_message(msg.chat.id,
        f"🛡️ **Pasarguard Bot**\n\n"
        f"📍 IP: `{ip}`\n"
        f"🔄 Otomatik: {'AÇIK' if state['auto'] else 'KAPALI'}\n"
        f"⚠️ Hata: {state['bad']}/3",
        parse_mode="Markdown",
        reply_markup=menu()
    )

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if call.from_user.id not in ALLOWED_IDS:
        bot.answer_callback_query(call.id, "Yetkiniz yok!")
        return
    
    state = load_state()
    data = call.data
    
    if data == "status":
        hosts = api.get_hosts()
        ip = hosts.get("ip", "Bilinmiyor")
        bot.edit_message_text(
            f"📊 **Durum**\n\n📍 IP: `{ip}`\n🔄 Otomatik: {'AÇIK' if state['auto'] else 'KAPALI'}\n⚠️ Hata: {state['bad']}/3\n🔄 Geçiş: {state['switches']}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=menu()
        )
    
    elif data == "switch":
        # Sıradaki node'a geç
        active = state["active"]
        for i, n in enumerate(NODES):
            if n["id"] == active:
                next_node = NODES[(i+1) % len(NODES)]
                break
        
        if api.update_ip(next_node["ip"]):
            state["active"] = next_node["id"]
            state["bad"] = 0
            state["switches"] = state.get("switches", 0) + 1
            save_state(state)
            bot.answer_callback_query(call.id, f"✅ {next_node['name']} geçildi!")
        else:
            bot.answer_callback_query(call.id, "❌ Geçiş başarısız!")
        
        # Menüyü güncelle
        hosts = api.get_hosts()
        ip = hosts.get("ip", "Bilinmiyor")
        bot.edit_message_text(
            f"🛡️ **Pasarguard Bot**\n\n📍 IP: `{ip}`\n🔄 Otomatik: {'AÇIK' if state['auto'] else 'KAPALI'}\n⚠️ Hata: {state['bad']}/3",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=menu()
        )
    
    elif data == "nodes":
        text = "📋 **Node'lar**\n\n"
        for n in NODES:
            active = "▶️ " if n["id"] == state["active"] else "   "
            stats = api.get_stats(n["id"])
            bps = stats.get("current_bps", 0)
            text += f"{active}{n['name']}: {n['ip']} ({bps} B/s)\n"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=menu())
    
    elif data == "auto":
        state["auto"] = not state.get("auto", True)
        save_state(state)
        bot.answer_callback_query(call.id, f"Otomatik: {'AÇIK' if state['auto'] else 'KAPALI'}")
        bot.edit_message_text(
            f"✅ Otomatik: {'AÇIK' if state['auto'] else 'KAPALI'}",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=menu()
        )

def auto_failover():
    while True:
        try:
            state = load_state()
            if state.get("auto", True):
                active = state["active"]
                stats = api.get_stats(active)
                bps = stats.get("current_bps", 0)
                
                if bps < 1000:
                    state["bad"] = state.get("bad", 0) + 1
                    if state["bad"] >= 3:
                        # Geçiş yap
                        for i, n in enumerate(NODES):
                            if n["id"] == active:
                                next_node = NODES[(i+1) % len(NODES)]
                                break
                        if api.update_ip(next_node["ip"]):
                            state["active"] = next_node["id"]
                            state["bad"] = 0
                            state["switches"] = state.get("switches", 0) + 1
                            save_state(state)
                            for uid in ALLOWED_IDS:
                                try:
                                    bot.send_message(uid, f"🔄 **Otomatik Failover!**\n{next_node['name']} ({next_node['ip']}) geçildi!")
                                except:
                                    pass
                else:
                    state["bad"] = 0
                save_state(state)
        except:
            pass
        time.sleep(30)

# Çalıştır
print("🚀 Bot başlatılıyor...")

# Giriş yap
if api.login():
    print("✅ Panel giriş başarılı!")
else:
    print("❌ Panel giriş başarısız!")

# Auto failover'ı başlat
threading.Thread(target=auto_failover, daemon=True).start()

print("✅ Bot çalışıyor!")
bot.infinity_polling()
