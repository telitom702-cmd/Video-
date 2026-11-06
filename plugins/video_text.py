# ভিডিওর ভেতরে লেখা (ক্যাপশন) বসানোর সিস্টেম
#
# ফ্লো:
#   ১. ইউজার ভিডিও পাঠায়
#   ২. বট লেখা চায়            [❌ Cancel]
#   ৩. ইউজার লেখা পাঠায়
#   ৪. বট লেখা দেখায়           [✅ Save] [❌ Cancel]
#   ৫. Save   -> লেখা ভিডিওর পর্দায় বসে (পুরো ভিডিও জুড়ে একই লেখা)
#      Cancel -> লেখা ছাড়া ভিডিও যায়
#   দুই ক্ষেত্রেই /settings অনুযায়ী ভিডিও বা ডকুমেন্ট হিসেবে যায়।

import logging
logger = logging.getLogger(__name__)

import os
import json
import time
import random
import shutil
import asyncio

from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from plugins.config import Config
from plugins.database.add import AddUser
from plugins.database.database import db
from plugins.functions.forcesub import handle_force_subscribe
from plugins.functions.help_Nekmo_ffmpeg import take_screen_shot
from plugins.functions.display_progress import progress_for_pyrogram

FONT_NAME = "Noto Sans Bengali"   # Dockerfile-এ fonts-noto-core ইনস্টল করা হয়েছে
MAX_TEXT_LEN = 300                # লেখার সর্বোচ্চ অক্ষর
PENDING_TTL = 15 * 60             # ১৫ মিনিটের মধ্যে লেখা না দিলে বাতিল

PENDING = {}   # (user_id, video_msg_id) -> dict
LATEST = {}    # user_id -> video_msg_id (সর্বশেষ ভিডিও, লেখা এটাতেই বসবে)

DEFAULT_VIDEO_CAPTION = "**এখানে আপনার ভিডিও 🎬**"


# ====================================================================
# ASS সাবটাইটেল তৈরি (পুরো ভিডিও জুড়ে একই লেখা)
# ====================================================================
def _ass_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape_ass(text):
    # ব্যাকস্ল্যাশের পরে অদৃশ্য অক্ষর বসে, যাতে \N \h ইত্যাদি কমান্ড না হয়ে যায়
    text = text.replace("\\", "\\\u200b")
    text = text.replace("{", "(").replace("}", ")")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\\N".join(lines)


def build_ass(text, width, height, duration):
    font_size = max(18, int(min(width, height) / 16))
    margin_v = max(20, int(height * 0.06))
    margin_h = max(20, int(width * 0.05))
    end = _ass_time(max(duration, 1) + 5)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{FONT_NAME},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,3,1,2,{margin_h},{margin_h},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,{_ass_time(0)},{end},Default,,0,0,0,,{_escape_ass(text)}\n"
    )


# ====================================================================
# ভিডিওর তথ্য
# ====================================================================
def get_meta(path):
    """hachoir থেকে (width, height, duration সেকেন্ডে)"""
    width = height = duration = 0
    parser = createParser(path)
    metadata = extractMetadata(parser) if parser else None
    if metadata is not None:
        if metadata.has("duration"):
            duration = metadata.get("duration").seconds
        if metadata.has("width"):
            width = metadata.get("width")
        if metadata.has("height"):
            height = metadata.get("height")
    return width, height, duration


