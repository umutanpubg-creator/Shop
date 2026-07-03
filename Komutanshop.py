#!/usr/bin/env python3
"""
Pasarguard Bot - DÜZELTİLMİŞ
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

# ============================================
# API SINIFI - DÜZELTİLMİŞ
# ============================================

class PasarGuardAPI:
    def __init__(self):
        self.token = None
        self.session = requests.Session()
        self.session.verify = False
        self.base_url = PANEL_URL
        self.nodes_cache = []
        self.last_nodes_update = 0
        
    def login(self):
        try:
            login_data = {
                "username": ADMIN_USER,
                "password": ADMIN_PASS
            }
            
            response = self.session.post(
                f"{self.base_url}/api/admin/token",
                data=login_data,
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
        """Node listesini getir - Hata yönetimi eklendi"""
        try:
            response = self.session.get(
                f"{self.base_url}/api/nodes",
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Eğer data string ise parse et
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except:
                        logging.error("Node verisi string ama JSON değil")
                        return []
                
                # Eğer data dict ise ve içinde nodes varsa
                if isinstance(data, dict):
                    if 'nodes' in data:
                        return data['nodes']
                    elif 'data' in data:
                        return data['data']
                    # Belki doğrudan node listesi dict içinde
                    return [data] if data else []
                
                # Eğer data list ise
                if isinstance(data, list):
                    return data
                
                logging.warning(f"Beklenmeyen node veri formatı: {type(data)}")
                return []
                
            return []
            
        except Exception as e:
            logging.error(f"Node listesi alınamadı: {e}")
            return []
    
    def get_node_stats(self, node_id):
        try:
            response = self.session.get(
                f"{self.base_url}/api/nodes/{node_id}/stats",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, str):
                    return json.loads(data)
                return data
            return {}
        except Exception as e:
            logging.error(f"Node stats alınamadı: {e}")
            return {}
    
    def get_active_node_ip(self):
        """Aktif node'un IP'sini getir"""
        try:
            nodes = self.get_nodes()
            if nodes and len(nodes) > 0:
                # İlk node'un IP'sini al
                first_node = nodes[0]
                if isinstance(first_node, dict):
                    return first_node.get("ip", "Bilinmiyor")
            return "Bilinmiyor"
        except:
            return "Bilinmiyor"
    
    def update_node_ip(self, node_id, ip):
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
        try:
            nodes = self.get_nodes()
            if not nodes:
                return False
            
            target_node = None
            for node in nodes:
                if isinstance(node, dict) and node.get("id") == new_node_id:
                    target_node = node
                    break
            
            if not target_node:
                return False
            
            return self.update_node_ip(new_node_id, target_node.get("ip"))
            
        except Exception as e:
            logging.error(f"Node switch hatası: {e}")
            return False

# ============================================
# BOT KODU - DÜZELTİLMİŞ
# ============================================

api = PasarGuardAPI()
bot = telebot.TeleBot(BOT_TOKEN)
STATE_FILE = "state.json"

# Varsayılan node'lar (panelden çekilecek)
DEFAULT_NODES = [
    {"id": 1, "name": "Node-1", "ip": "11.22.33.44"},
    {"id": 2, "name": "Node-2", "ip": "55.66.77.88"},
]

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"active": 1, "auto": True, "bad": 0, "switches": 0}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"active": 1, "auto": True, "bad": 0, "switches": 0}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)

def get_nodes_from_api():
    """API'den node'ları al, hata durumunda default kullan"""
    try:
        nodes = api.get_nodes()
        if nodes and len(nodes) > 0:
            # Node'ları düzgün formata çevir
            formatted_nodes = []
            for node in nodes:
                if isinstance(node, dict):
                    formatted_nodes.append({
                        "id": node.get("id", 0),
                        "name": node.get("name", f"Node-{node.get('id', 0)}"),
                        "ip": node.get("ip", "0.0.0.0")
                    })
            return formatted_nodes
    except Exception as e:
        logging.error(f"Node çekme hatası: {e}")
    
    return DEFAULT_NODES

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
    ip = api.get_active_node_ip()
    
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
    nodes = get_nodes_from_api()
    
    if data == "status":
        ip = api.get_active_node_ip()
        bot.edit_message_text(
            f"📊 **Durum**\n\n📍 IP: `{ip}`\n🔄 Otomatik: {'AÇIK' if state['auto'] else 'KAPALI'}\n⚠️ Hata: {state['bad']}/3\n🔄 Geçiş: {state['switches']}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=menu()
        )
    
    elif data == "switch":
        if not nodes:
            bot.answer_callback_query(call.id, "❌ Node listesi boş!")
            return
            
        active = state["active"]
        # Mevcut node'u bul
        current_index = 0
        for i, n in enumerate(nodes):
            if n["id"] == active:
                current_index = i
                break
        
        # Sıradaki node
        next_node = nodes[(current_index + 1) % len(nodes)]
        
        if api.switch_node(next_node["id"]):
            state["active"] = next_node["id"]
            state["bad"] = 0
            state["switches"] = state.get("switches", 0) + 1
            save_state(state)
            bot.answer_callback_query(call.id, f"✅ {next_node['name']} geçildi!")
        else:
            bot.answer_callback_query(call.id, "❌ Geçiş başarısız!")
        
        ip = api.get_active_node_ip()
        bot.edit_message_text(
            f"🛡️ **Pasarguard Bot**\n\n📍 IP: `{ip}`\n🔄 Otomatik: {'AÇIK' if state['auto'] else 'KAPALI'}\n⚠️ Hata: {state['bad']}/3",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=menu()
        )
    
    elif data == "nodes":
        text = "📋 **Node'lar**\n\n"
        if not nodes:
            text = "❌ Node listesi alınamadı!"
        else:
            for n in nodes:
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
    while True:
        try:
            state = load_state()
            if state.get("auto", True):
                active = state["active"]
                stats = api.get_node_stats(active)
                bps = stats.get("current_bps", 0)
                
                if bps < 1000:
                    state["bad"] = state.get("bad", 0) + 1
                    if state["bad"] >= 3:
                        nodes = get_nodes_from_api()
                        if nodes:
                            for i, n in enumerate(nodes):
                                if n["id"] == active:
                                    next_node = nodes[(i+1) % len(nodes)]
                                    break
                            
                            if api.switch_node(next_node["id"]):
                                state["active"] = next_node["id"]
                                state["bad"] = 0
                                state["switches"] = state.get("switches", 0) + 1
                                save_state(state)
                                
                                for uid in ALLOWED_IDS:
                                    try:
                                        bot.send_message(uid, 
                                            f"🔄 **Otomatik Failover!**\n"
                                            f"✅ {next_node['name']} ({next_node['ip']}) geçildi!"
                                        )
                                    except:
                                        pass
                else:
                    state["bad"] = 0
                save_state(state)
        except Exception as e:
            logging.error(f"Auto-failover hatası: {e}")
        time.sleep(30)

# ============================================
# BAŞLATMA
# ============================================

print("🚀 Pasarguard Bot başlatılıyor...")
print(f"📡 Panel URL: {PANEL_URL}")
print(f"👤 Kullanıcı: {ADMIN_USER}")

if api.login():
    print("✅ Panel API bağlantısı başarılı!")
    
    # Node'ları al
    nodes = get_nodes_from_api()
    print(f"📋 Paneldeki node'lar: {len(nodes)} adet")
    for node in nodes:
        print(f"   - Node {node['id']}: {node['name']} ({node['ip']})")
else:
    print("❌ Panel API bağlantısı başarısız!")

threading.Thread(target=auto_failover, daemon=True).start()
print("🔄 Otomatik failover başlatıldı!")

print("✅ Bot çalışıyor! Telegram'da /start yazın.")
print("=" * 50)

try:
    bot.infinity_polling()
except KeyboardInterrupt:
    print("\n🛑 Bot durduruldu.")
