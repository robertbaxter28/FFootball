import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "dynasty.db"
DEFAULT_CONTEXT_DIR = PROJECT_ROOT / "context"
ENV_PATH = PROJECT_ROOT / ".env"

REQUIRED_KEYS = ["ANTHROPIC_API_KEY", "SLEEPER_USERNAME", "SLEEPER_LEAGUE_ID", "SLEEPER_SEASON"]


class Config:
    def __init__(self):
        load_dotenv(ENV_PATH)
        self.anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
        self.sleeper_username: str = os.environ.get("SLEEPER_USERNAME", "")
        self.sleeper_league_id: str = os.environ.get("SLEEPER_LEAGUE_ID", "")
        self.sleeper_season: str = os.environ.get("SLEEPER_SEASON", "2025")
        self.db_path: Path = Path(os.environ.get("FFOOTBALL_DB_PATH", str(DEFAULT_DB_PATH)))
        self.context_dir: Path = DEFAULT_CONTEXT_DIR

    def validate(self) -> list[str]:
        missing = []
        for key in REQUIRED_KEYS:
            val = getattr(self, key.lower().replace("anthropic_", "anthropic_").replace("sleeper_", "sleeper_"), None)
            env_val = os.environ.get(key, "")
            if not env_val:
                missing.append(key)
        return missing

    def is_ready(self) -> bool:
        return len(self.validate()) == 0


def load_config() -> Config:
    return Config()


def write_env(values: dict[str, str]) -> None:
    lines = []
    if ENV_PATH.exists():
        existing = {}
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
        existing.update(values)
        values = existing

    for k, v in values.items():
        lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
