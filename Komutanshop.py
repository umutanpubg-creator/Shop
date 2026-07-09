#!/usr/bin/env python3
"""
PASARGUARD FULL FAILOVER BOT
✅ 3 node arasında döngüsel geçiş
✅ Trafik düşünce otomatik failover
✅ Manuel geçiş
✅ Durum sorgulama
✅ Node listesi görüntüleme
✅ Otomatik aç/kapa
✅ Admin bildirimi
✅ Trafik istatistikleri
✅ Hata sayacı
✅ Geçiş sayacı
"""

import os, json, time, logging, threading, asyncio
from contextlib import asynccontextmanager
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import urllib3
urllib3.disable_warnings()

# ============================================
# PASARGUARD API SINIFI (SENİN KODUNDAN)
# ============================================

class PasarguardAPI:
    """Pasarguard paneli için API istemcisi"""
    
    def __init__(self, base_url: str, verify: bool = False, timeout: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self.verify = verify
        self.timeout = timeout
        self.session = None
    
    async def __aenter__(self):
        import aiohttp
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            connector=aiohttp.TCPConnector(ssl=self.verify)
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def get_token(self, username: str, password: str) -> dict:
        """Kullanıcı giriş yapıp token alır"""
        async with self.session.post(
            f"{self.base_url}/api/admin/token",
            data={"username": username, "password": password}
        ) as response:
            response.raise_for_status()
            data = await response.json()
            return data
    
    async def get_users(self, token: str, offset: int = 0, limit: int = 50):
        """Kullanıcı listesini getir"""
        headers = {"Authorization": f"Bearer {token}"}
        async with self.session.get(
            f"{self.base_url}/api/users?offset={offset}&limit={limit}",
            headers=headers
        ) as response:
            if response.status == 200:
                return await response.json()
            return None
    
    async def get_user(self, username: str, token: str):
        """Belirli bir kullanıcıyı getir"""
        headers = {"Authorization": f"Bearer {token}"}
        async with self.session.get(
            f"{self.base_url}/api/users/{username}",
            headers=headers
        ) as response:
            if response.status == 200:
                return await response.json()
            return None
    
    async def create_user(self, username: str, token: str, data: dict):
        """Yeni kullanıcı oluştur"""
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        async with self.session.post(
            f"{self.base_url}/api/users",
            json=data,
            headers=headers
        ) as response:
            if response.status == 200 or response.status == 201:
                return await response.json()
            return None
    
    async def remove_user(self, username: str, token: str):
        """Kullanıcıyı sil"""
        headers = {"Authorization": f"Bearer {token}"}
        async with self.session.delete(
            f"{self.base_url}/api/users/{username}",
            headers=headers
        ) as response:
            return response.status == 200
    
    async def get_system_stats(self, token: str):
        """Sistem istatistiklerini getir"""
        headers = {"Authorization": f"Bearer {token}"}
        async with self.session.get(
            f"{self.base_url}/api/stats",
            headers=headers
        ) as response:
            if response.status == 200:
                return await response.json()
            return None

# ============================================
# KONFIGÜRASYON (SENİN VERDİKLERİN)
# ============================================

BOT_TOKEN = "8850038202:AAGkr_QlbJJxSAvHR20-zLpWFYUSrYcwa0U"
PASARGUARD_BASE_URL = "https://www.fastline-tm-belet-film.ru:8000"
ADMIN_USERNAME = "Komutan"
ADMIN_PASSWORD = "KomutanPanel_13"

# SADECE BU ID'LER KULLANABİLİR
ALLOWED_IDS = [8359722718, 7115611768]  # Sadece senin belirlediğin adminler

CHECK_INTERVAL = 30          # Kontrol aralığı (saniye)
MIN_TRAFFIC_BPS = 500        # Minimum trafik eşiği
FAIL_COUNT = 3               # Kaç hata sonrası geçiş
NET_IFACE = "eth0"           # Ağ arayüzü

# 3 NODE (SIRALI DÖNGÜ) - Burayı kendi node'larına göre düzenle
NODE_LIST = [
    {"id": 1, "name": "Node-1", "ip": "11.22.33.44"},
    {"id": 2, "name": "Node-2", "ip": "55.66.77.88"},
    {"id": 3, "name": "Node-3", "ip": "99.00.11.22"},
]

STATE_FILE = "full_state.json"
ACCESS_TOKEN = None
API_INSTANCE = None

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ============================================
# API YÖNETİMİ (SENİN KODUNDAN UYARLANDI)
# ============================================

async def get_token():
    """Token alır ve global değişkene kaydeder"""
    global ACCESS_TOKEN
    async with PasarguardAPI(
        base_url=PASARGUARD_BASE_URL,
        verify=False,
        timeout=30.0
    ) as api:
        token_data = await api.get_token(
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD
        )
        ACCESS_TOKEN = token_data.get("access_token")
        logging.info("✅ Token alındı")
        return ACCESS_TOKEN

@asynccontextmanager
async def get_api():
    """API bağlantısı için context manager"""
    async with PasarguardAPI(
        base_url=PASARGUARD_BASE_URL,
        verify=False,
        timeout=30.0
    ) as api:
        yield api

async def ensure_token():
    """Token varsa döndür, yoksa al"""
    global ACCESS_TOKEN
    if ACCESS_TOKEN is None:
        await get_token()
    return ACCESS_TOKEN

async def get_hosts():
    """Host konfigürasyonunu getir"""
    token = await ensure_token()
    async with get_api() as api:
        # Pasarguard API'sinde host'ları almak için endpoint
        # NOT: Bu endpoint senin panel versiyonuna göre değişebilir
        try:
            async with api.session.get(
                f"{PASARGUARD_BASE_URL}/api/hosts",
                headers={"Authorization": f"Bearer {token}"}
            ) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except:
            return None

async def get_nodes():
    """Node listesini getir"""
    token = await ensure_token()
    async with get_api() as api:
        try:
            async with api.session.get(
                f"{PASARGUARD_BASE_URL}/api/nodes",
                headers={"Authorization": f"Bearer {token}"}
            ) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except:
            return None

async def update_hosts(hosts_data):
    """Host konfigürasyonunu güncelle"""
    token = await ensure_token()
    async with get_api() as api:
        try:
            async with api.session.put(
                f"{PASARGUARD_BASE_URL}/api/hosts",
                json=hosts_data,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }
            ) as response:
                return response.status == 200
        except:
            return False

