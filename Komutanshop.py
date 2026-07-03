#!/usr/bin/env python3
"""
PasarGuard Failover Bot - FORM-DATA İLE ÇALIŞIYOR!
"""

import os
import json
import time
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# SSL uyarılarını kapat
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================
# KONFIGÜRASYON
# ============================================

BOT_TOKEN = "8850038202:AAGkr_QlbJJxSAvHR20-zLpWFYUSrYcwa0U"
PANEL_URL = "https://crc.fastline-tm-belet-film.ru:8000"
ADMIN_USER = "komutan31"
ADMIN_PASS = "KomutanPanel_13"
SS_TAG = "Shadowsocks TCP"
ALLOWED_IDS = [8359722718, 7115611768]

CHECK_INTERVAL = 30
MIN_TRAFFIC_BPS = 500
FAIL_COUNT = 3
NET_IFACE = "eth0"

NODE_LIST = [
    {"id": 1, "name": "Node-1", "ip": "11.22.33.44"},
    {"id": 2, "name": "Node-2", "ip": "55.66.77.88"},
    {"id": 3, "name": "Node-3", "ip": "99.00.11.22"},
]

STATE_FILE = "pasarguard_state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ============================================
# API SINIFI - FORM-DATA İLE GİRİŞ
# ============================================

class PasarAPI:
    def __init__(self):
        self.token = None
        self.session = requests.Session()
        self.session.verify = False
        self.base_url = PANEL_URL

    def login(self) -> bool:
        """FORM-DATA ile giriş - BU ÇALIŞIYOR!"""
        try:
            logging.info("🔐 API giriş (form-data)...")
            
            # ÖNEMLİ: data= kullan, json= DEĞİL!
            response = self.session.post(
                f"{self.base_url}/api/admin/token",
                data={  # ← data= kullan!
                    "username": ADMIN_USER,
                    "password": ADMIN_PASS
                },
                timeout=10
            )
            
            logging.info(f"   Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access_token")
                if self.token:
                    self.session.headers.update({
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json"
                    })
                    logging.info(f"✅ Token alındı: {self.token[:20]}...")
                    return True
            else:
                logging.error(f"❌ Hata {response.status_code}: {response.text[:200]}")
                
        except Exception as e:
            logging.error(f"❌ Exception: {e}")
            
        return False

    def ensure_login(self) -> bool:
        if not self.token:
            return self.login()
        return True

    def get_hosts(self) -> dict:
        if not self.ensure_login():
            return {}
        try:
            r = self.session.get(f"{self.base_url}/api/hosts", timeout=10)
            return r.json() if r.status_code == 200 else {}
        except:
            return {}

    def put_hosts(self, hosts: dict) -> bool:
        if not self.ensure_login():
            return False
        try:
            r = self.session.put(f"{self.base_url}/api/hosts", json=hosts, timeout=10)
            return r.status_code == 200
        except:
            return False

    def get_nodes(self) -> list:
        if not self.ensure_login():
            return []
        try:
            r = self.session.get(f"{self.base_url}/api/nodes", timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return data.get("nodes", [])
            return []
        except:
            return []


# ============================================
# STATE
# ============================================

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"node_index": 0, "auto_enabled": True, "bad_count": 0, 
                "last_rx": None, "last_tx": None, "last_ts": None, "switch_count": 0}
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
    except:
        data = {}
    data.setdefault("node_index", 0)
    data.setdefault("auto_enabled", True)
    data.setdefault("bad_count", 0)
    data.setdefault("last_rx", None)
    data.setdefault("last_tx", None)
    data.setdefault("last_ts", None)
    data.setdefault("switch_count", 0)
    return data

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ============================================
# TRAFİK
# ============================================

def read_iface_bytes(iface: str):
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f:
                if ":" in line:
                    name, data = line.split(":", 1)
                    if name.strip() == iface:
                        parts = data.split()
                        return int(parts[0]), int(parts[8])
    except:
        pass
    return 0, 0

