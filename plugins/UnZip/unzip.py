# ©️ LISA-KOREA | @LISA_FAN_LK | NT_BOT_CHANNEL

import os
import time
import shutil
import tempfile
import asyncio
import logging
import random
import re
import uuid

from pyrogram import Client, filters, ContinuePropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import MessageNotModified

from pyunpack import Archive

from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from PIL import Image

from plugins.config import Config
from plugins.database.database import db
from plugins.database.add import AddUser
from plugins.functions.forcesub import handle_force_subscribe
from plugins.functions.display_progress import progress_for_pyrogram
from plugins.functions.help_Nekmo_ffmpeg import take_screen_shot


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# SUPPORTED ARCHIVE FORMATS
# ============================================================

SUPPORTED_FORMATS = (
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".gz",
    ".bz2",
)


# ============================================================
# VIDEO FORMATS
# ============================================================

VIDEO_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".mpeg",
    ".mpg",
    ".3gp",
    ".ts",
    ".m2ts",
)


# ============================================================
# ACTIVE TASKS
# ============================================================

active_tasks = {}


# ============================================================
# MULTI-PART ARCHIVE STORAGE
#
# pending_multipart[user_id][group_id] = {
#     "base": ...,
#     "extension": ...,
#     "parts": {
#         part_number: {
#             "message": message,
#             "filename": filename,
#             "path": path
#         }
#     },
#     "directory": ...,
#     "status_message": ...
# }
# ============================================================

pending_multipart = {}


# ============================================================
# HELPER: SAFE EDIT
# ============================================================

async def safe_edit(message, text):
    try:
        if message and message.text != text:
            await message.edit_text(text)
    except MessageNotModified:
        pass
    except Exception:
        pass


# ============================================================
# HELPER: CAPTION / FILENAME
# ============================================================

def get_caption_filename(message):
    """
    Filename priority:

    1. Telegram document.file_name
    2. First non-empty caption line
    """

    # --------------------------------------------------------
    # DOCUMENT FILENAME
    # --------------------------------------------------------

    try:
        if (
            message.document
            and message.document.file_name
        ):
            filename = message.document.file_name.strip()

            if filename:
                return filename

    except Exception:
        pass

    # --------------------------------------------------------
    # CAPTION FALLBACK
    # --------------------------------------------------------

    try:
        caption = message.caption

        if caption:

            lines = [
                line.strip()
                for line in caption.splitlines()
                if line.strip()
            ]

            if lines:
                return lines[0]

    except Exception:
        pass

    return None


# ============================================================
# HELPER: MULTI-PART INFO
# ============================================================

def get_multipart_info(filename):
    """
    Supports:

        Movie.part1.rar
        Movie.part01.rar
        Movie.part001.rar

        Movie.rar.001
        Movie.rar.002

        Movie.zip.001
        Movie.zip.002

        Movie.001
        Movie.002

    Returns:

        {
            "is_multipart": True,
            "base": "...",
            "part": 1,
            "extension": ".rar"
        }

    or None
    """

    if not filename:
        return None

    filename = os.path.basename(
        filename.strip()
    )

    # --------------------------------------------------------
    # movie.part1.rar
    # --------------------------------------------------------

    match = re.match(
        r"^(?P<base>.+)\.part(?P<num>\d+)"
        r"(?P<ext>\.(?:rar|zip|7z))$",
        filename,
        re.IGNORECASE
    )

    if match:

        return {
            "is_multipart": True,
            "base": match.group("base"),
            "part": int(match.group("num")),
            "extension": match.group("ext").lower()
        }

    # --------------------------------------------------------
    # movie.rar.001
    # movie.zip.002
    # movie.7z.003
    # --------------------------------------------------------

    match = re.match(
        r"^(?P<base>.+)"
        r"(?P<ext>\.(?:rar|zip|7z))"
        r"\.(?P<num>\d+)$",
        filename,
        re.IGNORECASE
    )

    if match:

        return {
            "is_multipart": True,
            "base": match.group("base"),
            "part": int(match.group("num")),
            "extension": match.group("ext").lower()
        }

    # --------------------------------------------------------
    # movie.001
    # movie.002
    #
    # Only treat 3+ digit suffix as multipart.
    # --------------------------------------------------------

    match = re.match(
        r"^(?P<base>.+)\.(?P<num>\d{3,})$",
        filename,
        re.IGNORECASE
    )

    if match:

        return {
            "is_multipart": True,
            "base": match.group("base"),
            "part": int(match.group("num")),
            "extension": ""
        }

    return None


