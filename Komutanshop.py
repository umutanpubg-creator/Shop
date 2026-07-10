#!/usr/bin/env python3
"""
PASARGUARD FAILOVER BOT — TRAFİK KONTROLLÜ FULL VERSİYON
✅ Otomatik/Manuel mod
✅ Trafik kontrolü (son 30 sn)
✅ Düşük trafikte otomatik failover
✅ Ping + Port kontrolü
✅ Akıllı karar verme
✅ Telegram arayüzü
"""

import os
import json
import time
import logging
import asyncio
import subprocess
import re
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters, ConversationHandler
)
from telegram.request import HTTPXRequest
import httpx
import urllib3

urllib3.disable_warnings()

# ============================================================
# KONFIGÜRASYON
# ============================================================

BOT_TOKEN = "8850038202:AAGkr_QlbJJxSAvHR20-zLpWFYUSrYcwa0U"
PANEL_URL = "https://www.fastline-tm-belet-film.ru:8000"
ADMIN_USERNAME = "Komutan"
ADMIN_PASSWORD = "KomutanPanel_13"
ALLOWED_ADMINS = [8359722718, 7115611768, 6567422721]

TELEGRAM_PROXY = "socks5://192.168.0.101:10808"

DEFAULT_CHECK_INTERVAL = 30
DEFAULT_FAIL_COUNT = 3
CHECK_PORTS = [443, 80, 8080, 8443]

# TRAFİK KONFIGÜRASYONU
TRAFFIC_THRESHOLD_KB = 500  # 500 KB altı düşük trafik
TRAFFIC_FAIL_COUNT = 2       # 2 kez düşük trafik görürse failover
TRAFFIC_HISTORY_SECONDS = 30  # Son 30 saniye

STATE_FILE = "failover_state.json"
ACCESS_TOKEN = None

# Durumlar
ADD_NODE_NAME, ADD_NODE_IP, RENAME_NODE_SELECT, RENAME_NODE_NAME, SET_INTERVAL = range(5)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============================================================
# TRAFİK MONİTÖRÜ
# ============================================================

class TrafficMonitor:
    def __init__(self):
        self.traffic_history = {}
        self.low_traffic_count = {}
        self.last_check_time = {}
    
    async def get_traffic_from_panel(self, ip: str) -> Dict:
        """Panelden trafik verilerini al"""
        try:
            token = await ensure_token()
            async with api_client() as client:
                hosts = await client.get_hosts(token)
                if not hosts:
                    return {"inbound": 0, "outbound": 0, "total": 0}
                
                for host in hosts:
                    if isinstance(host, dict):
                        address = host.get('address')
                        if isinstance(address, list) and address:
                            if address[0] == ip:
                                # Traffic verilerini al - panelin API'sine göre düzenle
                                host_id = host.get('id')
                                if host_id:
                                    # Örnek: /api/host/{id}/traffic endpoint'i
                                    try:
                                        response = await client.client.get(
                                            f"{PANEL_URL}/api/host/{host_id}/traffic",
                                            headers={"Authorization": f"Bearer {token}"}
                                        )
                                        if response.status_code == 200:
                                            data = response.json()
                                            return {
                                                "inbound": data.get('inbound', 0),
                                                "outbound": data.get('outbound', 0),
                                                "total": data.get('total', 0)
                                            }
                                    except:
                                        pass
                return {"inbound": 0, "outbound": 0, "total": 0}
        except Exception as e:
            logger.error(f"Traffic check error: {e}")
            return {"inbound": 0, "outbound": 0, "total": 0}
    
    async def check_traffic(self, ip: str) -> Dict:
        """Trafik kontrolü yapar"""
        current_traffic = await self.get_traffic_from_panel(ip)
        current_time = time.time()
        
        # İlk kontrol
        if ip not in self.traffic_history:
            self.traffic_history[ip] = current_traffic
            self.low_traffic_count[ip] = 0
            self.last_check_time[ip] = current_time
            return {
                "status": "first_check",
                "traffic": {"upload_kb": 0, "download_kb": 0, "total_kb": 0},
                "low_count": 0,
                "is_low": False,
                "will_switch": False
            }
        
        # Önceki trafik
        previous_traffic = self.traffic_history[ip]
        time_diff = current_time - self.last_check_time.get(ip, current_time)
        
        # Trafik farkı (KB cinsinden)
        diff_inbound = current_traffic.get('inbound', 0) - previous_traffic.get('inbound', 0)
        diff_outbound = current_traffic.get('outbound', 0) - previous_traffic.get('outbound', 0)
        diff_total = diff_inbound + diff_outbound
        diff_total_kb = diff_total / 1024
        
        # Trafik durumu
        is_traffic_low = diff_total_kb < TRAFFIC_THRESHOLD_KB
        is_traffic_zero = diff_total == 0
        
        # Geçmişi güncelle
        self.traffic_history[ip] = current_traffic
        self.last_check_time[ip] = current_time
        
        # Sayaç güncelle
        if is_traffic_zero or (is_traffic_low and time_diff >= TRAFFIC_HISTORY_SECONDS):
            self.low_traffic_count[ip] = self.low_traffic_count.get(ip, 0) + 1
            status = "⚠️ DÜŞÜK TRAFİK"
            logger.info(f"📉 {ip} - Son {int(time_diff)}sn: {diff_total_kb:.2f} KB trafik (Düşük)")
        else:
            self.low_traffic_count[ip] = 0
            status = "✅ TRAFİK NORMAL"
            logger.info(f"📈 {ip} - Son {int(time_diff)}sn: {diff_total_kb:.2f} KB trafik (Normal)")
        
        # Failover kararı
        will_switch = self.low_traffic_count.get(ip, 0) >= TRAFFIC_FAIL_COUNT
        
        return {
            "status": status,
            "traffic": {
                "upload_kb": diff_inbound / 1024,
                "download_kb": diff_outbound / 1024,
                "total_kb": diff_total_kb,
                "is_zero": is_traffic_zero,
                "is_low": is_traffic_low
            },
            "low_count": self.low_traffic_count.get(ip, 0),
            "will_switch": will_switch,
            "time_diff": int(time_diff)
        }


