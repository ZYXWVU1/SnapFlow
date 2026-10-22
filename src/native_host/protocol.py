"""Native Messaging framing (Windows little endian), separate from local IPC."""
import json
import struct

from src.browser.protocol import MAX_MESSAGE_BYTES


def _exact(stream, length, *, eof=False):
    output = bytearray()
    while len(output) < length:
        part = stream.read(length - len(output))
        if not part:
            if eof and not output:
                return None
            raise ValueError('Truncated native message.')
        output.extend(part)
    return bytes(output)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate native message property.')
        result[key] = value
    return result


def read_message(stream):
    header = _exact(stream, 4, eof=True)
    if header is None:
        return None
    length = struct.unpack('<I', header)[0]
    if not 0 < length <= MAX_MESSAGE_BYTES:
        raise ValueError('Native message exceeds limit.')
    try:
        value = json.loads(_exact(stream, length).decode('utf-8'), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON constant.')))
        if type(value) is not dict:
            raise ValueError('Native message must be an object.')
        return value
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid native message encoding or depth.') from exc


def write_message(stream, message):
    try:
        body = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid native response.') from exc
    if not 0 < len(body) <= MAX_MESSAGE_BYTES:
        raise ValueError('Native response exceeds limit.')
    stream.write(struct.pack('<I', len(body)))
    stream.write(body)
    stream.flush()
