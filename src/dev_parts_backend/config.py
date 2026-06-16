import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    db_path: Path
    master_csv: Path
    detail_csv: Path


def get_settings() -> Settings:
    load_env_file()
    return Settings(
        db_path=Path(os.environ.get("DEV_PARTS_DB_PATH", "data/dev_parts.db")),
        master_csv=Path(os.environ.get("DEV_PARTS_MASTER_CSV", "data/input/master.csv")),
        detail_csv=Path(os.environ.get("DEV_PARTS_DETAIL_CSV", "data/input/detail.csv")),
    )
