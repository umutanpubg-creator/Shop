#!/usr/bin/env python3
"""
PasarGuard SS Failover Bot - BASİT VE NET
- Host'ta 2 IP var, 3 node döngüsel
- Trafik düşünce boş node devreye giriyor
"""

import os
import json
import time
import logging
from typing import Optional, Tuple, List
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ============================================
# KONFIGÜRASYON
# ============================================

BOT_TOKEN = "8850038202:AAGkr_QlbJJxSAvHR20-zLpWFYUSrYcwa0U"

# PasarGuard Panel
PANEL_URL = "https://crc.fastline-tm-belet-film.ru:8000"
ADMIN_USER = "komutan31"
ADMIN_PASS = "KomutanPanel_13"

# Shadowsocks Tag (PasarGuard'daki SS inbound adı)
SS_TAG = "Shadowsocks TCP"  # Kendi SS tag'ınla değiştir!

# Yetkili Telegram ID'leri
ALLOWED_IDS = [8359722718, 7115611768]

# Failover Ayarları
CHECK_INTERVAL = 30  # Kontrol aralığı (saniye)
MIN_TRAFFIC_BPS = 500  # Minimum trafik (bytes/s) - BUNUN ALTINA İNİNCE GEÇİŞ YAP
FAIL_COUNT = 3  # Kaç kez düşük trafik görünce geçiş yapılacak
NET_IFACE = "eth0"  # Ağ arayüzü (ifconfig ile bak)

# Node'lar (PasarGuard'daki node ID'leri)
# SIRALAMA ÖNEMLİ: Host'ta 2 IP var, sırayla değişecek
NODE_LIST = [
    {"id": 1, "name": "Node-1", "ip": "11.22.33.44"},
    {"id": 2, "name": "Node-2", "ip": "55.66.77.88"},
    {"id": 3, "name": "Node-3", "ip": "99.00.11.22"},
]

STATE_FILE = "pasarguard_state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ============================================
# PASARGUARD API
# ============================================

class PasarGuardAPI:
    def __init__(self):
        self.token = None
        self.session = requests.Session()
        self.session.verify = False
        self.base_url = PANEL_URL

    def login(self) -> bool:
        try:
            response = self.session.post(
                f"{self.base_url}/api/admin/token",
                data={"username": ADMIN_USER, "password": ADMIN_PASS},
                timeout=10
            )
            if response.status_code == 200:
                self.token = response.json().get("access_token")
                if self.token:
                    self.session.headers.update({
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json"
                    })
                    return True
            return False
        except:
            return False

    def get_hosts(self) -> dict:
        try:
            r = self.session.get(f"{self.base_url}/api/hosts", timeout=10)
            return r.json() if r.status_code == 200 else {}
        except:
            return {}

    def put_hosts(self, hosts: dict) -> bool:
        try:
            r = self.session.put(f"{self.base_url}/api/hosts", json=hosts, timeout=10)
            return r.status_code == 200
        except:
            return False

    def get_nodes(self) -> List[dict]:
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

    def update_node_ip(self, node_id: int, ip: str) -> bool:
        try:
            r = self.session.put(
                f"{self.base_url}/api/nodes/{node_id}",
                json={"ip": ip},
                timeout=10
            )
            return r.status_code == 200
        except:
            return False


# ============================================
# STATE YÖNETİMİ
# ============================================

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {
            "node_index": 0,  # Sıradaki node index'i (0,1,2)
            "auto_enabled": True,
            "bad_count": 0,
            "last_rx": None,
            "last_tx": None,
            "last_ts": None,
            "switch_count": 0,
        }
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
    
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
# TRAFİK ÖLÇÜM
# ============================================

def read_iface_bytes(iface: str) -> Tuple[int, int]:
    with open("/proc/net/dev", "r") as f:
        for line in f:
            if ":" in line:
                name, data = line.split(":", 1)
                if name.strip() == iface:
                    parts = data.split()
                    return int(parts[0]), int(parts[8])  # RX, TX
    raise RuntimeError(f"Interface {iface} not found")

def calc_bps(state: dict) -> Optional[int]:
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
# CORE MANTIK - HOST IP'LERİNİ GÜNCELLE
# ============================================

def get_current_host_ips(api: PasarGuardAPI) -> Tuple[Optional[str], Optional[str]]:
    """Host'taki SS IP'lerini al"""
    hosts = api.get_hosts()
    if not hosts:
        return None, None
    
    ss_hosts = hosts.get(SS_TAG, [])
    if isinstance(ss_hosts, list) and len(ss_hosts) >= 2:
        return ss_hosts[0].get("address"), ss_hosts[1].get("address")
    return None, None