def calc_bps(state: dict):
    now = time.time()
    try:
        rx, tx = read_iface_bytes(NET_IFACE)
    except:
        return None

    last_rx = state.get("last_rx")
    last_tx = state.get("last_tx")
    last_ts = state.get("last_ts")

    state["last_rx"] = rx
    state["last_tx"] = tx
    state["last_ts"] = now

    if last_rx is None:
        return None

    dt = max(1.0, now - float(last_ts))
    delta = (rx - int(last_rx)) + (tx - int(last_tx))
    return int(delta / dt)


# ============================================
# CORE
# ============================================

api = PasarAPI()

def get_current_host_ips():
    hosts = api.get_hosts()
    if not hosts:
        return None, None
    ss_hosts = hosts.get(SS_TAG, [])
    if isinstance(ss_hosts, list) and len(ss_hosts) >= 2:
        return ss_hosts[0].get("address"), ss_hosts[1].get("address")
    return None, None

def set_host_ips(ip1: str, ip2: str) -> bool:
    hosts = api.get_hosts()
    if not hosts or SS_TAG not in hosts:
        return False
    ss_hosts = hosts[SS_TAG]
    if not isinstance(ss_hosts, list) or len(ss_hosts) < 2:
        return False
    ss_hosts[0]["address"] = ip1
    ss_hosts[1]["address"] = ip2
    ss_hosts[0]["is_disabled"] = False
    ss_hosts[1]["is_disabled"] = False
    hosts[SS_TAG] = ss_hosts
    return api.put_hosts(hosts)

def switch_to_next_node(state: dict) -> bool:
    nodes = api.get_nodes()
    if not nodes:
        logging.error("❌ Node listesi alınamadı!")
        return False
    nodes_dict = {n.get("id"): n.get("ip") for n in nodes if isinstance(n, dict)}
    current_idx = state.get("node_index", 0) % len(NODE_LIST)
    next_idx = (current_idx + 1) % len(NODE_LIST)
    node1 = NODE_LIST[current_idx]
    node2 = NODE_LIST[next_idx]
    ip1 = nodes_dict.get(node1["id"], node1["ip"])
    ip2 = nodes_dict.get(node2["id"], node2["ip"])
    logging.info(f"🔄 Geçiş: {node1['name']} ({ip1}) / {node2['name']} ({ip2})")
    if set_host_ips(ip1, ip2):
        state["node_index"] = (next_idx + 1) % len(NODE_LIST)
        state["bad_count"] = 0
        state["switch_count"] = state.get("switch_count", 0) + 1
        return True
    return False


# ============================================
# TELEGRAM
# ============================================

