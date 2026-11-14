"""Where API keys live (plan step M2.3, decision D7).

Keys are held by the backend only: never in the database, never in logs,
never in an API response. Each is stored under an opaque name (the
connection row keeps the name and which store holds it):

* **keyring**: the OS credential store (Secret Service on Linux, Keychain on
  macOS, Credential Manager on Windows), through the ``keyring`` package.
* **file**: ``<data>/secrets.enc`` for headless machines and as a fallback when
  the keyring refuses. Each value is encrypted with Fernet under a key derived
  (scrypt) from the OS machine ID and a random per-install salt, the same
  approach as VoiceStudio. Copying the data folder to another machine leaves
  the keys unreadable there; they must be entered again.
* **memory**: tests only.

``FRAMESTUDIO_SECRETS`` picks one (``auto`` | ``keyring`` | ``file`` |
``memory``); ``auto`` prefers the keyring when a real one is available.
"""

from __future__ import annotations

import base64
import contextlib
import getpass
import json
import logging
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal, Protocol

log = logging.getLogger("framestudio.vault")

SecretBackend = Literal["keyring", "file", "memory"]
KEYRING_SERVICE = "FrameStudio"


class SecretStoreError(Exception):
    """A store could not save or remove a secret. Never carries the value."""


class SecretStore(Protocol):
    backend: SecretBackend

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class MemorySecrets:
    backend: SecretBackend = "memory"

    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._items.get(name)

    def set(self, name: str, value: str) -> None:
        self._items[name] = value

    def delete(self, name: str) -> None:
        self._items.pop(name, None)


class KeyringSecrets:
    backend: SecretBackend = "keyring"

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self.service = service

    @staticmethod
    def usable() -> bool:
        """Whether a real OS keyring is configured (not the fail or null one)."""
        try:
            import keyring
            from keyring.backends import fail

            current = keyring.get_keyring()
        except Exception:
            return False
        if isinstance(current, fail.Keyring):
            return False
        module = type(current).__module__
        if module.startswith(("keyring.backends.null", "keyrings.alt")):
            return False  # no-op, or a plaintext file
        return float(getattr(current, "priority", 0)) >= 1

    def get(self, name: str) -> str | None:
        import keyring

        try:
            return keyring.get_password(self.service, name)
        except Exception as exc:
            log.warning("The system keyring could not be read (%s).", type(exc).__name__)
            return None

    def set(self, name: str, value: str) -> None:
        import keyring

        try:
            keyring.set_password(self.service, name, value)
        except Exception as exc:
            raise SecretStoreError(
                f"The system keyring refused the key ({type(exc).__name__})."
            ) from None

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.service, name)
        except PasswordDeleteError:
            return
        except Exception as exc:
            raise SecretStoreError(
                f"The system keyring refused to remove the key ({type(exc).__name__})."
            ) from None