def set_host_ips(api: PasarGuardAPI, ip1: str, ip2: str) -> bool:
    """Host'a 2 IP yaz"""
    hosts = api.get_hosts()
    if not hosts:
        return False
    
    if SS_TAG not in hosts:
        logging.error(f'❌ "{SS_TAG}" bulunamadı!')
        return False
    
    ss_hosts = hosts[SS_TAG]
    if not isinstance(ss_hosts, list) or len(ss_hosts) < 2:
        logging.error(f'❌ "{SS_TAG}" en az 2 host olmalı!')
        return False
    
    # IP'leri güncelle
    ss_hosts[0]["address"] = ip1
    ss_hosts[1]["address"] = ip2
    ss_hosts[0]["is_disabled"] = False
    ss_hosts[1]["is_disabled"] = False
    
    hosts[SS_TAG] = ss_hosts
    
    success = api.put_hosts(hosts)
    if success:
        logging.info(f"✅ Host IP'leri güncellendi: {ip1} / {ip2}")
    return success

def switch_to_next_node(api: PasarGuardAPI, state: dict) -> bool:
    """
    ÖNEMLİ: Sıradaki node'u al, host'a yaz
    - Node-1 ve Node-2 host'ta
    - Node-3 boşta
    - Sıra geldiğinde Node-3 host'a giriyor, Node-1 yedek oluyor
    """
    # Node listesini al (ID ve IP eşleşmesi için)
    nodes = api.get_nodes()
    if not nodes:
        logging.error("❌ Node listesi alınamadı!")
        return False
    
    # Node'ları dict'e çevir
    nodes_dict = {n.get("id"): n.get("ip") for n in nodes if isinstance(n, dict)}
    
    # Şu anki index
    current_idx = state.get("node_index", 0) % len(NODE_LIST)
    next_idx = (current_idx + 1) % len(NODE_LIST)
    
    # Host'a yazılacak IP'ler
    # 1. IP: Sıradaki node
    # 2. IP: Bir sonraki node
    node1 = NODE_LIST[current_idx]
    node2 = NODE_LIST[next_idx]
    
    ip1 = nodes_dict.get(node1["id"], node1["ip"])
    ip2 = nodes_dict.get(node2["id"], node2["ip"])
    
    logging.info(f"🔄 Geçiş yapılıyor: {node1['name']} ({ip1}) / {node2['name']} ({ip2})")
    
    # Host'u güncelle
    if set_host_ips(api, ip1, ip2):
        state["node_index"] = (next_idx + 1) % len(NODE_LIST)  # Bir sonraki geçiş için
        state["bad_count"] = 0
        state["switch_count"] = state.get("switch_count", 0) + 1
        logging.info(f"✅ Geçiş başarılı! Yeni index: {state['node_index']}")
        return True
    
    return False


# ============================================
# TELEGRAM BOT
# ============================================

api = PasarGuardAPI()

