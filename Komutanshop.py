#!/usr/bin/env python3
"""
Pasarguard Node Manager Bot - TÜM ÖZELLİKLER TEK DOSYADA
- Otomatik Failover (Trafik/Kullanıcı kontrolü)
- Manuel Node Geçişi
- Anlık Trafik İzleme
- Kullanıcı Takibi
- Geçiş Geçmişi
- Trafik Grafiği (Metin tabanlı)
- Telegram Bildirimleri
- Dashboard
- Node Listesi
- Ayarlar
"""

import os
import json
import time
import logging
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================
# ⚙️ KONFIGÜRASYON - SADECE BURAYI DÜZENLE!
# ============================================

# Telegram Bot Token (https://t.me/BotFather)
BOT_TOKEN = "8850038202:AAGkr_QlbJJxSAvHR20-zLpWFYUSrYcwa0U"  # 👈 TOKEN'IN

# Pasarguard API - DOĞRUDAN API ADRESİ
API_URL = "https://crc.fastline-tm-belet-film.ru:8000/api"  # 👈 API URL'İN

# Admin Giriş Bilgileri
ADMIN_USERNAME = "komutan31"  # 👈 KULLANICI ADIN
ADMIN_PASSWORD = "KomutanPanel_13"  # 👈 ŞİFREN

# Node'lar (Pasarguard'daki node ID'leri ve IP'leri)
NODES = [
    {"id": 1, "name": "TR-Node-1", "ip": "11.22.33.44", "location": "İstanbul", "port": 443},
    {"id": 2, "name": "TR-Node-2", "ip": "55.66.77.88", "location": "Ankara", "port": 443},
    {"id": 3, "name": "DE-Node-1", "ip": "99.88.77.66", "location": "Frankfurt", "port": 443},
    {"id": 4, "name": "NL-Node-1", "ip": "44.33.22.11", "location": "Amsterdam", "port": 443},
]

# Telegram Kullanıcı ID'leri (@userinfobot'dan öğren)
ALLOWED_USERS = "7115611768 ,8359722718"  # 👈 KENDİ ID'LERİN
NOTIFY_USERS = "7115611768 ,8359722718"  # 👈 BİLDİRİM ALACAKLAR

# Bot Ayarları
CHECK_INTERVAL = 30  # Kontrol aralığı (saniye)
MIN_TRAFFIC = 1000  # Minimum trafik (bytes/saniye)
MIN_USERS = 1  # Minimum online kullanıcı
FAIL_COUNT = 3  # Kaç hatalı kontrolden sonra geçiş yapsın
HISTORY_DAYS = 7  # Kaç günlük geçmiş tutulsun
TRAFFIC_ALERT = 5000  # Trafik uyarı eşiği

# ============================================
# 🚀 BOT KODU - DEĞİŞTİRME!
# ============================================

ALLOWED_TELEGRAM_IDS = {int(x.strip()) for x in ALLOWED_USERS.split(",") if x.strip()}
NOTIFY_TELEGRAM_IDS = {int(x.strip()) for x in NOTIFY_USERS.split(",") if x.strip()}

STATE_FILE = "node_manager_state.json"
TRAFFIC_FILE = "node_traffic_history.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("node_manager.log"), logging.StreamHandler()]
)

logger = logging.getLogger(__name__)

# ---------------- State Management ----------------

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {
            "active_node_id": NODES[0]["id"],
            "nodes": NODES,
            "auto_enabled": True,
            "bad_count": 0,
            "last_switch_ts": 0,
            "switch_count": 0,
            "total_switches": 0,
            "uptime_start": int(time.time()),
            "last_traffic_check": 0,
        }
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return load_state()

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_traffic_history() -> dict:
    if not os.path.exists(TRAFFIC_FILE):
        return {"history": {}, "total_switches": 0}
    try:
        with open(TRAFFIC_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"history": {}, "total_switches": 0}

