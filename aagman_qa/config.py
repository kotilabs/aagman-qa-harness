import os
from pathlib import Path
from dotenv import load_dotenv
import yaml

load_dotenv(Path.cwd() / ".env")

ENV_URLS = {
    "prod": "https://app.aagman.ai",
    "staging": "https://app.staging.v2.aagman.ai",
}


def get_env_url(env: str) -> str:
    url = os.getenv("AAGMAN_BASE_URL")
    if url:
        return url.rstrip("/")
    if env not in ENV_URLS:
        raise ValueError(f"Unknown env: {env}. Choose from {list(ENV_URLS)}")
    return ENV_URLS[env]


def load_manifest(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def phone() -> str:
    return os.getenv("AAGMAN_PHONE", "")


def otp() -> str | None:
    return os.getenv("AAGMAN_OTP") or None


def github_owner() -> str:
    return os.getenv("GITHUB_OWNER", "kotilabs")


def github_repo() -> str:
    return os.getenv("GITHUB_REPO", "aagman-v2")


def github_token() -> str | None:
    return os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or os.getenv("GH_TOKEN_KL")


def browser_profile() -> str | None:
    return os.getenv("BROWSER_USE_PROFILE")


def browser_cdp_url() -> str | None:
    return os.getenv("BROWSER_USE_CDP_URL")


def screenshots_owner() -> str:
    return os.getenv("AAGMAN_QA_SCREENSHOTS_OWNER", "iamaryansinha")


def screenshots_repo() -> str:
    return os.getenv("AAGMAN_QA_SCREENSHOTS_REPO", "aagman-qa-screenshots")


def screenshots_token() -> str | None:
    return (
        os.getenv("AAGMAN_QA_SCREENSHOTS_TOKEN")
        or os.getenv("GH_TOKEN_PERSONAL")
        or os.getenv("GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
    )


def tester_name() -> str:
    return os.getenv("AAGMAN_QA_TESTER", "Aryan")


# LLM configuration for intelligent reply handling.
def deepseek_api_key() -> str | None:
    return os.getenv("DEEPSEEK_API_KEY")


def llm_model() -> str:
    return os.getenv("AAGMAN_QA_LLM_MODEL", "deepseek-chat")


def llm_base_url() -> str:
    return os.getenv("AAGMAN_QA_LLM_BASE_URL", "https://api.deepseek.com/v1")


def llm_enabled() -> bool:
    return bool(deepseek_api_key())


# Vision API configuration for screenshot-based verification (e.g. chart detection).
def vision_api_key() -> str | None:
    return (
        os.getenv("VISION_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("KIMI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
    )


def vision_base_url() -> str:
    return os.getenv("VISION_BASE_URL", "https://api.openai.com/v1")


def vision_model() -> str:
    return os.getenv("VISION_MODEL", "gpt-4o-mini")


def vision_enabled() -> bool:
    return bool(vision_api_key())