# ============================================================
# API KLIENT
# ============================================================

class PanelAPIClient:
    def __init__(self, base_url: str, verify: bool = False, timeout: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self.verify = verify
        self.timeout = timeout
        self.client = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(
            verify=self.verify,
            timeout=self.timeout,
            follow_redirects=True
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    async def authenticate(self, username: str, password: str) -> Dict:
        response = await self.client.post(
            f"{self.base_url}/api/admin/token",
            data={"username": username, "password": password}
        )
        response.raise_for_status()
        return response.json()

    async def get_hosts(self, token: str) -> Optional[List]:
        headers = {"Authorization": f"Bearer {token}"}
        response = await self.client.get(
            f"{self.base_url}/api/hosts",
            headers=headers
        )
        if response.status_code == 200:
            return response.json()
        return None

    async def update_hosts(self, token: str, hosts_data: List) -> bool:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        response = await self.client.put(
            f"{self.base_url}/api/hosts",
            json=hosts_data,
            headers=headers
        )
        return response.status_code == 200


# ============================================================
# TOKEN YÖNETİMİ
# ============================================================

async def get_access_token() -> str:
    global ACCESS_TOKEN
    async with PanelAPIClient(base_url=PANEL_URL, verify=False, timeout=30.0) as client:
        auth_data = await client.authenticate(
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD
        )
        ACCESS_TOKEN = auth_data.get("access_token")
        logger.info("✅ Token alındı")
        return ACCESS_TOKEN

@asynccontextmanager
async def api_client():
    async with PanelAPIClient(base_url=PANEL_URL, verify=False, timeout=30.0) as client:
        yield client

async def ensure_token() -> str:
    global ACCESS_TOKEN
    if ACCESS_TOKEN is None:
        await get_access_token()
    return ACCESS_TOKEN


# ============================================================
# NODE YÖNETİMİ
# ============================================================

async def get_all_hosts() -> List[Dict]:
    token = await ensure_token()
    async with api_client() as client:
        hosts_data = await client.get_hosts(token)
        return hosts_data if hosts_data else []

async def discover_nodes() -> List[Dict]:
    hosts = await get_all_hosts()
    unique_ips = {}
    for host in hosts:
        if isinstance(host, dict):
            address = host.get('address')
            if isinstance(address, list) and address:
                ip = address[0]
            elif isinstance(address, str):
                ip = address
            else:
                continue
            name = host.get('remark') or host.get('name') or 'Unknown'
            if ip not in unique_ips:
                unique_ips[ip] = []
            unique_ips[ip].append(name)

    nodes = []
    for idx, (ip, names) in enumerate(unique_ips.items(), 1):
        nodes.append({
            "id": idx,
            "name": f"Node-{idx} ({names[0] if names else 'Unknown'})",
            "ip": ip,
            "hosts": names
        })
    return nodes


# ============================================================
# PING + PORT KONTROLÜ
# ============================================================

def ping_check(ip: str) -> Tuple[bool, float]:
    try:
        cmd = ["ping", "-n", "1", "-w", "2000", ip]
        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        elapsed = (time.time() - start) * 1000
        if result.returncode == 0:
            return True, elapsed
        return False, elapsed
    except:
        return False, 0

def port_check(ip: str, port: int = 443) -> Tuple[bool, int]:
    try:
        cmd = [
            "curl", "-s", "-o", "/dev/null",
            "-w", "%{http_code}",
            "--connect-timeout", "3",
            f"https://{ip}:{port}"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        code = result.stdout.strip()
        if code and code.isdigit() and int(code) < 500:
            return True, int(code)
        return False, int(code) if code.isdigit() else 0
    except:
        return False, 0

def analyze_node(ip: str) -> Dict:
    result = {
        "ip": ip,
        "ping": {"alive": False, "ms": 0},
        "ports": {},
        "overall": False
    }
    
    ping_alive, ping_ms = ping_check(ip)
    result["ping"]["alive"] = ping_alive
    result["ping"]["ms"] = round(ping_ms, 1)
    
    ports_alive = 0
    for port in CHECK_PORTS:
        alive, code = port_check(ip, port)
        result["ports"][port] = {"alive": alive, "code": code}
        if alive:
            ports_alive += 1
    
    result["overall"] = ping_alive or ports_alive > 0
    return result

def is_server_alive(ip: str) -> bool:
    result = analyze_node(ip)
    return result["overall"]


# ============================================================
# PANEL İŞLEMLERİ
# ============================================================

async def replace_node(old_ip: str, new_ip: str) -> Tuple[bool, str]:
    try:
        hosts = await get_all_hosts()
        if not hosts:
            return False, "Host bulunamadı"
        
        count = 0
        for host in hosts:
            if isinstance(host, dict):
                address = host.get('address')
                if isinstance(address, list) and address:
                    if address[0] == old_ip:
                        host["address"] = [new_ip]
                        count += 1
        
        if count == 0:
            return False, f"{old_ip} IP'li host yok"
        
        token = await ensure_token()
        async with api_client() as client:
            success = await client.update_hosts(token, hosts)
            if success:
                return True, f"{count} host {old_ip} -> {new_ip} olarak güncellendi"
            else:
                return False, "Güncelleme hatası"
    except Exception as e:
        return False, f"Hata: {str(e)}"

async def add_node_to_panel(ip: str, name: str) -> Tuple[bool, str, Dict]:
    try:
        hosts = await get_all_hosts()
        if not hosts:
            return False, "Host bulunamadı", None
        
        for host in hosts:
            if isinstance(host, dict):
                address = host.get('address')
                if isinstance(address, list) and address:
                    if address[0] == ip:
                        return False, f"{ip} zaten var", None
        
        new_host = {
            "remark": name,
            "address": [ip],
            "is_disabled": False,
            "port": None,
            "sni": [],
            "host": [],
            "path": None,
            "security": "inbound_default",
            "alpn": [],
            "fingerprint": "",
            "allowinsecure": False
        }
        
        hosts.append(new_host)
        token = await ensure_token()
        async with api_client() as client:
            success = await client.update_hosts(token, hosts)
            if success:
                analysis = analyze_node(ip)
                return True, f"Node {name} ({ip}) eklendi!", analysis
            return False, "Güncelleme hatası", None
    except Exception as e:
        return False, f"Hata: {str(e)}", None

async def delete_node_from_panel(ip: str) -> Tuple[bool, str]:
    try:
        hosts = await get_all_hosts()
        if not hosts:
            return False, "Host bulunamadı"
        
        new_hosts = []
        found = False
        for host in hosts:
            if isinstance(host, dict):
                address = host.get('address')
                if isinstance(address, list) and address:
                    if address[0] == ip:
                        found = True
                        continue
                new_hosts.append(host)
        
        if not found:
            return False, f"{ip} bulunamadı"
        
        token = await ensure_token()
        async with api_client() as client:
            success = await client.update_hosts(token, new_hosts)
            if success:
                return True, f"{ip} silindi!"
            return False, "Güncelleme hatası"
    except Exception as e:
        return False, f"Hata: {str(e)}"

async def rename_node_in_panel(old_ip: str, new_name: str) -> Tuple[bool, str]:
    try:
        hosts = await get_all_hosts()
        if not hosts:
            return False, "Host bulunamadı"
        
        found = False
        for host in hosts:
            if isinstance(host, dict):
                address = host.get('address')
                if isinstance(address, list) and address:
                    if address[0] == old_ip:
                        host["remark"] = new_name
                        found = True
                        break
        
        if not found:
            return False, f"{old_ip} bulunamadı"
        
        token = await ensure_token()
        async with api_client() as client:
            success = await client.update_hosts(token, hosts)
            if success:
                return True, f"Node {old_ip} -> {new_name} olarak değiştirildi"
            return False, "Güncelleme hatası"
    except Exception as e:
        return False, f"Hata: {str(e)}"


# ============================================================
# STATE YÖNETİMİ
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "current_node_index": 0,
            "bad_count": 0,
            "switch_count": 0,
            "last_switch": None,
            "auto_mode": True,
            "check_interval": DEFAULT_CHECK_INTERVAL,
            "auto_return": False,
            "last_working_ip": None
        }
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
    data.setdefault("current_node_index", 0)
    data.setdefault("bad_count", 0)
    data.setdefault("switch_count", 0)
    data.setdefault("last_switch", None)
    data.setdefault("auto_mode", True)
    data.setdefault("check_interval", DEFAULT_CHECK_INTERVAL)
    data.setdefault("auto_return", False)
    data.setdefault("last_working_ip", None)
    return data

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ============================================================
# AKILLI FAILOVER (PING + PORT + TRAFİK)
# ============================================================

async def smart_failover(context):
    """Akıllı failover - Ping + Port + Trafik kontrolü"""
    state = load_state()
    
    # Otomatik mod kontrolü
    if not state.get("auto_mode", True):
        return
    
    # Trafik monitörünü al
    traffic_monitor = context.bot_data.get('traffic_monitor')
    if not traffic_monitor:
        traffic_monitor = TrafficMonitor()
        context.bot_data['traffic_monitor'] = traffic_monitor
    
    nodes = await discover_nodes()
    if not nodes:
        logger.warning("⚠️ Node bulunamadı")
        return
    
    # Aktif node'u bul
    current_index = state.get("current_node_index", 0)
    if current_index >= len(nodes):
        current_index = 0
        state["current_node_index"] = 0
        save_state(state)
    
    current_node = nodes[current_index]
    current_ip = current_node["ip"]
    
    logger.info(f"🔍 Kontrol: {current_node['name']} ({current_ip})")
    
    # 1. Ping + Port kontrolü
    is_alive = is_server_alive(current_ip)
    
    # 2. Trafik kontrolü
    traffic_result = await traffic_monitor.check_traffic(current_ip)
    
    # Karar verme
    need_switch = False
    reason = ""
    traffic_info = ""
    
    # Önce ping/port kontrolü
    if not is_alive:
        need_switch = True
        reason = "❌ Sunucu çalışmıyor (ping/port hatası)"
        state["bad_count"] = state.get("bad_count", 0) + 1
    
    # Trafik kontrolü
    elif traffic_result["traffic"]["is_zero"]:
        need_switch = True
        reason = "❌ Son 30 saniyede trafik yok (0 KB)"
        state["bad_count"] = state.get("bad_count", 0) + 1
    
    elif traffic_result["traffic"]["is_low"]:
        low_count = traffic_result["low_count"]
        total_kb = traffic_result["traffic"]["total_kb"]
        
        if low_count >= TRAFFIC_FAIL_COUNT:
            need_switch = True
            reason = f"⚠️ {low_count} kez düşük trafik ({total_kb:.2f} KB)"
            state["bad_count"] = state.get("bad_count", 0) + 1
        else:
            traffic_info = f"📉 Düşük trafik ({total_kb:.2f} KB) - {low_count}/{TRAFFIC_FAIL_COUNT}"
            state["bad_count"] = 0  # Reset bad_count for traffic
    else:
        # Her şey normal
        state["bad_count"] = 0
        save_state(state)
    
    # Failover kararı
    if need_switch:
        logger.info(f"⚠️ Failover adayı: {reason} ({state['bad_count']}/{DEFAULT_FAIL_COUNT})")
        
        if state["bad_count"] >= DEFAULT_FAIL_COUNT:
            # Yeni node bul
            for node in nodes:
                if node["ip"] == current_ip:
                    continue
                
                # Yeni node'u kontrol et (ping+port)
                if is_server_alive(node["ip"]):
                    # Yeni node trafiğini kontrol et
                    new_traffic = await traffic_monitor.check_traffic(node["ip"])
                    
                    if not new_traffic["traffic"]["is_low"]:
                        success, msg = await replace_node(current_ip, node["ip"])
                        
                        if success:
                            # State güncelle
                            state["current_node_index"] = nodes.index(node)
                            state["switch_count"] = state.get("switch_count", 0) + 1
                            state["last_switch"] = time.time()
                            state["bad_count"] = 0
                            state["last_working_ip"] = current_ip
                            save_state(state)
                            
                            # Bildirim
                            for admin_id in ALLOWED_ADMINS:
                                try:
                                    await context.bot.send_message(
                                        admin_id,
                                        f"🔄 **TRAFİK BAZLI FAILOVER!**\n\n"
                                        f"📡 Eski: {current_node['name']} ({current_ip})\n"
                                        f"➡️ Yeni: {node['name']} ({node['ip']})\n"
                                        f"📊 Sebep: {reason}\n"
                                        f"📊 Eski trafik: {traffic_result['traffic']['total_kb']:.2f} KB\n"
                                        f"📊 Yeni trafik: {new_traffic['traffic']['total_kb']:.2f} KB\n"
                                        f"📊 Toplam failover: {state['switch_count']}",
                                        parse_mode="Markdown"
                                    )
                                except:
                                    pass
                            
                            logger.info(f"✅ Failover tamamlandı -> {node['ip']}")
                            break
    else:
        # Normal durum
        if state.get("bad_count", 0) > 0:
            state["bad_count"] = 0
            save_state(state)


# ============================================================
# TELEGRAM BOT - KOMUTLAR
# ============================================================

def is_authorized(user_id: int) -> bool:
    return user_id in ALLOWED_ADMINS

def main_menu(state=None):
    if state is None:
        state = load_state()
    auto_mode = state.get("auto_mode", True)
    auto_return = state.get("auto_return", False)
    interval = state.get("check_interval", DEFAULT_CHECK_INTERVAL)
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("📊 Analiz", callback_data="analyze_nodes")],
        [InlineKeyboardButton("📊 Trafik Analizi", callback_data="traffic_analysis")],
        [InlineKeyboardButton("📋 Node Listesi", callback_data="list_nodes")],
        [InlineKeyboardButton(f"🔧 Mod: {'AUTO' if auto_mode else 'MANUEL'}", callback_data="toggle_mode")],
        [InlineKeyboardButton(f"🔄 Dönüş: {'ON' if auto_return else 'OFF'}", callback_data="toggle_return")],
        [InlineKeyboardButton(f"⏱️ İnterval: {interval}s", callback_data="set_interval")],
        [InlineKeyboardButton("🔄 Manuel Switch", callback_data="manual_switch")],
        [InlineKeyboardButton("➕ Node Ekle", callback_data="add_node")],
        [InlineKeyboardButton("❌ Node Sil", callback_data="delete_node")],
        [InlineKeyboardButton("✏️ Node Yeniden Adlandır", callback_data="rename_node")],
        [InlineKeyboardButton("📊 İstatistik", callback_data="stats")],
        [InlineKeyboardButton("🔄 Yenile", callback_data="refresh_nodes")],
    ])

