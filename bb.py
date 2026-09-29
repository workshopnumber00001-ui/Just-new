import logging
import os
import io
import re
import html
import tempfile
import asyncio
import threading
import zipfile
import aiohttp
from aiohttp import ClientTimeout, ClientError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from datetime import datetime

# ================= CONFIGURATION =================

BOT_TOKENS = [t.strip() for t in os.getenv("BOT_TOKENS", "").split(",") if t.strip()]
if not BOT_TOKENS:
    raise ValueError("Please set BOT_TOKENS environment variable (comma separated)")

LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "-1003621974261"))
API_BASE = os.getenv("API_BASE", "https://backend.multistreaming.site/api")

KGS_API_BASE = os.getenv("KGS_API_BASE", "https://kgs-main-api-scamer.vercel.app")
KGS_COURSES_ENDPOINT = os.getenv("KGS_COURSES_ENDPOINT", "/get-courses")
KGS_SUBJECTS_ENDPOINT = os.getenv("KGS_SUBJECTS_ENDPOINT", "/subjects/{}")
KGS_LESSONS_ENDPOINT = os.getenv("KGS_LESSONS_ENDPOINT", "/lessons/{}")

TW_API_BASE = os.getenv("TW_API_BASE", "https://node.topperswisdom.com/api")
TW_COURSES_ENDPOINT = os.getenv("TW_COURSES_ENDPOINT", "/courses")
TW_TOPICS_ENDPOINT = os.getenv("TW_TOPICS_ENDPOINT", "/topic-and-section?courseId={course_id}")
TW_CLASSES_ENDPOINT = os.getenv("TW_CLASSES_ENDPOINT", "/topics/{topic_id}/classes?courseId={course_id}")

try:
    auth_users_str = os.getenv("AUTHORIZED_USERS", "5349573682,8453406690,1193248592")
    AUTHORIZED_USERS = [int(uid.strip()) for uid in auth_users_str.split(",") if uid.strip()]
    if not AUTHORIZED_USERS:
        AUTHORIZED_USERS = [5349573682, 8453406690]
        logging.warning("No authorized users found, using fallback IDs")
except ValueError as e:
    AUTHORIZED_USERS = [5349573682, 8453406690]
    logging.warning(f"Invalid AUTHORIZED_USERS format: {e}, using fallback IDs")

STYLISH_NAME = os.getenv("STYLISH_NAME", "❣️")

START_THUMBNAIL = "https://ibb.co/PsmQWNJW"
EXTRACT_THUMBNAIL = "https://ibb.co/PsmQWNJW"

BANNER_LINE = "━━━━━━━━━━━━━━━━━━━━━━\n⚡ ᴏᴡɴᴇʀ: ⛧Ꮶʀɪsʜɴᴀㅤ⸙\n━━━━━━━━━━━━━━━━━━━━━━\n"

CW_API_BASE = os.getenv("CW_API_BASE", "https://yeasty-mufi-scammerbotscw1-ba766b94.koyeb.app")
CW_DOWNLOAD_PDF = os.getenv("CW_DOWNLOAD_PDF", f"{CW_API_BASE.rstrip('/')}/download-pdf")
CW_API_KEY = os.getenv("CW_API_KEY", "scammer09876")

CW_BATCH_API = os.getenv("CW_BATCH_API", f"{CW_API_BASE.rstrip('/')}/batch/{{}}")
CW_TOPIC_API = os.getenv("CW_TOPIC_API", f"{CW_API_BASE.rstrip('/')}/batch?batchid={{}}&topicid={{}}")
CW_VIDEO_API = os.getenv("CW_VIDEO_API", f"{CW_API_BASE.rstrip('/')}/get_video_details?name={{}}")

CW_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "X-API-Key": CW_API_KEY
}

