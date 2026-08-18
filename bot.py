import os
import time
import asyncio
import logging
import subprocess
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)

# ============ الإعدادات ============
BOT_TOKEN = "ضع_التوكن_هنا"
OWNER_ID = 123456789  # غيّره إلى ID الخاص بك

WORK_DIR = "/content/working"
os.makedirs(WORK_DIR, exist_ok=True)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ تخزين بيانات المستخدمين ============
user_data = {}

# ============ دوال مساعدة ============
def get_file_size_mb(file_path):
    return os.path.getsize(file_path) / (1024 * 1024)

def get_video_info(file_path):
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', file_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(result.stdout)

def compress_video(input_path, output_path, resolution, progress_callback=None):
    """ضغط الفيديو باستخدام GPU (NVENC)"""
    resolution_map = {
        "240p":  {"scale": "426:240",  "cq": "32", "audio_br": "64k"},
        "360p":  {"scale": "640:360",  "cq": "28", "audio_br": "96k"},
        "480p":  {"scale": "854:480",  "cq": "26", "audio_br": "128k"},
        "720p":  {"scale": "1280:720", "cq": "23", "audio_br": "128k"},
        "1080p": {"scale": "1920:1080","cq": "20", "audio_br": "192k"},
    }
    
    settings = resolution_map[resolution]
    
    # استخدام h264_nvenc لتسريع الضغط عبر GPU
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vf', f"scale={settings['scale']}",
        '-c:v', 'h264_nvenc', '-preset', 'p4',
        '-cq', settings['cq'],
        '-c:a', 'aac', '-b:a', settings['audio_br'],
        '-movflags', '+faststart',
        output_path
    ]
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    
    try:
        info = get_video_info(input_path)
        duration = float(info['format']['duration'])
    except:
        duration = 0
    
    for line in process.stderr:
        if "time=" in line:
            try:
                time_str = line.split("time=")[1].split(" ")[0]
                parts = time_str.split(":")
                current_time = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                if duration > 0 and progress_callback:
                    progress = min(100, int((current_time / duration) * 100))
                    progress_callback(progress)
            except:
                pass
    
    process.wait()
    return process.returncode == 0

def cleanup_user_files(user_id: int):
    for f in os.listdir(WORK_DIR):
        if f.startswith(f"{user_id}_"):
            try:
                os.remove(os.path.join(WORK_DIR, f))
            except:
                pass

