"""Storage for the DeepSeek API key.

Priority (highest first):

1. the ``DEEPSEEK_API_KEY`` environment variable (advanced users / CI),
2. **Windows Credential Manager** (the default, and where the GUI saves),
3. a ``.env`` file next to the program (fallback when the credential store is
   not available, e.g. on a non-Windows machine).

The key is never written to the SQLite database, never logged, and is only
shown in the GUI as a masked value.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from app.i18n import tr
from app.utils.logging_setup import get_logger

__all__ = [
    "ApiKeyStore",
    "CredentialError",
    "EnvFileStore",
    "SOURCE_CREDENTIAL",
    "SOURCE_ENV_FILE",
    "SOURCE_ENV_VAR",
    "SOURCE_NONE",
    "StoredKey",
    "WindowsCredentialStore",
    "default_api_key_store",
    "default_env_file",
    "mask_secret",
]

ENV_VAR_NAME = "DEEPSEEK_API_KEY"
CREDENTIAL_TARGET = "AI-Nutrition-Assistant:DeepSeekAPIKey"
CREDENTIAL_USER = "DeepSeek"

SOURCE_ENV_VAR = "env_var"
SOURCE_CREDENTIAL = "credential"
SOURCE_ENV_FILE = "env_file"
SOURCE_NONE = "none"

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168

_logger = get_logger("credentials")


class CredentialError(Exception):
    """Raised when the API key cannot be read or written."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.user_message = message
        self.detail = detail


@dataclass(frozen=True, slots=True)
class StoredKey:
    """The API key plus where it came from (never the key itself in logs)."""

    value: str = ""
    source: str = SOURCE_NONE
    location: str = ""

    @property
    def found(self) -> bool:
        return bool(self.value)

    def source_label(self) -> str:
        """Human-readable (Chinese) description of the source."""
        if self.source == SOURCE_ENV_VAR:
            return tr("settings.key_source_env_var")
        if self.source == SOURCE_CREDENTIAL:
            return tr("settings.key_source_credential")
        if self.source == SOURCE_ENV_FILE:
            return tr("settings.key_source_env_file", path=self.location or ".env")
        return tr("settings.key_source_none")


def mask_secret(secret: str) -> str:
    """Return a display-only mask such as ``sk-a********z``."""
    if not secret:
        return tr("settings.key_not_set")
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * min(24, max(4, len(secret) - 8))}{secret[-4:]}"