def menu_kb(state: dict) -> InlineKeyboardMarkup:
    auto = "🟢 Otomatik: AÇIK" if state.get("auto_enabled", True) else "🔴 Otomatik: KAPALI"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Durum", callback_data="status")],
        [InlineKeyboardButton("🔄 Manuel Geçiş", callback_data="switch")],
        [InlineKeyboardButton(auto, callback_data="toggle_auto")],
        [InlineKeyboardButton("📋 Node'lar", callback_data="nodes")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_IDS:
        await update.message.reply_text("❌ Yetkiniz yok!")
        return
    
    if not api.login():
        await update.message.reply_text("❌ PasarGuard API bağlantısı başarısız!")
        return
    
    state = load_state()
    await update.message.reply_text(
        "🛡️ **PasarGuard Failover Bot**\n\n"
        "Host'ta 2 IP, 3 node döngüsel.\n"
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
        ip1, ip2 = get_current_host_ips(api)
        bps = calc_bps(state)
        save_state(state)
        
        # Aktif node'ları bul
        current_idx = state.get("node_index", 0) % len(NODE_LIST)
        next_idx = (current_idx + 1) % len(NODE_LIST)
        standby_idx = (next_idx + 1) % len(NODE_LIST)
        
        text = "📊 **Durum**\n\n"
        text += f"**Host IP'leri:**\n"
        text += f"  - Host 1: `{ip1 or 'Bilinmiyor'}`\n"
        text += f"  - Host 2: `{ip2 or 'Bilinmiyor'}`\n\n"
        text += f"**Node Döngüsü:**\n"
        text += f"  - ✅ {NODE_LIST[current_idx]['name']} (Aktif-1)\n"
        text += f"  - ✅ {NODE_LIST[next_idx]['name']} (Aktif-2)\n"
        text += f"  - ⏸️ {NODE_LIST[standby_idx]['name']} (Yedek)\n\n"
        text += f"**Trafik:** {bps if bps is not None else 'Ölçülemiyor'} B/s\n"
        text += f"**Eşik:** {MIN_TRAFFIC_BPS} B/s\n\n"
        text += f"**Otomatik:** {'AÇIK' if state.get('auto_enabled') else 'KAPALI'}\n"
        text += f"**Geçiş sayısı:** {state.get('switch_count', 0)}\n"
        text += f"**Hata sayısı:** {state.get('bad_count', 0)}/{FAIL_COUNT}"
        
        await q.edit_message_text(text, reply_markup=menu_kb(state), parse_mode="Markdown")
    
    elif action == "switch":
        if switch_to_next_node(api, state):
            save_state(state)
            await q.edit_message_text("✅ Manuel geçiş başarılı!", reply_markup=menu_kb(state))
        else:
            await q.edit_message_text("❌ Manuel geçiş başarısız!", reply_markup=menu_kb(state))
    
    elif action == "toggle_auto":
        state["auto_enabled"] = not state.get("auto_enabled", True)
        save_state(state)
        await q.edit_message_text(
            f"✅ Otomatik: {'AÇIK' if state['auto_enabled'] else 'KAPALI'}",
            reply_markup=menu_kb(state)
        )
    
    elif action == "nodes":
        nodes = api.get_nodes()
        text = "📋 **Node'lar**\n\n"
        for node in nodes:
            if not isinstance(node, dict):
                continue
            nid = node.get("id")
            name = node.get("name", f"Node-{nid}")
            ip = node.get("ip", "IP yok")
            text += f"  - {name} (ID:{nid}): {ip}\n"
        
        await q.edit_message_text(text, reply_markup=menu_kb(state))


# ============================================
# OTOMATİK FAILOVER JOB
# ============================================

async def auto_failover_job(context: ContextTypes.DEFAULT_TYPE):
    """Her CHECK_INTERVAL saniyede bir çalışır"""
    state = load_state()
    
    if not state.get("auto_enabled", True):
        return
    
    if not api.login():
        logging.error("API giriş başarısız!")
        return
    
    # Trafik ölç
    bps = calc_bps(state)
    save_state(state)
    
    if bps is None:
        logging.warning("Trafik ölçülemedi!")
        return
    
    # Trafik düşük mü?
    if bps < MIN_TRAFFIC_BPS:
        state["bad_count"] = state.get("bad_count", 0) + 1
        logging.info(f"⚠️ Düşük trafik: {bps} B/s ({state['bad_count']}/{FAIL_COUNT})")
        
        if state["bad_count"] >= FAIL_COUNT:
            logging.info("🔄 Otomatik failover başlatılıyor...")
            
            if switch_to_next_node(api, state):
                save_state(state)
                
                # Admin'lere bildir
                for uid in ALLOWED_IDS:
                    try:
                        await context.bot.send_message(
                            uid,
                            f"🔄 **Otomatik Failover!**\n"
                            f"Trafik: {bps} B/s (Eşik: {MIN_TRAFFIC_BPS})\n"
                            f"Geçiş: {state.get('switch_count', 0)}. kez\n"
                            f"Saat: {datetime.now().strftime('%H:%M:%S')}"
                        )
                    except:
                        pass
            else:
                logging.error("❌ Failover başarısız!")
                state["bad_count"] = 0
                save_state(state)
    else:
        # Trafik normal
        if state["bad_count"] > 0:
            state["bad_count"] = 0
            save_state(state)
            logging.info(f"✅ Trafik normale döndü: {bps} B/s")


# ============================================
# MAIN
# ============================================

def main():
    print("🚀 PasarGuard Failover Bot")
    print(f"📡 Panel: {PANEL_URL}")
    print(f"📊 Node'lar: {len(NODE_LIST)} adet")
    print(f"⚙️  Kontrol: {CHECK_INTERVAL}s")
    print(f"📉 Eşik: {MIN_TRAFFIC_BPS} B/s")
    print("=" * 40)
    
    if not api.login():
        print("❌ API bağlantısı başarısız!")
        return
    
    print("✅ API bağlantısı başarılı!")
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.job_queue.run_repeating(auto_failover_job, interval=CHECK_INTERVAL, first=10)
    
    print("✅ Bot çalışıyor! /start yazın.")
    app.run_polling()

if __name__ == "__main__":
    main()
