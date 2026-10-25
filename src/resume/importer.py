"""User-selected text-only resume import with bounded, inert parsing.

No network requests, model calls, original-file persistence or diagnostic payloads.
Candidate facts are conservative exact excerpts and always need user review.
"""
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zipfile

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 100000
MAX_PDF_PAGES = 20
MAX_DOCX_ENTRIES = 100
MAX_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_PDF_CONTENT_BYTES = 1024 * 1024
MAX_PDF_RESOURCE_NODES = 1000
MAX_PDF_RESOURCE_ITEMS = 10000
MAX_PDF_OPERATIONS = 50000
MAX_PDF_FORM_INVOCATIONS = 200
MAX_PDF_FORM_DEPTH = 8
MAX_PDF_IMAGE_PIXELS = 20000000
MAX_PDF_PAGE_IMAGE_PIXELS = 40000000


class ResumeImportError(ValueError):
    pass


class ImportCancelled(ResumeImportError):
    pass


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ImportCancelled('Resume import cancelled.')


def _bounded_text(value):
    if len(value) > MAX_TEXT_CHARS:
        raise ResumeImportError('Resume exceeds 100,000 text characters; paste a shorter version.')
    if not value.strip():
        raise ResumeImportError('No readable text; paste text or use a text-based file.')
    return value.strip()


def read_resume(path, *, cancel=None, isolated=False):
    """Read only the local path selected by the desktop picker."""
    _cancelled(cancel)
    selected = Path(path)
    kind = selected.suffix.lower().lstrip('.')
    if kind not in ('pdf', 'docx'):
        raise ResumeImportError('Choose a text PDF or DOCX, or paste resume text.')
    if selected.is_symlink() or not selected.is_file() or selected.stat().st_size > MAX_FILE_BYTES:
        raise ResumeImportError('Choose a regular resume file no larger than 5 MiB.')
    try:
        with selected.open('rb') as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        return (_extract_isolated(data, kind, cancel=cancel) if isolated
                else extract_text(data, kind, cancel=cancel))
    except OSError as exc:
        raise ResumeImportError('Unable to read this file; paste resume text instead.') from exc


def _helper_command(kind, *options):
    frozen = bool(getattr(sys, 'frozen', False))
    args = ([sys.executable, '--resume-import-helper', kind, *options] if frozen else
            [sys.executable, '-m', 'src.resume.importer', '--helper', kind, *options])
    return args, None if frozen else Path(__file__).resolve().parents[2]