# ============================================
# STATE YÖNETİMİ
# ============================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "node_index": 0,
            "auto_enabled": True,
            "bad_count": 0,
            "switch_count": 0,
            "last_rx": None,
            "last_tx": None,
            "last_ts": None,
            "last_switch_time": None,
            "traffic_history": [],
        }
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
    data.setdefault("node_index", 0)
    data.setdefault("auto_enabled", True)
    data.setdefault("bad_count", 0)
    data.setdefault("switch_count", 0)
    data.setdefault("last_rx", None)
    data.setdefault("last_tx", None)
    data.setdefault("last_ts", None)
    data.setdefault("last_switch_time", None)
    data.setdefault("traffic_history", [])
    return data

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ============================================
# TRAFİK ÖLÇÜM
# ============================================

def calc_bps(state):
    """/proc/net/dev'den trafik ölçer"""
    now = time.time()
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f:
                if ":" in line:
                    name, data = line.split(":", 1)
                    if name.strip() == NET_IFACE:
                        parts = data.split()
                        rx = int(parts[0])
                        tx = int(parts[8])
                        break
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
    bps = int(delta / dt)

    history = state.get("traffic_history", [])
    history.append({"time": now, "bps": bps})
    if len(history) > 20:
        history = history[-20:]
    state["traffic_history"] = history

    return bps

