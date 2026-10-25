"""Bounded, transient resume evidence with stable local source locators.

The original bytes belong only to the calling process. Helper JSON deliberately
omits them; callers must never persist this document as a profile or diagnostic.
"""
import hashlib
import json
from pathlib import Path
import re
import uuid

from src.resume.importer import (
    MAX_FILE_BYTES, MAX_PDF_PAGES, MAX_TEXT_CHARS, ResumeImportError,
    _bounded_text, _cancelled, _docx_paragraphs, _helper_command,
    _pdf_page_records, _run_bounded_helper,
)

MAX_BLOCKS = 1000
MAX_BLOCK_CHARS = 2000
MAX_DOCUMENT_OUTPUT_BYTES = MAX_TEXT_CHARS * 6 + MAX_BLOCKS * 512 + 8192
_WARNINGS = frozenset(('reading_order_uncertain', 'no_extractable_text',
                      'link_annotations', 'page_locator_unavailable'))
_WIRE_KEYS = {'schema_version', 'import_id', 'fingerprint', 'kind', 'blocks', 'pages', 'warnings'}


def _new_document(kind, fingerprint, raw_bytes=None):
    return {'schema_version': 1, 'import_id': uuid.uuid4().hex,
            'fingerprint': fingerprint, 'kind': kind, 'blocks': [],
            'pages': [], 'warnings': [], 'raw_bytes': raw_bytes}


