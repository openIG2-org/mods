"""Minisign signatures (https://jedisct1.github.io/minisign/) with libsodium's Ed25519 through PyNaCl.

Signs in the pre-hashed form (`ED`: Ed25519 over the BLAKE2b-512 of the file), the one `minisign -S` writes by
default; verifies both forms. Secret keys must be unencrypted (`minisign -G -W`).
"""
import base64
import hashlib
import os
import time

import nacl.signing

UNTRUSTED_SIG = "untrusted comment: signature from openIG2 mods index key"


def _payload(text):
    """The base64 line of a key or signature file (the last line that isn't a comment)."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip() and not l.lower().startswith(("untrusted comment:", "trusted comment:"))]
    if not lines:
        raise ValueError("no key or signature line")
    return base64.b64decode(lines[0], validate=True)


def key_id_hex(key_id):
    return key_id[::-1].hex().upper()


def read_public_key(text):
    """(key id, 32-byte Ed25519 public key) from a `.pub` file's text."""
    b = _payload(text)
    if len(b) != 42 or b[:2] != b"Ed":
        raise ValueError("not a minisign Ed25519 public key")
    return b[2:10], b[10:]


def read_secret_key(text):
    """(key id, libsodium 64-byte secret key) from an unencrypted minisign secret key."""
    b = _payload(text)
    if len(b) != 158 or b[:2] != b"Ed" or b[4:6] != b"B2":
        raise ValueError("not a minisign Ed25519 secret key")
    if b[2:4] != b"\0\0":
        raise ValueError("the secret key is password-protected; CI needs an unencrypted key (minisign -G -W)")
    key_id, sk, checksum = b[54:62], b[62:126], b[126:158]
    # minisign -W leaves the checksum zero (it only computes one when encrypting); sign() still checks sk's halves.
    if checksum != bytes(32) and hashlib.blake2b(b"Ed" + key_id + sk, digest_size=32).digest() != checksum:
        raise ValueError("secret key checksum mismatch")
    return key_id, sk


def generate():
    """A new key pair as (public key text, secret key text), as `minisign -G -W` writes them."""
    signing = nacl.signing.SigningKey.generate()
    key_id = os.urandom(8)
    sk = bytes(signing) + bytes(signing.verify_key)
    checksum = hashlib.blake2b(b"Ed" + key_id + sk, digest_size=32).digest()
    secret = b"Ed" + b"\0\0" + b"B2" + bytes(32) + bytes(8) + bytes(8) + key_id + sk + checksum
    pub = b"Ed" + key_id + bytes(signing.verify_key)
    kid = key_id_hex(key_id)
    return (f"untrusted comment: minisign public key {kid}\n{base64.b64encode(pub).decode()}\n",
            f"untrusted comment: minisign encrypted secret key\n{base64.b64encode(secret).decode()}\n")


def sign(data, secret_text, trusted_comment=None):
    """The `.minisig` text for `data`."""
    key_id, sk = read_secret_key(secret_text)
    signing = nacl.signing.SigningKey(sk[:32])
    if bytes(signing.verify_key) != sk[32:]:
        raise ValueError("secret key halves don't match")
    sig = signing.sign(hashlib.blake2b(data, digest_size=64).digest()).signature
    trusted = trusted_comment or f"timestamp:{int(time.time())}"
    global_sig = signing.sign(sig + trusted.encode()).signature
    return (f"{UNTRUSTED_SIG}\n{base64.b64encode(b'ED' + key_id + sig).decode()}\n"
            f"trusted comment: {trusted}\n{base64.b64encode(global_sig).decode()}\n")


def verify(data, sig_text, public_texts):
    """The trusted comment when `sig_text` signs `data` under one of `public_texts`; raises otherwise."""
    lines = [l.strip() for l in sig_text.strip().splitlines()]
    if len(lines) != 4 or not lines[0].startswith("untrusted comment:") or not lines[2].startswith("trusted comment: "):
        raise ValueError("malformed signature file")
    b = base64.b64decode(lines[1], validate=True)
    if len(b) != 74 or b[:2] not in (b"Ed", b"ED"):
        raise ValueError("not an Ed25519 minisign signature")
    keys = dict(read_public_key(t) for t in public_texts)
    pk = keys.get(b[2:10])
    if pk is None:
        raise ValueError(f"signed by unknown key {key_id_hex(b[2:10])}")
    vk = nacl.signing.VerifyKey(pk)
    msg = hashlib.blake2b(data, digest_size=64).digest() if b[:2] == b"ED" else data
    vk.verify(msg, b[10:])
    trusted = lines[2][len("trusted comment: "):]
    vk.verify(b[10:] + trusted.encode(), base64.b64decode(lines[3], validate=True))
    return trusted
