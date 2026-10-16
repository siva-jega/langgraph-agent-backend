"""
Token encryption: AES-GCM decrypt for stored tokens.

Expects encrypted_data as "hex:auth_tag" and iv as hex. Requires cryptography.
"""

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None


class TokenEncryption:
    """Decrypt token payloads (format: encrypted_hex:auth_tag_hex, iv hex)."""

    def __init__(self, encryption_key: str):
        if not encryption_key:
            raise ValueError("Encryption key is required")
        key_bytes = encryption_key.encode("utf-8")[:32]
        if len(key_bytes) < 32:
            key_bytes = key_bytes + b"\x00" * (32 - len(key_bytes))
        self._key = key_bytes

    def decrypt(self, encrypted_data: str, iv: str) -> str:
        if not AESGCM:
            raise ValueError("cryptography package is required for token encryption")
        try:
            iv_bytes = bytes.fromhex(iv)
            idx = encrypted_data.rfind(":")
            if idx == -1:
                raise ValueError("Invalid encrypted data: missing auth tag separator")
            enc_hex, tag_hex = encrypted_data[:idx], encrypted_data[idx + 1 :]
            combined = bytes.fromhex(enc_hex) + bytes.fromhex(tag_hex)
            dec = AESGCM(self._key).decrypt(iv_bytes, combined, None)
            return dec.decode("utf-8")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to decrypt token: {e}") from e
