# bot.py
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = "8437293037:AAHrYlUY2SU8VdM-EXedLcTF-Trg_4KHZ2w"
ADMIN_ID = 7279061074  # kendi Telegram ID

URUN_DOSYA = "urunler.json"

# --- Dosya okuma/yazma ---
def urunleri_oku():
    if not os.path.exists(URUN_DOSYA):
        with open(URUN_DOSYA, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return []
    with open(URUN_DOSYA, "r", encoding="utf-8") as f:
        return json.load(f)

def urunleri_yaz(veri):
    with open(URUN_DOSYA, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)

def is_admin(user_id):
    return user_id == ADMIN_ID

# --- /start komutu (müşteri tarafı) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    urunler = urunleri_oku()
    if not urunler:
        await update.message.reply_text("Mağazamızda henüz ürün yok.")
        return
    bolumler = sorted(list(set(u["bolum"] for u in urunler)))
    keyboard = [[InlineKeyboardButton(b, callback_data=f"user_bolum::{b}")] for b in bolumler]
    await update.message.reply_text("🛍️ Mağazaya hoş geldin! Bölümler:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- /panel komutu (admin) ---
async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Bu komutu kullanma yetkin yok.")
        return
    urunler = urunleri_oku()
    bolumler = sorted(list(set(u["bolum"] for u in urunler))) or ["Yeni Bölüm"]
    keyboard = [[InlineKeyboardButton(b, callback_data=f"bolum::{b}")] for b in bolumler]
    keyboard.append([InlineKeyboardButton("❌ Kapat", callback_data="panel_close")])
    await update.message.reply_text("👑 Admin Paneli: Bölümler", reply_markup=InlineKeyboardMarkup(keyboard))

# --- Callback query handler ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    urunler = urunleri_oku()

    # --- Admin Panel ---
    if is_admin(user_id):
        # Panel kapatma
        if data == "panel_close":
            await query.edit_message_text("Panel kapatıldı ✔️")
            return

        # Bölüm seçimi (admin)
        if data.startswith("bolum::"):
            bolum = data.split("::",1)[1]
            urun_listesi = [u for u in urunler if u["bolum"]==bolum]
            if not urun_listesi:
                await query.edit_message_text(f"{bolum} bölümünde ürün yok.")
                return
            keyboard = []
            for u in urun_listesi:
                keyboard.append([InlineKeyboardButton(f"🗑️ {u['isim']}", callback_data=f"delete::{bolum}::{u['isim']}")])
                keyboard.append([InlineKeyboardButton(f"✏️ {u['isim']}", callback_data=f"edit::{bolum}::{u['isim']}")])
            keyboard.append([InlineKeyboardButton("➕ Ürün Ekle", callback_data=f"add::{bolum}")])
            keyboard.append([InlineKeyboardButton("🔙 Bölümler", callback_data="panel")])
            await query.edit_message_text(f"📂 {bolum} Bölümü", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # Ürün sil
        if data.startswith("delete::"):
            _, bolum, isim = data.split("::")
            urunler = [u for u in urunler if not (u["bolum"]==bolum and u["isim"]==isim)]
            urunleri_yaz(urunler)
            await query.edit_message_text(f"✅ '{isim}' ürünü silindi.")
            return

        # Ürün ekleme
        if data.startswith("add::"):
            _, bolum = data.split("::")
            context.user_data["state"] = "adding"
            context.user_data["add_bolum"] = bolum
            await query.edit_message_text(f"➕ {bolum} bölümüne ürün ekleme moduna girdin.\nLütfen mesajla gönder:\nisim;fiyat\nÖrn: Tişört;150")
            return

        # Ürün fiyat düzenleme
        if data.startswith("edit::"):
            _, bolum, isim = data.split("::")
            context.user_data["state"] = "editing"
            context.user_data["edit_bolum"] = bolum
            context.user_data["edit_target"] = isim
            await query.edit_message_text(f"✏️ '{isim}' ürünü için yeni fiyatı gönder (sadece sayı).")
            return

        # Ana panele geri dön
        if data == "panel":
            await panel(update, context)
            return

    # --- Kullanıcı tarafı (müşteri) ---
    if data.startswith("user_bolum::"):
        bolum = data.split("::",1)[1]
        urun_listesi = [u for u in urunler if u["bolum"]==bolum]
        if not urun_listesi:
            await query.edit_message_text(f"{bolum} bölümünde ürün yok.")
            return
        keyboard = [[InlineKeyboardButton(f"🛒 {u['isim']} ({u['fiyat']} TL)", callback_data=f"buy::{bolum}::{u['isim']}")] for u in urun_listesi]
        keyboard.append([InlineKeyboardButton("🔙 Bölümler", callback_data="user_back")])
        await query.edit_message_text(f"📂 {bolum} Bölümü Ürünler:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "user_back":
        # Bölüm seçimine dön
        urunler = urunleri_oku()
        bolumler = sorted(list(set(u["bolum"] for u in urunler)))
        keyboard = [[InlineKeyboardButton(b, callback_data=f"user_bolum::{b}")] for b in bolumler]
        await query.edit_message_text("🛍️ Mağazaya hoş geldin! Bölümler:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Satın alma butonu
    if data.startswith("buy::"):
        _, bolum, isim = data.split("::")
        # Admin'e bildirim gönder
        try:
            await context.bot.send_message(ADMIN_ID, f"🛒 Kullanıcı @{query.from_user.username or query.from_user.first_name} {bolum} bölümünden '{isim}' almak istiyor.")
        except:
            # Kullanıcının username yoksa first_name
            await context.bot.send_message(ADMIN_ID, f"🛒 Kullanıcı {query.from_user.first_name} {bolum} bölümünden '{isim}' almak istiyor.")
        await query.edit_message_text(f"✅ Sipariş talebiniz iletildi: '{isim}'")
        return

# --- Metin mesajları (admin ekleme/düzenleme) ---
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    state = context.user_data.get("state")
    text = (update.message.text or "").strip()

    if state == "adding":
        bolum = context.user_data.get("add_bolum")
        if ";" not in text:
            await update.message.reply_text("Format yanlış. Lütfen: isim;fiyat")
            return
        isim, fiyat_raw = map(str.strip, text.split(";",1))
        try:
            fiyat = int(fiyat_raw)
        except ValueError:
            await update.message.reply_text("Fiyat sayı olmalı.")
            return
        urunler = urunleri_oku()
        urunler.append({"bolum": bolum, "isim": isim, "fiyat": fiyat})
        urunleri_yaz(urunler)
        await update.message.reply_text(f"✅ '{isim}' ürünü eklendi. Fiyat: {fiyat} TL")
        context.user_data.pop("state", None)
        context.user_data.pop("add_bolum", None)
        return

    if state == "editing":
        bolum = context.user_data.get("edit_bolum")
        hedef = context.user_data.get("edit_target")
        try:
            yeni_fiyat = int(text)
        except ValueError:
            await update.message.reply_text("Fiyat sayı olmalı.")
            return
        urunler = urunleri_oku()
        changed = False
        for u in urunler:
            if u["bolum"]==bolum and u["isim"]==hedef:
                u["fiyat"] = yeni_fiyat
                changed = True
                break
        if changed:
            urunleri_yaz(urunler)
            await update.message.reply_text(f"✅ '{hedef}' fiyatı {yeni_fiyat} TL olarak güncellendi.")
        else:
            await update.message.reply_text("Ürün bulunamadı.")
        context.user_data.pop("state", None)
        context.user_data.pop("edit_bolum", None)
        context.user_data.pop("edit_target", None)
        return

# --- Ana fonksiyon ---
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("Bot çalışıyor...")
    app.run_polling()

if __name__ == "__main__":
    main()