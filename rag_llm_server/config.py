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

    CONTEXT_MANAGER_ENABLED = os.getenv("CONTEXT_MANAGER_ENABLED", "true").lower() == "true"
    CONTEXT_BUDGET_TOKENS = int(os.getenv("CONTEXT_BUDGET_TOKENS", "6000"))
    CONTEXT_MAX_INPUT_TOKENS = int(os.getenv("CONTEXT_MAX_INPUT_TOKENS", "750"))
    CONTEXT_OUTPUT_RESERVE_TOKENS = int(os.getenv("CONTEXT_OUTPUT_RESERVE_TOKENS", "800"))
    CONTEXT_PROMPT_SAFE_THRESHOLD = float(os.getenv("CONTEXT_PROMPT_SAFE_THRESHOLD", "0.90"))
    CONTEXT_T1_THRESHOLD = float(os.getenv("CONTEXT_T1_THRESHOLD", "0.30"))
    CONTEXT_T2_THRESHOLD = float(os.getenv("CONTEXT_T2_THRESHOLD", "0.50"))
    CONTEXT_T3_THRESHOLD = float(os.getenv("CONTEXT_T3_THRESHOLD", "0.75"))
    CONTEXT_T4_THRESHOLD = float(os.getenv("CONTEXT_T4_THRESHOLD", "0.90"))
    CONTEXT_T5_THRESHOLD = float(os.getenv("CONTEXT_T5_THRESHOLD", "0.95"))
    CONTEXT_RECENT_ROUNDS = int(os.getenv("CONTEXT_RECENT_ROUNDS", "6"))
    CONTEXT_MAX_MESSAGE_CHARS = int(os.getenv("CONTEXT_MAX_MESSAGE_CHARS", "1200"))
    CONTEXT_SESSION_ID = os.getenv("CONTEXT_SESSION_ID", "text_default")
    CONTEXT_DEMO_MODE = os.getenv("CONTEXT_DEMO_MODE", "false").lower() == "true"
    CONTEXT_SUMMARY_CACHE_TTL_SECONDS = int(os.getenv("CONTEXT_SUMMARY_CACHE_TTL_SECONDS", "1800"))
    
    SERVER_URL = os.getenv("SERVER_URL")

settings = Config()
