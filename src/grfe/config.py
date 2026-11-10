"""Paths and configuration. Everything on disk lives under DATA_DIR (the HDD), never the SD card."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

IST = ZoneInfo("Asia/Kolkata")

REPO_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("GRFE_DATA_DIR", "/mnt/hdd/governance-red-flag-engine"))
DB_PATH = DATA_DIR / "data" / "grfe.db"
CACHE_DIR = DATA_DIR / "cache"
FILES_DIR = DATA_DIR / "files"      # downloaded PDFs / XBRL
LOG_DIR = DATA_DIR / "logs"
SITE_DIR = DATA_DIR / "site"
LOCK_FILE = Path("/tmp/governance-red-flag-engine.lock")

CONFIG_DIR = REPO_DIR / "config"
SCORING_YAML = CONFIG_DIR / "scoring.yaml"
XBRL_MAP_YAML = CONFIG_DIR / "xbrl_map.yaml"
SOURCES_YAML = CONFIG_DIR / "sources.yaml"


def ensure_dirs() -> None:
    for d in (DB_PATH.parent, CACHE_DIR, FILES_DIR, LOG_DIR, SITE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def scoring() -> dict:
    return load_yaml(SCORING_YAML)


def xbrl_map() -> dict:
    return load_yaml(XBRL_MAP_YAML)


def sources() -> dict:
    return load_yaml(SOURCES_YAML)
