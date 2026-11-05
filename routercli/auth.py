"""Enable-secret hashing (scrypt, constant-time verification)."""

import hashlib
import hmac
import os

N, R, P = 2 ** 14, 8, 1


def hash_secret(secret: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(secret.encode(), salt=salt, n=N, r=R, p=P, dklen=64)
    return f"scrypt${N}${R}${P}${salt.hex()}${dk.hex()}"


def verify_secret(secret: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(secret.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(dk) // 2)
        return hmac.compare_digest(calc.hex(), dk)
    except (ValueError, TypeError):
        return False


# Cisco "type 7" password obfuscation (as used by 'ppp chap password 7 ...').
# It is NOT encryption - it only keeps passwords from being read over a shoulder
# in 'show running-config'. Supported so IOS configurations can be pasted as-is.
_XLAT = "dsfd;kfoA,.iyewrkldJKDHSUBsgvca69834ncxv9873254k;fg87"


def type7_encode(text: str, seed: int = None) -> str:
    if seed is None:
        seed = sum(text.encode()) % 16
    return f"{seed:02d}" + "".join(f"{ord(c) ^ ord(_XLAT[(seed + i) % len(_XLAT)]):02X}"
                                   for i, c in enumerate(text))


def type7_decode(enc: str) -> str:
    """Raise ValueError for malformed input."""
    if len(enc) < 2 or len(enc) % 2 or not enc[:2].isdigit():
        raise ValueError("malformed type 7 string")
    seed = int(enc[:2])
    if seed > 15:
        raise ValueError("malformed type 7 string")
    out = []
    for i in range(2, len(enc), 2):
        out.append(chr(int(enc[i:i + 2], 16) ^ ord(_XLAT[(seed + i // 2 - 1) % len(_XLAT)])))
    return "".join(out)
