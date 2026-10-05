"""Envelope encryption for mailbox credentials.

A refresh token is the key to a client's support inbox. It is encrypted with
AES-256-GCM before it reaches the database, under a key held in Secret Manager
(C02 `mailbox-token-encryption-key`), never in configuration or source.

Three properties matter more than the cipher choice:

1. **Binding.** The ciphertext's associated data names the organization, the
   mailbox, and the purpose. Decrypting a token under any other mailbox fails
   authentication. A row copied from mailbox A onto mailbox B cannot be used to
   read B - and cannot be used to read A through B either.

2. **Rotation.** Every ciphertext records the id of the key that produced it.
   A keyring may hold several keys; new writes use the active one and old rows
   remain readable until re-encrypted.

3. **No silent fallback.** An unknown key id, a malformed envelope, or a failed
   tag is an error, never an empty string or a retry with another key.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_VERSION = "v1"
NONCE_BYTES = 12
KEY_BYTES = 32
KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class VaultError(Exception):
    """Credential material could not be sealed or opened. Never retried silently."""


@dataclass(frozen=True)
class SealedCredential:
    """What is stored. Contains no plaintext and no key material."""

    key_id: str
    envelope: str


def _aad(organization_id: str, mailbox_id: str, purpose: str) -> bytes:
    if not organization_id or not mailbox_id or not purpose:
        raise VaultError("Credential binding requires organization, mailbox, and purpose.")
    return f"resolveflow:{ENVELOPE_VERSION}:{purpose}:{organization_id}:{mailbox_id}".encode()


class TokenVault:
    """AES-256-GCM keyring. Keys are supplied by the caller, never generated here."""

    def __init__(self, keys: dict[str, bytes], active_key_id: str):
        if not keys:
            raise VaultError("The vault requires at least one key.")
        for key_id, material in keys.items():
            if not KEY_ID_PATTERN.fullmatch(key_id):
                raise VaultError(f"Key id {key_id!r} is not a valid identifier.")
            if len(material) != KEY_BYTES:
                raise VaultError(
                    f"Key {key_id!r} is {len(material)} bytes; AES-256 requires {KEY_BYTES}."
                )
        if active_key_id not in keys:
            raise VaultError(f"Active key {active_key_id!r} is not in the keyring.")
        self._keys = dict(keys)
        self._active = active_key_id

    @property
    def active_key_id(self) -> str:
        return self._active

    def seal(
        self, plaintext: str, *, organization_id: str, mailbox_id: str, purpose: str
    ) -> SealedCredential:
        if not plaintext:
            raise VaultError("Refusing to seal an empty credential.")
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self._keys[self._active]).encrypt(
            nonce, plaintext.encode("utf-8"), _aad(organization_id, mailbox_id, purpose)
        )
        encoded = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return SealedCredential(key_id=self._active, envelope=f"{ENVELOPE_VERSION}.{encoded}")

    def open(
        self,
        sealed: SealedCredential,
        *,
        organization_id: str,
        mailbox_id: str,
        purpose: str,
    ) -> str:
        key = self._keys.get(sealed.key_id)
        if key is None:
            raise VaultError(
                f"Credential was sealed with key {sealed.key_id!r}, which is not in the keyring."
            )

        version, _, encoded = sealed.envelope.partition(".")
        if version != ENVELOPE_VERSION or not encoded:
            raise VaultError("Credential envelope is malformed or from an unknown version.")

        try:
            raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (binascii.Error, ValueError) as error:
            raise VaultError("Credential envelope is not valid base64.") from error
        if len(raw) <= NONCE_BYTES:
            raise VaultError("Credential envelope is truncated.")

        try:
            plaintext = AESGCM(key).decrypt(
                raw[:NONCE_BYTES],
                raw[NONCE_BYTES:],
                _aad(organization_id, mailbox_id, purpose),
            )
        except InvalidTag as error:
            # Deliberately does not distinguish "wrong mailbox" from "tampered":
            # both mean this ciphertext must not be used here.
            raise VaultError(
                "Credential failed authentication for this mailbox. It was sealed for a "
                "different mailbox or has been altered."
            ) from error
        return plaintext.decode("utf-8")

    def needs_rotation(self, sealed: SealedCredential) -> bool:
        return sealed.key_id != self._active

    def reseal(
        self, sealed: SealedCredential, *, organization_id: str, mailbox_id: str, purpose: str
    ) -> SealedCredential:
        plaintext = self.open(
            sealed, organization_id=organization_id, mailbox_id=mailbox_id, purpose=purpose
        )
        return self.seal(
            plaintext, organization_id=organization_id, mailbox_id=mailbox_id, purpose=purpose
        )


def vault_from_environment(environ: dict[str, str] | None = None) -> TokenVault:
    """Build the keyring from `MAILBOX_TOKEN_ENCRYPTION_KEY`.

    Format: `key-id:base64key[,key-id:base64key...]`. The first entry is active.
    On Cloud Run the value is injected from Secret Manager (C02); it never
    appears in source, Terraform, or logs.
    """
    source = os.environ if environ is None else environ
    raw = (source.get("MAILBOX_TOKEN_ENCRYPTION_KEY") or "").strip()
    if not raw:
        raise VaultError(
            "MAILBOX_TOKEN_ENCRYPTION_KEY is not configured. Mailbox connection is "
            "disabled until an operator supplies it from Secret Manager."
        )

    keys: dict[str, bytes] = {}
    order: list[str] = []
    for entry in raw.split(","):
        key_id, separator, encoded = entry.strip().partition(":")
        if not separator:
            raise VaultError("Each keyring entry must be 'key-id:base64key'.")
        try:
            material = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise VaultError(f"Key {key_id!r} is not valid base64.") from error
        if key_id in keys:
            raise VaultError(f"Key id {key_id!r} appears twice in the keyring.")
        keys[key_id] = material
        order.append(key_id)

    return TokenVault(keys, active_key_id=order[0])
