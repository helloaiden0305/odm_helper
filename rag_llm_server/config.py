import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    VOLC_AK = os.getenv("VOLC_ACCESS_KEY")
    VOLC_SK = os.getenv("VOLC_SECRET_KEY")
    ARK_ENDPOINT_ID = os.getenv("ARK_ENDPOINT_ID")
    ARK_API_KEY = os.getenv("ARK_API_KEY")

    RTC_APP_ID = os.getenv("RTC_APP_ID")
    RTC_APP_KEY = os.getenv("RTC_APP_KEY")
    RTC_TOKEN = os.getenv("RTC_TOKEN")
    RTC_TOKEN_MODE = os.getenv("RTC_TOKEN_MODE", "auto")
    RTC_DEBUG_PRINT_TOKEN = os.getenv("RTC_DEBUG_PRINT_TOKEN", "false").lower() == "true"
    RTC_DYNAMIC_SESSION = os.getenv("RTC_DYNAMIC_SESSION", "true").lower() == "true"
    RTC_OPENAPI_TIMEOUT = float(os.getenv("RTC_OPENAPI_TIMEOUT", "30"))
    RTC_ROOM_ID = os.getenv("RTC_ROOM_ID", "XzyDemoRoom")
    RTC_USER_ID = os.getenv("RTC_USER_ID", "XzyTester")
    VOICE_CONFIRM_MODE = os.getenv("VOICE_CONFIRM_MODE", "true").lower() == "true"
    ASR_APP_ID = os.getenv("ASR_APP_ID")
    TTS_APP_ID = os.getenv("TTS_APP_ID")
    TTS_SPEED_RATIO = float(os.getenv("TTS_SPEED_RATIO", "1.5"))
    
    SERVER_URL = os.getenv("SERVER_URL")

settings = Config()
