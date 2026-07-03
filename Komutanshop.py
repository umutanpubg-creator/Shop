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

import os,json,time,logging,threading
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import urllib3
urllib3.disable_warnings()

# ============================================
# KONFIGÜRASYON
# ============================================

BOT_TOKEN = "8850038202:AAGkr_QlbJJxSAvHR20-zLpWFYUSrYcwa0U"
PANEL_URL = "https://crc.fastline-tm-belet-film.ru:8000"
ADMIN_USER = "komutan31"
ADMIN_PASS = "KomutanPanel_13"
SS_TAG = "Shadowsocks TCP"
ALLOWED_IDS = [8359722718, 7115611768]

CHECK_INTERVAL = 30          # Kontrol aralığı (saniye)
MIN_TRAFFIC_BPS = 500        # Minimum trafik eşiği
FAIL_COUNT = 3               # Kaç hata sonrası geçiş
NET_IFACE = "eth0"           # Ağ arayüzü

# 3 NODE (SIRALI DÖNGÜ)
NODE_LIST = [
    {"id": 1, "name": "Node-1", "ip": "11.22.33.44"},
    {"id": 2, "name": "Node-2", "ip": "55.66.77.88"},
    {"id": 3, "name": "Node-3", "ip": "99.00.11.22"},
]

STATE_FILE = "full_state.json"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ============================================
# API SINIFI
# ============================================

class PasarAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.token = None

    def login(self):
        try:
            r = self.session.post(
                f"{PANEL_URL}/api/admin/token",
                data={"username": ADMIN_USER, "password": ADMIN_PASS},
                timeout=10
            )
            if r.status_code == 200:
                self.token = r.json().get("access_token")
                self.session.headers.update({
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                })
                logging.info("✅ API giriş başarılı")
                return True
            logging.error(f"❌ Giriş başarısız: {r.status_code}")
            return False
        except Exception as e:
            logging.error(f"❌ Hata: {e}")
            return False

    def get(self, endpoint):
        try:
            r = self.session.get(f"{PANEL_URL}{endpoint}", timeout=10)
            return r.json() if r.status_code == 200 else None
        except:
            return None

    def put(self, endpoint, data):
        try:
            r = self.session.put(f"{PANEL_URL}{endpoint}", json=data, timeout=10)
            return r.status_code == 200
        except:
            return False

api = PasarAPI()

# ============================================
# STATE YÖNETİMİ
# ============================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "node_index": 0,           # Sıradaki node
            "auto_enabled": True,      # Otomatik açık/kapalı
            "bad_count": 0,            # Hata sayacı
            "switch_count": 0,         # Toplam geçiş
            "last_rx": None,
            "last_tx": None,
            "last_ts": None,
            "last_switch_time": None,  # Son geçiş zamanı
            "traffic_history": [],     # Trafik geçmişi
        }
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
    # Eksik alanları doldur
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

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)

# ============================================
# TRAFİK ÖLÇÜM
# ============================================

def calc_bps(state):
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

    # Trafik geçmişini kaydet (son 20 kayıt)
    history = state.get("traffic_history", [])
    history.append({"time": now, "bps": bps})
    if len(history) > 20:
        history = history[-20:]
    state["traffic_history"] = history

    return bps

# ============================================
# CORE MANTIK
# ============================================

def get_host_ips():
    h = api.get("/api/hosts")
    if not h:
        return None, None
    ss = h.get(SS_TAG, [])
    if isinstance(ss, list) and len(ss) >= 2:
        return ss[0].get("address"), ss[1].get("address")
    return None, None

def set_host_ips(ip1, ip2):
    h = api.get("/api/hosts")
    if not h or SS_TAG not in h:
        return False
    ss = h[SS_TAG]
    if not isinstance(ss, list) or len(ss) < 2:
        return False
    ss[0]["address"] = ip1
    ss[1]["address"] = ip2
    ss[0]["is_disabled"] = False
    ss[1]["is_disabled"] = False
    h[SS_TAG] = ss
    return api.put("/api/hosts", h)

def switch_to_next_node(state, notify=True):
    """Sıradaki node'a geç"""
    nodes = api.get("/api/nodes")
    if not nodes:
        logging.error("❌ Node listesi alınamadı!")
        return False, None

    # Node IP'lerini al
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

    if set_host_ips(ip1, ip2):
        state["node_index"] = (nxt + 1) % len(NODE_LIST)
        state["bad_count"] = 0
        state["switch_count"] = state.get("switch_count", 0) + 1
        state["last_switch_time"] = time.time()
        logging.info(f"✅ Geçiş başarılı! ({state['switch_count']}. kez)")
        return True, f"{node1['name']} → {node2['name']}"
    else:
        logging.error("❌ Geçiş başarısız!")
        return False, None

def get_node_stats():
    """Node istatistiklerini al"""
    nodes = api.get("/api/nodes")
    if not nodes:
        return []
    stats = []
    for n in nodes:
        if isinstance(n, dict):
            stats.append({
                "id": n.get("id"),
                "name": n.get("name", f"Node-{n.get('id')}"),
                "ip": n.get("ip", "IP yok"),
                "status": n.get("status", "unknown"),
            })
    return stats

# ============================================
# TELEGRAM BOT
# ============================================

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
    if update.effective_user.id not in ALLOWED_IDS:
        await update.message.reply_text("❌ Yetkiniz yok!")
        return
    if not api.login():
        await update.message.reply_text("❌ API bağlantısı başarısız!")
        return
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
    if q.from_user.id not in ALLOWED_IDS:
        await q.edit_message_text("❌ Yetkiniz yok!")
        return

    state = load_state()
    action = q.data

    # ===== DURUM =====
    if action == "status":
        ip1, ip2 = get_host_ips()
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
        success, msg = switch_to_next_node(state)
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
        nodes = get_node_stats()
        text = "📋 **NODE LİSTESİ**\n\n"
        if nodes:
            for n in nodes:
                text += f"  ├ {n['name']} (ID:{n['id']})\n"
                text += f"  │  ├ IP: {n['ip']}\n"
                text += f"  │  └ Durum: {n['status']}\n\n"
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

    # API kontrol
    if not api.login():
        logging.error("API giriş başarısız!")
        return

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
            success, msg = switch_to_next_node(state)

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

def main():
    print("🚀 PasarGuard FULL Failover Bot")
    print("=" * 40)
    print(f"📡 Panel: {PANEL_URL}")
    print(f"📊 Node: {len(NODE_LIST)} adet (3 node döngüsel)")
    print(f"⚙️  Kontrol: {CHECK_INTERVAL}s")
    print(f"📉 Eşik: {MIN_TRAFFIC_BPS} B/s")
    print(f"🔄 Geçiş: {FAIL_COUNT} hata sonrası")
    print("=" * 40)

    if not api.login():
        print("❌ API bağlantısı başarısız!")
        return

    print("✅ API bağlantısı başarılı!")

    # Bot'u başlat
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))

    if app.job_queue:
        app.job_queue.run_repeating(auto_failover, interval=CHECK_INTERVAL, first=5)
        print(f"🔄 Otomatik failover başlatıldı!")
    else:
        print("❌ JobQueue başlatılamadı!")
        return

    print("✅ Bot çalışıyor! Telegram'da /start yazın.")
    app.run_polling()

if __name__ == "__main__":
    main()
