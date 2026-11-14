"""M2.3: keys live in the OS keyring or an encrypted file, never in plain text."""

from __future__ import annotations

import logging
import stat
from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail

from framestudio.vault import (
    FileSecrets,
    KeyringSecrets,
    MemorySecrets,
    SecretStoreError,
    SecretVault,
    open_vault,
)

KEY = "sk-proj-THISISASECRET0123456789abcdef"


def test_file_store_round_trip_encrypted_and_private(tmp_path: Path) -> None:
    path = tmp_path / "secrets.enc"
    store = FileSecrets(path, machine=b"machine-a")
    assert store.get("k") is None
    store.set("k", KEY)
    assert store.get("k") == KEY
    assert FileSecrets(path, machine=b"machine-a").get("k") == KEY  # a new process
    raw = path.read_bytes()
    assert KEY.encode() not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    store.delete("k")
    assert store.get("k") is None
    store.delete("k")  # already gone: fine


def test_file_store_on_another_machine_cannot_decrypt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "secrets.enc"
    FileSecrets(path, machine=b"machine-a").set("k", KEY)
    with caplog.at_level(logging.WARNING):
        assert FileSecrets(path, machine=b"machine-b").get("k") is None
    assert "could not be decrypted" in caplog.text
    assert KEY not in caplog.text


def test_file_store_survives_a_damaged_file(tmp_path: Path) -> None:
    path = tmp_path / "secrets.enc"
    path.write_text("{not json")
    store = FileSecrets(path, machine=b"m")
    assert store.get("k") is None
    store.set("k", KEY)
    assert store.get("k") == KEY


class Refusing:
    backend = "keyring"

    def get(self, name: str) -> str | None:
        return None

    def set(self, name: str, value: str) -> None:
        raise SecretStoreError("locked")

    def delete(self, name: str) -> None:
        raise SecretStoreError("locked")


def test_vault_falls_back_when_the_keyring_refuses(tmp_path: Path) -> None:
    file_store = FileSecrets(tmp_path / "s.enc", machine=b"m")
    vault = SecretVault([Refusing(), file_store])  # type: ignore[list-item]
    assert vault.preferred == "keyring"
    assert vault.put("k", KEY) == "file"
    assert vault.get("k", "file") == KEY
    assert vault.get("k", "keyring") is None
    assert vault.get("k", "nowhere") is None
    vault.delete("k", "file")
    assert vault.get("k", "file") is None


def test_vault_reports_when_no_store_takes_the_key() -> None:
    vault = SecretVault([Refusing()])  # type: ignore[list-item]
    with pytest.raises(SecretStoreError) as caught:
        vault.put("k", KEY)
    assert KEY not in str(caught.value)


class DictKeyring(KeyringBackend):
    priority = 5  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        from keyring.errors import PasswordDeleteError

        if (service, username) not in self.items:
            raise PasswordDeleteError(username)
        del self.items[(service, username)]


@pytest.fixture
def fake_keyring() -> Iterator[DictKeyring]:
    previous = keyring.get_keyring()
    backend = DictKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


def test_keyring_store(fake_keyring: DictKeyring) -> None:
    store = KeyringSecrets()
    assert KeyringSecrets.usable()
    store.set("k", KEY)
    assert fake_keyring.items == {("FrameStudio", "k"): KEY}
    assert store.get("k") == KEY
    store.delete("k")
    store.delete("k")
    assert store.get("k") is None


def test_auto_prefers_a_real_keyring(fake_keyring: DictKeyring, tmp_path: Path) -> None:
    vault = open_vault(tmp_path / "s.enc", "auto")
    assert vault.preferred == "keyring"
    assert vault.put("k", KEY) == "keyring"


def test_auto_without_a_keyring_uses_the_file(tmp_path: Path) -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        assert not KeyringSecrets.usable()
        assert open_vault(tmp_path / "s.enc", "auto").preferred == "file"
    finally:
        keyring.set_keyring(previous)


def test_explicit_modes(tmp_path: Path) -> None:
    assert open_vault(tmp_path / "s.enc", "file").preferred == "file"
    assert open_vault(tmp_path / "s.enc", "memory").preferred == "memory"
    assert open_vault(tmp_path / "s.enc", "keyring").preferred == "keyring"
    memory = MemorySecrets()
    memory.set("a", "b")
    assert memory.get("a") == "b"
