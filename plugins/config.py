
import os
from os import environ, getenv
import logging

logging.basicConfig(
    format='%(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('log.txt'),
              logging.StreamHandler()],
    level=logging.INFO
)

class Config(object):

    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    API_ID = int(os.environ.get("API_ID","24776633"))
    API_HASH = os.environ.get("API_HASH", "57b1f632044b4e718f5dce004a988d69")

    DOWNLOAD_LOCATION = "./DOWNLOADS"
    MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024
    TG_MAX_FILE_SIZE = 2194304000
    FREE_USER_MAX_FILE_SIZE = 2194304000
    CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 128))
    DEF_THUMB_NAIL_VID_S = os.environ.get("DEF_THUMB_NAIL_VID_S", "https://placehold.it/90x90")
    HTTP_PROXY = os.environ.get("HTTP_PROXY", "")

    OUO_IO_API_KEY = ""
    MAX_MESSAGE_LENGTH = 4096
    PROCESS_MAX_TIMEOUT = 3600
    DEF_WATER_MARK_FILE = "@UploaderXNTBot"

    ADMIN = set(
        int(x) for x in environ.get("ADMIN", "").split()
        if x.isdigit()
    )

    BANNED_USERS = set(
        int(x) for x in environ.get("BANNED_USERS", "").split()
        if x.isdigit()
    )

    DATABASE_NAME = "Cluster0"

    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "mongodb+srv://mongodbpy_db_user:pPgtRKyHsm8GvJF2@cluster0.u2ft5ps.mongodb.net/?appName=Cluster0"
    )

    LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "-1004456487791"))
    LOGGER = logging
    OWNER_ID = int(os.environ.get("OWNER_ID", "8248792819"))
    SESSION_NAME = "UploaderXNTBot"
    UPDATES_CHANNEL = os.environ.get("UPDATES_CHANNEL", "-1004468070238")

    TG_MIN_FILE_SIZE = 2194304000
    BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
    ADL_BOT_RQ = {}

    # Set False off else True
    TRUE_OR_FALSE = os.environ.get("TRUE_OR_FALSE", "").lower() == "true"

    # Shortlink settings
    SHORT_DOMAIN = environ.get("SHORT_DOMAIN", "")
    SHORT_API = environ.get("SHORT_API", "")

    # Verification video link
    VERIFICATION = environ.get("VERIFICATION", "")
