import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config.settings import settings


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

    key = _get_key()
    aesgcm = AESGCM(key)

    # GCM: ciphertext = data + tag(appended)
    decrpyted_data = aesgcm.decrypt(iv, data + tag, None)
    return decrpyted_data.decode("utf-8")

def encrypt(plaintext:str) -> str:
    """
    Produces payload from payload produced by nodejs crypto.service.ts
    """
    key = _get_key()
    iv = os.urandom(16)
    aesgcm = AESGCM(key)
    encrypted_with_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)

    # Split: last 16 bytes = GCM auth tag
    data = encrypted_with_tag[:-16]
    tag = encrypted_with_tag[-16:]

    payload = {
        "iv": iv.hex(),
        "tag": tag.hex(),
        "data": data.hex(),
    }
    return json.dumps(payload)