def menu_kb(state: dict):
    auto = "🟢 AÇIK" if state.get("auto_enabled", True) else "🔴 KAPALI"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Durum", callback_data="status")],
        [InlineKeyboardButton("🔄 Manuel Geçiş", callback_data="switch")],
        [InlineKeyboardButton(f"⚙️ Otomatik: {auto}", callback_data="toggle_auto")],
        [InlineKeyboardButton("📋 Node'lar", callback_data="nodes")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_IDS:
        await update.message.reply_text("❌ Yetkiniz yok!")
        return
    if not api.login():
        await update.message.reply_text("❌ API bağlantısı başarısız! Logları kontrol edin.")
        return
    state = load_state()
    await update.message.reply_text(
        "🛡️ **PasarGuard Failover Bot**\n\n"
        "3 node arasında otomatik geçiş.\n"
        "Trafik düşünce sıradaki node devreye girer.\n\n"
        "📌 Menüyü kullanın.",
        reply_markup=menu_kb(state),
        parse_mode="Markdown"
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ALLOWED_IDS:
        await q.edit_message_text("❌ Yetkiniz yok!")
        return
    state = load_state()
    action = q.data
    
    if action == "status":
        ip1, ip2 = get_current_host_ips()
        bps = calc_bps(state)
        save_state(state)
        current_idx = state.get("node_index", 0) % len(NODE_LIST)
        next_idx = (current_idx + 1) % len(NODE_LIST)
        standby_idx = (next_idx + 1) % len(NODE_LIST)
        text = f"📊 **Durum**\n\n"
        text += f"**Host IP'leri:**\n  - Host 1: `{ip1 or 'Bilinmiyor'}`\n  - Host 2: `{ip2 or 'Bilinmiyor'}`\n\n"
        text += f"**Node Döngüsü:**\n  - ✅ {NODE_LIST[current_idx]['name']} (Aktif-1)\n  - ✅ {NODE_LIST[next_idx]['name']} (Aktif-2)\n  - ⏸️ {NODE_LIST[standby_idx]['name']} (Yedek)\n\n"
        text += f"**Trafik:** {bps if bps is not None else 'Ölçülemiyor'} B/s\n"
        text += f"**Eşik:** {MIN_TRAFFIC_BPS} B/s\n\n"
        text += f"**Otomatik:** {'AÇIK' if state.get('auto_enabled') else 'KAPALI'}\n"
        text += f"**Geçiş:** {state.get('switch_count', 0)} kez\n"
        text += f"**Hata:** {state.get('bad_count', 0)}/{FAIL_COUNT}"
        await q.edit_message_text(text, reply_markup=menu_kb(state), parse_mode="Markdown")
    
    elif action == "switch":
        if switch_to_next_node(state):
            save_state(state)
            await q.edit_message_text("✅ Manuel geçiş başarılı!", reply_markup=menu_kb(state))
        else:
            await q.edit_message_text("❌ Manuel geçiş başarısız!", reply_markup=menu_kb(state))
    
    elif action == "toggle_auto":
        state["auto_enabled"] = not state.get("auto_enabled", True)
        save_state(state)
        await q.edit_message_text(f"✅ Otomatik: {'AÇIK' if state['auto_enabled'] else 'KAPALI'}", reply_markup=menu_kb(state))
    
    elif action == "nodes":
        nodes = api.get_nodes()
        text = "📋 **Node'lar**\n\n"
        for node in nodes:
            if isinstance(node, dict):
                nid = node.get("id")
                name = node.get("name", f"Node-{nid}")
                ip = node.get("ip", "IP yok")
                text += f"  - {name} (ID:{nid}): {ip}\n"
        await q.edit_message_text(text, reply_markup=menu_kb(state))


# ============================================
# OTOMATİK FAILOVER
# ============================================

async def auto_failover_job(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state.get("auto_enabled", True):
        return
    if not api.ensure_login():
        logging.error("API giriş başarısız!")
        return
    bps = calc_bps(state)
    save_state(state)
    if bps is None:
        return
    if bps < MIN_TRAFFIC_BPS:
        state["bad_count"] = state.get("bad_count", 0) + 1
        if state["bad_count"] >= FAIL_COUNT:
            logging.info("🔄 Otomatik failover başlatılıyor...")
            if switch_to_next_node(state):
                save_state(state)
                for uid in ALLOWED_IDS:
                    try:
                        await context.bot.send_message(uid, f"🔄 **Failover!**\nTrafik: {bps} B/s\nGeçiş: {state.get('switch_count', 0)}. kez")
                    except:
                        pass
            else:
                state["bad_count"] = 0
                save_state(state)
    else:
        if state["bad_count"] > 0:
            state["bad_count"] = 0
            save_state(state)


# ============================================
# MAIN
# ============================================

def main():
    print("🚀 PasarGuard Failover Bot")
    print(f"📡 Panel: {PANEL_URL}")
    print(f"📊 Node'lar: {len(NODE_LIST)} adet")
    print("=" * 40)
    
    # Önce giriş testi yap
    if not api.login():
        print("❌ API bağlantısı başarısız! Çıkılıyor...")
        return
    
    print("✅ API bağlantısı başarılı!")
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # JobQueue'yi başlat
    if app.job_queue:
        app.job_queue.run_repeating(auto_failover_job, interval=CHECK_INTERVAL, first=5)
        print(f"🔄 Otomatik failover başlatıldı! (Her {CHECK_INTERVAL}s)")
    else:
        print("❌ JobQueue başlatılamadı!")
        return
    
    print("✅ Bot çalışıyor! /start yazın.")
    app.run_polling()

if __name__ == "__main__":
    main()
