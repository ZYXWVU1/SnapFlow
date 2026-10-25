"""Killable PDFium rendering into a bounded in-memory PNG; no forms or files."""
import math
import struct
import zlib

from src.resume.importer import (MAX_FILE_BYTES, MAX_PDF_PAGES, ResumeImportError,
    _cancelled, _helper_command, _pdf_page_records, _run_bounded_helper)

MAX_RENDER_EDGE = 2000
MAX_RENDER_PIXELS = 4000000
MAX_RENDER_BYTES = 4 * 1024 * 1024


def _png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def _png_from_bitmap(bitmap, cancel):
    width, height = bitmap.width, bitmap.height
    if not 0 < width <= MAX_RENDER_EDGE or not 0 < height <= MAX_RENDER_EDGE or width * height > MAX_RENDER_PIXELS:
        raise ResumeImportError('PDF page exceeds safe rendering limits; paste text instead.')
    compressor, compressed = zlib.compressobj(), bytearray()
    pixels = memoryview(bitmap.buffer).cast('B')
    for row in range(height):
        _cancelled(cancel)
        start = row * bitmap.stride
        compressed.extend(compressor.compress(b'\x00' + pixels[start:start + width * 3].tobytes()))
        if len(compressed) > MAX_RENDER_BYTES - 128:
            raise ResumeImportError('PDF page image exceeds 4 MiB; paste text instead.')
    compressed.extend(compressor.flush())
    if len(compressed) > MAX_RENDER_BYTES - 128:
        raise ResumeImportError('PDF page image exceeds 4 MiB; paste text instead.')
    return (b'\x89PNG\r\n\x1a\n' + _png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            _png_chunk(b'IDAT', bytes(compressed)) + _png_chunk(b'IEND', b''))


def _validate_png(data):
    def refuse():
        raise ResumeImportError('PDF renderer returned unsupported content; paste text instead.')

    if (type(data) is not bytes or not 45 <= len(data) <= MAX_RENDER_BYTES or
            not data.startswith(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR')):
        refuse()
    width, height = struct.unpack('>II', data[16:24])
    if not 0 < width <= MAX_RENDER_EDGE or not 0 < height <= MAX_RENDER_EDGE or width * height > MAX_RENDER_PIXELS:
        refuse()
    # This helper emits exactly RGB IHDR, one IDAT, IEND. Validate the full
    # envelope and decoded length, so a truncated image cannot reach the model.
    offset, chunks = 8, []
    for expected in (b'IHDR', b'IDAT', b'IEND'):
        if offset + 12 > len(data):
            refuse()
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(data) or data[offset + 4:offset + 8] != expected:
            refuse()
        chunk = data[offset + 8:end - 4]
        checksum = struct.unpack('>I', data[end - 4:end])[0]
        if zlib.crc32(expected + chunk) & 0xffffffff != checksum:
            refuse()
        chunks.append(chunk)
        offset = end
    if (offset != len(data) or chunks[0] != struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0) or chunks[2]):
        refuse()
    expected_bytes = (width * 3 + 1) * height
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(chunks[1], expected_bytes + 1)
        if (len(pixels) != expected_bytes or not decoder.eof or decoder.unused_data or
                decoder.unconsumed_tail or any(pixels[y * (width * 3 + 1)] != 0 for y in range(height))):
            refuse()
    except zlib.error:
        refuse()
    return data


def render_pdf_page(data, page, *, cancel=None, isolated=True):
    """Render one 1-based page. Callers keep at most two returned images in RAM."""
    _cancelled(cancel)
    if (type(data) is not bytes or len(data) > MAX_FILE_BYTES or not data.startswith(b'%PDF-') or
            type(page) is not int or not 1 <= page <= MAX_PDF_PAGES):
        raise ResumeImportError('Invalid PDF page or document exceeds import limits; paste text instead.')
    if isolated:
        args, cwd = _helper_command('pdf', '--render-page', str(page))
        return _validate_png(_run_bounded_helper(args, data, max_output_bytes=MAX_RENDER_BYTES,
                                               cancel=cancel, cwd=cwd))
    try:
        records = _pdf_page_records(data, cancel, extract=False, render_page=page)
        if page > len(records):
            raise ResumeImportError('PDF page is unavailable; paste text instead.')
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
        # No form environment, JavaScript callbacks, annotation rendering,
        # external-resource loaders, or persistence are installed.
        with pdfium.PdfDocument(data) as document:
            source = document[page - 1]
            try:
                width, height = source.get_size()
                if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                    raise ResumeImportError('Invalid PDF page size; paste text instead.')
                scale = min(2.0, (MAX_RENDER_EDGE - 1) / max(width, height),
                            math.sqrt((MAX_RENDER_PIXELS - 4000) / (width * height)))
                _cancelled(cancel)
                bitmap = source.render(scale=scale, may_draw_forms=False, draw_annots=False,
                        limit_image_cache=True, rev_byteorder=True,
                        force_bitmap_format=raw.FPDFBitmap_BGR)
                try:
                    return _validate_png(_png_from_bitmap(bitmap, cancel))
                finally:
                    bitmap.close()
            finally:
                source.close()
    except ResumeImportError:
        raise
    except Exception:
        raise ResumeImportError('Unable to render this PDF page; paste text instead.') from None
