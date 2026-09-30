"""Application configuration: file locations, .env loading, API settings."""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.ai.model_catalog import DEFAULT_REASONING_EFFORT, normalise_effort
from app.utils.logging_setup import get_logger

__all__ = [
    "APP_NAME",
    "APP_ID",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_REASONING_EFFORT",
    "ENV_WINS_VAR",
    "Settings",
    "app_data_dir",
    "data_dir",
    "db_path",
    "env_file_candidates",
    "env_overrides_settings",
    "is_frozen",
    "load_environment",
    "log_dir",
    "project_root",
    "resource_path",
]

APP_NAME = "AI Nutrition Assistant"
APP_ID = "AI-Nutrition-Assistant"  # used for folder / executable names

# DeepSeek API (OpenAI-compatible). The model identifier for DeepSeek V4.1
# Flash is "deepseek-flash" - see https://api-docs.deepseek.com/quick_start/pricing
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_TOKENS = 2048

# Set ``NUTRITION_ENV_WINS=1`` (CI, scripted installs) to make the environment
# variables win over whatever the GUI saved. Off by default: clicking "save"
# in the interface must always have an effect.
ENV_WINS_VAR = "NUTRITION_ENV_WINS"

_TRUTHY = {"1", "true", "yes"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def env_overrides_settings() -> bool:
    """True when the environment must win over the GUI-saved settings."""
    return _env_flag(ENV_WINS_VAR)


def is_frozen() -> bool:
    """True when running from a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """Folder that contains the source code (or the EXE when packaged)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


def data_dir() -> Path:
    """Folder holding the SQLite database and other user data.

    Several locations are tried in order so a restricted or redirected
    ``%LOCALAPPDATA%`` cannot stop the app from starting. The first folder that
    can be created wins; the last resort is a folder in the temporary directory.
    """
    cache_key = (os.environ.get("NUTRITION_APP_DATA_DIR", "").strip(), is_frozen())
    cached = _DATA_DIR_CACHE.get(cache_key)
    if cached is not None and cached.is_dir():
        return cached

    problems: list[str] = []
    candidates = _data_dir_candidates()
    for index, base in enumerate(candidates):
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problems.append(f"{base} ({exc.strerror or exc})")
            continue
        if index > 0:
            get_logger().warning("Using fallback data folder: %s", base)
        _DATA_DIR_CACHE[cache_key] = base
        return base

    fallback = Path(tempfile.gettempdir()) / APP_NAME / "data"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        get_logger().warning(
            "No usual data folder was writable; using %s. Tried: %s",
            fallback,
            "; ".join(problems),
        )
        _DATA_DIR_CACHE[cache_key] = fallback
        return fallback
    except OSError as exc:  # pragma: no cover - would need a broken system
        raise RuntimeError(
            "No writable folder was found for the app data. Tried: "
            + "; ".join([*problems, f"{fallback} ({exc.strerror or exc})"])
        ) from exc


def _data_dir_candidates() -> list[Path]:
    """Possible data folders, most appropriate first."""
    override = os.environ.get("NUTRITION_APP_DATA_DIR", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    if is_frozen():
        candidates.append(_local_app_data() / "data")
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / APP_NAME / "data")
        candidates.append(project_root() / "data")
    else:
        candidates.append(project_root() / "data")
        candidates.append(_local_app_data() / "data")
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


# Resolved once per (override, frozen) combination: the folder must not be
# created over and over during a single run.
_DATA_DIR_CACHE: dict[tuple[str, bool], Path] = {}


def db_path() -> Path:
    """Full path of the SQLite database file."""
    return data_dir() / "nutrition.db"


def app_data_dir() -> Path:
    """Top-level folder for this app (contains ``data`` and ``logs``)."""
    return _local_app_data() if is_frozen() else project_root()


def log_dir() -> Path:
    candidates = (
        [_local_app_data() / "logs", data_dir() / "logs"]
        if is_frozen()
        else [project_root() / "logs", data_dir() / "logs"]
    )
    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            return base
        except OSError:
            continue
    # Logging must never stop the app: setup_logging() copes with a bad path.
    return candidates[-1]


def resource_path(relative: str) -> Path:
    """Locate a bundled resource (``assets/...``) in dev and frozen builds."""
    if is_frozen():
        bundle = Path(getattr(sys, "_MEIPASS", project_root()))
        candidate = bundle / relative
        if candidate.exists():
            return candidate
    return project_root() / relative


def env_file_candidates() -> list[Path]:
    """Possible locations for the ``.env`` file, best match first."""
    candidates = [
        project_root() / ".env",
        Path.cwd() / ".env",
    ]
    if is_frozen():
        candidates.append(_local_app_data() / ".env")
    return candidates


def load_environment() -> list[Path]:
    """Load every ``.env`` found. Real environment variables always win.

    ``DEEPSEEK_API_KEY`` is deliberately *not* mirrored into ``os.environ``:
    the key is owned by :class:`app.utils.credentials.ApiKeyStore`, which reads
    the ``.env`` file itself. Otherwise a key saved (or deleted) in the GUI
    would be shadowed by a stale copy inside the process environment.
    """
    loaded: list[Path] = []
    seen: set[Path] = set()
    key_before = os.environ.get("DEEPSEEK_API_KEY")
    for path in env_file_candidates():
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        load_dotenv(path, override=False)
        loaded.append(path)
    if key_before is None and os.environ.get("DEEPSEEK_API_KEY") is not None:
        # It came from a .env file, not from the real environment.
        os.environ.pop("DEEPSEEK_API_KEY", None)
    return loaded


def normalise_thinking(value: str | None) -> str:
    """``"enabled"`` / ``"disabled"``; anything else means disabled."""
    text = (value or "").strip().lower()
    return "enabled" if text == "enabled" else "disabled"


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings. Secrets are never written to disk by the app."""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    thinking: str = "disabled"
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    max_tokens: int = DEFAULT_MAX_TOKENS
    debug: bool = False
    env_files: tuple[str, ...] = ()
    api_key_source: str = "not found"

    @classmethod
    def from_env(cls, *, key_store: "ApiKeyStore | None" = None) -> "Settings":
        loaded = load_environment()
        if key_store is None:
            from app.utils.credentials import default_api_key_store

            key_store = default_api_key_store()
        stored = key_store.load()
        api_key = stored.value
        source = stored.source_label()

        thinking = normalise_thinking(os.environ.get("DEEPSEEK_THINKING"))
        reasoning_effort = normalise_effort(os.environ.get("DEEPSEEK_REASONING_EFFORT"))

        return cls(
            api_key=api_key,
            base_url=(os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).strip(),
            model=(os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip(),
            timeout=_safe_float(os.environ.get("DEEPSEEK_TIMEOUT"), DEFAULT_TIMEOUT),
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            max_tokens=int(
                _safe_float(os.environ.get("DEEPSEEK_MAX_TOKENS"), float(DEFAULT_MAX_TOKENS))
            ),
            debug=_env_flag("NUTRITION_DEBUG"),
            env_files=tuple(str(p) for p in loaded),
            api_key_source=source,
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def masked_api_key(self) -> str:
        """A safe-to-display version of the key (never the full key)."""
        from app.utils.credentials import mask_secret

        return mask_secret(self.api_key)


def _safe_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