def _run_bounded_helper(args, data, *, max_output_bytes, cancel=None,
                        timeout_seconds=120, cwd=None):
    """Incrementally drain bounded stdout while input and native work are killable.

    The reader retains at most the agreed limit plus one byte. A separate writer
    prevents a helper that stops consuming stdin from blocking cancellation.
    Neither subprocess arguments nor stderr contain document contents or paths.
    """
    _cancelled(cancel)
    try:
        child = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=cwd,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError:
        raise ResumeImportError('Resume parser is unavailable; paste text instead.') from None
    results = queue.Queue(maxsize=1)

    def read_output():
        output = bytearray()
        try:
            while True:
                part = child.stdout.read(min(16384, max_output_bytes + 1 - len(output)))
                if not part:
                    results.put(bytes(output))
                    return
                output.extend(part)
                if len(output) > max_output_bytes:
                    results.put(None)
                    return
        except (OSError, ValueError):
            results.put(None)

    def write_input():
        try:
            for start in range(0, len(data), 16384):
                child.stdin.write(data[start:start + 16384])
            child.stdin.close()
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=read_output, daemon=True)
    writer = threading.Thread(target=write_input, daemon=True)
    reader.start()
    writer.start()
    deadline = time.monotonic() + min(120, max(0, timeout_seconds))
    output = None
    try:
        while True:
            _cancelled(cancel)
            if time.monotonic() >= deadline:
                raise ResumeImportError('Resume import timed out; paste text instead.')
            try:
                output = results.get(timeout=min(.05, max(.001, deadline - time.monotonic())))
                if output is None:
                    raise ResumeImportError('Unable to read this document within import limits; paste text instead.')
                break
            except queue.Empty:
                continue
        while child.poll() is None:
            _cancelled(cancel)
            if time.monotonic() >= deadline:
                raise ResumeImportError('Resume import timed out; paste text instead.')
            try:
                child.wait(timeout=.05)
            except subprocess.TimeoutExpired:
                pass
        _cancelled(cancel)
        if child.returncode not in (0, 1):
            raise ResumeImportError('Resume parser is unavailable; paste text instead.')
        return output
    finally:
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
        reader.join(timeout=.5)
        writer.join(timeout=.5)
        for stream in (child.stdin, child.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def _extract_isolated(data, kind, *, cancel=None, timeout_seconds=120):
    """Keep parser work killable even inside a single pypdf/XML operation.

    Pipe only bounded bytes/text; no source path or personal data in arguments,
    temporary files or stderr. Frozen apps dispatch the same helper before Qt.
    """
    _cancelled(cancel)
    if kind not in ('pdf', 'docx') or type(data) is not bytes or len(data) > MAX_FILE_BYTES:
        raise ResumeImportError('Resume file exceeds import limits; paste text instead.')
    args, cwd = _helper_command(kind)
    output = _run_bounded_helper(args, data, max_output_bytes=MAX_TEXT_CHARS * 6 + 512,
        cancel=cancel, timeout_seconds=timeout_seconds, cwd=cwd)
    try:
        result = json.loads(output.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ResumeImportError('Resume parser is unavailable; paste text instead.') from None
    if type(result) is dict and set(result) == {'error'} and type(result['error']) is str and len(result['error']) <= 300:
        raise ResumeImportError('Unable to read this document within import limits; paste text instead.')
    if type(result) is not dict or set(result) != {'text'} or type(result['text']) is not str:
        raise ResumeImportError('Resume parser returned unsupported content; paste text instead.')
    return _bounded_text(result['text'])


def run_import_helper(kind, *, document=False, render_page=None):
    """Stdio entry for the source/frozen parser subprocess, without Qt startup."""
    if kind not in ('pdf', 'docx'):
        return 2
    # PyInstaller's windowed executable may set sys.stdin/stdout to None even
    # when the parent supplies inherited pipe handles.
    incoming = getattr(sys.stdin, 'buffer', None) or os.fdopen(0, 'rb', closefd=False)
    outgoing = getattr(sys.stdout, 'buffer', None) or os.fdopen(1, 'wb', closefd=False)
    code = 0
    try:
        data = incoming.read(MAX_FILE_BYTES + 1)
        if render_page is not None:
            from src.resume.pdf_pages import render_pdf_page
            result = render_pdf_page(data, render_page, isolated=False)
        elif document:
            from src.resume.document import extract_document
            result = extract_document(data, kind)
            result.pop('raw_bytes', None)
        else:
            result = {'text': extract_text(data, kind)}
    except ResumeImportError as exc:
        result, code = {'error': str(exc)}, 1
    except Exception:
        result, code = {'error': 'Unable to read this document; paste resume text instead.'}, 1
    outgoing.write(result if type(result) is bytes else json.dumps(result, ensure_ascii=False).encode('utf-8'))
    outgoing.flush()
    return code


def extract_text(data, kind='text', *, cancel=None):
    _cancelled(cancel)
    if kind in ('text', 'txt', 'paste'):
        if type(data) is not str:
            raise ResumeImportError('Pasted resume must be text.')
        return _bounded_text(data)
    if type(data) is not bytes or len(data) > MAX_FILE_BYTES:
        raise ResumeImportError('Resume file exceeds 5 MiB or has invalid content.')
    try:
        if kind == 'docx':
            result = _docx(data, cancel)
        elif kind == 'pdf':
            result = _pdf(data, cancel)
        else:
            raise ResumeImportError('Unsupported resume format; paste text instead.')
        _cancelled(cancel)
        return _bounded_text(result)
    except ResumeImportError:
        raise
    except Exception as exc:
        # Parser exceptions can include document fragments or local paths.
        raise ResumeImportError('Unable to read this document; paste resume text instead.') from None


def _docx(data, cancel):
    return '\n'.join(_docx_paragraphs(data, cancel))


def _docx_paragraphs(data, cancel):
    with zipfile.ZipFile(BytesIO(data)) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if (len(infos) > MAX_DOCX_ENTRIES or len(set(names)) != len(names) or
                sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES or
                any(i.flag_bits & 1 for i in infos)):
            raise ResumeImportError('DOCX archive is encrypted or exceeds import limits; paste text instead.')
        if 'word/document.xml' not in names:
            raise ResumeImportError('DOCX has no readable document; paste text instead.')
        document = None
        for info in infos:
            _cancelled(cancel)
            lower = info.filename.lower()
            if 'vbaproject' in lower or '/embeddings/' in lower:
                raise ResumeImportError('Macro or embedded-object documents are unsupported; paste text instead.')
            if lower.endswith(('.xml', '.rels')):
                raw = archive.read(info)
                # Check the decoded XML as well as bytes to catch UTF-16 declarations.
                clean = raw.replace(b'\x00', b'').upper()
                if b'<!DOCTYPE' in clean or b'<!ENTITY' in clean:
                    raise ResumeImportError('XML entity declarations are unsupported; paste text instead.')
                if info.filename == 'word/document.xml':
                    document = ET.fromstring(raw)
        namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        paragraphs = [''.join(node.itertext()) for node in document.iter(namespace + 't')]
        # DOCX runs often split a single word, so retain paragraph boundaries only.
        lines = [''.join(n.text or '' for n in p.iter(namespace + 't'))
                 for p in document.iter(namespace + 'p')]
        return lines or paragraphs


class _QuietParser(logging.Filter):
    """Do not let parser warnings put resume fragments into app diagnostics."""
    def __init__(self):
        super().__init__()
        self.thread = threading.get_ident()

    def filter(self, record):
        return record.thread != self.thread or not (record.name == 'pypdf' or record.name.startswith('pypdf.'))


class _PDFBudget:
    """Preflight the invoked form graph before pypdf constructs page text.

    Aggregate resource streams are bounded independently from encoded file size.
    Repeated forms count once for stream bytes, every time for operations/text.
    The extraction visitor separately checks decoded glyph output and cancellation.
    """
    def __init__(self, reader, cancel, *, rendering=False):
        self.reader, self.cancel = reader, cancel
        self.nodes, self.decoded, self.content_cache = set(), set(), {}
        self.resource_items = self.stream_bytes = self.operations = self.invocations = 0
        self.text_units = self.output_chars = 0
        self.rendering, self.render_images, self.image_pixels = rendering, set(), 0

    def refuse(self):
        raise ResumeImportError('PDF content exceeds safe import limits; paste resume text instead.')

    def decode(self, stream, *, content=False):
        _cancelled(self.cancel)
        data = stream.get_data()
        if content and len(data) > MAX_PDF_CONTENT_BYTES:
            self.refuse()
        key = id(stream)
        if key not in self.decoded:
            self.decoded.add(key)
            self.stream_bytes += len(data)
            if self.stream_bytes > MAX_EXPANDED_BYTES:
                self.refuse()
        return data

    def image(self, value):
        """Bound compressed-image allocation before the native decoder sees it."""
        if id(value) in self.render_images:
            return
        self.render_images.add(id(value))
        width, height = value.get('/Width', value.get('/W')), value.get('/Height', value.get('/H'))
        if (not isinstance(width, int) or isinstance(width, bool) or
                not isinstance(height, int) or isinstance(height, bool) or
                width <= 0 or height <= 0 or width * height > MAX_PDF_IMAGE_PIXELS):
            self.refuse()
        self.image_pixels += width * height
        if self.image_pixels > MAX_PDF_PAGE_IMAGE_PIXELS:
            self.refuse()

    def resources(self, value, depth=0):
        from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject
        _cancelled(self.cancel)
        if depth > 32:
            self.refuse()
        if isinstance(value, IndirectObject):
            value = value.get_object()
        if not isinstance(value, (ArrayObject, DictionaryObject)):
            return
        key = id(value)
        if key in self.nodes:
            return
        self.nodes.add(key)
        if len(self.nodes) > MAX_PDF_RESOURCE_NODES:
            self.refuse()
        self.resource_items += len(value)
        if self.resource_items > MAX_PDF_RESOURCE_ITEMS:
            self.refuse()
        if isinstance(value, StreamObject):
            if value.get('/Subtype') != '/Image':
                self.decode(value)
            elif self.rendering:
                self.image(value)
        for child in value.values() if isinstance(value, DictionaryObject) else value:
            self.resources(child, depth + 1)

    def content(self, stream, resources, depth=0, active=()):
        from pypdf.generic import ContentStream
        _cancelled(self.cancel)
        if stream is None:
            return
        if hasattr(resources, 'get_object'):
            resources = resources.get_object()
        key = id(stream)
        if depth > MAX_PDF_FORM_DEPTH or key in active:
            self.refuse()
        if key not in self.content_cache:
            self.decode(stream, content=True)
            operations = ContentStream(stream, self.reader).operations
            if len(operations) > MAX_PDF_OPERATIONS:
                self.refuse()
            self.content_cache[key] = operations
        operations = self.content_cache[key]
        self.operations += len(operations)
        if self.operations > MAX_PDF_OPERATIONS:
            self.refuse()
        for operands, operator in operations:
            _cancelled(self.cancel)
            if operator == b'INLINE IMAGE' and self.rendering:
                self.image(operands['settings'])
            if operator in (b'Tj', b"'", b'"'):
                self.text_units += len(operands[-1]) if operands else 0
            elif operator == b'TJ':
                self.text_units += sum(len(part) for part in operands[0] if isinstance(part, (str, bytes))) if operands else 0
            if self.text_units > MAX_TEXT_CHARS:
                self.refuse()
            if operator == b'Do':
                self.invocations += 1
                if self.invocations > MAX_PDF_FORM_INVOCATIONS:
                    self.refuse()
                objects = resources.get('/XObject', {})
                if hasattr(objects, 'get_object'):
                    objects = objects.get_object()
                if not operands or operands[0] not in objects:
                    self.refuse()
                obj = objects[operands[0]].get_object()
                if obj.get('/Subtype') == '/Form':
                    self.content(obj, obj.get('/Resources', resources), depth + 1, (*active, key))

    def visit_text(self, text, *_):
        _cancelled(self.cancel)
        self.output_chars += len(text)
        if self.output_chars > MAX_TEXT_CHARS:
            self.refuse()


def _pdf(data, cancel):
    return '\n'.join(record['text'] for record in _pdf_page_records(data, cancel))


def _pdf_page_records(data, cancel, *, extract=True, render_page=None):
    if not data.startswith(b'%PDF-'):
        raise ResumeImportError('Invalid PDF; paste text instead.')
    try:
        from pypdf import PdfReader, apply_configuration
    except ImportError:
        raise ResumeImportError('PDF reader is unavailable; paste resume text instead.') from None
    quiet = _QuietParser()
    loggers = [logging.getLogger('pypdf')]
    loggers.extend(logger for name, logger in list(logging.Logger.manager.loggerDict.items())
                   if name.startswith('pypdf.') and isinstance(logger, logging.Logger))
    # Logger filters do not apply to propagated descendant records. Cover root
    # handlers and lastResort too, including loggers pypdf creates lazily while
    # decoding fonts. Other threads and non-PDF diagnostics remain visible.
    handlers = {handler for logger in [logging.getLogger(), *loggers] for handler in logger.handlers}
    if logging.lastResort is not None:
        handlers.add(logging.lastResort)
    for logger in loggers:
        logger.addFilter(quiet)
    for handler in handlers:
        handler.addFilter(quiet)
    try:
        with apply_configuration(zlib_maximum_output_length=MAX_EXPANDED_BYTES,
                lzw_maximum_output_length=MAX_EXPANDED_BYTES,
                run_length_maximum_output_length=MAX_EXPANDED_BYTES,
                array_based_stream_maximum_output_length=MAX_EXPANDED_BYTES):
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ResumeImportError('Encrypted PDFs are unsupported; paste text instead.')
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ResumeImportError('PDF exceeds 20 pages; paste a shorter resume instead.')
            budget = _PDFBudget(reader, cancel)
            pages = []
            for number, page in enumerate(reader.pages, 1):
                _cancelled(cancel)
                content = page.get_contents()
                resources = page.get('/Resources', {}).get_object() if page.get('/Resources') is not None else {}
                budget.resources(resources)
                budget.content(content, resources)
                if number == render_page:
                    rendering_budget = _PDFBudget(reader, cancel, rendering=True)
                    rendering_budget.resources(resources)
                    rendering_budget.content(content, resources)
                pages.append(page)
            records, text_chars = [], 0
            for page in pages:
                _cancelled(cancel)
                parts = []
                def visitor(text, cm, tm, font, size):
                    budget.visit_text(text)
                    if text.strip():
                        # Transform text origin into page coordinates. These
                        # positions only flag uncertainty; they never reorder it.
                        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                        parts.append((text, x, y))
                text = (page.extract_text(visitor_text=visitor) or '') if extract else ''
                # A parser may catch errors from a nested form visitor. Keep the
                # document budget authoritative even if it returns partial text.
                _cancelled(cancel)
                if budget.output_chars > MAX_TEXT_CHARS:
                    budget.refuse()
                text_chars += len(text)
                if text_chars > MAX_TEXT_CHARS:
                    raise ResumeImportError('Resume exceeds 100,000 text characters; paste a shorter version.')
                links = []
                if extract:
                    from urllib.parse import urlsplit
                    annotations = page.get('/Annots', [])
                    if hasattr(annotations, 'get_object'):
                        annotations = annotations.get_object()
                    if len(annotations) > MAX_PDF_RESOURCE_NODES:
                        budget.refuse()
                    for annotation in annotations:
                        _cancelled(cancel)
                        annotation = annotation.get_object()
                        action = annotation.get('/A')
                        if hasattr(action, 'get_object'):
                            action = action.get_object()
                        if not isinstance(action, dict) or action.get('/S') != '/URI':
                            continue
                        url = action.get('/URI')
                        if isinstance(url, str) and len(url) <= 2000 and not any(ord(c) < 32 for c in url):
                            try:
                                parsed = urlsplit(url)
                                if parsed.scheme.lower() in ('http', 'https') and parsed.hostname and url not in links:
                                    links.append(url)
                            except ValueError:
                                pass
                width = float(page.mediabox.width)
                uncertain = any(abs(a[1] - b[1]) > width * .25 and abs(a[2] - b[2]) < 30
                                for i, a in enumerate(parts[:1000]) for b in parts[:i])
                records.append({'text': text, 'parts': parts, 'links': links,
                                'warnings': ['reading_order_uncertain'] if uncertain else []})
            return records
    finally:
        for logger in loggers:
            logger.removeFilter(quiet)
        for handler in handlers:
            handler.removeFilter(quiet)


def propose_facts(text):
    """Only exact, unambiguous applicant contact candidates; never infer jobs."""
    text = extract_text(text)
    from src.resume.extraction import extract_explicit_facts
    return extract_explicit_facts(text)['facts']


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--helper':
        if len(sys.argv) == 3:
            raise SystemExit(run_import_helper(sys.argv[2]))
        if len(sys.argv) == 4 and sys.argv[3] == '--document':
            raise SystemExit(run_import_helper(sys.argv[2], document=True))
        if len(sys.argv) == 5 and sys.argv[2:4] == ['pdf', '--render-page'] and sys.argv[4].isdigit():
            raise SystemExit(run_import_helper('pdf', render_page=int(sys.argv[4])))
    raise SystemExit(2)