def save_traffic_history(data: dict) -> None:
    with open(TRAFFIC_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------------- Pasarguard API ----------------

class PasarguardAPI:
    def __init__(self, api_url: str, username: str, password: str):
        self.api_url = api_url.rstrip('/')
        self.username = username
        self.password = password
        self.token = None
        self.token_expiry = 0
        self.session = requests.Session()
        self.session.timeout = 30
        self.session.verify = False

    def _get_token(self) -> Optional[str]:
        if self.token and time.time() < self.token_expiry:
            return self.token
        
        try:
            url = f"{self.api_url}/auth/token"
            payload = {"username": self.username, "password": self.password}
            
            logger.info(f"🔑 Token alınıyor: {url}")
            response = self.session.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access_token") or data.get("token")
                expires_in = data.get("expires_in", 3600)
                self.token_expiry = time.time() + expires_in - 60
                self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                logger.info("✅ Token başarıyla alındı")
                return self.token
            else:
                logger.error(f"Token hatası: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Token alınamadı: {e}")
            return None

    def _request(self, method: str, endpoint: str, data: dict = None, params: dict = None) -> dict:
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        
        try:
            if not self._get_token():
                return {}
            
            response = self.session.request(
                method=method,
                url=url,
                json=data,
                params=params,
                timeout=30
            )
            
            if response.status_code == 401:
                self.token = None
                if not self._get_token():
                    return {}
                response = self.session.request(
                    method=method,
                    url=url,
                    json=data,
                    params=params,
                    timeout=30
                )
            
            response.raise_for_status()
            return response.json() if response.content else {}
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API isteği başarısız: {e}")
            return {}

    def get_hosts(self) -> dict:
        return self._request("GET", "hosts")

    def update_host_ip(self, ip: str) -> bool:
        result = self._request("POST", "hosts/update", {"ip": ip})
        return bool(result)

    def get_nodes(self) -> List[dict]:
        return self._request("GET", "nodes")

    def get_node_stats(self, node_id: int) -> dict:
        return self._request("GET", f"nodes/{node_id}/stats")

    def get_node_traffic(self, node_id: int, days: int = 7) -> dict:
        return self._request("GET", f"nodes/{node_id}/traffic", params={"days": days})

    def get_current_ip(self) -> Optional[str]:
        hosts = self.get_hosts()
        return hosts.get("ip") or hosts.get("address")

    def test_connection(self) -> bool:
        try:
            return bool(self._get_token())
        except:
            return False

# API'yi başlat
api = PasarguardAPI(API_URL, ADMIN_USERNAME, ADMIN_PASSWORD)

# ---------------- Yardımcı Fonksiyonlar ----------------

def format_bytes(bytes_value: int) -> str:
    if bytes_value < 0:
        return "0 B"
    if bytes_value < 1024:
        return f"{bytes_value} B"
    elif bytes_value < 1024 * 1024:
        return f"{bytes_value / 1024:.1f} KB"
    elif bytes_value < 1024 * 1024 * 1024:
        return f"{bytes_value / (1024 * 1024):.1f} MB"
    elif bytes_value < 1024 * 1024 * 1024 * 1024:
        return f"{bytes_value / (1024 * 1024 * 1024):.2f} GB"
    else:
        return f"{bytes_value / (1024 * 1024 * 1024 * 1024):.2f} TB"

def format_traffic(bps: int) -> str:
    if bps < 0:
        return "0 B/s"
    elif bps < 1024:
        return f"{bps} B/s"
    elif bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    elif bps < 1024 * 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    else:
        return f"{bps / (1024 * 1024 * 1024):.2f} GB/s"

def get_node_traffic_info(node_id: int) -> Dict:
    try:
        stats = api.get_node_stats(node_id)
        traffic = api.get_node_traffic(node_id, HISTORY_DAYS)
        
        return {
            "total": stats.get("total_bytes", 0),
            "monthly": stats.get("monthly_bytes", 0),
            "daily": stats.get("daily_bytes", 0),
            "current_bps": stats.get("current_bps", 0),
            "online_users": stats.get("online_users", 0),
            "total_users": stats.get("total_users", 0),
            "uptime": str(timedelta(seconds=stats.get("uptime", 0))) if stats.get("uptime") else "N/A",
            "status": stats.get("status", "unknown"),
            "last_update": stats.get("last_update", int(time.time())),
            "load": stats.get("load", 0),
        }
    except Exception as e:
        logger.error(f"Node {node_id} bilgileri alınamadı: {e}")
        return {
            "total": 0, "monthly": 0, "daily": 0,
            "current_bps": 0, "online_users": 0, "total_users": 0,
            "uptime": "N/A", "status": "error", "last_update": int(time.time()),
            "load": 0,
        }

def check_node_health(node_id: int) -> Tuple[bool, Dict, str]:
    info = get_node_traffic_info(node_id)
    
    traffic_ok = info["current_bps"] >= MIN_TRAFFIC
    users_ok = info["online_users"] >= MIN_USERS
    status_ok = info["status"] == "online"
    
    is_healthy = traffic_ok and users_ok and status_ok
    
    issues = []
    if not traffic_ok:
        issues.append(f"Trafik düşük ({format_traffic(info['current_bps'])})")
    if not users_ok:
        issues.append(f"Kullanıcı az ({info['online_users']})")
    if not status_ok:
        issues.append(f"Durum: {info['status']}")
    
    status_msg = "✅ Sağlıklı" if is_healthy else f"⚠️ {' - '.join(issues)}"
    
    return is_healthy, info, status_msg

# ---------------- Node Yönetimi ----------------

def get_next_node(state: dict) -> Optional[dict]:
    nodes = state.get("nodes", NODES)
    active_id = state.get("active_node_id")
    
    if not nodes:
        return None
    
    current_idx = None
    for i, node in enumerate(nodes):
        if node["id"] == active_id:
            current_idx = i
            break
    
    if current_idx is None:
        return nodes[0]
    
    for i in range(1, len(nodes) + 1):
        next_idx = (current_idx + i) % len(nodes)
        node = nodes[next_idx]
        is_healthy, _, _ = check_node_health(node["id"])
        if is_healthy:
            return node
    
    next_idx = (current_idx + 1) % len(nodes)
    return nodes[next_idx]

def switch_to_node(node_id: int, state: dict, force: bool = False) -> Tuple[bool, str]:
    nodes = state.get("nodes", NODES)
    
    target_node = None
    for node in nodes:
        if node["id"] == node_id:
            target_node = node
            break
    
    if not target_node:
        return False, "❌ Node bulunamadı!"
    
    if not force:
        is_healthy, info, status = check_node_health(node_id)
        if not is_healthy:
            return False, f"⚠️ Node sağlıksız! {status}"
    
    try:
        if api.update_host_ip(target_node["ip"]):
            old_node_id = state.get("active_node_id")
            state["active_node_id"] = node_id
            state["bad_count"] = 0
            state["last_switch_ts"] = int(time.time())
            state["switch_count"] = state.get("switch_count", 0) + 1
            state["total_switches"] = state.get("total_switches", 0) + 1
            save_state(state)
            
            traffic_data = load_traffic_history()
            if "history" not in traffic_data:
                traffic_data["history"] = {}
            
            timestamp = int(time.time())
            node_key = str(node_id)
            if node_key not in traffic_data["history"]:
                traffic_data["history"][node_key] = []
            
            traffic_data["history"][node_key].append({
                "timestamp": timestamp,
                "switch_from": old_node_id,
                "reason": "manuel" if force else "otomatik",
                "node_name": target_node["name"],
                "node_ip": target_node["ip"]
            })
            
            if len(traffic_data["history"][node_key]) > 100:
                traffic_data["history"][node_key] = traffic_data["history"][node_key][-100:]
            
            traffic_data["total_switches"] = traffic_data.get("total_switches", 0) + 1
            save_traffic_history(traffic_data)
            
            return True, f"✅ {target_node['name']} ({target_node['ip']}) node'una geçildi!"
        else:
            return False, "❌ API hatası! IP güncellenemedi."
        
    except Exception as e:
        logger.error(f"Node {node_id} geçiş başarısız: {e}")
        return False, f"❌ Geçiş başarısız: {str(e)}"

# ---------------- Otomatik Failover ----------------

async def auto_failover(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    
    if not state.get("auto_enabled", True):
        return
    
    active_id = state.get("active_node_id")
    is_healthy, info, status = check_node_health(active_id)
    
    if not is_healthy:
        state["bad_count"] = int(state.get("bad_count", 0)) + 1
        logger.info(f"⚠️ Node {active_id} sağlıksız - {status} (Bad: {state['bad_count']}/{FAIL_COUNT})")
    else:
        state["bad_count"] = 0
        if info["current_bps"] < TRAFFIC_ALERT:
            logger.info(f"⚠️ Trafik düşük: {format_traffic(info['current_bps'])}")
    
    if state["bad_count"] >= FAIL_COUNT:
        next_node = get_next_node(state)
        
        if next_node and next_node["id"] != active_id:
            success, message = switch_to_node(next_node["id"], state, force=False)
            
            if success:
                msg = (
                    f"🔄 **Otomatik Failover Tetiklendi!**\n\n"
                    f"❌ Eski Node: {next((n for n in state.get('nodes', []) if n['id'] == active_id), {}).get('name', 'Bilinmiyor')}\n"
                    f"✅ Yeni Node: {next_node['name']} ({next_node['ip']})\n\n"
                    f"📊 Trafik: {format_traffic(info['current_bps'])}\n"
                    f"👥 Online Kullanıcı: {info['online_users']}\n"
                    f"📈 Başarısız Kontrol: {FAIL_COUNT}\n"
                    f"🔄 Toplam Geçiş: {state.get('total_switches', 0)}"
                )
                
                notify_ids = NOTIFY_TELEGRAM_IDS or ALLOWED_TELEGRAM_IDS
                for chat_id in notify_ids:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=msg,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Bildirim gönderilemedi {chat_id}: {e}")
            else:
                logger.warning(f"Otomatik failover başarısız: {message}")
        else:
            state["bad_count"] = 0
            logger.warning("⚠️ Geçiş yapılabilecek sağlıklı node yok!")
    
    save_state(state)

# ---------------- Telegram Arayüzü ----------------

def format_node_status(node: dict, traffic_info: dict, is_active: bool) -> str:
    status_emoji = "✅" if traffic_info.get("status") == "online" else "❌"
    active_emoji = "▶️" if is_active else "  "
    health = "🟢" if traffic_info.get("current_bps", 0) >= MIN_TRAFFIC else "🔴"
    
    traffic = format_traffic(traffic_info.get("current_bps", 0))
    total = format_bytes(traffic_info.get("total", 0))
    monthly = format_bytes(traffic_info.get("monthly", 0))
    daily = format_bytes(traffic_info.get("daily", 0))
    
    return (
        f"{active_emoji} {status_emoji} **{node['name']}** {health}\n"
        f"   📍 {node.get('location', 'N/A')} | IP: `{node['ip']}`\n"
        f"   📊 Trafik: {traffic} | Günlük: {daily}\n"
        f"   📦 Aylık: {monthly} | Toplam: {total}\n"
        f"   👥 Kullanıcı: {traffic_info.get('online_users', 0)}/{traffic_info.get('total_users', 0)}\n"
        f"   ⏱ Çalışma: {traffic_info.get('uptime', 'N/A')}\n"
    )

def menu_kb(state: dict) -> InlineKeyboardMarkup:
    auto = "🟢 Otomatik: AÇIK" if state.get("auto_enabled", True) else "🔴 Otomatik: KAPALI"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Gösterge Paneli", callback_data="m:dashboard")],
        [InlineKeyboardButton("📋 Tüm Node'lar", callback_data="m:list_nodes")],
        [InlineKeyboardButton("🔄 Sonraki Node", callback_data="m:switch_next")],
        [InlineKeyboardButton("✋ Node Seç", callback_data="m:pick_node")],
        [InlineKeyboardButton(auto, callback_data="m:toggle_auto")],
        [InlineKeyboardButton("📈 Geçiş Geçmişi", callback_data="m:traffic_history")],
        [InlineKeyboardButton("📊 Trafik Grafiği", callback_data="m:traffic_chart")],
        [InlineKeyboardButton("⚙️ Ayarlar", callback_data="m:settings")],
    ])