# --------------------------------------------------------------------------
# Windows Credential Manager
# --------------------------------------------------------------------------
class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CredentialAttribute(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(ctypes.c_byte)),
    ]


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FileTime),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_CredentialAttribute)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    """Read/write one generic credential through ``advapi32``.

    Uses ctypes only, so packaging the app adds no extra dependency.
    """

    def __init__(self, target: str = CREDENTIAL_TARGET) -> None:
        self.target = target
        self._dll: ctypes.WinDLL | None = None

    # ------------------------------------------------------------------ DLL
    def _library(self) -> "ctypes.WinDLL":
        if self._dll is None:
            advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            advapi32.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
            advapi32.CredWriteW.restype = wintypes.BOOL
            advapi32.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(_Credential)),
            ]
            advapi32.CredReadW.restype = wintypes.BOOL
            advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            advapi32.CredDeleteW.restype = wintypes.BOOL
            advapi32.CredFree.argtypes = [ctypes.c_void_p]
            advapi32.CredFree.restype = None
            self._dll = advapi32
        return self._dll

    def is_available(self) -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            self._library()
        except OSError:
            return False
        return True

    # --------------------------------------------------------------- access
    def get(self) -> str | None:
        """Return the stored key, or ``None`` when nothing is stored."""
        advapi32 = self._library()
        pointer = ctypes.POINTER(_Credential)()
        if not advapi32.CredReadW(
            self.target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            error = ctypes.get_last_error()
            if error == _ERROR_NOT_FOUND:
                return None
            raise CredentialError(
                tr("error.credential_read", detail=f"WinError {error}"),
                detail=f"CredReadW failed with {error}",
            )
        try:
            credential = pointer.contents
            size = int(credential.CredentialBlobSize)
            if size <= 0:
                return ""
            raw = ctypes.string_at(credential.CredentialBlob, size)
        finally:
            advapi32.CredFree(pointer)
        try:
            return raw.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            return raw.decode("utf-8", "replace")

    def set(self, secret: str) -> None:
        """Store (or overwrite) the key."""
        advapi32 = self._library()
        blob = secret.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(blob, len(blob))
        credential = _Credential()
        credential.Flags = 0
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = self.target
        credential.Comment = "AI Nutrition Assistant - DeepSeek API Key"
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.UserName = CREDENTIAL_USER
        if not advapi32.CredWriteW(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise CredentialError(
                tr("error.credential_write", detail=f"WinError {error}"),
                detail=f"CredWriteW failed with {error}",
            )

    def delete(self) -> None:
        """Remove the stored key (no error when it does not exist)."""
        advapi32 = self._library()
        if not advapi32.CredDeleteW(self.target, _CRED_TYPE_GENERIC, 0):
            error = ctypes.get_last_error()
            if error != _ERROR_NOT_FOUND:
                raise CredentialError(
                    tr("error.credential_delete", detail=f"WinError {error}"),
                    detail=f"CredDeleteW failed with {error}",
                )


# --------------------------------------------------------------------------
# .env fallback
# --------------------------------------------------------------------------
class EnvFileStore:
    """Read/write ``DEEPSEEK_API_KEY`` inside a ``.env`` file."""

    HEADER = (
        "# This file is written by AI Nutrition Assistant (AI 饮食助手).\n"
        "# It is used only when the Windows Credential Manager is unavailable.\n"
        "# Never commit this file to Git.\n"
    )

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get(self) -> str | None:
        if not self.path.is_file():
            return None
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            if name.strip() == ENV_VAR_NAME:
                value = value.strip().strip('"').strip("'")
                return value or None
        return None

    def set(self, secret: str) -> None:
        self._write(secret)

    def delete(self) -> None:
        self._write("")

    def _write(self, value: str) -> None:
        lines: list[str] = []
        if self.path.is_file():
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
        if not lines:
            lines = self.HEADER.splitlines()

        replaced = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, _ = stripped.partition("=")
            if name.strip() == ENV_VAR_NAME:
                lines[index] = f"{ENV_VAR_NAME}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{ENV_VAR_NAME}={value}")

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
        except OSError as exc:
            raise CredentialError(
                tr("error.env_write", path=str(self.path), detail=str(exc)),
                detail=str(exc),
            ) from exc


# --------------------------------------------------------------------------
# Facade
# --------------------------------------------------------------------------
class ApiKeyStore:
    """Chooses where the key lives and hides the details from the GUI."""

    def __init__(
        self,
        *,
        env_file: str | Path,
        credential_store: WindowsCredentialStore | None = None,
    ) -> None:
        self.env_file = EnvFileStore(env_file)
        self.credential_store = (
            credential_store if credential_store is not None else WindowsCredentialStore()
        )

    # -------------------------------------------------------------- loading
    def load(self) -> StoredKey:
        environment_value = (os.environ.get(ENV_VAR_NAME) or "").strip()
        if environment_value:
            return StoredKey(environment_value, SOURCE_ENV_VAR)

        if self.credential_store.is_available():
            try:
                stored = self.credential_store.get()
            except CredentialError as exc:
                _logger.warning("Credential Manager read failed: %s", exc.detail)
                stored = None
            if stored:
                return StoredKey(stored, SOURCE_CREDENTIAL)

        file_value = self.env_file.get()
        if file_value:
            return StoredKey(file_value, SOURCE_ENV_FILE, str(self.env_file.path))
        return StoredKey()

    # -------------------------------------------------------------- saving
    def save(self, secret: str) -> StoredKey:
        value = (secret or "").strip()
        if not value:
            raise CredentialError(tr("apikey.empty_message"))
        if self.credential_store.is_available():
            self.credential_store.set(value)
            _logger.info("API key stored in the Windows Credential Manager")
            return StoredKey(value, SOURCE_CREDENTIAL)
        self.env_file.set(value)
        _logger.info("API key stored in %s (Credential Manager unavailable)", self.env_file.path)
        return StoredKey(value, SOURCE_ENV_FILE, str(self.env_file.path))

    def delete(self) -> None:
        """Remove the key from every local store."""
        if self.credential_store.is_available():
            try:
                self.credential_store.delete()
            except CredentialError as exc:
                _logger.warning("Credential Manager delete failed: %s", exc.detail)
        self.env_file.delete()
        _logger.info("API key removed from local storage")

    # -------------------------------------------------------------- helpers
    @property
    def credential_store_available(self) -> bool:
        return self.credential_store.is_available()

    def save_location_label(self, stored: StoredKey) -> str:
        if stored.source == SOURCE_CREDENTIAL:
            return tr("settings.key_saved_credential")
        if stored.source == SOURCE_ENV_FILE:
            return tr("settings.key_saved_env_file", path=stored.location or self.env_file.path)
        return tr("settings.key_not_set")


def default_env_file() -> Path:
    """Where the ``.env`` fallback lives (next to the program)."""
    from app.utils.config import env_file_candidates

    return env_file_candidates()[0]


def default_api_key_store() -> ApiKeyStore:
    return ApiKeyStore(env_file=default_env_file())
