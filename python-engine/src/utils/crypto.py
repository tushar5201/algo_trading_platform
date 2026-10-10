import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config.settings import settings

_TAG_LEN = 16

def _get_key() -> bytes:
    key = settings.aes_secret_key.encode("utf-8")
    if len(key) != 32:
        raise ValueError("Invalid secret key")
    return key

def decrypt(encrypted_json:str) -> str:
    """
    Decrypts payload produced by nodejs crypto.service.ts
    payload shape: {iv: hex, tag: hex, data: hex}
    """
    payload = json.loads(encrypted_json)
    iv = bytes.fromhex(payload["iv"])
    tag = bytes.fromhex(payload["tag"])
    data = bytes.fromhex(payload["data"])

    return AESGCM(_get_key()).decrypt(iv, data + tag, None).decode("utf-8")

def encrypt(plaintext:str) -> str:
    """
    Produces payload from payload produced by nodejs crypto.service.ts
    """
    iv = os.urandom(12)
    encrypted_with_tag = AESGCM(_get_key()).encrypt(iv, plaintext.encode("utf-8"), None)

    return json.dumps({
        "iv": iv.hex(),
        "tag": encrypted_with_tag[-_TAG_LEN:].hex(),
        "data": encrypted_with_tag[:-_TAG_LEN].hex(),
    })