# ---------------- Komutlar ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("🚫 Yetkiniz yok!")
        return
    
    state = load_state()
    
    try:
        if api.test_connection():
            status_text = "✅ API bağlantısı başarılı"
        else:
            status_text = "⚠️ API bağlantısı başarısız!"
    except:
        status_text = "⚠️ API bağlantısı başarısız!"
    
    current_ip = api.get_current_ip() or "Bilinmiyor"
    
    await update.message.reply_text(
        f"🚀 **Pasarguard Node Yöneticisi**\n\n"
        f"{status_text}\n\n"
        f"📍 **Mevcut IP:** `{current_ip}`\n"
        f"🔄 **Otomatik Mod:** {'✅ AÇIK' if state.get('auto_enabled', True) else '❌ KAPALI'}\n"
        f"📊 **Toplam Geçiş:** {state.get('total_switches', 0)}\n\n"
        f"📌 **Komutlar:**\n"
        f"• `/node 2` - 2. node'a geç\n"
        f"• `/stats` - Tüm node istatistikleri\n"
        f"• `/history` - Geçiş geçmişi\n"
        f"• `/status` - Anlık durum\n\n"
        f"📡 **API:** {API_URL}",
        reply_markup=menu_kb(state),
        parse_mode="Markdown"
    )

async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    if not is_allowed(q.from_user.id):
        await safe_edit(q, "🚫 Yetkiniz yok!")
        return
    
    state = load_state()
    action = (q.data or "").split(":", 1)[1]
    
    if action == "dashboard":
        await show_dashboard(q, state)
    elif action == "list_nodes":
        await show_nodes(q, state)
    elif action == "switch_next":
        await switch_next(q, state)
    elif action == "pick_node":
        await show_pick_node(q, state)
    elif action == "toggle_auto":
        state["auto_enabled"] = not state.get("auto_enabled", True)
        save_state(state)
        durum = "AÇIK" if state['auto_enabled'] else "KAPALI"
        await safe_edit(q, f"✅ Otomatik mod: {durum}", reply_markup=menu_kb(state))
    elif action == "traffic_history":
        await show_traffic_history(q, state)
    elif action == "traffic_chart":
        await show_traffic_chart(q, state)
    elif action == "settings":
        await show_settings(q, state)
    elif action == "home":
        await safe_edit(q, "🚀 **Node Yöneticisi Menüsü**", reply_markup=menu_kb(state), parse_mode="Markdown")