# ============================================
# CORE MANTIK
# ============================================

async def get_host_ips():
    """Host IP'lerini getir"""
    hosts = await get_hosts()
    if not hosts:
        return None, None
    # Shadowsocks TCP host'larını bul
    ss_hosts = hosts.get("Shadowsocks TCP", [])
    if isinstance(ss_hosts, list) and len(ss_hosts) >= 2:
        return ss_hosts[0].get("address"), ss_hosts[1].get("address")
    return None, None

async def set_host_ips(ip1, ip2):
    """Host IP'lerini güncelle"""
    hosts = await get_hosts()
    if not hosts:
        return False
    ss_hosts = hosts.get("Shadowsocks TCP", [])
    if not isinstance(ss_hosts, list) or len(ss_hosts) < 2:
        return False
    ss_hosts[0]["address"] = ip1
    ss_hosts[1]["address"] = ip2
    ss_hosts[0]["is_disabled"] = False
    ss_hosts[1]["is_disabled"] = False
    hosts["Shadowsocks TCP"] = ss_hosts
    return await update_hosts(hosts)

async def switch_to_next_node(state, notify=True):
    """Sıradaki node'a geç"""
    nodes = await get_nodes()
    if not nodes:
        logging.error("❌ Node listesi alınamadı!")
        return False, None

    node_ips = {}
    for n in nodes:
        if isinstance(n, dict):
            node_ips[n.get("id")] = n.get("ip")

    idx = state.get("node_index", 0) % len(NODE_LIST)
    nxt = (idx + 1) % len(NODE_LIST)

    node1 = NODE_LIST[idx]
    node2 = NODE_LIST[nxt]

    ip1 = node_ips.get(node1["id"], node1["ip"])
    ip2 = node_ips.get(node2["id"], node2["ip"])

    logging.info(f"🔄 Geçiş: {node1['name']} ({ip1}) / {node2['name']} ({ip2})")

    if await set_host_ips(ip1, ip2):
        state["node_index"] = (nxt + 1) % len(NODE_LIST)
        state["bad_count"] = 0
        state["switch_count"] = state.get("switch_count", 0) + 1
        state["last_switch_time"] = time.time()
        logging.info(f"✅ Geçiş başarılı! ({state['switch_count']}. kez)")
        return True, f"{node1['name']} → {node2['name']}"
    else:
        logging.error("❌ Geçiş başarısız!")
        return False, None

# ============================================
# TELEGRAM BOT (SADECE ADMINLER)
# ============================================

def is_admin(user_id):
    """Kullanıcının admin olup olmadığını kontrol et"""
    return user_id in ALLOWED_IDS

def main_menu(state):
    auto = "🟢 AÇIK" if state.get("auto_enabled", True) else "🔴 KAPALI"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Durum", callback_data="status"),
         InlineKeyboardButton("📈 Trafik", callback_data="traffic")],
        [InlineKeyboardButton("🔄 Manuel Geçiş", callback_data="switch"),
         InlineKeyboardButton("📋 Node'lar", callback_data="nodes")],
        [InlineKeyboardButton(f"⚙️ Otomatik: {auto}", callback_data="toggle"),
         InlineKeyboardButton("📊 İstatistik", callback_data="stats")],
    ])