# ============================================================
# TELEGRAM KOMUT İŞLEYİCİLER
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    await ensure_token()
    nodes = await discover_nodes()
    state = load_state()
    
    if not nodes:
        await update.message.reply_text("⚠️ Node bulunamadı")
        return
    
    if state.get("current_node_index", 0) >= len(nodes):
        state["current_node_index"] = 0
        save_state(state)
    
    current = nodes[state["current_node_index"]]
    alive = is_server_alive(current["ip"])
    icon = "🟢" if alive else "🔴"
    
    await update.message.reply_text(
        f"🛡️ **PASARGUARD FAILOVER BOT**\n\n"
        f"{icon} Aktif: {current['name']} ({current['ip']})\n"
        f"✅ Toplam Node: {len(nodes)}\n"
        f"🔧 Mod: {'AUTO' if state.get('auto_mode', True) else 'MANUEL'}\n"
        f"⏱️ İnterval: {state.get('check_interval', DEFAULT_CHECK_INTERVAL)}s\n"
        f"✅ Failover: {state.get('switch_count', 0)}\n\n"
        f"📌 Komutlar: /status, /switch, /nodes, /analyze, /traffic",
        reply_markup=main_menu(state),
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    state = load_state()
    nodes = await discover_nodes()
    if not nodes:
        await update.message.reply_text("⚠️ Node bulunamadı")
        return
    
    if state.get("current_node_index", 0) >= len(nodes):
        state["current_node_index"] = 0
        save_state(state)
    
    current = nodes[state["current_node_index"]]
    alive = is_server_alive(current["ip"])
    icon = "🟢" if alive else "🔴"
    
    response = f"📊 **STATUS**\n\n"
    response += f"{icon} Aktif: {current['name']} ({current['ip']})\n"
    response += f"📊 Durum: {'✅ ÇALIŞIYOR' if alive else '❌ ÖLÜ'}\n"
    response += f"⚠️ Hata: {state.get('bad_count', 0)}/{DEFAULT_FAIL_COUNT}\n"
    response += f"📊 Failover: {state.get('switch_count', 0)}\n\n"
    response += f"🌐 Tüm Node'lar:\n"
    for node in nodes:
        alive_node = is_server_alive(node["ip"])
        icon_node = "🟢" if alive_node else "🔴"
        response += f"  {icon_node} {node['name']} ({node['ip']})\n"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    nodes = await discover_nodes()
    if not nodes:
        await update.message.reply_text("⚠️ Node bulunamadı")
        return
    
    keyboard = []
    for node in nodes:
        alive = is_server_alive(node["ip"])
        icon = "🟢" if alive else "🔴"
        keyboard.append([
            InlineKeyboardButton(
                f"{icon} {node['name']} ({node['ip']})",
                callback_data=f"switch_to_{node['ip']}"
            )
        ])
    keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel_action")])
    
    await update.message.reply_text(
        "🔄 **Switch yapılacak node'u seç:**",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cmd_nodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    nodes = await discover_nodes()
    if not nodes:
        await update.message.reply_text("⚠️ Node bulunamadı")
        return
    
    response = "📋 **NODE LİSTESİ**\n\n"
    for node in nodes:
        alive = is_server_alive(node["ip"])
        icon = "🟢" if alive else "🔴"
        response += f"{icon} {node['name']}\n"
        response += f"  ├ IP: `{node['ip']}`\n"
        response += f"  └ Host: {len(node.get('hosts', []))}\n\n"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    nodes = await discover_nodes()
    if not nodes:
        await update.message.reply_text("⚠️ Node bulunamadı")
        return
    
    response = "📊 **NODE ANALİZİ**\n\n"
    for node in nodes:
        ip = node["ip"]
        analysis = analyze_node(ip)
        icon = "🟢" if analysis["overall"] else "🔴"
        response += f"{icon} **{node['name']}**\n"
        response += f"  ├ IP: `{ip}`\n"
        response += f"  ├ Ping: {'✅' if analysis['ping']['alive'] else '❌'} {analysis['ping']['ms']}ms\n"
        response += f"  └ Port: "
        ports = []
        for port, data in analysis["ports"].items():
            ports.append(f"{port}:{'✅' if data['alive'] else '❌'}")
        response += f"{' '.join(ports)}\n\n"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def cmd_traffic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    nodes = await discover_nodes()
    if not nodes:
        await update.message.reply_text("⚠️ Node bulunamadı")
        return
    
    traffic_monitor = context.bot_data.get('traffic_monitor', TrafficMonitor())
    
    response = f"📊 **TRAFİK ANALİZİ (SON 30sn)**\n\n"
    response += f"⚙️ Eşik: {TRAFFIC_THRESHOLD_KB} KB\n"
    response += f"⚠️ Limit: {TRAFFIC_FAIL_COUNT} kez\n\n"
    
    for node in nodes:
        ip = node["ip"]
        traffic = await traffic_monitor.check_traffic(ip)
        alive = is_server_alive(ip)
        
        status_icon = "🟢" if alive else "🔴"
        traffic_icon = "📶" if not traffic["traffic"]["is_low"] else "📉"
        
        response += f"{status_icon} **{node['name']}**\n"
        response += f"  ├ IP: `{ip}`\n"
        response += f"  ├ Trafik: {traffic_icon} {traffic['traffic']['total_kb']:.2f} KB\n"
        response += f"  ├ Upload: {traffic['traffic']['upload_kb']:.2f} KB\n"
        response += f"  ├ Download: {traffic['traffic']['download_kb']:.2f} KB\n"
        response += f"  ├ Durum: {traffic['status']}\n"
        response += f"  └ Sayaç: {traffic['low_count']}/{TRAFFIC_FAIL_COUNT}\n\n"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    context.user_data["cmd_add"] = True
    await update.message.reply_text(
        "➕ **Node Ekle**\n\n"
        "📝 İsim ve IP girin:\n"
        "Örnek: `New-Server 192.168.1.100`"
    )

async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    nodes = await discover_nodes()
    if not nodes:
        await update.message.reply_text("⚠️ Node bulunamadı")
        return
    
    keyboard = []
    for node in nodes:
        keyboard.append([
            InlineKeyboardButton(
                f"{node['name']} ({node['ip']})",
                callback_data=f"delete_confirm_{node['ip']}"
            )
        ])
    keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel_action")])
    
    await update.message.reply_text(
        "❌ **Silinecek node'u seç:**",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cmd_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    context.user_data["cmd_rename"] = True
    await update.message.reply_text(
        "✏️ **Node Yeniden Adlandır**\n\n"
        "📝 IP ve yeni isim girin:\n"
        "Örnek: `192.168.1.100 New-Name`"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ Yetkiniz yok")
        return
    
    text = update.message.text.strip()
    
    # Add node
    if context.user_data.get("cmd_add"):
        parts = text.split(" ", 1)
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: `İsim IP`")
            context.user_data.pop("cmd_add", None)
            return
        
        name, ip = parts[0], parts[1]
        if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip):
            await update.message.reply_text("❌ Geçersiz IP")
            context.user_data.pop("cmd_add", None)
            return
        
        success, msg, analysis = await add_node_to_panel(ip, name)
        if success:
            await update.message.reply_text(f"✅ {msg}")
            nodes = await discover_nodes()
            state = load_state()
            state["current_node_index"] = 0
            save_state(state)
        else:
            await update.message.reply_text(f"❌ {msg}")
        
        context.user_data.pop("cmd_add", None)
        return
    
    # Rename node
    if context.user_data.get("cmd_rename"):
        parts = text.split(" ", 1)
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: `IP Yeni_İsim`")
            context.user_data.pop("cmd_rename", None)
            return
        
        ip, new_name = parts[0], parts[1]
        if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip):
            await update.message.reply_text("❌ Geçersiz IP")
            context.user_data.pop("cmd_rename", None)
            return
        
        success, msg = await rename_node_in_panel(ip, new_name)
        if success:
            await update.message.reply_text(f"✅ {msg}")
        else:
            await update.message.reply_text(f"❌ {msg}")
        
        context.user_data.pop("cmd_rename", None)
        return


# ============================================================
# BUTON İŞLEYİCİ
# ============================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not is_authorized(user_id):
        await query.edit_message_text("⛔ Yetkiniz yok")
        return
    
    state = load_state()
    nodes = await discover_nodes()
    action = query.data
    
    if not nodes and action not in ["add_node", "delete_node", "cancel_action", "toggle_mode", "toggle_return", "set_interval", "rename_node"]:
        await query.edit_message_text("⚠️ Node bulunamadı", reply_markup=main_menu(state))
        return
    
    if state.get("current_node_index", 0) >= len(nodes) and nodes:
        state["current_node_index"] = 0
        save_state(state)
    
    current = nodes[state["current_node_index"]] if nodes else None
    
    # ===== SET INTERVAL =====
    if action == "set_interval":
        keyboard = [
            [InlineKeyboardButton("10s", callback_data="interval_10")],
            [InlineKeyboardButton("30s", callback_data="interval_30")],
            [InlineKeyboardButton("60s", callback_data="interval_60")],
            [InlineKeyboardButton("120s", callback_data="interval_120")],
            [InlineKeyboardButton("❌ İptal", callback_data="cancel_action")]
        ]
        await query.edit_message_text(
            f"⏱️ **İnterval: {state.get('check_interval', DEFAULT_CHECK_INTERVAL)}s**\n\nSeç:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if action.startswith("interval_"):
        interval = int(action.replace("interval_", ""))
        state["check_interval"] = interval
        save_state(state)
        await query.edit_message_text(f"✅ İnterval: {interval}s", reply_markup=main_menu(state))
        return
    
    # ===== TOGGLE MODE =====
    if action == "toggle_mode":
        state["auto_mode"] = not state.get("auto_mode", True)
        save_state(state)
        await query.edit_message_text(
            f"✅ Mod: {'AUTO' if state['auto_mode'] else 'MANUEL'}",
            reply_markup=main_menu(state)
        )
        return
    
    # ===== TOGGLE RETURN =====
    if action == "toggle_return":
        state["auto_return"] = not state.get("auto_return", False)
        save_state(state)
        await query.edit_message_text(
            f"✅ Otomatik dönüş: {'ON' if state['auto_return'] else 'OFF'}",
            reply_markup=main_menu(state)
        )
        return
    
    # ===== STATUS =====
    if action == "status":
        if not current:
            await query.edit_message_text("⚠️ Aktif node yok", reply_markup=main_menu(state))
            return
        alive = is_server_alive(current["ip"])
        icon = "🟢" if alive else "🔴"
        response = f"📊 **STATUS**\n\n"
        response += f"{icon} Aktif: {current['name']} ({current['ip']})\n"
        response += f"📊 Durum: {'✅ ÇALIŞIYOR' if alive else '❌ ÖLÜ'}\n"
        response += f"⚠️ Hata: {state.get('bad_count', 0)}/{DEFAULT_FAIL_COUNT}\n"
        response += f"📊 Failover: {state.get('switch_count', 0)}\n\n"
        response += f"🌐 Tüm Node'lar:\n"
        for node in nodes:
            alive_node = is_server_alive(node["ip"])
            icon_node = "🟢" if alive_node else "🔴"
            response += f"  {icon_node} {node['name']} ({node['ip']})\n"
        await query.edit_message_text(response, parse_mode="Markdown")
        return
    
    # ===== LIST NODES =====
    if action == "list_nodes":
        response = "📋 **NODE LİSTESİ**\n\n"
        for node in nodes:
            alive = is_server_alive(node["ip"])
            icon = "🟢" if alive else "🔴"
            response += f"{icon} {node['name']}\n"
            response += f"  ├ IP: `{node['ip']}`\n"
            response += f"  └ Host: {len(node.get('hosts', []))}\n\n"
        await query.edit_message_text(response, parse_mode="Markdown")
        return
    
    # ===== ANALYZE NODES =====
    if action == "analyze_nodes":
        response = "📊 **NODE ANALİZİ**\n\n"
        for node in nodes:
            ip = node["ip"]
            analysis = analyze_node(ip)
            icon = "🟢" if analysis["overall"] else "🔴"
            response += f"{icon} **{node['name']}**\n"
            response += f"  ├ IP: `{ip}`\n"
            response += f"  ├ Ping: {'✅' if analysis['ping']['alive'] else '❌'} {analysis['ping']['ms']}ms\n"
            response += f"  └ Port: "
            ports = []
            for port, data in analysis["ports"].items():
                ports.append(f"{port}:{'✅' if data['alive'] else '❌'}")
            response += f"{' '.join(ports)}\n\n"
        await query.edit_message_text(response, parse_mode="Markdown")
        return
    
    # ===== TRAFFIC ANALYSIS =====
    if action == "traffic_analysis":
        traffic_monitor = context.bot_data.get('traffic_monitor', TrafficMonitor())
        response = f"📊 **TRAFİK ANALİZİ (SON 30sn)**\n\n"
        response += f"⚙️ Eşik: {TRAFFIC_THRESHOLD_KB} KB\n"
        response += f"⚠️ Limit: {TRAFFIC_FAIL_COUNT} kez\n\n"
        
        for node in nodes:
            ip = node["ip"]
            traffic = await traffic_monitor.check_traffic(ip)
            alive = is_server_alive(ip)
            
            status_icon = "🟢" if alive else "🔴"
            traffic_icon = "📶" if not traffic["traffic"]["is_low"] else "📉"
            
            response += f"{status_icon} **{node['name']}**\n"
            response += f"  ├ IP: `{ip}`\n"
            response += f"  ├ Trafik: {traffic_icon} {traffic['traffic']['total_kb']:.2f} KB\n"
            response += f"  ├ Upload: {traffic['traffic']['upload_kb']:.2f} KB\n"
            response += f"  ├ Download: {traffic['traffic']['download_kb']:.2f} KB\n"
            response += f"  ├ Durum: {traffic['status']}\n"
            response += f"  └ Sayaç: {traffic['low_count']}/{TRAFFIC_FAIL_COUNT}\n\n"
        
        await query.edit_message_text(response, parse_mode="Markdown")
        return
    
    # ===== MANUAL SWITCH =====
    if action == "manual_switch":
        keyboard = []
        for node in nodes:
            alive = is_server_alive(node["ip"])
            icon = "🟢" if alive else "🔴"
            is_current = " ✅" if current and node["ip"] == current["ip"] else ""
            keyboard.append([
                InlineKeyboardButton(
                    f"{icon} {node['name']} ({node['ip']}){is_current}",
                    callback_data=f"switch_to_{node['ip']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel_action")])
        
        await query.edit_message_text(
            "🔄 **Switch yapılacak node'u seç:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if action.startswith("switch_to_"):
        target_ip = action.replace("switch_to_", "")
        if not current:
            await query.edit_message_text("⚠️ Aktif node yok", reply_markup=main_menu(state))
            return
        
        if current["ip"] == target_ip:
            await query.edit_message_text("⚠️ Zaten aktif!", reply_markup=main_menu(state))
            return
        
        target_name = next((n["name"] for n in nodes if n["ip"] == target_ip), "Unknown")
        
        keyboard = [
            [InlineKeyboardButton("✅ EVET", callback_data=f"confirm_switch_{current['ip']}_{target_ip}")],
            [InlineKeyboardButton("❌ İptal", callback_data="cancel_action")]
        ]
        
        await query.edit_message_text(
            f"⚠️ **Switch Onayı**\n\n"
            f"📡 Eski: {current['name']} ({current['ip']})\n"
            f"➡️ Yeni: {target_name} ({target_ip})\n\n"
            f"Onaylıyor musunuz?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if action.startswith("confirm_switch_"):
        parts = action.replace("confirm_switch_", "").split("_")
        old_ip, new_ip = parts[0], parts[1]
        
        success, msg = await replace_node(old_ip, new_ip)
        if success:
            for i, node in enumerate(nodes):
                if node["ip"] == new_ip:
                    state["current_node_index"] = i
                    break
            state["switch_count"] = state.get("switch_count", 0) + 1
            state["last_switch"] = time.time()
            state["bad_count"] = 0
            save_state(state)
            
            await query.edit_message_text(f"✅ {msg}", reply_markup=main_menu(state))
            
            for admin_id in ALLOWED_ADMINS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"🔄 **Manuel Switch**\n{old_ip} -> {new_ip}"
                    )
                except:
                    pass
        else:
            await query.edit_message_text(f"❌ {msg}", reply_markup=main_menu(state))
        return
    
    # ===== STATS =====
    if action == "stats":
        response = f"📊 **İSTATİSTİK**\n\n"
        response += f"📊 Failover: {state.get('switch_count', 0)}\n"
        response += f"🔄 Son: {time.strftime('%H:%M:%S', time.localtime(state.get('last_switch', 0))) if state.get('last_switch') else 'Hiç'}\n"
        response += f"⚠️ Hata: {state.get('bad_count', 0)}/{DEFAULT_FAIL_COUNT}\n"
        response += f"🔧 Mod: {'AUTO' if state.get('auto_mode', True) else 'MANUEL'}\n"
        response += f"🔄 Dönüş: {'ON' if state.get('auto_return', False) else 'OFF'}\n"
        response += f"⏱️ İnterval: {state.get('check_interval', DEFAULT_CHECK_INTERVAL)}s\n"
        if current:
            response += f"📡 Aktif: {current['name']} ({current['ip']})"
        await query.edit_message_text(response, reply_markup=main_menu(state))
        return
    
    # ===== REFRESH =====
    if action == "refresh_nodes":
        nodes = await discover_nodes()
        if not nodes:
            await query.edit_message_text("⚠️ Node bulunamadı", reply_markup=main_menu(state))
            return
        state["current_node_index"] = 0
        save_state(state)
        await query.edit_message_text(f"✅ {len(nodes)} node bulundu!", reply_markup=main_menu(state))
        return
    
    # ===== DELETE NODE =====
    if action == "delete_node":
        keyboard = []
        for node in nodes:
            keyboard.append([
                InlineKeyboardButton(
                    f"{node['name']} ({node['ip']})",
                    callback_data=f"delete_confirm_{node['ip']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel_action")])
        
        await query.edit_message_text(
            "❌ **Silinecek node'u seç:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if action.startswith("delete_confirm_"):
        ip = action.replace("delete_confirm_", "")
        node_name = next((n["name"] for n in nodes if n["ip"] == ip), "Unknown")
        
        keyboard = [
            [InlineKeyboardButton("✅ EVET", callback_data=f"delete_execute_{ip}")],
            [InlineKeyboardButton("❌ İptal", callback_data="cancel_action")]
        ]
        
        await query.edit_message_text(
            f"⚠️ **{node_name} ({ip}) silinsin mi?**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if action.startswith("delete_execute_"):
        ip = action.replace("delete_execute_", "")
        success, msg = await delete_node_from_panel(ip)
        if success:
            nodes = await discover_nodes()
            state = load_state()
            state["current_node_index"] = 0
            save_state(state)
            await query.edit_message_text(f"✅ {msg}", reply_markup=main_menu(state))
        else:
            await query.edit_message_text(f"❌ {msg}", reply_markup=main_menu(state))
        return
    
    # ===== RENAME NODE =====
    if action == "rename_node":
        keyboard = []
        for node in nodes:
            keyboard.append([
                InlineKeyboardButton(
                    f"{node['name']} ({node['ip']})",
                    callback_data=f"rename_select_{node['ip']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel_action")])
        
        await query.edit_message_text(
            "✏️ **Yeniden adlandırılacak node'u seç:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if action.startswith("rename_select_"):
        ip = action.replace("rename_select_", "")
        context.user_data["rename_ip"] = ip
        await query.edit_message_text(
            f"✏️ **{ip} için yeni isim girin:**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ İptal", callback_data="cancel_action")]
            ])
        )
        return
    
    # ===== CANCEL =====
    if action == "cancel_action":
        await query.edit_message_text("❌ İptal edildi", reply_markup=main_menu(state))
        return


# ============================================================
# ANA FONKSİYON
# ============================================================

async def main():
    print("\n" + "=" * 60)
    print("🛡️ PASARGUARD FAILOVER BOT — TRAFİK KONTROLLÜ FULL VERSİYON")
    print("=" * 60)
    print(f"📡 Panel: {PANEL_URL}")
    print(f"🔗 Proxy: {TELEGRAM_PROXY}")
    print(f"⏱️ İnterval: {DEFAULT_CHECK_INTERVAL}s")
    print(f"📊 Trafik Eşiği: {TRAFFIC_THRESHOLD_KB} KB")
    print(f"⚠️ Trafik Limiti: {TRAFFIC_FAIL_COUNT} kez")
    print("=" * 60)
    print("✅ Ping + Port + Trafik kontrolü")
    print("✅ Düşük trafikte otomatik failover")
    print("✅ Otomatik/Manuel mod")
    print("✅ Node yönetimi (ekle/sil/yeniden adlandır)")
    print("=" * 60 + "\n")
    
    try:
        await get_access_token()
        print("✅ Panel bağlantısı başarılı")
    except Exception as e:
        print(f"❌ Panel bağlantı hatası: {e}")
        return
    
    request = HTTPXRequest(proxy=TELEGRAM_PROXY, connection_pool_size=8)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    # Komutlar
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("switch", cmd_switch))
    app.add_handler(CommandHandler("nodes", cmd_nodes))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("traffic", cmd_traffic))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("rename", cmd_rename))
    
    # Butonlar ve mesajlar
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Job queue
    if app.job_queue:
        app.job_queue.run_repeating(smart_failover, interval=DEFAULT_CHECK_INTERVAL, first=5)
        print(f"🔄 Failover başlatıldı (her {DEFAULT_CHECK_INTERVAL}s)")
    else:
        print("❌ JobQueue hatası")
        return
    
    print("✅ Bot çalışıyor!")
    print("📱 /start ile başlayın")
    print("📋 Komutlar: /status, /switch, /nodes, /analyze, /traffic, /add, /delete, /rename")
    print("Press Ctrl+C to stop\n")
    
    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Kapatılıyor...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