async def show_dashboard(q, state: dict):
    active_id = state.get("active_node_id")
    active_node = next((n for n in state.get("nodes", []) if n["id"] == active_id), None)
    
    total_traffic = 0
    total_users = 0
    healthy_nodes = 0
    node_statuses = []
    
    for node in state.get("nodes", []):
        is_healthy, info, status = check_node_health(node["id"])
        total_traffic += info.get("current_bps", 0)
        total_users += info.get("online_users", 0)
        if is_healthy:
            healthy_nodes += 1
        node_statuses.append(f"{'✅' if is_healthy else '❌'} {node['name']}")
    
    current_ip = api.get_current_ip() or "Bilinmiyor"
    
    text = (
        f"📊 **Gösterge Paneli**\n\n"
        f"🎯 **Aktif Node:** {active_node['name'] if active_node else 'Yok'} ({current_ip})\n"
        f"🔄 **Toplam Geçiş:** {state.get('total_switches', 0)}\n"
        f"⏱ **Çalışma Süresi:** {str(timedelta(seconds=int(time.time() - state.get('uptime_start', time.time()))))}\n\n"
        f"📈 **Genel İstatistikler:**\n"
        f"• Toplam Trafik: {format_traffic(total_traffic)}\n"
        f"• Toplam Kullanıcı: {total_users}\n"
        f"• Sağlıklı Node: {healthy_nodes}/{len(state.get('nodes', []))}\n"
        f"• Otomatik Mod: {'✅ AÇIK' if state.get('auto_enabled', True) else '❌ KAPALI'}\n\n"
        f"📊 **Node Durumları:**\n" + "\n".join(node_statuses) + "\n\n"
        f"⚙️ **Eşik Değerler:**\n"
        f"• Min Trafik: {format_traffic(MIN_TRAFFIC)}\n"
        f"• Min Kullanıcı: {MIN_USERS}\n"
        f"• Başarısızlık Sayısı: {FAIL_COUNT}\n\n"
        f"📡 **API:** {API_URL}"
    )
    
    await safe_edit(q, text, reply_markup=menu_kb(state), parse_mode="Markdown")