async def start(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ **YETKİNİZ YOK!**\nBu bot sadece adminler tarafından kullanılabilir.", parse_mode="Markdown")
        return
    
    # Token'ı kontrol et
    await ensure_token()
    
    state = load_state()
    await update.message.reply_text(
        "🛡️ **PasarGuard Failover Bot**\n\n"
        "✅ 3 node arasında otomatik döngü\n"
        "✅ Trafik düşünce failover\n"
        "✅ Manuel geçiş\n"
        "✅ Anlık takip\n\n"
        "📌 Menüyü kullanın.",
        reply_markup=main_menu(state),
        parse_mode="Markdown"
    )

async def callback_handler(update, context):
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    if not is_admin(user_id):
        await q.edit_message_text("❌ **YETKİNİZ YOK!**", parse_mode="Markdown")
        return

    state = load_state()
    action = q.data

    # ===== DURUM =====
    if action == "status":
        ip1, ip2 = await get_host_ips()
        bps = calc_bps(state)
        save_state(state)

        idx = state.get("node_index", 0) % len(NODE_LIST)
        nxt = (idx + 1) % len(NODE_LIST)
        std = (nxt + 1) % len(NODE_LIST)

        text = f"📊 **DURUM**\n\n"
        text += f"**Host IP'leri:**\n"
        text += f"  ├ Host 1: `{ip1 or 'Bilinmiyor'}`\n"
        text += f"  └ Host 2: `{ip2 or 'Bilinmiyor'}`\n\n"
        text += f"**Node Döngüsü:**\n"
        text += f"  ├ ✅ {NODE_LIST[idx]['name']} (Aktif-1)\n"
        text += f"  ├ ✅ {NODE_LIST[nxt]['name']} (Aktif-2)\n"
        text += f"  └ ⏸️ {NODE_LIST[std]['name']} (Yedek)\n\n"
        text += f"**Trafik:** {bps if bps is not None else 'Ölçülemiyor'} B/s\n"
        text += f"**Eşik:** {MIN_TRAFFIC_BPS} B/s\n\n"
        text += f"**Otomatik:** {'✅ AÇIK' if state.get('auto_enabled') else '❌ KAPALI'}\n"
        text += f"**Geçiş:** {state.get('switch_count', 0)} kez\n"
        text += f"**Hata:** {state.get('bad_count', 0)}/{FAIL_COUNT}"

        await q.edit_message_text(text, reply_markup=main_menu(state), parse_mode="Markdown")

    # ===== TRAFİK =====
    elif action == "traffic":
        bps = calc_bps(state)
        history = state.get("traffic_history", [])
        save_state(state)

        text = f"📈 **TRAFİK DURUMU**\n\n"
        text += f"**Anlık:** {bps if bps is not None else 'Ölçülemiyor'} B/s\n"
        text += f"**Eşik:** {MIN_TRAFFIC_BPS} B/s\n\n"
        text += f"**Son 5 ölçüm:**\n"
        for h in history[-5:]:
            text += f"  ├ {time.strftime('%H:%M:%S', time.localtime(h['time']))}: {h['bps']} B/s\n"

        await q.edit_message_text(text, reply_markup=main_menu(state))

    # ===== MANUEL GEÇİŞ =====
    elif action == "switch":
        success, msg = await switch_to_next_node(state)
        if success:
            save_state(state)
            await q.edit_message_text(f"✅ **Manuel geçiş başarılı!**\n{msg}", reply_markup=main_menu(state))
            # Admin'e bildir
            for uid in ALLOWED_IDS:
                try:
                    await context.bot.send_message(uid, f"🔄 **Manuel Failover!**\n{msg}")
                except:
                    pass
        else:
            await q.edit_message_text("❌ Manuel geçiş başarısız!", reply_markup=main_menu(state))

    # ===== OTOMATİK AÇ/KAPA =====
    elif action == "toggle":
        state["auto_enabled"] = not state.get("auto_enabled", True)
        save_state(state)
        await q.edit_message_text(
            f"✅ Otomatik: {'✅ AÇIK' if state['auto_enabled'] else '❌ KAPALI'}",
            reply_markup=main_menu(state)
        )

    # ===== NODE LİSTESİ =====
    elif action == "nodes":
        nodes = await get_nodes()
        text = "📋 **NODE LİSTESİ**\n\n"
        if nodes:
            for n in nodes:
                if isinstance(n, dict):
                    text += f"  ├ {n.get('name', 'Bilinmiyor')} (ID:{n.get('id')})\n"
                    text += f"  │  ├ IP: {n.get('ip', 'Bilinmiyor')}\n"
                    text += f"  │  └ Durum: {n.get('status', 'Bilinmiyor')}\n\n"
        else:
            text = "❌ Node listesi alınamadı!"
        await q.edit_message_text(text, reply_markup=main_menu(state))

    # ===== İSTATİSTİK =====
    elif action == "stats":
        text = f"📊 **İSTATİSTİKLER**\n\n"
        text += f"**Toplam Geçiş:** {state.get('switch_count', 0)} kez\n"
        text += f"**Son Geçiş:** {time.strftime('%H:%M:%S', time.localtime(state.get('last_switch_time', 0))) if state.get('last_switch_time') else 'Henüz yok'}\n"
        text += f"**Hata Sayısı:** {state.get('bad_count', 0)}/{FAIL_COUNT}\n"
        text += f"**Otomatik:** {'AÇIK' if state.get('auto_enabled') else 'KAPALI'}\n"
        text += f"**Aktif Node:** {NODE_LIST[state.get('node_index', 0) % len(NODE_LIST)]['name']} (Sıradaki)"
        await q.edit_message_text(text, reply_markup=main_menu(state))

# ============================================
# OTOMATİK FAILOVER
# ============================================

async def auto_failover(context):
    state = load_state()

    # Otomatik kapalıysa çık
    if not state.get("auto_enabled", True):
        return

    # Token'ı kontrol et
    await ensure_token()

    # Trafik ölç
    bps = calc_bps(state)
    save_state(state)

    if bps is None:
        return

    # Trafik kontrolü
    if bps < MIN_TRAFFIC_BPS:
        state["bad_count"] = state.get("bad_count", 0) + 1
        logging.info(f"⚠️ Düşük trafik: {bps} B/s ({state['bad_count']}/{FAIL_COUNT})")

        if state["bad_count"] >= FAIL_COUNT:
            logging.info("🔄 Otomatik failover başlatılıyor...")
            success, msg = await switch_to_next_node(state)

            if success:
                save_state(state)
                # Admin'lere bildir
                for uid in ALLOWED_IDS:
                    try:
                        await context.bot.send_message(
                            uid,
                            f"🔄 **Otomatik Failover!**\n"
                            f"Trafik: {bps} B/s\n"
                            f"Geçiş: {msg}\n"
                            f"Toplam: {state.get('switch_count', 0)}. kez"
                        )
                    except:
                        pass
                logging.info(f"✅ Failover başarılı! {msg}")
            else:
                logging.error("❌ Failover başarısız!")
                state["bad_count"] = 0
                save_state(state)
    else:
        # Trafik normal, hatayı sıfırla
        if state.get("bad_count", 0) > 0:
            state["bad_count"] = 0
            save_state(state)
            logging.info(f"✅ Trafik normale döndü: {bps} B/s")

# ============================================
# MAIN
# ============================================

async def main():
    print("🚀 PasarGuard FULL Failover Bot")
    print("=" * 40)
    print(f"📡 Panel: {PASARGUARD_BASE_URL}")
    print(f"📊 Node: {len(NODE_LIST)} adet (3 node döngüsel)")
    print(f"⚙️  Kontrol: {CHECK_INTERVAL}s")
    print(f"📉 Eşik: {MIN_TRAFFIC_BPS} B/s")
    print(f"🔄 Geçiş: {FAIL_COUNT} hata sonrası")
    print(f"👥 Adminler: {ALLOWED_IDS}")
    print("=" * 40)

    # Token al
    await ensure_token()
    print("✅ API bağlantısı başarılı!")

    # Bot'u başlat
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))

    if app.job_queue:
        app.job_queue.run_repeating(auto_failover, interval=CHECK_INTERVAL, first=5)
        print("🔄 Otomatik failover başlatıldı!")
    else:
        print("❌ JobQueue başlatılamadı!")
        return

    print("✅ Bot çalışıyor! Telegram'da /start yazın.")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
