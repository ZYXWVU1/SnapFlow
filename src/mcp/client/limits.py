"""Bound transport bytes before SDK parsing, using its public encoding/hooks."""
import codecs

WIRE_LIMIT = 1048576
STDIO_ENCODING = 'snapflow_bounded_utf8'


class WireLimitError(ValueError):
    pass


def _line_budget(data, pending=0):
    start = 0
    while True:
        end = data.find(b'\n', start)
        length = (len(data) if end < 0 else end) - start
        pending += length
        if pending > WIRE_LIMIT:
            raise WireLimitError('MCP message exceeds the transport size limit.')
        if end < 0:
            return pending
        pending, start = 0, end + 1


class _BoundedUTF8Decoder(codecs.IncrementalDecoder):
    def __init__(self, errors='strict'):
        super().__init__(errors)
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors)
        self.pending = 0

    def decode(self, data, final=False):
        self.pending = _line_budget(data, self.pending)
        return self.decoder.decode(data, final)

    def reset(self):
        self.decoder.reset()
        self.pending = 0

    def getstate(self):
        return self.decoder.getstate()[0], self.pending

    def setstate(self, state):
        self.decoder.setstate((state[0], 0))
        self.pending = state[1]


def _encode(text, errors='strict'):
    result = codecs.utf_8_encode(text, errors)
    _line_budget(result[0])
    return result


def _decode(data, errors='strict'):
    if isinstance(data, memoryview):
        data = data.tobytes()
    return _BoundedUTF8Decoder(errors).decode(data, final=True), len(data)


def _codec(name):
    if name == STDIO_ENCODING:
        return codecs.CodecInfo(name=STDIO_ENCODING, encode=_encode, decode=_decode,
            incrementalencoder=codecs.getincrementalencoder('utf-8'),
            incrementaldecoder=_BoundedUTF8Decoder)


codecs.register(_codec)


class _SSEBudget:
    def __init__(self):
        self.size = 0
        self.line_has_data = False
        self.previous_cr = False

    def consume(self, data):
        # CR, LF and split CRLF all delimit SSE lines. Only a blank line ends
        # the event; many small data/comment lines cannot bypass this bound.
        for value in data:
            if value == 10 and self.previous_cr:
                if self.size:
                    self.size += 1
                    if self.size > WIRE_LIMIT:
                        raise WireLimitError('MCP event exceeds the transport size limit.')
                self.previous_cr = False
                continue
            self.size += 1
            if self.size > WIRE_LIMIT:
                raise WireLimitError('MCP event exceeds the transport size limit.')
            if value in (10, 13):
                if not self.line_has_data:
                    self.size = 0
                self.line_has_data = False
            else:
                self.line_has_data = True
            self.previous_cr = value == 13


async def bounded_http_response(response):
    import httpx2
    # Reject compressed bodies before the HTTP decoder can allocate a bomb.
    # Requests explicitly negotiate identity, including OAuth subrequests.
    if response.headers.get('content-encoding', 'identity').strip().lower() != 'identity':
        raise WireLimitError('Compressed MCP responses are unsupported.')
    sse = response.headers.get('content-type', '').partition(';')[0].strip().lower() == 'text/event-stream'
    if not sse and 'content-length' in response.headers:
        try:
            length = int(response.headers['content-length'])
        except ValueError:
            raise WireLimitError('Invalid MCP response length.') from None
        if not 0 <= length <= WIRE_LIMIT:
            raise WireLimitError('MCP response exceeds the transport size limit.')
    source = response.stream

    class BoundedStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            budget = _SSEBudget() if sse else None
            size = 0
            async for chunk in source:
                if budget is not None:
                    budget.consume(chunk)
                else:
                    size += len(chunk)
                    if size > WIRE_LIMIT:
                        raise WireLimitError('MCP response exceeds the transport size limit.')
                yield chunk

        async def aclose(self):
            await source.aclose()

    response.stream = BoundedStream()