async def show_nodes(q, state: dict):
    lines = ["📋 **Tüm Node Durumları**\n"]
    active_id = state.get("active_node_id")
    
    for node in state.get("nodes", []):
        is_healthy, info, status = check_node_health(node["id"])
        is_active = node["id"] == active_id
        lines.append(format_node_status(node, info, is_active))
        lines.append(f"   {status}\n")
    
    total_traffic = sum(check_node_health(n['id'])[1].get('current_bps', 0) for n in state.get('nodes', []))
    lines.append(f"\n📊 **Toplam Trafik:** {format_traffic(total_traffic)}")
    
    await safe_edit(q, "\n".join(lines), reply_markup=menu_kb(state), parse_mode="Markdown")

async def switch_next(q, state: dict):
    next_node = get_next_node(state)
    
    if not next_node:
        await safe_edit(q, "❌ Geçiş yapılabilecek node yok!", reply_markup=menu_kb(state))
        return
    
    if next_node["id"] == state.get("active_node_id"):
        await safe_edit(q, "ℹ️ Sıradaki node aktif node ile aynı.", reply_markup=menu_kb(state))
        return
    
    success, message = switch_to_node(next_node["id"], state, force=True)
    await safe_edit(q, message, reply_markup=menu_kb(state))

async def show_pick_node(q, state: dict):
    nodes = state.get("nodes", [])
    buttons = []
    
    for node in nodes:
        is_healthy, info, _ = check_node_health(node["id"])
        status = "✅" if is_healthy else "❌"
        active = "▶️" if node["id"] == state.get("active_node_id") else ""
        traffic = format_traffic(info.get('current_bps', 0))
        buttons.append([InlineKeyboardButton(
            f"{active} {status} {node['name']} ({traffic})",
            callback_data=f"p:{node['id']}"
        )])
    
    buttons.append([InlineKeyboardButton("⬅️ Geri", callback_data="m:home")])
    await safe_edit(q, "**Node seçin:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def show_traffic_history(q, state: dict):
    traffic_data = load_traffic_history()
    history = traffic_data.get("history", {})
    
    lines = ["📈 **Geçiş Geçmişi**\n"]
    total = 0
    
    for node in state.get("nodes", []):
        node_history = history.get(str(node["id"]), [])
        total += len(node_history)
        if node_history:
            last = node_history[-1]
            time_str = datetime.fromtimestamp(last["timestamp"]).strftime("%Y-%m-%d %H:%M")
            reason = last.get("reason", "bilinmiyor")
            lines.append(f"• {node['name']}: {len(node_history)} geçiş (son: {time_str} - {reason})")
        else:
            lines.append(f"• {node['name']}: Henüz geçiş yok")
    
    lines.append(f"\n📊 **Toplam geçiş:** {traffic_data.get('total_switches', 0)}")
    await safe_edit(q, "\n".join(lines), reply_markup=menu_kb(state), parse_mode="Markdown")

async def show_traffic_chart(q, state: dict):
    lines = ["📊 **Trafik Durumu**\n"]
    
    for node in state.get("nodes", []):
        _, info, status = check_node_health(node["id"])
        bps = info.get('current_bps', 0)
        traffic = format_traffic(bps)
        
        max_bars = 20
        bar_count = min(int(bps / (MIN_TRAFFIC / 10)), max_bars) if bps > 0 else 0
        bar = "█" * bar_count + "░" * (max_bars - bar_count)
        
        lines.append(f"{node['name']}:")
        lines.append(f"  {bar} {traffic}")
        lines.append(f"  👥 {info.get('online_users', 0)} kullanıcı | {status}\n")
    
    await safe_edit(q, "\n".join(lines), reply_markup=menu_kb(state), parse_mode="Markdown")

async def show_settings(q, state: dict):
    text = (
        "⚙️ **Ayarlar**\n\n"
        f"• Kontrol Aralığı: {CHECK_INTERVAL} saniye\n"
        f"• Min Trafik: {format_traffic(MIN_TRAFFIC)}\n"
        f"• Min Kullanıcı: {MIN_USERS}\n"
        f"• Başarısızlık Sayısı: {FAIL_COUNT}\n"
        f"• Geçmiş Günü: {HISTORY_DAYS} gün\n"
        f"• Trafik Uyarı: {format_traffic(TRAFFIC_ALERT)}\n\n"
        f"📡 API: {API_URL}\n"
        f"📊 Node Sayısı: {len(state.get('nodes', []))}\n\n"
        "Ayarları değiştirmek için dosyayı düzenleyip botu yeniden başlatın."
    )
    await safe_edit(q, text, reply_markup=menu_kb(state), parse_mode="Markdown")

async def on_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    if not is_allowed(q.from_user.id):
        await safe_edit(q, "🚫 Yetkiniz yok!")
        return
    
    node_id = int((q.data or "").split(":", 1)[1])
    state = load_state()
    
    success, message = switch_to_node(node_id, state, force=True)
    await safe_edit(q, message, reply_markup=menu_kb(state))

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    
    text = (update.message.text or "").strip()
    parts = text.split()
    
    if not parts:
        return
    
    command = parts[0].lower()
    
    if command == "/node" and len(parts) == 2:
        try:
            node_id = int(parts[1])
            state = load_state()
            
            node_exists = any(n["id"] == node_id for n in state.get("nodes", []))
            if not node_exists:
                await update.message.reply_text(f"❌ Node {node_id} bulunamadı!")
                return
            
            success, message = switch_to_node(node_id, state, force=True)
            await update.message.reply_text(message, reply_markup=menu_kb(state))
        except ValueError:
            await update.message.reply_text("Kullanım: `/node 2`", parse_mode="Markdown")
    
    elif command == "/stats":
        state = load_state()
        lines = ["📊 **Node İstatistikleri**\n"]
        
        for node in state.get("nodes", []):
            _, info, status = check_node_health(node["id"])
            is_active = node["id"] == state.get("active_node_id")
            lines.append(format_node_status(node, info, is_active))
            lines.append(f"   {status}\n")
        
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    
    elif command == "/history":
        traffic_data = load_traffic_history()
        history = traffic_data.get("history", {})
        
        lines = ["📈 **Geçiş Geçmişi**\n"]
        
        for node in state.get("nodes", []):
            node_history = history.get(str(node["id"]), [])
            if node_history:
                last = node_history[-1]
                time_str = datetime.fromtimestamp(last["timestamp"]).strftime("%Y-%m-%d %H:%M")
                reason = last.get("reason", "bilinmiyor")
                lines.append(f"• {node['name']}: {len(node_history)} geçiş (son: {time_str} - {reason})")
            else:
                lines.append(f"• {node['name']}: Henüz geçiş yok")
        
        lines.append(f"\n📊 **Toplam geçiş:** {traffic_data.get('total_switches', 0)}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    
    elif command == "/status":
        state = load_state()
        current_ip = api.get_current_ip() or "Bilinmiyor"
        
        active_id = state.get("active_node_id")
        active_node = next((n for n in state.get("nodes", []) if n["id"] == active_id), None)
        is_healthy, info, status = check_node_health(active_id)
        
        text = (
            f"📊 **Anlık Durum**\n\n"
            f"📍 **Aktif Node:** {active_node['name'] if active_node else 'Yok'}\n"
            f"🔗 **IP:** `{current_ip}`\n"
            f"📊 **Trafik:** {format_traffic(info.get('current_bps', 0))}\n"
            f"👥 **Kullanıcı:** {info.get('online_users', 0)}/{info.get('total_users', 0)}\n"
            f"📦 **Aylık:** {format_bytes(info.get('monthly', 0))}\n"
            f"⏱ **Çalışma:** {info.get('uptime', 'N/A')}\n"
            f"📈 **Durum:** {status}\n"
            f"🔄 **Otomatik:** {'✅ AÇIK' if state.get('auto_enabled', True) else '❌ KAPALI'}\n"
            f"⚠️ **Hata Sayısı:** {state.get('bad_count', 0)}/{FAIL_COUNT}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    
    else:
        await update.message.reply_text(
            "📌 **Kullanılabilir Komutlar:**\n\n"
            "• `/node 2` - 2. node'a geç\n"
            "• `/stats` - Tüm node istatistikleri\n"
            "• `/history` - Geçiş geçmişi\n"
            "• `/status` - Anlık durum\n\n"
            "Veya aşağıdaki butonları kullanın.",
            parse_mode="Markdown",
            reply_markup=menu_kb(load_state())
        )

# ---------------- Yardımcılar ----------------

def is_allowed(user_id: int) -> bool:
    return (not ALLOWED_TELEGRAM_IDS) or (user_id in ALLOWED_TELEGRAM_IDS)

async def safe_edit(q, text: str, reply_markup=None, parse_mode=None):
    try:
        await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Güncelleme işlenirken hata oluştu:", exc_info=context.error)

# ---------------- Ana Fonksiyon ----------------

def main():
    print("=" * 60)
    print("🚀 Pasarguard Node Manager Bot")
    print("=" * 60)
    print(f"📡 API: {API_URL}")
    
    try:
        if api.test_connection():
            print("✅ API bağlantısı başarılı!")
        else:
            print("❌ API bağlantısı başarısız!")
            print("   Kullanıcı adı/şifre kontrol et!")
    except Exception as e:
        print(f"❌ Bağlantı hatası: {e}")
    
    print(f"\n📋 Yüklenen Node'lar ({len(NODES)}):")
    for node in NODES:
        print(f"   - {node['name']}: {node['ip']} ({node.get('location', 'N/A')})")
    
    print(f"\n📊 Ayarlar:")
    print(f"   - Kontrol Aralığı: {CHECK_INTERVAL} saniye")
    print(f"   - Min Trafik: {format_traffic(MIN_TRAFFIC)}")
    print(f"   - Min Kullanıcı: {MIN_USERS}")
    print(f"   - Başarısızlık Sayısı: {FAIL_COUNT}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("node", on_text))
    app.add_handler(CommandHandler("stats", on_text))
    app.add_handler(CommandHandler("history", on_text))
    app.add_handler(CommandHandler("status", on_text))
    
    app.add_handler(CallbackQueryHandler(on_menu, pattern=r"^m:"))
    app.add_handler(CallbackQueryHandler(on_pick, pattern=r"^p:"))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    
    app.add_error_handler(error_handler)
    
    try:
        app.job_queue.run_repeating(auto_failover, interval=CHECK_INTERVAL, first=5)
        print("✅ Otomatik failover aktif!")
    except Exception as e:
        print(f"⚠️ Otomatik failover başlatılamadı: {e}")
        print("   Çalıştır: pip3 install python-telegram-bot[job-queue]")
    
    print("\n" + "=" * 60)
    print("✅ Bot başarıyla başlatıldı!")
    print("📱 Telegram'da /start yaz")
    print("=" * 60)
    
    app.run_polling()

if __name__ == "__main__":
    main()
