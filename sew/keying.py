import hashlib
import hmac


def site_message(sid) -> bytes:
    return "|".join(str(x) for x in sid).encode()


def target_bit(key: bytes, sid) -> int:
    return hmac.new(key, site_message(sid), hashlib.sha256).digest()[0] & 1
