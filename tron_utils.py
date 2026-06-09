import hashlib

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(value: str) -> bytes:
    num = 0
    for char in value:
        num *= 58
        if char not in BASE58_ALPHABET:
            raise ValueError("invalid base58 character")
        num += BASE58_ALPHABET.index(char)

    raw = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(value) - len(value.lstrip("1"))
    return b"\x00" * pad + raw


def is_valid_tron_address(address: str) -> bool:
    if not isinstance(address, str):
        return False

    address = address.strip()
    if len(address) != 34 or not address.startswith("T"):
        return False

    try:
        decoded = _b58decode(address)
    except ValueError:
        return False

    if len(decoded) != 25:
        return False

    payload, checksum = decoded[:-4], decoded[-4:]
    if not payload or payload[0] != 0x41:
        return False

    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return checksum == expected