async def probe_size(path):
    """ffprobe থেকে পর্দায় আসলে যে সাইজে দেখাবে (রোটেশন ধরে)। ব্যর্থ হলে (0, 0)"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:stream_side_data=rotation:stream_tags=rotate",
            "-of", "json", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        stream = json.loads(out.decode())["streams"][0]
        w, h = int(stream["width"]), int(stream["height"])
        rot = 0
        for sd in stream.get("side_data_list", []):
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        if not rot:
            rot = int(stream.get("tags", {}).get("rotate", 0) or 0)
        if abs(rot) % 180 == 90:
            w, h = h, w
        return w, h
    except Exception:
        return 0, 0


# ====================================================================
# ভিডিওতে লেখা বসানো
# ====================================================================
async def burn_text(input_file, text, work_dir):
    """সফল হলে (নতুন_ফাইল, None), ব্যর্থ হলে (None, এরর মেসেজ)"""
    width, height = await probe_size(input_file)
    _, h2, duration = get_meta(input_file)
    if not width or not height:
        width, height, _ = get_meta(input_file)
    if not width or not height:
        return None, "ভিডিওর সাইজ পড়তে পারিনি।"

    ass_name = "sub.ass"
    out_name = "output.mp4"
    with open(os.path.join(work_dir, ass_name), "w", encoding="utf-8") as f:
        f.write(build_ass(text, width, height, duration))

    cmd = [
        "ffmpeg", "-y", "-i", os.path.abspath(input_file),
        "-vf", f"ass={ass_name}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_name,
    ]
    # cwd = work_dir, তাই ফিল্টারে পাথের ঝামেলা হয় না
    process = await asyncio.create_subprocess_exec(
        *cmd, cwd=work_dir,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=Config.PROCESS_MAX_TIMEOUT
        )
    except asyncio.TimeoutError:
        process.kill()
        return None, "লেখা বসাতে অনেক সময় লেগে গেছে।"

    out_path = os.path.join(work_dir, out_name)
    if process.returncode != 0 or not os.path.exists(out_path):
        logger.error(stderr.decode(errors="ignore")[-1500:])
        return None, "ffmpeg দিয়ে লেখা বসানো যায়নি।"
    return out_path, None


# ====================================================================
# ডাউনলোড -> (লেখা বসানো) -> সেটিং অনুযায়ী পাঠানো
# ====================================================================
async def process_and_send(bot, src_msg, status, text):
    user_id = src_msg.from_user.id
    media = src_msg.video or src_msg.document
    orig_name = getattr(media, "file_name", None) or f"{src_msg.id}.mp4"

    work_dir = os.path.join(Config.DOWNLOAD_LOCATION, f"{user_id}_{src_msg.id}_vt")
    os.makedirs(work_dir, exist_ok=True)

    try:
        c_time = time.time()
        file = await bot.download_media(
            message=src_msg,
            file_name=os.path.join(work_dir, "input_" + os.path.basename(orig_name)),
            progress=progress_for_pyrogram,
            progress_args=("**ডাউনলোড হচ্ছে... ⏳**", status, c_time),
        )

        send_name = orig_name
        if text:
            await status.edit_text("**✍️ ভিডিওতে লেখা বসানো হচ্ছে...\nভিডিও বড় হলে সময় লাগবে ⏳**")
            out_path, err = await burn_text(file, text, work_dir)
            if err:
                await status.edit_text(f"**❌ {err}**")
                return
            file = out_path
            send_name = os.path.splitext(orig_name)[0] + ".mp4"

        w, h, duration = get_meta(file)
        pw, ph = await probe_size(file)
        if pw and ph:
            w, h = pw, ph

        # থাম্বনেইল: ইউজারের কাস্টম থাকলে সেটা, না থাকলে ভিডিও থেকে স্ক্রিনশট
        custom_thumb = f"{Config.DOWNLOAD_LOCATION}/{user_id}.jpg"
        thumb = None
        if (await db.get_thumbnail(user_id)) is not None and os.path.exists(custom_thumb):
            thumb = custom_thumb
        elif duration > 1:
            thumb = await take_screen_shot(file, work_dir, random.randint(0, duration - 1))

        caption = src_msg.caption if src_msg.caption else DEFAULT_VIDEO_CAPTION
        caption = caption[:1024]

        await status.edit_text("**✅ প্রসেস সম্পন্ন\nপাঠানো হচ্ছে... 🚀**")
        u_time = time.time()
        progress_args = ("**আপলোড হচ্ছে... 🚀**", status, u_time)

        # সেটিং: upload_as_doc = True মানে DOCUMENT, False মানে VIDEO
        if await db.get_upload_as_doc(user_id):
            await bot.send_document(
                chat_id=src_msg.chat.id, document=file, thumb=thumb,
                caption=caption, file_name=send_name,
                progress=progress_for_pyrogram, progress_args=progress_args,
            )
        else:
            await bot.send_video(
                chat_id=src_msg.chat.id, video=file, duration=duration,
                width=w, height=h, supports_streaming=True, thumb=thumb,
                caption=caption, file_name=send_name,
                progress=progress_for_pyrogram, progress_args=progress_args,
            )
        await status.delete()

    except Exception as e:
        logger.error(f"video_text error: {e}")
        try:
            await status.edit_text(f"**এরর হয়েছে ❌\nকারণ: {e}**")
        except Exception:
            pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ====================================================================
# ফিল্টার
# ====================================================================
def _is_video_doc(_, __, m):
    return bool(m.document and (m.document.mime_type or "").startswith("video/"))


def _latest_pending(user_id):
    vid_id = LATEST.get(user_id)
    if vid_id is None:
        return None
    data = PENDING.get((user_id, vid_id))
    if data is None or time.time() - data["ts"] > PENDING_TTL:
        PENDING.pop((user_id, vid_id), None)
        LATEST.pop(user_id, None)
        return None
    return data


def _waiting_for_text(_, __, m):
    if not m.from_user or not m.text or m.text.startswith("/"):
        return False
    data = _latest_pending(m.from_user.id)
    if data is None:
        return False
    # অন্য মেসেজের রিপ্লাই হলে (পুরনো /caption ফিচার) ধরব না
    if m.reply_to_message and m.reply_to_message.id != data["prompt"].id:
        return False
    return True


video_doc_filter = filters.create(_is_video_doc)
waiting_filter = filters.create(_waiting_for_text)


def _cancel_markup(vid_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"vt_cancel:{vid_id}")]])


def _confirm_markup(vid_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Save", callback_data=f"vt_save:{vid_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"vt_cancel:{vid_id}"),
    ]])


# ====================================================================
# ১. ভিডিও এলো -> লেখা চাওয়া
# ====================================================================
@Client.on_message(
    filters.private & (filters.video | video_doc_filter),
    group=-1
)
async def video_received(bot, update):
    await AddUser(bot, update)
    if Config.UPDATES_CHANNEL:
        fsub = await handle_force_subscribe(bot, update)
        if fsub == 400:
            raise StopPropagation

    user_id = update.from_user.id
    prompt = await update.reply_text(
        "ভিডিওতে যে লেখা দেখাতে চান সেটা পাঠান ✍️\n\n"
        "লেখা ছাড়া পাঠাতে চাইলে Cancel চাপুন।",
        reply_markup=_cancel_markup(update.id),
        parse_mode=enums.ParseMode.DISABLED,
        quote=True,
    )
    PENDING[(user_id, update.id)] = {
        "video": update, "prompt": prompt, "text": None, "ts": time.time(),
    }
    LATEST[user_id] = update.id
    raise StopPropagation


# ====================================================================
# ২. লেখা এলো -> Save / Cancel বাটন
# ====================================================================
@Client.on_message(filters.private & filters.text & waiting_filter, group=-1)
async def text_received(bot, update):
    user_id = update.from_user.id
    data = _latest_pending(user_id)
    if data is None:
        raise StopPropagation

    text = update.text.strip()
    if len(text) > MAX_TEXT_LEN:
        await update.reply_text(
            f"লেখা অনেক বড়। সর্বোচ্চ {MAX_TEXT_LEN} অক্ষর, আপনারটা {len(text)} অক্ষর। ছোট করে আবার পাঠান।",
            parse_mode=enums.ParseMode.DISABLED,
        )
        raise StopPropagation

    data["text"] = text
    data["ts"] = time.time()
    vid_id = data["video"].id
    try:
        await data["prompt"].edit_text(
            f"ভিডিওতে এই লেখা দেখানো হবে:\n\n{text}\n\n"
            "Save করবেন? (বদলাতে চাইলে নতুন লেখা পাঠান)",
            reply_markup=_confirm_markup(vid_id),
            parse_mode=enums.ParseMode.DISABLED,
        )
    except Exception as e:
        logger.warning(f"prompt edit failed: {e}")
    raise StopPropagation


# ====================================================================
# ৩. Save / Cancel
# ====================================================================
@Client.on_callback_query(filters.regex(r"^vt_(save|cancel):\d+$"), group=-1)
async def vt_buttons(bot, cb):
    action, vid_id = cb.data.split(":")
    key = (cb.from_user.id, int(vid_id))
    data = PENDING.pop(key, None)
    if LATEST.get(cb.from_user.id) == int(vid_id):
        LATEST.pop(cb.from_user.id, None)

    if data is None:
        await cb.answer("এই অনুরোধের সময় শেষ। ভিডিওটা আবার পাঠান।", show_alert=True)
        raise StopPropagation

    text = data["text"] if action == "vt_save" else None
    if action == "vt_save" and not text:
        # লেখা ছাড়া Save চাপা সম্ভব নয়, তবু নিরাপত্তার জন্য
        PENDING[key] = data
        LATEST[cb.from_user.id] = int(vid_id)
        await cb.answer("আগে লেখা পাঠান।", show_alert=True)
        raise StopPropagation

    await cb.answer()
    status = cb.message
    try:
        await status.edit_text("**ভিডিও রিসিভ করেছি ✅\nডাউনলোড শুরু হচ্ছে... ⏳**")
    except Exception:
        pass
    await process_and_send(bot, data["video"], status, text)
    raise StopPropagation
