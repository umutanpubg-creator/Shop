#!/usr/bin/env python3
"""
Pasarguard Bot - ÇALIŞAN VERSİYON
API: Form-Data ile giriş yapıyor
"""

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import threading
import time
import json
import os
import logging
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# ============================================
# KONFIGÜRASYON
# ============================================

BOT_TOKEN = '8850038202:AAGkr_QlbJJxSAvHR20-zLpWFYUSrYcwa0U'
PANEL_URL = "https://crc.fastline-tm-belet-film.ru:8000"
ADMIN_USER = "komutan31"
ADMIN_PASS = "KomutanPanel_13"
ALLOWED_IDS = [8359722718, 7115611768]

# Node'lar (paneldeki gerçek node ID'leri ile güncelle)
NODES = [
    {"id": 1, "name": "Node-1", "ip": "11.22.33.44"},
    {"id": 2, "name": "Node-2", "ip": "55.66.77.88"},
]

# ============================================
# API SINIFI - FORM-DATA İLE GİRİŞ
# ============================================

class PasarGuardAPI:
    def __init__(self):
        self.token = None
        self.session = requests.Session()
        self.session.verify = False
        self.base_url = PANEL_URL
        
    def login(self):
        """PasarGuard API'sine giriş yap - Form-Data formatında"""
        try:
            # Form-data olarak gönder (ÇALIŞIYOR!)
            login_data = {
                "username": ADMIN_USER,
                "password": ADMIN_PASS
            }
            
            response = self.session.post(
                f"{self.base_url}/api/admin/token",
                data=login_data,  # data= kullan, json= değil!
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access_token")
                if self.token:
                    self.session.headers.update({
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json"
                    })
                    logging.info("✅ API giriş başarılı!")
                    logging.info(f"Token: {self.token[:20]}...")
                    return True
            else:
                logging.error(f"❌ Giriş başarısız: {response.status_code}")
                logging.error(f"Response: {response.text}")
                
        except Exception as e:
            logging.error(f"❌ Giriş hatası: {e}")
            
        return False
    
    def get_nodes(self):
        """Node listesini getir"""
        try:
            response = self.session.get(
                f"{self.base_url}/api/nodes",
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            logging.error(f"Node listesi alınamadı: {e}")
            return []
    
    def get_node_stats(self, node_id):
        """Node istatistiklerini getir"""
        try:
            response = self.session.get(
                f"{self.base_url}/api/nodes/{node_id}/stats",
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            return {}
        except Exception as e:
            logging.error(f"Node stats alınamadı: {e}")
            return {}
    
    def get_hosts(self):
        """Host bilgilerini getir"""
        try:
            # Önce node'ları al
            nodes = self.get_nodes()
            if nodes and len(nodes) > 0:
                # İlk node'un IP'sini döndür
                return {"ip": nodes[0].get("ip", "Bilinmiyor")}
            return {"ip": "Bilinmiyor"}
        except:
            return {"ip": "Bilinmiyor"}
    
    def update_node_ip(self, node_id, ip):
        """Node IP'sini güncelle"""
        try:
            response = self.session.put(
                f"{self.base_url}/api/nodes/{node_id}",
                json={"ip": ip},
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logging.error(f"Node IP güncelleme hatası: {e}")
            return False
    
    def switch_node(self, new_node_id):
        """Aktif node'u değiştir"""
        try:
            # Önce tüm node'ları al
            nodes = self.get_nodes()
            if not nodes:
                return False
            
            # Hedef node'u bul
            target_node = None
            for node in nodes:
                if node.get("id") == new_node_id:
                    target_node = node
                    break
            
            if not target_node:
                return False
            
            # Node'u güncelle
            return self.update_node_ip(new_node_id, target_node.get("ip"))
            
        except Exception as e:
            logging.error(f"Node switch hatası: {e}")
            return False

# ============================================
# BOT KODU
# ============================================

api = PasarGuardAPI()
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
        
        if api.switch_node(next_node["id"]):
            state["active"] = next_node["id"]
            state["bad"] = 0
            state["switches"] = state.get("switches", 0) + 1
            save_state(state)
            bot.answer_callback_query(call.id, f"✅ {next_node['name']} geçildi!")
        else:
            bot.answer_callback_query(call.id, "❌ Geçiş başarısız!")
        
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
            stats = api.get_node_stats(n["id"])
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
    """Otomatik failover kontrolü"""
    while True:
        try:
            state = load_state()
            if state.get("auto", True):
                active = state["active"]
                stats = api.get_node_stats(active)
                bps = stats.get("current_bps", 0)
                
                # Eğer trafik 1000 bytes/s altındaysa hata sayısını artır
                if bps < 1000:
                    state["bad"] = state.get("bad", 0) + 1
                    logging.info(f"⚠️ Düşük trafik: {bps} B/s, Hata: {state['bad']}/3")
                    
                    if state["bad"] >= 3:
                        # Geçiş yap
                        for i, n in enumerate(NODES):
                            if n["id"] == active:
                                next_node = NODES[(i+1) % len(NODES)]
                                break
                        
                        logging.info(f"🔄 Failover başlatılıyor: {next_node['name']}")
                        
                        if api.switch_node(next_node["id"]):
                            state["active"] = next_node["id"]
                            state["bad"] = 0
                            state["switches"] = state.get("switches", 0) + 1
                            save_state(state)
                            
                            # Admin'lere bildir
                            for uid in ALLOWED_IDS:
                                try:
                                    bot.send_message(uid, 
                                        f"🔄 **Otomatik Failover!**\n"
                                        f"✅ {next_node['name']} ({next_node['ip']}) geçildi!"
                                    )
                                except:
                                    pass
                else:
                    # Trafik normal, hatayı sıfırla
                    if state["bad"] > 0:
                        state["bad"] = 0
                        save_state(state)
                        logging.info(f"✅ Trafik normale döndü: {bps} B/s")
                        
        except Exception as e:
            logging.error(f"Auto-failover hatası: {e}")
        
        time.sleep(30)

# ============================================
# BAŞLATMA
# ============================================

print("🚀 Pasarguard Bot başlatılıyor...")
print(f"📡 Panel URL: {PANEL_URL}")
print(f"👤 Kullanıcı: {ADMIN_USER}")

# API'ye giriş yap
if api.login():
    print("✅ Panel API bağlantısı başarılı!")
    
    # Node'ları kontrol et
    nodes = api.get_nodes()
    if nodes:
        print(f"📋 Paneldeki node'lar: {len(nodes)} adet")
        for node in nodes:
            print(f"   - Node {node.get('id')}: {node.get('name', 'Isimsiz')} ({node.get('ip', 'IP yok')})")
    else:
        print("⚠️ Node listesi alınamadı veya boş!")
else:
    print("❌ Panel API bağlantısı başarısız!")
    print("   Lütfen şunları kontrol edin:")
    print(f"   - Panel URL: {PANEL_URL}")
    print(f"   - Kullanıcı adı: {ADMIN_USER}")
    print("   - Şifre doğru mu?")

# Auto failover'ı başlat
threading.Thread(target=auto_failover, daemon=True).start()
print("🔄 Otomatik failover başlatıldı!")

print("✅ Bot çalışıyor! Telegram'da /start yazın.")
print("📊 Loglar aşağıda görünecek...")
print("=" * 50)

# Bot'u başlat
try:
    bot.infinity_polling()
except KeyboardInterrupt:
    print("\n🛑 Bot durduruldu.")
except Exception as e:
    print(f"❌ Bot hatası: {e}")