CAREERWILL_BUILD_ID = os.getenv("CAREERWILL_BUILD_ID", "")
CAREERWILL_COOKIE = os.getenv("CAREERWILL_COOKIE", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ================= LOGGING HELPER =================
async def send_log(bot, text, parse_mode=ParseMode.HTML):
    try:
        await bot.send_message(chat_id=LOG_CHANNEL_ID, text=text, parse_mode=parse_mode)
    except Exception as e:
        logging.error(f"Failed to send log to channel: {e}")

# ================= HELPERS =================

def is_authorized(user_id):
    return user_id in AUTHORIZED_USERS

def sanitize_filename(name):
    return re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')

async def fetch_with_retry(session, url, retries=3, timeout=10):
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logging.warning(f"Attempt {attempt+1}: {url} returned {resp.status}")
        except (ClientError, asyncio.TimeoutError) as e:
            logging.warning(f"Attempt {attempt+1} failed: {e}")
        await asyncio.sleep(1)
    return None

def extract_links_from_item(item):
    links = []
    if isinstance(item, dict):
        title_candidates = []
        for key in ['title', 'name', 'lessonName', 'videoName', 'pdfName', 'description']:
            if key in item and item[key]:
                title_candidates.append(str(item[key]))
        title = title_candidates[0] if title_candidates else "Untitled"

        for key, value in item.items():
            if isinstance(value, str) and value.startswith('http'):
                links.append((value, title))
            elif isinstance(value, (dict, list)):
                sub_links = extract_links_from_item(value)
                for sub_link, sub_title in sub_links:
                    if sub_title != "Untitled":
                        links.append((sub_link, sub_title))
                    else:
                        links.append((sub_link, title))
    elif isinstance(item, list):
        for elem in item:
            links.extend(extract_links_from_item(elem))
    return links

async def decrypt_video_token_async(session, raw_token_str):
    if not raw_token_str or str(raw_token_str).startswith('http'):
        return raw_token_str, ""
    try:
        target_url = CW_VIDEO_API.format(raw_token_str)
        async with session.get(target_url, timeout=8) as dec_res:
            if dec_res.status == 200:
                dec_data = await dec_res.json()
                if dec_data.get("status") is True and "data" in dec_data and "link" in dec_data["data"]:
                    link_obj = dec_data["data"]["link"]
                    return link_obj.get("file_url"), link_obj.get("token", "")
                else:
                    file_url = dec_data.get('videoUrl') or dec_data.get('url') or dec_data.get('link') or dec_data.get('file_url')
                    return file_url or target_url, dec_data.get('token', '')
    except Exception:
        pass
    return CW_VIDEO_API.format(raw_token_str), ""

async def process_single_video_async(session, vid, topic_name):
    if not isinstance(vid, dict):
        return "", 0
    vid_name = vid.get('title') or vid.get('videoName') or vid.get('name') or 'Video'
    raw_token = vid.get('video_url') or vid.get('videoLink') or vid.get('url') or vid.get('link') or vid.get('video_url_raw')
    if not raw_token:
        for val in vid.values():
            val_str = str(val).strip()
            if "_" in val_str and not val_str.startswith('http') and len(val_str) > 8:
                raw_token = val_str
                break
    if raw_token:
        raw_token_str = str(raw_token).strip()
        if not raw_token_str.startswith('http'):
            video_url, key_token = await decrypt_video_token_async(session, raw_token_str)
            v_text = f"[{topic_name}] {STYLISH_NAME} {vid_name} : {video_url}\n"
            if key_token:
                v_text += f"🔑 DRM Key: {key_token}\n"
            return v_text, 1
        else:
            return f"[{topic_name}] {STYLISH_NAME} {vid_name} : {raw_token_str}\n", 1
    return "", 0

async def extract_topic_content(session, batch_id, topic, t_index):
    if not isinstance(topic, dict):
        return "", 0, 0
    topic_name = topic.get('topicName') or topic.get('name') or 'Unnamed Topic'
    topic_id = topic.get('topicId') or topic.get('id') or topic.get('_id')
    if not topic_id:
        return "", 0, 0
    try:
        content_url = CW_TOPIC_API.format(batch_id, topic_id)
        async with session.get(content_url, timeout=12) as content_res:
            if content_res.status != 200:
                return "", 0, 0
            content_json = await content_res.json()
            inner_data = content_json.get('data', content_json) if isinstance(content_json, dict) else content_json
            raw_videos, raw_pdfs = [], []
            if isinstance(inner_data, dict):
                raw_videos = inner_data.get('classes', []) or inner_data.get('videos', []) or inner_data.get('class', [])
                raw_pdfs = inner_data.get('notes', []) or inner_data.get('pdfs', []) or inner_data.get('batch-notes', [])
            v_count, p_count = 0, 0
            t_text = ""
            if raw_videos:
                tasks = [process_single_video_async(session, vid, topic_name) for vid in raw_videos]
                video_results = await asyncio.gather(*tasks)
                for res_str, count in video_results:
                    t_text += res_str
                    v_count += count
            if raw_pdfs:
                for pdf in raw_pdfs:
                    if isinstance(pdf, dict):
                        pdf_name = pdf.get('title') or pdf.get('pdfName') or pdf.get('name') or 'Document'
                        pdf_path = pdf.get('download_url') or pdf.get('view_url') or pdf.get('pdfLink') or pdf.get('path') or pdf.get('file_path')
                        if pdf_path:
                            if not pdf_path.startswith('http'):
                                encrypted_id = pdf.get('encrypted_id') or pdf.get('file_id') or ''
                                if encrypted_id:
                                    pdf_url = f"{CW_DOWNLOAD_PDF}?url={encrypted_id}:{pdf_path}&key={CW_API_KEY}"
                                else:
                                    pdf_url = f"{CW_DOWNLOAD_PDF}?url={pdf_path}&key={CW_API_KEY}"
                            else:
                                pdf_url = pdf_path
                        else:
                            pdf_url = 'No Link'
                        t_text += f"[{topic_name}] {STYLISH_NAME} {pdf_name} : {pdf_url}\n"
                        p_count += 1
            return t_text, v_count, p_count
    except Exception:
        return "", 0, 0

async def fetch_cw_batch_topics(batch_id):
    async with aiohttp.ClientSession(headers=CW_HEADERS) as session:
        topics_data = await fetch_with_retry(session, CW_BATCH_API.format(batch_id), retries=2)
        if topics_data is None:
            return None
        batch_details = topics_data.get('data', topics_data) if isinstance(topics_data, dict) else topics_data
        topics = batch_details.get('topics', []) if isinstance(batch_details, dict) else batch_details
        if not isinstance(topics, list) or not topics:
            return None
        return topics, batch_details.get('batchName') or batch_details.get('name') or f"Batch_{batch_id}"

async def get_careerwill_build_id():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://web.careerwill.com/live-classes", headers={"User-Agent": "Mozilla/5.0"}) as resp:
                html_content = await resp.text()
                match = re.search(r'/_next/static/([^/]+)/_buildManifest', html_content)
                if match:
                    return match.group(1)
                match = re.search(r'"buildId":"([^"]+)"', html_content)
                if match:
                    return match.group(1)
    except Exception:
        pass
    return None

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await send_log(context.bot, f"📥 /start used by {user.first_name} (@{user.username}) ID: {user.id}")
    welcome_text = (
        f"✨ <b>Welcome to {STYLISH_NAME} Bot</b> 🤩🎭\n\n"
        f"🚀 <b>Commands:</b> ⚡\n"
        f"🔹 /extract - Extract Batches (Auth Required) 🕵️‍♂️📦\n"
        f"🔹 /cw - Extract Careerwill Batches (Auth Required) 🧩\n"
        f"🔹 /careerwill - Same as /cw 🎯\n"
        f"🔹 /kgs - Extract KGS Courses (Auth Required) 📘\n"
        f"🔹 /tw - Extract Toppers Wisdom Courses (Auth Required) 🧠\n"
        f"🔹 /split - Interactive Subject-wise split (Auth Required) 🧠💡\n"
        f"🔹 /dedup - Remove duplicate links from TXT (Auth Required) 🧹\n\n"
        f"📢 Send a .txt file after typing /split or /dedup. 🤓👇"
    )
    await update.message.reply_photo(photo=START_THUMBNAIL, caption=welcome_text, parse_mode=ParseMode.HTML)

async def split_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await send_log(context.bot, f"⛔ Unauthorized /split attempt by {user.id}")
        await update.message.reply_text("🚫 <b>Access Denied!</b> 🤡", parse_mode=ParseMode.HTML)
        return
    await send_log(context.bot, f"📂 /split started by {user.first_name} (@{user.username})")
    context.user_data['split_mode'] = True
    context.user_data.pop('dedup_mode', None)
    await update.message.reply_text(
        "📂 Ab wo <b>.txt</b> file bhejein. 🤔\n\n"
        "✅ I Will split your txt 🌻 topic wise 🎞️",
        parse_mode=ParseMode.HTML
    )

async def dedup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await send_log(context.bot, f"⛔ Unauthorized /dedup attempt by {user.id}")
        await update.message.reply_text("🚫 <b>Access Denied!</b> 🤡", parse_mode=ParseMode.HTML)
        return
    await send_log(context.bot, f"🧹 /dedup started by {user.first_name} (@{user.username})")
    context.user_data['dedup_mode'] = True
    context.user_data.pop('split_mode', None)
    await update.message.reply_text(
        "📂 Ab wo <b>.txt</b> file bhejein jiske duplicate links hatane hain. 🔍\n\n"
        "✅ Main duplicate URLs hata kar ek clean file bhej dunga. 🧹",
        parse_mode=ParseMode.HTML
    )

async def handle_any_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

async def handle_split_txt(update: Update, context: ContextTypes.DEFAULT_TYPE, doc):
    user = update.effective_user
    msg = await update.message.reply_text("⏳ Analyzing file and extracting subjects... 🤖⚡")
    
    original_filename = doc.file_name
    base_name = os.path.splitext(original_filename)[0]
    sanitized_base = sanitize_filename(base_name)
    
    tmp_in_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp_in:
            bot_file = await context.bot.get_file(doc.file_id)
            await bot_file.download_to_drive(tmp_in.name)
            tmp_in_path = tmp_in.name

        groups_dict = {}

        with open(tmp_in_path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if not line or "http" not in line:
                    continue

                parts = re.split(r'\s*:\s*', line, 1)
                if len(parts) < 2:
                    continue

                title, url = parts[0].strip(), parts[1].strip()

                subject = "General"
                if '[' in title and ']' in title:
                    bracket_content = title.split(']', 1)[0].replace('[', '').strip()
                    subject_parts = re.split(r'[-|,;_]', bracket_content)
                    subject = subject_parts[0].strip()
                    if not subject:
                        subject = "General"
                else:
                    subject = "General"

                subject = re.sub(r'^[^\w\s]+', '', subject).strip()
                if not subject:
                    subject = "General"

                norm_key = re.sub(r'\s+', '_', subject.strip())

                if norm_key not in groups_dict:
                    groups_dict[norm_key] = {
                        'subject': subject,
                        'lines': [],
                        'videos': 0,
                        'pdfs': 0
                    }

                is_pdf = (
                    ".pdf" in url.lower() or 
                    "/file/d/" in url.lower() or 
                    "drive.google.com" in url.lower() or
                    "docs.google.com" in url.lower()
                )
                
                if is_pdf:
                    groups_dict[norm_key]['pdfs'] += 1
                else:
                    groups_dict[norm_key]['videos'] += 1

                groups_dict[norm_key]['lines'].append(line)

        if not groups_dict:
            await msg.edit_text("❌ No valid entries found in the TXT file. 💀", parse_mode=ParseMode.HTML)
            return

        groups_list = list(groups_dict.values())
        total_subjects = len(groups_list)

        context.user_data['split_data'] = {
            'groups': groups_list,
            'selected': [True] * total_subjects,
            'base_name': sanitized_base,
            'original_filename': original_filename
        }

        await display_split_menu(update, context, msg, page=0)

    except Exception as e:
        logging.exception("Split error")
        await send_log(context.bot, f"❌ Split error: {str(e)} by {user.id}")
        await update.message.reply_text(f"❌ Error: {str(e)} 😵‍💫")
    finally:
        if tmp_in_path and os.path.exists(tmp_in_path):
            os.remove(tmp_in_path)

async def display_split_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, status_msg=None, page=0):
    data = context.user_data.get('split_data')
    if not data:
        return

    groups = data['groups']
    selected = data['selected']
    original_filename = data['original_filename']
    total_subjects = len(groups)
    total_lines = sum(len(g['lines']) for g in groups)

    ITEMS_PER_PAGE = 8
    total_pages = (total_subjects + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, total_subjects)
    
    keyboard = []
    
    for idx in range(start_idx, end_idx):
        group = groups[idx]
        subject = group['subject']
        total_count = group['videos'] + group['pdfs']
        icon = "✅" if selected[idx] else "🔲"
        btn_text = f"{icon} {subject} ({total_count})" 
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"toggle_{idx}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page_{page-1}"))
    nav_buttons.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("✅ Select All", callback_data="select_all"),
        InlineKeyboardButton("❌ Deselect All", callback_data="deselect_all")
    ])
    keyboard.append([InlineKeyboardButton("📨 Done", callback_data="split_done")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    header_text = (
        f"🟢 <b>Subjects Extracted Successfully !</b> 🥳🎯\n"
        f"🗂️ Total Subjects (Folders): {total_subjects}\n"
        f"📝 Total Items: {total_lines}\n\n"
        f"👉 <b>Select Subjects from below Buttons :</b> 🧩"
    )

    if status_msg:
        await status_msg.edit_text(header_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.callback_query.edit_message_text(header_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def split_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    split_data = context.user_data.get('split_data')
    if not split_data:
        await query.edit_message_text("⚠️ Session expired. Please use /split command again. 💀")
        return

    groups = split_data['groups']
    selected = split_data['selected']

    if data.startswith("toggle_"):
        idx = int(data.split("_")[1])
        selected[idx] = not selected[idx]
        current_page = context.user_data.get('split_current_page', 0)
        await display_split_menu(update, context, page=current_page)
        
    elif data == "select_all":
        for i in range(len(selected)):
            selected[i] = True
        current_page = context.user_data.get('split_current_page', 0)
        await display_split_menu(update, context, page=current_page)
        
    elif data == "deselect_all":
        for i in range(len(selected)):
            selected[i] = False
        current_page = context.user_data.get('split_current_page', 0)
        await display_split_menu(update, context, page=current_page)

    elif data.startswith("page_"):
        page = int(data.split("_")[1])
        context.user_data['split_current_page'] = page
        await display_split_menu(update, context, page=page)
        
    elif data == "split_done":
        selected_indices = [i for i, s in enumerate(selected) if s]
        if not selected_indices:
            await query.edit_message_text("⚠️ Koi subject select nahi kiya gaya. Please select at least one subject. 🫣")
            return

        await query.edit_message_text("⏳ Generating selected files... Please wait. 🤞✨")

        base_name = split_data['base_name']
        original_filename = split_data['original_filename']
        
        for idx in selected_indices:
            group = groups[idx]
            subject = group['subject']
            lines = group['lines']
            video_count = group['videos']
            pdf_count = group['pdfs']
            total = len(lines)

            content = "\n".join(lines)
            file_buffer = io.BytesIO(content.encode('utf-8'))
            
            filename = f"{base_name}_{sanitize_filename(subject)}.txt"
            display_name = subject

            caption = (
                f"📂 <b>{display_name}</b> (<code>{original_filename}</code>)\n"
                f"📦 Total: {total}\n"
                f"🎥 Videos: {video_count}\n"
                f"📄 PDFs: {pdf_count}"
            )

            await query.message.reply_document(
                document=file_buffer,
                filename=filename,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
            await asyncio.sleep(0.3)

        await query.message.reply_text(
            f"✅ Split Complete! 🎉\n"
            f"📁 Total Files Generated: {len(selected_indices)} 🤌\n"
            f"✨ {STYLISH_NAME}",
            parse_mode=ParseMode.HTML
        )
        
        context.user_data.pop('split_data', None)
        context.user_data.pop('split_mode', None)
        context.user_data.pop('split_current_page', None)
        await query.edit_message_text("✅ Done! Files sent above. 🚀")
        await send_log(context.bot, f"✅ Split completed by {query.from_user.first_name} - {len(selected_indices)} files generated")

async def handle_dedup_txt(update: Update, context: ContextTypes.DEFAULT_TYPE, doc):
    user = update.effective_user
    msg = await update.message.reply_text("⏳ Removing duplicate links... 🔍🧹")
    tmp_in_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp_in:
            bot_file = await context.bot.get_file(doc.file_id)
            await bot_file.download_to_drive(tmp_in.name)
            tmp_in_path = tmp_in.name

        with open(tmp_in_path, 'r', encoding='utf-8-sig') as f:
            lines = f.read().splitlines()

        seen_urls = set()
        unique_lines = []
        total_lines = len(lines)

        for line in lines:
            if not line.strip():
                continue
            url_match = re.search(r'https?://\S+', line)
            if url_match:
                url = url_match.group(0)
                if url not in seen_urls:
                    seen_urls.add(url)
                    unique_lines.append(line)
            else:
                unique_lines.append(line)

        unique_count = len(unique_lines)
        removed_count = total_lines - unique_count

        output_content = "\n".join(unique_lines)
        file_buffer = io.BytesIO(output_content.encode('utf-8'))
        filename = f"{sanitize_filename(doc.file_name.replace('.txt', ''))}_dedup.txt"

        caption = (
            f"🧹 <b>Duplicate Removal Complete!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 Original Lines: {total_lines}\n"
            f"🗑️ Duplicates Removed: {removed_count}\n"
            f"✅ Unique Lines: {unique_count}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✨ {STYLISH_NAME}"
        )

        await update.message.reply_document(
            document=file_buffer,
            filename=filename,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
        await msg.delete()
        context.user_data.pop('dedup_mode', None)
        await send_log(context.bot, f"✅ Dedup completed by {user.first_name} - removed {removed_count} duplicates")

    except Exception as e:
        logging.exception("Dedup error")
        await send_log(context.bot, f"❌ Dedup error: {str(e)} by {user.id}")
        await update.message.reply_text(f"❌ Error: {str(e)} 😵‍💫")
    finally:
        if tmp_in_path and os.path.exists(tmp_in_path):
            os.remove(tmp_in_path)

async def extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await send_log(context.bot, f"⛔ Unauthorized /extract attempt by {user.id}")
        await update.message.reply_text(f"🚫 <b>Access Denied!</b>\n\nApki User ID <code>{user.id}</code> 🤡", parse_mode=ParseMode.HTML)
        return

    await send_log(context.bot, f"📂 /extract started by {user.first_name} (@{user.username})")
    await update.message.reply_photo(photo=EXTRACT_THUMBNAIL, caption="⏳ <b>Fetching Batches...</b> 🔎", parse_mode=ParseMode.HTML)
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            data = await fetch_with_retry(session, f"{API_BASE}/courses/")
            if data is None:
                await send_log(context.bot, f"❌ /extract API error for {user.id}")
                await update.message.reply_text("❌ API Error! Please try again later. 😵‍💫")
                return
            batches = data.get("data", [])
            if not batches:
                await send_log(context.bot, f"⚠️ /extract no batches found for {user.id}")
                await update.message.reply_text("⚠️ No batches found. 🥲")
                return

            txt_content = f"{BANNER_LINE}\n         BATCHES LIST\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for idx, batch in enumerate(batches, 1):
                txt_content += f"{idx}. {batch['title']} (ID: {batch['id']})\n"
            file_buffer = io.BytesIO(txt_content.encode('utf-8'))
            file_buffer.name = "Batch_List.txt"
            await update.message.reply_document(
                document=file_buffer,
                filename="Batch_List.txt",
                caption="📋 **List of all available batches.** Now use the buttons below to extract any batch.",
                parse_mode=ParseMode.HTML
            )

            context.user_data['all_batches'] = batches
            context.user_data['batch_map'] = {str(b['id']): b['title'] for b in batches}
            context.user_data['extract_page'] = 0
            await show_extract_page(update, context, page=0)
    except Exception as e:
        logging.exception("Extract error")
        await send_log(context.bot, f"❌ /extract error: {str(e)} by {user.id}")
        await update.message.reply_text(f"❌ Error: {str(e)} 😵‍💫")

async def show_extract_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, edit=False):
    batches = context.user_data.get('all_batches', [])
    total = len(batches)
    per_page = 10
    total_pages = (total + per_page - 1) // per_page
    if page < 0: page = 0
    if page >= total_pages: page = total_pages - 1
    start = page * per_page
    end = min(start + per_page, total)
    page_batches = batches[start:end]

    header = f"📚 <b>Available Batches</b> 📂\n━━━━━━━━━━━━━━━━━━━━━━\nPage {page+1}/{total_pages} – {total} batches\nSelect a batch to extract its content.\n━━━━━━━━━━━━━━━━━━━━━━\n"
    kb = []
    for b in page_batches:
        kb.append([InlineKeyboardButton(f"📁 {b['title'][:40]}", callback_data=f"sel_{b['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"extract_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"extract_page_{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_extract")])
    reply_markup = InlineKeyboardMarkup(kb)

    context.user_data['extract_page'] = page

    if edit:
        await update.callback_query.edit_message_text(header, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(header, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def cw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await careerwill(update, context)

async def careerwill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await send_log(context.bot, f"⛔ Unauthorized /cw attempt by {user.id}")
        await update.message.reply_text(f"🚫 <b>Access Denied!</b>\n\nApki User ID <code>{user.id}</code> 🤡", parse_mode=ParseMode.HTML)
        return

    await send_log(context.bot, f"📂 /cw started by {user.first_name} (@{user.username})")
    msg = await update.message.reply_text("⏳ Fetching Careerwill batches... 🧭")
    try:
        build_id = await get_careerwill_build_id()
        if not build_id:
            build_id = "WuAwSZiJu-Vol1R998vWW"
        CAREERWILL_BASE = f"https://web.careerwill.com/_next/data/{build_id}"
        CAREERWILL_BATCHES = f"{CAREERWILL_BASE}/live-classes.json?view=List&interface_id=1"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://web.careerwill.com/live-classes?view=List&interface_id=1",
            "Accept": "application/json",
        }
        if CAREERWILL_COOKIE:
            headers["Cookie"] = CAREERWILL_COOKIE

        async with aiohttp.ClientSession(headers=headers) as session:
            data = await fetch_with_retry(session, CAREERWILL_BATCHES)
            if data is None:
                if CAREERWILL_COOKIE:
                    headers.pop("Cookie", None)
                    data = await fetch_with_retry(session, CAREERWILL_BATCHES)
                if data is None:
                    await send_log(context.bot, f"❌ /cw fetch failed for {user.id}")
                    await msg.edit_text(
                        "❌ Failed to fetch batches.\n"
                        "Please set CAREERWILL_COOKIE environment variable."
                    )
                    return

        live_classes = data.get("pageProps", {}).get("liveClasses", [])
        if not live_classes:
            await send_log(context.bot, f"⚠️ /cw no batches found for {user.id}")
            await msg.edit_text("⚠️ No batches found. Check your cookie or try later.")
            return

        txt_content = f"{BANNER_LINE}\n         CAREERWILL BATCHES LIST\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for batch in live_classes:
            b_id = batch.get('id')
            b_name = batch.get('batchName', 'Unknown')
            txt_content += f"ID: {b_id} - {b_name}\n"
        file_buffer = io.BytesIO(txt_content.encode('utf-8'))
        file_buffer.name = "Careerwill_Batches.txt"
        await update.message.reply_document(
            document=file_buffer,
            filename="Careerwill_Batches.txt",
            caption=f"📋 Available Batches: {len(live_classes)}\n\nSend the Batch ID to start extraction.",
            parse_mode=ParseMode.HTML
        )

        context.user_data['careerwill_batches'] = live_classes
        context.user_data['careerwill_batch_map'] = {str(b['id']): b['batchName'] for b in live_classes}
        context.user_data['waiting_for_cw_batch'] = True
        await msg.edit_text("📩 Send the Batch ID from the file to start downloading:")

    except Exception as e:
        logging.exception("Careerwill error")
        await send_log(context.bot, f"❌ /cw error: {str(e)} by {user.id}")
        await msg.edit_text(f"❌ Error: {str(e)} 😵‍💫")

async def handle_cw_batch_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return
    if not context.user_data.get('waiting_for_cw_batch'):
        return

    batch_id = update.message.text.strip()
    if not batch_id.isdigit():
        await update.message.reply_text("❌ Invalid Batch ID. Please send a numeric ID from the list.")
        return

    batch_name = context.user_data.get('careerwill_batch_map', {}).get(batch_id, "Batch")
    processing_msg = await update.message.reply_text(f"⏳ Extracting content from <b>{batch_name}</b>... 🎬", parse_mode=ParseMode.HTML)

    try:
        topics_result = await fetch_cw_batch_topics(batch_id)
        if topics_result:
            topics, batch_name_api = topics_result
            if topics:
                async with aiohttp.ClientSession(headers=CW_HEADERS) as session:
                    txt_content = f"{BANNER_LINE}\n👤 Extracted By: {user.first_name}\n📛 BATCH: {batch_name_api.upper()}\n🆔 ID: {batch_id}\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    total_videos, total_pdfs = 0, 0
                    for topic in topics:
                        text, v_c, p_c = await extract_topic_content(session, batch_id, topic, 0)
                        txt_content += text
                        total_videos += v_c
                        total_pdfs += p_c
                    if total_videos == 0 and total_pdfs == 0:
                        await processing_msg.edit_text("⚠️ No content found via API. Trying web fallback...")
                        await extract_cw_web(update, context, batch_id, batch_name, processing_msg)
                        return
                    filename = f"CW_{sanitize_filename(batch_name_api)}.txt"
                    file_buffer = io.BytesIO(txt_content.encode('utf-8'))
                    caption = (
                        f"✨ <b>{STYLISH_NAME}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"✅ <b>Extraction Complete!</b>\n"
                        f"📛 <b>Batch:</b> {batch_name_api}\n"
                        f"🎥 <b>Videos:</b> {total_videos}\n"
                        f"📄 <b>PDFs:</b> {total_pdfs}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>By:</b> {user.first_name}"
                    )
                    await update.message.reply_document(document=file_buffer, filename=filename, caption=caption, parse_mode=ParseMode.HTML)
                    await processing_msg.delete()
                    context.user_data.pop('waiting_for_cw_batch', None)
                    await send_log(context.bot, f"✅ CW extraction complete (API) for batch {batch_name_api} by {user.first_name} - Videos: {total_videos}, PDFs: {total_pdfs}")
                    return

        await extract_cw_web(update, context, batch_id, batch_name, processing_msg)

    except Exception as e:
        logging.exception("CW batch error")
        await send_log(context.bot, f"❌ CW batch error: {str(e)} by {user.id}")
        await processing_msg.edit_text(f"❌ Error: {str(e)} 😵‍💫")
    finally:
        context.user_data.pop('waiting_for_cw_batch', None)

async def extract_cw_web(update: Update, context: ContextTypes.DEFAULT_TYPE, batch_id, batch_name, processing_msg):
    try:
        build_id = await get_careerwill_build_id()
        if not build_id:
            build_id = "WuAwSZiJu-Vol1R998vWW"
        CAREERWILL_BASE = f"https://web.careerwill.com/_next/data/{build_id}"
        CAREERWILL_BATCH_DETAIL = f"{CAREERWILL_BASE}/live-classes/{{}}.json?interface_id=1"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://web.careerwill.com/live-classes?view=List&interface_id=1",
            "Accept": "application/json",
        }
        if CAREERWILL_COOKIE:
            headers["Cookie"] = CAREERWILL_COOKIE

        async with aiohttp.ClientSession(headers=headers) as session:
            url = CAREERWILL_BATCH_DETAIL.format(batch_id)
            data = await fetch_with_retry(session, url)
            if data is None:
                await send_log(context.bot, f"❌ CW web fallback failed for batch {batch_id} by {update.effective_user.id}")
                await processing_msg.edit_text("❌ Failed to fetch batch details. 🫠")
                return

            batch_class_data = data.get("pageProps", {}).get("batchClassData", {})
            classes = batch_class_data.get("classes", [])
            if not classes:
                await send_log(context.bot, f"⚠️ CW web no videos for batch {batch_id} by {update.effective_user.id}")
                await processing_msg.edit_text("⚠️ No videos found in this batch. 🥲")
                return

            output = [
                f"{BANNER_LINE}",
                f"👤 <b>Extracted By:</b> {update.effective_user.first_name}",
                f"📛 <b>Batch:</b> {batch_name} (ID: {batch_id})",
                f"📅 <b>Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
            ]
            for cls in classes:
                lesson_name = cls.get('lessonName', 'Untitled')
                lesson_url = cls.get('lessonUrl', '')
                if lesson_url:
                    if not lesson_url.startswith('http'):
                        video_url = f"https://www.youtube.com/watch?v={lesson_url}"
                    else:
                        video_url = lesson_url
                    output.append(f"🎥 {lesson_name} : {video_url}")

            filename = f"Careerwill_{sanitize_filename(batch_name)}.txt"
            file_buffer = io.BytesIO("\n".join(output).encode('utf-8'))
            caption = (
                f"✨ <b>{STYLISH_NAME}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Extraction Complete (Web Fallback)</b>\n"
                f"📛 <b>Batch:</b> {batch_name}\n"
                f"🎥 <b>Videos:</b> {len(classes)}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>By:</b> {update.effective_user.first_name}"
            )
            await update.message.reply_document(document=file_buffer, filename=filename, caption=caption, parse_mode=ParseMode.HTML)
            await processing_msg.delete()
            await send_log(context.bot, f"✅ CW web fallback complete for batch {batch_name} by {update.effective_user.first_name} - Videos: {len(classes)}")

    except Exception as e:
        logging.exception("CW web fallback error")
        await send_log(context.bot, f"❌ CW web fallback error: {str(e)} by {update.effective_user.id}")
        await processing_msg.edit_text(f"❌ Error: {str(e)} 😵‍💫")

async def kgs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await send_log(context.bot, f"⛔ Unauthorized /kgs attempt by {user.id}")
        await update.message.reply_text(f"🚫 <b>Access Denied!</b>\n\nApki User ID <code>{user.id}</code> 🤡", parse_mode=ParseMode.HTML)
        return

    await send_log(context.bot, f"📂 /kgs started by {user.first_name} (@{user.username})")
    msg = await update.message.reply_text("⏳ <b>Fetching KGS Courses...</b> 📚", parse_mode=ParseMode.HTML)

    url = f"{KGS_API_BASE}{KGS_COURSES_ENDPOINT}"
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        data = await fetch_with_retry(session, url)
        if data is None:
            await send_log(context.bot, f"❌ /kgs API error for {user.id}")
            await msg.edit_text(f"❌ Failed to fetch courses from KGS.\nURL: <code>{url}</code>", parse_mode=ParseMode.HTML)
            return

    courses = None
    if isinstance(data, list):
        courses = data
    elif isinstance(data, dict):
        for key in ["data", "courses", "items", "results", "courseList", "list"]:
            if key in data and isinstance(data[key], list):
                courses = data[key]
                break
        if courses is None:
            for v in data.values():
                if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                    if "id" in v[0] or "title" in v[0]:
                        courses = v
                        break

    if not courses:
        await send_log(context.bot, f"⚠️ /kgs no courses for {user.id}")
        await msg.edit_text(f"⚠️ No courses found.\nRaw: <code>{str(data)[:300]}</code>", parse_mode=ParseMode.HTML)
        return

    course_map = {}
    for c in courses:
        cid = c.get('id') or c.get('_id') or c.get('courseId')
        if not cid:
            continue
        title = c.get('title') or c.get('name') or c.get('courseName') or f"Course {cid}"
        course_map[str(cid)] = title

    if not course_map:
        await send_log(context.bot, f"⚠️ /kgs no valid course IDs for {user.id}")
        await msg.edit_text(f"⚠️ No valid course IDs found.", parse_mode=ParseMode.HTML)
        return

    context.user_data['kgs_courses'] = courses
    context.user_data['kgs_course_map'] = course_map
    context.user_data['kgs_api_base'] = KGS_API_BASE
    context.user_data['kgs_subjects_endpoint'] = KGS_SUBJECTS_ENDPOINT
    context.user_data['kgs_lessons_endpoint'] = KGS_LESSONS_ENDPOINT
    context.user_data['kgs_page'] = 0

    await show_kgs_page(update, context, page=0, edit=False)

async def show_kgs_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, edit=False):
    course_map = context.user_data.get('kgs_course_map', {})
    total = len(course_map)
    per_page = 10
    total_pages = (total + per_page - 1) // per_page
    if page < 0: page = 0
    if page >= total_pages: page = total_pages - 1
    start = page * per_page
    end = min(start + per_page, total)
    items = list(course_map.items())[start:end]

    header = f"📚 <b>KGS Courses</b> 📂\n━━━━━━━━━━━━━━━━━━━━━━\nPage {page+1}/{total_pages} – {total} courses\nSelect a course to extract content.\n━━━━━━━━━━━━━━━━━━━━━━\n"
    kb = []
    for cid, title in items:
        kb.append([InlineKeyboardButton(f"📁 {title[:40]}", callback_data=f"kgs_sel_{cid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"kgs_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"kgs_page_{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_extract")])
    reply_markup = InlineKeyboardMarkup(kb)

    context.user_data['kgs_page'] = page

    if edit:
        await update.callback_query.edit_message_text(header, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(header, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def handle_kgs_course(update: Update, context: ContextTypes.DEFAULT_TYPE, course_id: str):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    course_name = context.user_data.get('kgs_course_map', {}).get(course_id, "Course")
    kgs_api_base = context.user_data.get('kgs_api_base', KGS_API_BASE)
    subjects_endpoint = context.user_data.get('kgs_subjects_endpoint', KGS_SUBJECTS_ENDPOINT)
    lessons_endpoint = context.user_data.get('kgs_lessons_endpoint', KGS_LESSONS_ENDPOINT)

    processing_msg = await query.edit_message_text(f"⏳ Extracting content from <b>{course_name}</b>... ⚡", parse_mode=ParseMode.HTML)

    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            subjects_url = f"{kgs_api_base}{subjects_endpoint.format(course_id)}"
            subjects_data = await fetch_with_retry(session, subjects_url)
            if subjects_data is None:
                await send_log(context.bot, f"❌ KGS subjects fetch failed for {course_name} by {user.id}")
                await processing_msg.edit_text(f"❌ Failed to fetch subjects.\nURL: <code>{subjects_url}</code>", parse_mode=ParseMode.HTML)
                return

            subjects = None
            if isinstance(subjects_data, list):
                subjects = subjects_data
            elif isinstance(subjects_data, dict):
                for key in ["data", "subjects", "items", "results"]:
                    if key in subjects_data and isinstance(subjects_data[key], list):
                        subjects = subjects_data[key]
                        break
                if subjects is None:
                    for v in subjects_data.values():
                        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                            if "id" in v[0] or "name" in v[0] or "subjectName" in v[0]:
                                subjects = v
                                break

            if not subjects:
                await send_log(context.bot, f"⚠️ KGS no subjects for {course_name} by {user.id}")
                await processing_msg.edit_text(f"⚠️ No subjects found.\nRaw: <code>{str(subjects_data)[:300]}</code>", parse_mode=ParseMode.HTML)
                return

            output = [
                f"{BANNER_LINE}",
                f"👤 <b>Extracted By:</b> {user.first_name} (@{user.username or 'N/A'})",
                f"📛 <b>Course:</b> {course_name} (ID: {course_id})",
                f"📅 <b>Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            total_videos = 0
            total_pdfs = 0

            for subject in subjects:
                if not isinstance(subject, dict):
                    continue
                subject_id = subject.get('id') or subject.get('_id') or subject.get('subjectId')
                subject_name = subject.get('name') or subject.get('subjectName') or subject.get('title') or 'Unnamed Subject'

                if not subject_id:
                    continue

                lessons_url = f"{kgs_api_base}{lessons_endpoint.format(subject_id)}"
                lessons_data = await fetch_with_retry(session, lessons_url)
                if lessons_data is None:
                    logging.warning(f"Failed to fetch lessons for subject {subject_id}")
                    continue

                extracted = extract_links_from_item(lessons_data)

                if not extracted:
                    continue

                for link, title in extracted:
                    is_pdf = (
                        ".pdf" in link.lower() or
                        "drive.google.com" in link.lower() or
                        "docs.google.com" in link.lower() or
                        "/file/d/" in link.lower()
                    )
                    if is_pdf:
                        output.append(f"📄 [{subject_name}] {title} : {link}")
                        total_pdfs += 1
                    else:
                        output.append(f"🎥 [{subject_name}] {title} : {link}")
                        total_videos += 1

            if total_videos == 0 and total_pdfs == 0:
                await send_log(context.bot, f"⚠️ KGS no links for {course_name} by {user.id}")
                await processing_msg.edit_text("⚠️ No valid links found in the course content.", parse_mode=ParseMode.HTML)
                return

            filename = f"KGS_{sanitize_filename(course_name)}.txt"
            file_buffer = io.BytesIO("\n".join(output).encode('utf-8'))
            file_buffer.name = filename

            caption = (
                f"✨ <b>{STYLISH_NAME}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Extraction Complete!</b>\n"
                f"📛 <b>Course:</b> {course_name}\n"
                f"🎥 <b>Videos:</b> {total_videos}\n"
                f"📄 <b>PDFs:</b> {total_pdfs}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>By:</b> {user.first_name}"
            )

            await query.message.reply_document(document=file_buffer, filename=filename, caption=caption, parse_mode=ParseMode.HTML)
            await processing_msg.delete()
            await send_log(context.bot, f"✅ KGS extraction complete for {course_name} by {user.first_name} - Videos: {total_videos}, PDFs: {total_pdfs}")

    except Exception as e:
        logging.exception("KGS course error")
        await send_log(context.bot, f"❌ KGS error: {str(e)} by {user.id}")
        await processing_msg.edit_text(f"❌ Error: {str(e)} 😵‍💫")

async def tw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await send_log(context.bot, f"⛔ Unauthorized /tw attempt by {user.id}")
        await update.message.reply_text(f"🚫 <b>Access Denied!</b>\n\nApki User ID <code>{user.id}</code> 🤡", parse_mode=ParseMode.HTML)
        return

    await send_log(context.bot, f"📂 /tw started by {user.first_name} (@{user.username})")
    msg = await update.message.reply_text("⏳ <b>Fetching Toppers Wisdom Courses...</b> 📚", parse_mode=ParseMode.HTML)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = f"{TW_API_BASE}{TW_COURSES_ENDPOINT}"
        data = await fetch_with_retry(session, url)
        if data is None:
            await send_log(context.bot, f"❌ /tw API error for {user.id}")
            await msg.edit_text(f"❌ Failed to fetch courses.\nURL: <code>{url}</code>", parse_mode=ParseMode.HTML)
            return

    courses = None
    if isinstance(data, list):
        courses = data
    elif isinstance(data, dict):
        for key in ["data", "courses", "items", "results", "courseList"]:
            if key in data and isinstance(data[key], list):
                courses = data[key]
                break
        if courses is None:
            for v in data.values():
                if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                    if "id" in v[0] or "title" in v[0]:
                        courses = v
                        break

    if not courses:
        await send_log(context.bot, f"⚠️ /tw no courses for {user.id}")
        await msg.edit_text(f"⚠️ No courses found.\nRaw: <code>{str(data)[:300]}</code>", parse_mode=ParseMode.HTML)
        return

    course_map = {}
    for c in courses:
        cid = c.get('id') or c.get('_id') or c.get('courseId')
        if not cid:
            continue
        title = c.get('title') or c.get('name') or c.get('courseName') or f"Course {cid}"
        course_map[str(cid)] = title

    if not course_map:
        await send_log(context.bot, f"⚠️ /tw no valid course IDs for {user.id}")
        await msg.edit_text(f"⚠️ No valid course IDs found.", parse_mode=ParseMode.HTML)
        return

    context.user_data['tw_courses'] = courses
    context.user_data['tw_course_map'] = course_map

    header = f"📚 <b>Toppers Wisdom Courses</b> 📂\n━━━━━━━━━━━━━━━━━━━━━━\nTotal: {len(course_map)}\nSelect a course to extract content.\n━━━━━━━━━━━━━━━━━━━━━━\n"
    kb = []
    for cid, title in list(course_map.items())[:30]:
        kb.append([InlineKeyboardButton(f"📁 {title[:40]}", callback_data=f"tw_sel_{cid}")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_extract")])

    await msg.edit_text(header, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def handle_tw_course(update: Update, context: ContextTypes.DEFAULT_TYPE, course_id: str):
    query = update.callback_query
    user = query.from_user
    await query.answer()

    course_name = context.user_data.get('tw_course_map', {}).get(course_id, "Course")
    processing_msg = await query.edit_message_text(f"⏳ Extracting content from <b>{course_name}</b>... ⚡", parse_mode=ParseMode.HTML)

    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            topics_url = f"{TW_API_BASE}{TW_TOPICS_ENDPOINT.format(course_id=course_id)}"
            topics_data = await fetch_with_retry(session, topics_url)
            if topics_data is None:
                await send_log(context.bot, f"❌ TW topics fetch failed for {course_name} by {user.id}")
                await processing_msg.edit_text(f"❌ Failed to fetch topics.\nURL: <code>{topics_url}</code>", parse_mode=ParseMode.HTML)
                return

            topics = None
            if isinstance(topics_data, dict):
                if 'data' in topics_data and isinstance(topics_data['data'], dict):
                    topics = topics_data['data'].get('topics')
                elif 'topics' in topics_data:
                    topics = topics_data['topics']
                elif 'data' in topics_data and isinstance(topics_data['data'], list):
                    topics = topics_data['data']
                else:
                    for v in topics_data.values():
                        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                            if 'topicName' in v[0] or 'name' in v[0]:
                                topics = v
                                break
            elif isinstance(topics_data, list):
                topics = topics_data

            if not topics:
                await send_log(context.bot, f"⚠️ TW no topics for {course_name} by {user.id}")
                await processing_msg.edit_text(f"⚠️ No topics found.\nRaw: <code>{str(topics_data)[:300]}</code>", parse_mode=ParseMode.HTML)
                return

            output = [
                f"{BANNER_LINE}",
                f"👤 <b>Extracted By:</b> {user.first_name} (@{user.username or 'N/A'})",
                f"📛 <b>Course:</b> {course_name} (ID: {course_id})",
                f"📅 <b>Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            total_videos = 0
            total_pdfs = 0

            for topic in topics:
                if not isinstance(topic, dict):
                    continue
                topic_id = topic.get('id') or topic.get('_id') or topic.get('topicId')
                topic_name = topic.get('topicName') or topic.get('name') or topic.get('title') or 'Unnamed Topic'

                if not topic_id:
                    continue

                classes_url = f"{TW_API_BASE}{TW_CLASSES_ENDPOINT.format(topic_id=topic_id, course_id=course_id)}"
                classes_data = await fetch_with_retry(session, classes_url)
                if classes_data is None:
                    logging.warning(f"Failed to fetch classes for topic {topic_id}")
                    continue

                extracted = extract_links_from_item(classes_data)

                sections = topic.get('sections', [])
                if sections:
                    for section in sections:
                        if isinstance(section, dict):
                            extracted.extend(extract_links_from_item(section))

                if not extracted:
                    continue

                for link, title in extracted:
                    is_pdf = (
                        ".pdf" in link.lower() or
                        "drive.google.com" in link.lower() or
                        "docs.google.com" in link.lower() or
                        "/file/d/" in link.lower()
                    )
                    if is_pdf:
                        output.append(f"📄 [{topic_name}] {title} : {link}")
                        total_pdfs += 1
                    else:
                        output.append(f"🎥 [{topic_name}] {title} : {link}")
                        total_videos += 1

            if total_videos == 0 and total_pdfs == 0:
                await send_log(context.bot, f"⚠️ TW no links for {course_name} by {user.id}")
                await processing_msg.edit_text("⚠️ No valid links found in the course content.", parse_mode=ParseMode.HTML)
                return

            filename = f"ToppersWisdom_{sanitize_filename(course_name)}.txt"
            file_buffer = io.BytesIO("\n".join(output).encode('utf-8'))
            file_buffer.name = filename

            caption = (
                f"✨ <b>{STYLISH_NAME}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Extraction Complete!</b>\n"
                f"📛 <b>Course:</b> {course_name}\n"
                f"🎥 <b>Videos:</b> {total_videos}\n"
                f"📄 <b>PDFs:</b> {total_pdfs}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>By:</b> {user.first_name}"
            )

            await query.message.reply_document(document=file_buffer, filename=filename, caption=caption, parse_mode=ParseMode.HTML)
            await processing_msg.delete()
            await send_log(context.bot, f"✅ TW extraction complete for {course_name} by {user.first_name} - Videos: {total_videos}, PDFs: {total_pdfs}")

    except Exception as e:
        logging.exception("Toppers Wisdom course error")
        await send_log(context.bot, f"❌ TW error: {str(e)} by {user.id}")
        await processing_msg.edit_text(f"❌ Error: {str(e)} 😵‍💫")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    if not is_authorized(user.id):
        await query.answer("🚫 Access Denied! 🤡", show_alert=True)
        return
    await query.answer()
    data = query.data

    if data.startswith("toggle_") or data in ["select_all", "deselect_all", "split_done", "ignore"] or data.startswith("page_"):
        await split_callback_handler(update, context)
        return

    if data.startswith("kgs_sel_"):
        course_id = data.split("_")[2]
        await handle_kgs_course(update, context, course_id)
        return
    if data.startswith("kgs_page_"):
        page = int(data.split("_")[2])
        await show_kgs_page(update, context, page, edit=True)
        return

    if data.startswith("extract_page_"):
        page = int(data.split("_")[2])
        await show_extract_page(update, context, page, edit=True)
        return

    if data.startswith("sel_"):
        b_id = data.split("_")[1]
        b_name = context.user_data.get('batch_map', {}).get(b_id, "Batch")
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                batch_data = await fetch_with_retry(session, f"{API_BASE}/courses/{b_id}/classes?populate=full")
                if batch_data is None:
                    await query.edit_message_text("❌ Error loading batch details. 🫠")
                    return
                topics = batch_data.get("data", {}).get("classes", [])
                v_count = 0
                p_count = 0
                for t in topics:
                    for cls in t.get("classes", []):
                        if cls.get('class_link'):
                            v_count += 1
                        if cls.get("classPdf"):
                            p_count += len(cls.get("classPdf", []))
                context.user_data.update({
                    'curr_topics': topics,
                    'curr_batch_name': b_name,
                    'curr_batch_id': b_id,
                    'curr_v_count': v_count,
                    'curr_p_count': p_count
                })
                summary = (
                    f"✅ <b>Batch Selected</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📛 <b>Name:</b> {b_name}\n"
                    f"📂 <b>Folders:</b> {len(topics)}\n"
                    f"📹 <b>Videos:</b> {v_count}\n"
                    f"📑 <b>PDFs:</b> {p_count}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Click the button below to extract all content as a TXT file."
                )
                kb = [
                    [InlineKeyboardButton("📥 Extract Full TXT", callback_data="act_full")],
                    [InlineKeyboardButton("🔙 Back to Batches", callback_data="back_to_batches")]
                ]
                await query.edit_message_text(summary, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.exception("Callback selection error")
            await send_log(context.bot, f"❌ Batch selection error: {str(e)} by {user.id}")
            await query.edit_message_text("❌ Error loading batch. 😵‍💫")

    elif data == "act_full":
        topics = context.user_data.get('curr_topics', [])
        b_name = context.user_data.get('curr_batch_name', 'Batch')
        b_id = context.user_data.get('curr_batch_id', '')
        v_count = context.user_data.get('curr_v_count', 0)
        p_count = context.user_data.get('curr_p_count', 0)
        processing_msg = await query.edit_message_text(
            f"⏳ <b>Extracting content from</b> <code>{b_name}</code>...\nPlease wait. ⚡",
            parse_mode=ParseMode.HTML
        )
        output = [
            f"{BANNER_LINE}",
            f"👤 <b>Extracted By:</b> {user.first_name} (@{user.username or 'N/A'})",
            f"📛 <b>Batch:</b> {b_name} (ID: {b_id})",
            f"📅 <b>Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        for t in topics:
            topic_name = t.get('topicName', 'General')
            for cls in t.get("classes", []):
                title = cls.get('title', 'Untitled').strip()
                v_link = cls.get('class_link')
                if v_link:
                    output.append(f"🎥 [{topic_name}] {STYLISH_NAME} {title} (VIDEO) : {v_link}")
                for pdf in cls.get("classPdf", []):
                    p_url = pdf.get("url") if isinstance(pdf, dict) else str(pdf)
                    if p_url:
                        output.append(f"📄 [{topic_name}] {STYLISH_NAME} {title} (PDF) : {p_url}")
        filename = f"🍁{sanitize_filename(b_name)}.txt"
        file_buffer = io.BytesIO("\n".join(output).encode('utf-8'))
        file_buffer.name = filename
        caption = (
            f"✨ <b>{STYLISH_NAME}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Extraction Complete!</b>\n"
            f"📛 <b>Batch:</b> {b_name}\n"
            f"📹 <b>Videos:</b> {v_count}\n"
            f"📑 <b>PDFs:</b> {p_count}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>By:</b> {user.first_name}"
        )
        await query.message.reply_document(document=file_buffer, filename=filename, caption=caption, parse_mode=ParseMode.HTML)
        await processing_msg.delete()
        await send_log(context.bot, f"✅ Batch extraction complete for {b_name} by {user.first_name} - Videos: {v_count}, PDFs: {p_count}")

    elif data == "back_to_batches":
        page = context.user_data.get('extract_page', 0)
        await show_extract_page(update, context, page, edit=True)

    elif data.startswith("tw_sel_"):
        course_id = data.split("_")[2]
        await handle_tw_course(update, context, course_id)

    elif data == "cancel_extract":
        await query.edit_message_text("❌ Action cancelled. 🙅‍♂️")

# ================= FILE HANDLER =================
async def handle_split_or_dedup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('split_mode', False):
        await handle_split_txt(update, context, update.message.document)
    elif context.user_data.get('dedup_mode', False):
        await handle_dedup_txt(update, context, update.message.document)
    else:
        await update.message.reply_text("Please use /split or /dedup command first, then send the TXT file.")

# ================= BOT RUNNER =================

def run_single_bot(token):
    try:
        app = ApplicationBuilder().token(token).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("extract", extract))
        app.add_handler(CommandHandler("cw", cw_command))
        app.add_handler(CommandHandler("careerwill", cw_command))
        app.add_handler(CommandHandler("kgs", kgs_command))
        app.add_handler(CommandHandler("tw", tw_command))
        app.add_handler(CommandHandler("topperswisdom", tw_command))
        app.add_handler(CommandHandler("split", split_cmd))
        app.add_handler(CommandHandler("dedup", dedup_cmd))
        app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_split_or_dedup))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d+$'), handle_cw_batch_id))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_any_text))
        app.add_handler(CallbackQueryHandler(handle_callback))
        logging.info(f"✅ Bot started: {token[:10]}... 🚀")
        app.run_polling(drop_pending_updates=True)
    except Exception as e:
        logging.exception(f"Bot failure with token {token[:10]}")


# ================= HEALTH SERVER (for Render Web Service) =================
async def health_server():
    try:
        from aiohttp import web
        async def health(request):
            return web.Response(text="Bot is running ✅")
        web_app = web.Application()
        web_app.router.add_get("/", health)
        web_app.router.add_get("/health", health)
        runner = web.AppRunner(web_app)
        await runner.setup()
        port = int(os.getenv("PORT", 8080))
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logging.info(f"🌐 Health server started on port {port}")
        await asyncio.Event().wait()
    except Exception as e:
        logging.error(f"Health server error: {e}")


def _run_health_thread():
    try:
        asyncio.run(health_server())
    except Exception as e:
        logging.error(f"Health thread died: {e}")


if __name__ == '__main__':
    # Start health server (needed for Render Web Service free tier)
    if os.getenv("PORT"):
        threading.Thread(target=_run_health_thread, daemon=True).start()

    # Start each bot in its own thread
    threads = []
    for bot_token in BOT_TOKENS:
        t = threading.Thread(target=run_single_bot, args=(bot_token.strip(),), daemon=True)
        t.start()
        threads.append(t)
        logging.info(f"🧵 Started thread for bot: {bot_token[:10]}...")

    # Keep main thread alive
    for t in threads:
        t.join()