# ============ معالجات الأوامر ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔️ هذا البوت للاستخدام الخاص فقط.")
        return
    
    await update.message.reply_text(
        "🎬 مرحباً بك في بوت ضغط الفيديو (مدعوم بـ GPU 🚀)\n\n"
        "📤 أرسل لي ملف فيديو (حتى 2GB)\n"
        "⚡️ الأحجام المتاحة: 240p, 360p, 480p, 720p, 1080p"
    )

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    
    message = update.message
    user_id = update.effective_user.id
    
    if message.document:
        file = message.document
        if not file.file_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
            await message.reply_text("⚠️ الرجاء إرسال ملف فيديو فقط")
            return
        filename = file.file_name
        filesize = file.file_size
    elif message.video:
        file = message.video
        filename = file.file_name or "video.mp4"
        filesize = file.file_size
    else:
        await message.reply_text("⚠️ الرجاء إرسال ملف فيديو")
        return
    
    if filesize > 2 * 1024 * 1024 * 1024:
        await message.reply_text("⚠️ حجم الملف أكبر من 2GB")
        return
    
    user_data[user_id] = {
        'file': file,
        'filename': filename,
        'filesize': filesize,
        'status': 'waiting_resolution'
    }
    
    keyboard = [
        [InlineKeyboardButton("📱 240p", callback_data="res_240p"), InlineKeyboardButton("📱 360p", callback_data="res_360p")],
        [InlineKeyboardButton("💻 480p", callback_data="res_480p"), InlineKeyboardButton("💻 720p", callback_data="res_720p")],
        [InlineKeyboardButton("🖥️ 1080p", callback_data="res_1080p")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    size_mb = filesize / (1024 * 1024)
    await message.reply_text(
        f"✅ تم استلام الملف:\n📄 الاسم: {filename}\n💾 الحجم: {size_mb:.2f} MB\n\n🎯 اختر دقة الضغط المطلوبة:",
        reply_markup=reply_markup
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    
    user_id = update.effective_user.id
    if user_id not in user_data or user_data[user_id].get('status') != 'waiting_cover':
        await update.message.reply_text("⚠️ الرجاء إرسال فيديو أولاً")
        return
    
    photo = update.message.photo[-1]
    file = await photo.get_file()
    cover_path = f"{WORK_DIR}/{user_id}_cover.jpg"
    await file.download_to_drive(cover_path)
    
    user_data[user_id]['cover_path'] = cover_path
    await process_video(update, context, user_id)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id != OWNER_ID:
        return
    
    if query.data == "cancel":
        if user_id in user_data:
            cleanup_user_files(user_id)
            del user_data[user_id]
        await query.edit_message_text("❌ تم إلغاء العملية")
        return
    
    if query.data.startswith("res_"):
        resolution = query.data.replace("res_", "")
        user_data[user_id]['resolution'] = resolution
        user_data[user_id]['status'] = 'waiting_cover'
        
        await query.edit_message_text(
            f"✅ تم اختيار: {resolution}\n\n🖼️ الآن أرسل صورة الغلاف للفيديو\n(أو أرسل /skip لتخطي الغلاف)"
        )

async def skip_cover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data and user_data[user_id].get('status') == 'waiting_cover':
        user_data[user_id]['cover_path'] = None
        await process_video(update, context, user_id)

async def process_video(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    data = user_data[user_id]
    resolution = data['resolution']
    filename = data['filename']
    cover_path = data.get('cover_path')
    
    progress_msg = await context.bot.send_message(chat_id=user_id, text="⏳ جاري تحميل الفيديو...")
    
    try:
        file = await data['file'].get_file()
        input_path = f"{WORK_DIR}/{user_id}_input.mp4"
        await file.download_to_drive(input_path)
        await progress_msg.edit_text("✅ تم التحميل، جاري الضغط عبر GPU...")
    except Exception as e:
        await context.bot.send_message(user_id, f"❌ خطأ في التحميل: {e}")
        cleanup_user_files(user_id)
        del user_data[user_id]
        return
    
    base_name = os.path.splitext(filename)[0]
    output_path = f"{WORK_DIR}/{user_id}_{base_name}_{resolution}.mp4"
    
    last_update = [0]
    async def update_progress(progress):
        current_time = time.time()
        if current_time - last_update[0] > 3:
            try:
                bar = "█" * (progress // 5) + "░" * (20 - progress // 5)
                await progress_msg.edit_text(f"🔄 جاري الضغط [{resolution}]\n\n[{bar}] {progress}%\n\n⏰ الرجاء الانتظار...")
                last_update[0] = current_time
            except:
                pass
    
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(
        None,
        lambda: compress_video(input_path, output_path, resolution, 
                               lambda p: asyncio.run_coroutine_threadsafe(update_progress(p), loop))
    )
    
    if not success:
        await progress_msg.edit_text("❌ فشل ضغط الفيديو")
        cleanup_user_files(user_id)
        del user_data[user_id]
        return
    
    try:
        filesize_mb = get_file_size_mb(output_path)
        await progress_msg.edit_text("📤 جاري إرسال الفيديو...")
        
        thumb_file = open(cover_path, 'rb') if cover_path and os.path.exists(cover_path) else None
        with open(output_path, 'rb') as video_file:
            await context.bot.send_video(
                chat_id=user_id,
                video=video_file,
                caption=f"✅ تم الضغط بنجاح\n🎯 الدقة: {resolution}\n💾 الحجم: {filesize_mb:.2f} MB",
                supports_streaming=True,
                thumbnail=thumb_file
            )
        
        await progress_msg.delete()
        await context.bot.send_message(user_id, "✨ تم بنجاح! أرسل فيديو آخر للضغط")
        
    except Exception as e:
        await context.bot.send_message(user_id, f"❌ خطأ في الإرسال: {e}")
    finally:
        cleanup_user_files(user_id)
        if user_id in user_data:
            del user_data[user_id]

# ============ تشغيل البوت ============
def main():
    if BOT_TOKEN == "ضع_التوكن_هنا":
        print("❌ الرجاء وضع التوكن في المتغير BOT_TOKEN")
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("skip", skip_cover))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("✅ البوت يعمل الآن...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