def _append_blocks(document, text, page, locator, warnings=()):
    """Keep each excerpt literal, bounded and in original parser order."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for start in range(0, len(line), MAX_BLOCK_CHARS):
            if len(document['blocks']) >= MAX_BLOCKS:
                raise ResumeImportError('Resume exceeds 1,000 source blocks; paste a shorter version.')
            excerpt = line[start:start + MAX_BLOCK_CHARS]
            order = len(document['blocks'])
            digest = hashlib.sha256(excerpt.encode('utf-8')).hexdigest()[:12]
            document['blocks'].append({'id': f'{locator}-b{order}-{digest}',
                'page': page, 'text': excerpt, 'order': order, 'warnings': list(warnings)})
    if sum(len(b['text']) for b in document['blocks']) > MAX_TEXT_CHARS:
        raise ResumeImportError('Resume exceeds 100,000 text characters; paste a shorter version.')


def document_from_text(text, kind='text'):
    if type(text) is not str or kind not in ('text', 'pdf', 'docx'):
        raise ResumeImportError('Pasted resume must be text.')
    _bounded_text(text)
    document = _new_document(kind, hashlib.sha256(text.encode('utf-8')).hexdigest())
    _append_blocks(document, text, 1, f'{kind}-p1')
    document['pages'] = [{'page': 1, 'warnings': [], 'needs_vision': False}]
    return document


def extract_document(data, kind, *, cancel=None):
    """Parse bytes in a helper, or directly only for explicitly local tests."""
    _cancelled(cancel)
    if kind not in ('pdf', 'docx') or type(data) is not bytes or len(data) > MAX_FILE_BYTES:
        raise ResumeImportError('Resume file exceeds 5 MiB or has invalid content.')
    document = _new_document(kind, hashlib.sha256(data).hexdigest(), data)
    try:
        if kind == 'docx':
            paragraphs = _docx_paragraphs(data, cancel)
            # Count whitespace too, so many empty paragraphs cannot evade limits.
            if sum(map(len, paragraphs)) + max(0, len(paragraphs) - 1) > MAX_TEXT_CHARS:
                raise ResumeImportError('Resume exceeds 100,000 text characters; paste a shorter version.')
            for index, paragraph in enumerate(paragraphs, 1):
                _cancelled(cancel)
                _append_blocks(document, paragraph, 1, f'docx-p{index}', ['page_locator_unavailable'])
            if not document['blocks']:
                raise ResumeImportError('No readable text; paste text or use a text-based file.')
            document['pages'] = [{'page': 1, 'warnings': ['page_locator_unavailable'], 'needs_vision': False}]
        else:
            records = _pdf_page_records(data, cancel)
            if not records:
                raise ResumeImportError('PDF has no pages; paste resume text instead.')
            for number, record in enumerate(records, 1):
                _cancelled(cancel)
                warnings = list(record['warnings'])
                needs_vision = not bool(record['text'].strip())
                if needs_vision:
                    warnings.append('no_extractable_text')
                # Normally preserve pypdf's joined words across font changes.
                # Positioned runs keep two columns separate when their merged
                # string has no paragraph boundary; the uncertainty is visible.
                parts = ([part[0] for part in record['parts']] if record['warnings'] else
                         [record['text']]) or [record['text']]
                for part in parts:
                    _append_blocks(document, part, number, f'pdf-p{number}', warnings)
                for link in record['links']:
                    _append_blocks(document, link, number, f'pdf-p{number}-link', ['link_annotations'])
                document['pages'].append({'page': number, 'warnings': warnings, 'needs_vision': needs_vision})
        document['warnings'] = list(dict.fromkeys(w for p in document['pages'] for w in p['warnings']))
        _cancelled(cancel)
        return document
    except ResumeImportError:
        raise
    except Exception:
        raise ResumeImportError('Unable to read this document; paste resume text instead.') from None


def validate_document(value, *, kind, fingerprint):
    """Reject malformed helper output before it enters evidence processing."""
    def refuse():
        raise ResumeImportError('Resume parser returned unsupported content; paste text instead.')

    def warnings(items):
        return type(items) is list and len(items) <= len(_WARNINGS) and all(type(w) is str and w in _WARNINGS for w in items)

    if (type(value) is not dict or set(value) != _WIRE_KEYS or type(value['schema_version']) is not int or
            value['schema_version'] != 1 or value['kind'] != kind or
            type(value['import_id']) is not str or re.fullmatch('[0-9a-f]{32}', value['import_id']) is None or
            type(value['fingerprint']) is not str or re.fullmatch('[0-9a-f]{64}', value['fingerprint']) is None or
            value['fingerprint'] != fingerprint or not warnings(value['warnings'])):
        refuse()
    pages, blocks = value['pages'], value['blocks']
    if type(pages) is not list or not 1 <= len(pages) <= MAX_PDF_PAGES or type(blocks) is not list or len(blocks) > MAX_BLOCKS:
        refuse()
    for number, page in enumerate(pages, 1):
        if (type(page) is not dict or set(page) != {'page', 'warnings', 'needs_vision'} or
                type(page['page']) is not int or page['page'] != number or
                not warnings(page['warnings']) or type(page['needs_vision']) is not bool):
            refuse()
    if kind != 'pdf' and (len(pages) != 1 or not blocks or pages[0]['needs_vision']):
        refuse()
    total, ids, last_page = 0, set(), 1
    for index, block in enumerate(blocks):
        if (type(block) is not dict or set(block) != {'id', 'page', 'text', 'order', 'warnings'} or
                type(block['id']) is not str or len(block['id']) > 128 or
                re.fullmatch('[A-Za-z0-9_-]+', block['id']) is None or block['id'] in ids or
                type(block['page']) is not int or not last_page <= block['page'] <= len(pages) or
                type(block['order']) is not int or block['order'] != index or
                type(block['text']) is not str or not block['text'].strip() or len(block['text']) > MAX_BLOCK_CHARS or
                not warnings(block['warnings'])):
            refuse()
        total += len(block['text'])
        ids.add(block['id'])
        last_page = block['page']
    if total > MAX_TEXT_CHARS:
        refuse()
    occupied_pages = {b['page'] for b in blocks if 'link_annotations' not in b['warnings']}
    if any(not p['needs_vision'] and p['page'] not in occupied_pages for p in pages):
        refuse()
    return value


def read_resume_document(path, *, cancel=None, isolated=True):
    _cancelled(cancel)
    selected = Path(path)
    kind = selected.suffix.lower().lstrip('.')
    if kind not in ('pdf', 'docx'):
        raise ResumeImportError('Choose a PDF or DOCX, or paste resume text.')
    try:
        if selected.is_symlink() or not selected.is_file() or selected.stat().st_size > MAX_FILE_BYTES:
            raise ResumeImportError('Choose a regular resume file no larger than 5 MiB.')
        with selected.open('rb') as incoming:
            data = incoming.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise ResumeImportError('Unable to read this file; paste resume text instead.') from None
    _cancelled(cancel)
    if len(data) > MAX_FILE_BYTES:
        raise ResumeImportError('Choose a regular resume file no larger than 5 MiB.')
    if not isolated:
        return extract_document(data, kind, cancel=cancel)
    args, cwd = _helper_command(kind, '--document')
    output = _run_bounded_helper(args, data, max_output_bytes=MAX_DOCUMENT_OUTPUT_BYTES,
                                 cancel=cancel, cwd=cwd)
    try:
        value = json.loads(output.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ResumeImportError('Resume parser is unavailable; paste text instead.') from None
    if type(value) is dict and set(value) == {'error'}:
        # Helper failures use fixed local messages; never echo arbitrary helper
        # strings into UI, diagnostics or exceptions in the parent process.
        raise ResumeImportError('Unable to read this document within import limits; paste text instead.')
    document = validate_document(value, kind=kind, fingerprint=hashlib.sha256(data).hexdigest())
    document['raw_bytes'] = data
    _cancelled(cancel)
    return document