# ============================================================
# HELPER: NORMAL ARCHIVE CHECK
# ============================================================

def is_supported_archive(filename):

    if not filename:
        return False

    filename = filename.lower().strip()

    # Normal archive
    if any(
        filename.endswith(ext)
        for ext in SUPPORTED_FORMATS
    ):
        return True

    # Multi-part archive
    multipart = get_multipart_info(
        filename
    )

    if multipart:
        return True

    return False


# ============================================================
# HELPER: VIDEO CHECK
# ============================================================

def is_video_file(filename):

    if not filename:
        return False

    return filename.lower().endswith(
        VIDEO_EXTENSIONS
    )


# ============================================================
# HELPER: METADATA
# ============================================================

def get_video_metadata(file_path):

    width = 0
    height = 0
    duration = 0

    try:

        parser = createParser(
            file_path
        )

        if not parser:
            return width, height, duration

        metadata = extractMetadata(
            parser
        )

        if metadata:

            if metadata.has("duration"):

                duration_value = (
                    metadata.get("duration")
                )

                if duration_value:

                    duration = int(
                        duration_value.total_seconds()
                    )

            if metadata.has("width"):

                width = (
                    metadata.get("width")
                    or 0
                )

            if metadata.has("height"):

                height = (
                    metadata.get("height")
                    or 0
                )

    except Exception as e:

        logger.warning(
            f"Could not read video metadata: {e}"
        )

    return width, height, duration


# ============================================================
# HELPER: GET CUSTOM THUMBNAIL
# ============================================================

async def get_custom_thumbnail(
    client,
    user_id
):

    try:

        download_dir = (
            Config.DOWNLOAD_LOCATION
        )

        os.makedirs(
            download_dir,
            exist_ok=True
        )

        thumb_path = os.path.join(
            download_dir,
            f"{user_id}.jpg"
        )

        # ----------------------------------------------------
        # LOCAL THUMBNAIL
        # ----------------------------------------------------

        if os.path.exists(
            thumb_path
        ):

            try:

                with Image.open(
                    thumb_path
                ) as img:

                    img.verify()

                return thumb_path

            except Exception:

                try:
                    os.remove(
                        thumb_path
                    )
                except Exception:
                    pass

        # ----------------------------------------------------
        # DATABASE THUMBNAIL
        # ----------------------------------------------------

        thumbnail = await db.get_thumbnail(
            user_id
        )

        if not thumbnail:
            return None

        # ----------------------------------------------------
        # DOWNLOAD THUMBNAIL
        # ----------------------------------------------------

        try:

            downloaded = (
                await client.download_media(
                    message=thumbnail,
                    file_name=thumb_path
                )
            )

            if (
                downloaded
                and os.path.exists(
                    thumb_path
                )
            ):
                return thumb_path

        except Exception as e:

            logger.warning(
                f"Could not download custom thumbnail: {e}"
            )

    except Exception as e:

        logger.warning(
            f"Custom thumbnail error: {e}"
        )

    return None


# ============================================================
# HELPER: PREPARE THUMBNAIL
# ============================================================

async def prepare_thumbnail(
    client,
    user_id,
    video_path,
    duration
):

    # --------------------------------------------------------
    # CUSTOM THUMBNAIL
    # --------------------------------------------------------

    custom_thumb = (
        await get_custom_thumbnail(
            client,
            user_id
        )
    )

    if (
        custom_thumb
        and os.path.exists(
            custom_thumb
        )
    ):

        return custom_thumb, False

    # --------------------------------------------------------
    # SCREENSHOT FALLBACK
    # --------------------------------------------------------

    if duration > 1:

        try:

            screenshot = (
                await take_screen_shot(
                    video_path,
                    os.path.dirname(
                        video_path
                    ),
                    random.randint(
                        0,
                        max(
                            1,
                            duration - 1
                        )
                    )
                )
            )

            if (
                screenshot
                and os.path.exists(
                    screenshot
                )
            ):

                return screenshot, True

        except Exception as e:

            logger.warning(
                f"Could not generate screenshot: {e}"
            )

    return None, False


# ============================================================
# HELPER: GET UPLOAD MODE
# ============================================================