def machine_id() -> bytes:
    """A stable identifier of this machine (best effort, per OS)."""
    try:
        if sys.platform.startswith("linux"):
            for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                with contextlib.suppress(OSError):
                    value = Path(candidate).read_text(encoding="utf-8").strip()
                    if value:
                        return value.encode()
        elif sys.platform == "darwin":
            out = subprocess.run(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    value = line.split("=", 1)[-1].strip().strip('"')
                    if value:
                        return value.encode()
        elif sys.platform.startswith("win"):
            import winreg  # type: ignore[import-not-found,unused-ignore]

            with winreg.OpenKey(  # type: ignore[attr-defined,unused-ignore]
                winreg.HKEY_LOCAL_MACHINE,  # type: ignore[attr-defined,unused-ignore]
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")  # type: ignore[attr-defined,unused-ignore]
                if value:
                    return str(value).encode()
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    log.warning("No OS machine ID found; the key file is bound to host and user name instead.")
    return f"{socket.gethostname()}::{getpass.getuser()}".encode()


class FileSecrets:
    """Fernet-encrypted values in one JSON file (mode 0600)."""

    backend: SecretBackend = "file"
    SCRYPT_N = 2**14

    def __init__(self, path: Path, machine: bytes | None = None) -> None:
        self.path = path
        self._machine = machine
        self._lock = threading.Lock()
        self._fernet: object | None = None

    def _read(self) -> dict[str, object]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "items": {}}
        except (OSError, ValueError):
            log.warning("The key file %s is unreadable; starting a new one.", self.path)
            return {"version": 1, "items": {}}
        return data if isinstance(data, dict) else {"version": 1, "items": {}}

    def _write(self, data: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, self.path)

    def _cipher(self, data: dict[str, object]) -> object:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

        salt_text = data.get("salt")
        if not isinstance(salt_text, str):
            salt_text = base64.b64encode(os.urandom(16)).decode()
            data["salt"] = salt_text
            self._fernet = None
        if self._fernet is None:
            kdf = Scrypt(salt=base64.b64decode(salt_text), length=32, n=self.SCRYPT_N, r=8, p=1)
            key = kdf.derive(self._machine if self._machine is not None else machine_id())
            self._fernet = Fernet(base64.urlsafe_b64encode(key))
        return self._fernet

    def get(self, name: str) -> str | None:
        from cryptography.fernet import InvalidToken

        with self._lock:
            data = self._read()
            items = data.get("items")
            token = items.get(name) if isinstance(items, dict) else None
            if not isinstance(token, str) or "salt" not in data:
                return None
            try:
                return self._cipher(data).decrypt(token.encode()).decode()  # type: ignore[attr-defined,no-any-return]
            except InvalidToken:
                log.warning(
                    "A stored key could not be decrypted (data folder moved to another "
                    "machine?). Enter it again in Settings › Connections."
                )
                return None

    def set(self, name: str, value: str) -> None:
        with self._lock:
            data = self._read()
            cipher = self._cipher(data)
            items = data.setdefault("items", {})
            assert isinstance(items, dict)
            items[name] = cipher.encrypt(value.encode()).decode()  # type: ignore[attr-defined]
            try:
                self._write(data)
            except OSError as exc:
                raise SecretStoreError(
                    f"The key file could not be written ({exc.strerror})."
                ) from None

    def delete(self, name: str) -> None:
        with self._lock:
            data = self._read()
            items = data.get("items")
            if isinstance(items, dict) and items.pop(name, None) is not None:
                try:
                    self._write(data)
                except OSError as exc:
                    raise SecretStoreError(
                        f"The key file could not be written ({exc.strerror})."
                    ) from None


class SecretVault:
    """The stores in order of preference; a key goes to the first that takes it."""

    def __init__(self, stores: list[SecretStore]) -> None:
        if not stores:
            raise ValueError("at least one secret store is needed")
        self._stores: dict[str, SecretStore] = {store.backend: store for store in stores}
        self._order = [store.backend for store in stores]

    @property
    def preferred(self) -> SecretBackend:
        return self._order[0]  # type: ignore[return-value]

    def put(self, name: str, value: str) -> SecretBackend:
        """Store ``value``; returns the backend that holds it."""
        problems: list[str] = []
        for backend in self._order:
            try:
                self._stores[backend].set(name, value)
                return backend  # type: ignore[return-value]
            except SecretStoreError as exc:
                problems.append(str(exc))
                log.warning("Secret store %s failed; trying the next one.", backend)
        raise SecretStoreError(" ".join(problems))

    def get(self, name: str, backend: str) -> str | None:
        store = self._stores.get(backend)
        return store.get(name) if store else None

    def delete(self, name: str, backend: str) -> None:
        store = self._stores.get(backend)
        if store is not None:
            store.delete(name)


def open_vault(key_file: Path, mode: str = "auto") -> SecretVault:
    if mode == "memory":
        return SecretVault([MemorySecrets()])
    if mode == "file":
        return SecretVault([FileSecrets(key_file)])
    if mode == "keyring":
        return SecretVault([KeyringSecrets()])
    if KeyringSecrets.usable():
        return SecretVault([KeyringSecrets(), FileSecrets(key_file)])
    return SecretVault([FileSecrets(key_file)])