async def get_upload_mode(user_id):

    """
    Existing database setting:

    False = VIDEO
    True  = DOCUMENT
    """

    try:

        return await db.get_upload_as_doc(
            user_id
        )

    except Exception as e:

        logger.warning(
            f"Could not read upload mode: {e}"
        )

        return False


# ============================================================
# MULTI-PART PROCESS BUTTON
# ============================================================

def multipart_keyboard(group_id):

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "▶️ Process Archive",
                    callback_data=(
                        f"process_multipart:{group_id}"
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=(
                        f"cancel_multipart:{group_id}"
                    )
                )
            ]
        ]
    )


# ============================================================
# NORMAL CANCEL BUTTON
# ============================================================

CANCEL_BUTTON = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="cancel_unzip"
            )
        ]
    ]
)


# ============================================================
# GET PARTS TEXT
# ============================================================

def get_multipart_status(data):

    parts = data.get(
        "parts",
        {}
    )

    if not parts:
        return "No parts received."

    sorted_parts = sorted(
        parts.keys()
    )

    lines = []

    for part_number in sorted_parts:

        part = parts[part_number]

        lines.append(
            f"✅ Part `{part_number}` — "
            f"`{part['filename']}`"
        )

    return "\n".join(lines)


# ============================================================
# MULTI-PART PROCESS CALLBACK
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^process_multipart:"
    )
)
async def process_multipart_callback(
    client,
    callback_query
):

    user_id = (
        callback_query.from_user.id
    )

    data = (
        callback_query.data
    )

    group_id = data.split(
        ":",
        1
    )[1]

    user_groups = pending_multipart.get(
        user_id
    )

    if not user_groups:
        await callback_query.answer(
            "❌ Archive session expired.",
            show_alert=True
        )
        return

    archive_data = user_groups.get(
        group_id
    )

    if not archive_data:

        await callback_query.answer(
            "❌ Archive session not found.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # PREVENT DOUBLE PROCESS
    # --------------------------------------------------------

    if archive_data.get(
        "processing"
    ):

        await callback_query.answer(
            "⏳ Archive is already processing.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # MARK PROCESSING
    # --------------------------------------------------------

    archive_data["processing"] = True

    try:

        await callback_query.answer(
            "⏳ Processing archive...",
            show_alert=False
        )

    except Exception:
        pass

    # --------------------------------------------------------
    # CREATE TASK
    # --------------------------------------------------------

    task = asyncio.create_task(
        process_multipart_archive(
            client,
            user_id,
            group_id
        )
    )

    active_tasks[user_id] = task

    # --------------------------------------------------------
    # WAIT FOR TASK
    # --------------------------------------------------------

    try:

        await task

    except asyncio.CancelledError:

        logger.info(
            f"Multipart task cancelled: {user_id}"
        )

    except Exception as e:

        logger.exception(
            f"Multipart processing failed: {e}"
        )


# ============================================================
# MULTI-PART CANCEL CALLBACK
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^cancel_multipart:"
    )
)
async def cancel_multipart_callback(
    client,
    callback_query
):

    user_id = (
        callback_query.from_user.id
    )

    group_id = (
        callback_query.data.split(
            ":",
            1
        )[1]
    )

    user_groups = pending_multipart.get(
        user_id
    )

    if not user_groups:

        await callback_query.answer(
            "No active archive.",
            show_alert=True
        )

        return

    archive_data = user_groups.get(
        group_id
    )

    if not archive_data:

        await callback_query.answer(
            "Archive session not found.",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # CANCEL RUNNING TASK
    # --------------------------------------------------------

    task = active_tasks.get(
        user_id
    )

    if task:

        try:
            task.cancel()
        except Exception:
            pass

        active_tasks.pop(
            user_id,
            None
        )

    # --------------------------------------------------------
    # CLEANUP
    # --------------------------------------------------------

    await cleanup_multipart_group(
        user_id,
        group_id
    )

    try:

        await callback_query.answer(
            "❌ Archive cancelled.",
            show_alert=True
        )

    except Exception:
        pass

    try:

        await callback_query.message.edit_text(
            "❌ **Multi-part archive cancelled.**"
        )

    except Exception:
        pass


# ============================================================
# NORMAL CANCEL CALLBACK
# ============================================================

@Client.on_callback_query(
    filters.regex(
        "^cancel_unzip$"
    )
)
async def cancel_unzip(
    client,
    callback_query
):

    user_id = (
        callback_query.from_user.id
    )

    task = active_tasks.get(
        user_id
    )

    if task:

        try:
            task.cancel()
        except Exception:
            pass

        active_tasks.pop(
            user_id,
            None
        )

        try:

            await callback_query.answer(
                "❌ Processing cancelled.",
                show_alert=True
            )

        except Exception:
            pass

        try:

            await callback_query.message.edit_text(
                "❌ **Archive processing cancelled.**"
            )

        except Exception:
            pass

    else:

        try:

            await callback_query.answer(
                "No active task found.",
                show_alert=True
            )

        except Exception:
            pass


# ============================================================
# SEND DOCUMENT
# ============================================================

async def send_document(
    client,
    chat_id,
    file_path,
    status_message,
    file_number,
    total_files
):

    filename = os.path.basename(
        file_path
    )

    text = (
        f"📦 **File {file_number}/{total_files}**\n\n"
        f"📄 `{filename}`\n\n"
        f"📤 **Uploading...**"
    )

    await safe_edit(
        status_message,
        text
    )

    upload_time = time.time()

    await client.send_document(
        chat_id=chat_id,
        document=file_path,

        progress=progress_for_pyrogram,

        progress_args=(
            f"📤 **Uploading File "
            f"{file_number}/{total_files}...**",
            status_message,
            upload_time
        )
    )


# ============================================================
# SEND VIDEO
# ============================================================

async def send_video(
    client,
    chat_id,
    user_id,
    video_path,
    status_message,
    file_number,
    total_files
):

    original_filename = os.path.basename(
        video_path
    )

    await safe_edit(
        status_message,
        (
            f"🎬 `{original_filename}`\n\n"
            f"📦 **File {file_number}/{total_files}**\n"
            f"📤 **Preparing video...**"
        )
    )

    width, height, duration = (
        get_video_metadata(
            video_path
        )
    )

    thumbnail_path = None
    generated_thumbnail = False

    try:

        (
            thumbnail_path,
            generated_thumbnail
        ) = await prepare_thumbnail(
            client,
            user_id,
            video_path,
            duration
        )

        await safe_edit(
            status_message,
            (
                f"🎬 `{original_filename}`\n\n"
                f"📦 **File {file_number}/{total_files}**\n"
                f"⏱️ Duration: `{duration}s`\n"
                f"📐 Resolution: "
                f"`{width}x{height}`\n\n"
                f"📤 **Uploading... 🚀**"
            )
        )

        upload_time = time.time()

        await client.send_video(

            chat_id=chat_id,

            video=video_path,

            duration=duration,

            width=width,

            height=height,

            thumb=thumbnail_path,

            file_name=original_filename,

            supports_streaming=True,

            progress=progress_for_pyrogram,

            progress_args=(
                f"📤 **Uploading File "
                f"{file_number}/{total_files}...**",
                status_message,
                upload_time
            )
        )

    finally:

        # ----------------------------------------------------
        # DELETE GENERATED SCREENSHOT ONLY
        # ----------------------------------------------------

        if generated_thumbnail:

            try:

                if (
                    thumbnail_path
                    and os.path.exists(
                        thumbnail_path
                    )
                ):

                    os.remove(
                        thumbnail_path
                    )

            except Exception as e:

                logger.warning(
                    f"Could not remove generated "
                    f"thumbnail: {e}"
                )


# ============================================================
# PROCESS EXTRACTED FILES
# ============================================================

async def process_extracted_files(
    client,
    message,
    user_id,
    extract_dir,
    status_message
):

    # --------------------------------------------------------
    # FIND ALL FILES
    # --------------------------------------------------------

    files = []

    for root, dirs, filenames in os.walk(
        extract_dir
    ):

        for filename in filenames:

            full_path = os.path.join(
                root,
                filename
            )

            if os.path.isfile(
                full_path
            ):

                files.append(
                    full_path
                )

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    files.sort(
        key=lambda x: x.lower()
    )

    total_files = len(files)

    if total_files == 0:

        await safe_edit(
            status_message,
            "❌ **Archive is empty. "
            "No files found.**"
        )

        return

    # --------------------------------------------------------
    # GET USER MODE
    # --------------------------------------------------------

    upload_as_doc = (
        await get_upload_mode(
            user_id
        )
    )

    # --------------------------------------------------------
    # PROCESS ONE BY ONE
    # --------------------------------------------------------

    for index, file_path in enumerate(
        files,
        start=1
    ):

        current_task = (
            asyncio.current_task()
        )

        if current_task.cancelled():

            raise asyncio.CancelledError

        # ----------------------------------------------------
        # DOCUMENT MODE
        # ----------------------------------------------------

        if upload_as_doc:

            await send_document(
                client=client,
                chat_id=message.chat.id,
                file_path=file_path,
                status_message=status_message,
                file_number=index,
                total_files=total_files
            )

        # ----------------------------------------------------
        # VIDEO MODE
        # ----------------------------------------------------

        else:

            if is_video_file(
                file_path
            ):

                await send_video(
                    client=client,
                    chat_id=message.chat.id,
                    user_id=user_id,
                    video_path=file_path,
                    status_message=status_message,
                    file_number=index,
                    total_files=total_files
                )

            else:

                await send_document(
                    client=client,
                    chat_id=message.chat.id,
                    file_path=file_path,
                    status_message=status_message,
                    file_number=index,
                    total_files=total_files
                )

    # --------------------------------------------------------
    # COMPLETE
    # --------------------------------------------------------

    await safe_edit(
        status_message,
        (
            f"✅ **All files completed!**\n\n"
            f"📦 Total files: `{total_files}`"
        )
    )


# ============================================================
# CLEANUP MULTI-PART GROUP
# ============================================================

async def cleanup_multipart_group(
    user_id,
    group_id
):

    try:

        user_groups = pending_multipart.get(
            user_id
        )

        if not user_groups:
            return

        archive_data = user_groups.get(
            group_id
        )

        if not archive_data:
            return

        directory = archive_data.get(
            "directory"
        )

        # ----------------------------------------------------
        # DELETE WHOLE MULTIPART DIRECTORY
        # ----------------------------------------------------

        if (
            directory
            and os.path.exists(
                directory
            )
        ):

            shutil.rmtree(
                directory,
                ignore_errors=True
            )

        # ----------------------------------------------------
        # REMOVE GROUP
        # ----------------------------------------------------

        user_groups.pop(
            group_id,
            None
        )

        if not user_groups:

            pending_multipart.pop(
                user_id,
                None
            )

    except Exception as e:

        logger.warning(
            f"Multipart cleanup failed: {e}"
        )


# ============================================================
# DOWNLOAD MULTI-PART
# ============================================================

async def download_multipart_part(
    client,
    message,
    filename,
    part_path,
    status_message,
    part_number
):

    download_time = time.time()

    await client.download_media(

        message=message,

        file_name=part_path,

        progress=progress_for_pyrogram,

        progress_args=(
            (
                f"📥 **Downloading "
                f"Part {part_number}... ⏳**"
            ),
            status_message,
            download_time
        )
    )


# ============================================================
# PROCESS MULTI-PART ARCHIVE
# ============================================================

async def process_multipart_archive(
    client,
    user_id,
    group_id
):

    user_groups = pending_multipart.get(
        user_id
    )

    if not user_groups:
        return

    archive_data = user_groups.get(
        group_id
    )

    if not archive_data:
        return

    status_message = (
        archive_data.get(
            "status_message"
        )
    )

    message = (
        archive_data.get(
            "message"
        )
    )

    directory = (
        archive_data.get(
            "directory"
        )
    )

    parts = archive_data.get(
        "parts",
        {}
    )

    try:

        # ----------------------------------------------------
        # CHECK PARTS
        # ----------------------------------------------------

        if not parts:

            await safe_edit(
                status_message,
                "❌ **No archive parts found.**"
            )

            return

        # ----------------------------------------------------
        # PART NUMBERS
        # ----------------------------------------------------

        part_numbers = sorted(
            parts.keys()
        )

        # ----------------------------------------------------
        # REQUIRE PART 1
        # ----------------------------------------------------

        if 1 not in parts:

            await safe_edit(
                status_message,
                (
                    "❌ **Part 1 is missing.**\n\n"
                    "Multi-part archive processing "
                    "requires Part 1."
                )
            )

            return

        # ----------------------------------------------------
        # CHECK CONTINUOUS PARTS
        #
        # Example:
        # 1,2,3,4 = OK
        #
        # 1,2,4 = missing 3
        # ----------------------------------------------------

        expected_parts = list(
            range(
                1,
                max(part_numbers) + 1
            )
        )

        missing_parts = [
            number
            for number in expected_parts
            if number not in parts
        ]

        if missing_parts:

            missing_text = ", ".join(
                str(number)
                for number in missing_parts
            )

            await safe_edit(
                status_message,
                (
                    "⚠️ **Some archive parts are missing.**\n\n"
                    f"📦 Received: "
                    f"`{len(parts)}` parts\n"
                    f"❌ Missing: `{missing_text}`\n\n"
                    "Please send the missing parts "
                    "and press **Process Archive** again."
                )
            )

            archive_data["processing"] = False

            return

        # ----------------------------------------------------
        # VERIFY ALL FILES EXIST
        # ----------------------------------------------------

        missing_files = []

        for number in part_numbers:

            part = parts[number]

            path = part.get(
                "path"
            )

            if (
                not path
                or not os.path.exists(
                    path
                )
            ):

                missing_files.append(
                    number
                )

        if missing_files:

            missing_text = ", ".join(
                str(number)
                for number in missing_files
            )

            await safe_edit(
                status_message,
                (
                    "❌ **Archive parts are incomplete.**\n\n"
                    f"Missing downloaded parts: "
                    f"`{missing_text}`"
                )
            )

            archive_data["processing"] = False

            return

        # ----------------------------------------------------
        # MAIN PART
        # ----------------------------------------------------

        first_part = parts[1]

        archive_path = first_part[
            "path"
        ]

        # ----------------------------------------------------
        # EXTRACT DIRECTORY
        # ----------------------------------------------------

        extract_dir = tempfile.mkdtemp(
            prefix=f"unzip_{user_id}_"
        )

        archive_data[
            "extract_dir"
        ] = extract_dir

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        await safe_edit(
            status_message,
            (
                "📦 **All archive parts received!**\n\n"
                f"📦 Total parts: "
                f"`{len(parts)}`\n\n"
                "📂 **Extracting... ⏳**"
            )
        )

        # ----------------------------------------------------
        # EXTRACT
        # ----------------------------------------------------

        def extract_archive():

            Archive(
                archive_path
            ).extractall(
                extract_dir
            )

        await asyncio.to_thread(
            extract_archive
        )

        # ----------------------------------------------------
        # SEND FILES
        # ----------------------------------------------------

        await process_extracted_files(
            client=client,
            message=message,
            user_id=user_id,
            extract_dir=extract_dir,
            status_message=status_message
        )

    except asyncio.CancelledError:

        logger.info(
            f"Multipart task cancelled "
            f"for user {user_id}"
        )

        try:

            await safe_edit(
                status_message,
                "❌ **Archive processing cancelled.**"
            )

        except Exception:
            pass

        raise

    except Exception as e:

        logger.exception(
            "Multipart archive processing failed"
        )

        try:

            await safe_edit(
                status_message,
                (
                    "❌ **Failed to process "
                    "multi-part archive.**\n\n"
                    f"**Error:** `{str(e)[:1000]}`"
                )
            )

        except Exception:
            pass

    finally:

        # ----------------------------------------------------
        # REMOVE ACTIVE TASK
        # ----------------------------------------------------

        current_task = active_tasks.get(
            user_id
        )

        if (
            current_task
            is asyncio.current_task()
        ):

            active_tasks.pop(
                user_id,
                None
            )

        # ----------------------------------------------------
        # CLEANUP
        #
        # Keep group if missing parts.
        # Otherwise delete after processing.
        # ----------------------------------------------------

        try:

            user_groups = (
                pending_multipart.get(
                    user_id
                )
            )

            current_group = (
                user_groups.get(
                    group_id
                )
                if user_groups
                else None
            )

            if current_group:

                # If processing finished successfully,
                # delete everything.
                if current_group.get(
                    "processing"
                ):

                    await cleanup_multipart_group(
                        user_id,
                        group_id
                    )

        except Exception as e:

            logger.warning(
                f"Could not cleanup multipart: {e}"
            )


# ============================================================
# ADD MULTI-PART MESSAGE
# ============================================================

async def add_multipart_part(
    client,
    message,
    user_id,
    filename,
    multipart_info
):

    base = (
        multipart_info["base"]
        .lower()
    )

    extension = (
        multipart_info["extension"]
        .lower()
    )

    part_number = (
        multipart_info["part"]
    )

    # --------------------------------------------------------
    # EXISTING GROUP SEARCH
    # --------------------------------------------------------

    user_groups = pending_multipart.setdefault(
        user_id,
        {}
    )

    group_id = None
    archive_data = None

    for gid, data in user_groups.items():

        if (
            data.get("base")
            == base
            and data.get("extension")
            == extension
        ):

            group_id = gid
            archive_data = data
            break

    # --------------------------------------------------------
    # CREATE NEW GROUP
    # --------------------------------------------------------

    if not archive_data:

        group_id = uuid.uuid4().hex[
            :10
        ]

        multipart_dir = os.path.join(
            Config.DOWNLOAD_LOCATION,
            "archives",
            f"multipart_{user_id}_{group_id}"
        )

        os.makedirs(
            multipart_dir,
            exist_ok=True
        )

        status_message = await message.reply_text(
            (
                "📦 **Multi-part archive detected**\n\n"
                f"📁 `{filename}`\n\n"
                "📥 **Downloading... ⏳**"
            ),
            reply_markup=multipart_keyboard(
                group_id
            )
        )

        archive_data = {

            "base": base,

            "extension": extension,

            "directory": multipart_dir,

            "parts": {},

            "status_message": status_message,

            "message": message,

            "processing": False,

            "extract_dir": None
        }

        user_groups[
            group_id
        ] = archive_data

    else:

        status_message = archive_data[
            "status_message"
        ]

        # ----------------------------------------------------
        # UPDATE ORIGINAL MESSAGE
        # ----------------------------------------------------

        await safe_edit(
            status_message,
            (
                "📦 **Multi-part archive**\n\n"
                f"📁 `{archive_data['base']}`\n\n"
                "📥 **Downloading new part... ⏳**"
            )
        )

    # --------------------------------------------------------
    # DUPLICATE PART CHECK
    # --------------------------------------------------------

    if part_number in archive_data[
        "parts"
    ]:

        await safe_edit(
            status_message,
            (
                f"⚠️ **Part {part_number} "
                f"already received.**\n\n"
                f"{get_multipart_status(archive_data)}"
            )
        )

        return

    # --------------------------------------------------------
    # PART PATH
    # --------------------------------------------------------

    part_path = os.path.join(
        archive_data["directory"],
        os.path.basename(
            filename
        )
    )

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    await download_multipart_part(
        client=client,
        message=message,
        filename=filename,
        part_path=part_path,
        status_message=status_message,
        part_number=part_number
    )

    # --------------------------------------------------------
    # SAVE PART
    # --------------------------------------------------------

    archive_data[
        "parts"
    ][part_number] = {

        "message": message,

        "filename": filename,

        "path": part_path
    }

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    part_numbers = sorted(
        archive_data[
            "parts"
        ].keys()
    )

    await safe_edit(
        status_message,
        (
            "📦 **Multi-part archive ready**\n\n"
            f"📁 `{archive_data['base']}`\n\n"
            f"📦 Parts received: "
            f"`{len(part_numbers)}`\n\n"
            f"{get_multipart_status(archive_data)}\n\n"
            "➡️ Send all remaining parts, "
            "then press **Process Archive**."
        )
    )


# ============================================================
# MAIN ARCHIVE HANDLER
# ============================================================

@Client.on_message(
    filters.private & filters.document
)
async def unzip_handler(
    client,
    message
):

    await AddUser(
        client,
        message
    )

    # --------------------------------------------------------
    # FORCE SUBSCRIBE
    # --------------------------------------------------------

    if Config.UPDATES_CHANNEL:

        fsub = await handle_force_subscribe(
            client,
            message
        )

        if fsub == 400:
            return

    # --------------------------------------------------------
    # GET FILENAME
    #
    # document.file_name first
    # caption fallback second
    # --------------------------------------------------------

    file_name = get_caption_filename(
        message
    )

    if not file_name:
        raise ContinuePropagation

    # --------------------------------------------------------
    # CHECK ARCHIVE
    # --------------------------------------------------------

    if not is_supported_archive(
        file_name
    ):
        raise ContinuePropagation

    # --------------------------------------------------------
    # USER ID
    # --------------------------------------------------------

    user_id = message.from_user.id

    # --------------------------------------------------------
    # MULTI-PART CHECK
    # --------------------------------------------------------

    multipart_info = get_multipart_info(
        file_name
    )

    # ========================================================
    # MULTI-PART ARCHIVE
    # ========================================================

    if multipart_info:

        # ----------------------------------------------------
        # PREVENT DIFFERENT ACTIVE PROCESS
        # ----------------------------------------------------

        if user_id in active_tasks:

            await message.reply_text(
                (
                    "⚠️ **আপনার একটি archive "
                    "ইতিমধ্যে process হচ্ছে।**\n\n"
                    "আগের কাজ শেষ হওয়ার পর "
                    "আবার পাঠান।"
                )
            )

            return

        try:

            await add_multipart_part(
                client=client,
                message=message,
                user_id=user_id,
                filename=file_name,
                multipart_info=multipart_info
            )

        except Exception as e:

            logger.exception(
                "Could not add multipart part"
            )

            await message.reply_text(
                (
                    "❌ **Could not download "
                    "archive part.**\n\n"
                    f"`{str(e)[:1000]}`"
                )
            )

        return

    # ========================================================
    # NORMAL ARCHIVE
    # ========================================================

    # --------------------------------------------------------
    # PREVENT MULTIPLE TASKS
    # --------------------------------------------------------

    if user_id in active_tasks:

        await message.reply_text(
            (
                "⚠️ **আপনার একটি archive "
                "ইতিমধ্যে process হচ্ছে।**\n\n"
                "আগের কাজ শেষ হওয়ার পর "
                "আবার পাঠান।"
            )
        )

        return

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    status_message = await message.reply_text(
        (
            f"📦 **Archive received**\n\n"
            f"📁 `{file_name}`\n\n"
            f"📥 **Downloading... ⏳**"
        ),
        reply_markup=CANCEL_BUTTON
    )

    archive_path = None
    extract_dir = None

    current_task = (
        asyncio.current_task()
    )

    active_tasks[
        user_id
    ] = current_task

    try:

        # ====================================================
        # DOWNLOAD DIRECTORY
        # ====================================================

        download_dir = os.path.join(
            Config.DOWNLOAD_LOCATION,
            "archives"
        )

        os.makedirs(
            download_dir,
            exist_ok=True
        )

        # ----------------------------------------------------
        # ARCHIVE PATH
        # ----------------------------------------------------

        archive_path = os.path.join(
            download_dir,
            file_name
        )

        download_time = time.time()

        # ====================================================
        # DOWNLOAD
        # ====================================================

        await client.download_media(

            message=message,

            file_name=archive_path,

            progress=progress_for_pyrogram,

            progress_args=(
                "📥 **Archive downloading... ⏳**",
                status_message,
                download_time
            )
        )

        # ====================================================
        # EXTRACTION DIRECTORY
        # ====================================================

        extract_dir = tempfile.mkdtemp(
            prefix=f"unzip_{user_id}_"
        )

        await safe_edit(
            status_message,
            (
                f"📦 **Archive downloaded**\n\n"
                f"📁 `{file_name}`\n\n"
                f"📂 **Extracting... ⏳**"
            )
        )

        # ====================================================
        # EXTRACT
        # ====================================================

        def extract_archive():

            Archive(
                archive_path
            ).extractall(
                extract_dir
            )

        await asyncio.to_thread(
            extract_archive
        )

        # ====================================================
        # SEND EXTRACTED FILES
        # ====================================================

        await process_extracted_files(
            client=client,
            message=message,
            user_id=user_id,
            extract_dir=extract_dir,
            status_message=status_message
        )

    except asyncio.CancelledError:

        logger.info(
            f"Archive task cancelled "
            f"for user {user_id}"
        )

        try:

            await safe_edit(
                status_message,
                "❌ **Archive processing cancelled.**"
            )

        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "Archive processing failed"
        )

        try:

            await safe_edit(
                status_message,
                (
                    "❌ **Failed to process archive.**\n\n"
                    f"**Error:** `{str(e)[:1000]}`"
                )
            )

        except Exception:
            pass

    finally:

        # ====================================================
        # REMOVE ACTIVE TASK
        # ====================================================

        active_tasks.pop(
            user_id,
            None
        )

        # ====================================================
        # DELETE ORIGINAL ARCHIVE
        # ====================================================

        try:

            if (
                archive_path
                and os.path.exists(
                    archive_path
                )
            ):

                os.remove(
                    archive_path
                )

        except Exception as e:

            logger.warning(
                f"Could not remove archive: {e}"
            )

        # ====================================================
        # DELETE EXTRACTION DIRECTORY
        # ====================================================

        try:

            if (
                extract_dir
                and os.path.exists(
                    extract_dir
                )
            ):

                shutil.rmtree(
                    extract_dir,
                    ignore_errors=True
                )

        except Exception as e:

            logger.warning(
                f"Could not remove extraction "
                f"directory: {e}"
            )
