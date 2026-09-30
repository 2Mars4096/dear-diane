"""Bounded local document parsing. Never renders HTML, fetches URLs or opens attachments."""
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import PurePosixPath
import struct

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 65536


def bounded(text):
    text = text.strip()
    if not text:
        raise ValueError('No readable text was found. Paste a transcription instead')
    if '\0' in text or len(text.encode('utf-8')) > MAX_TEXT_BYTES:
        raise ValueError('Extracted text exceeds 64 KiB or contains null bytes; select a smaller source')
    return text


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'head'}:
            self.hidden += 1
        elif tag in {'br', 'p', 'div', 'li', 'tr'} and not self.hidden:
            self.parts.append('\n')
        elif tag == 'a' and not self.hidden:
            href = dict(attrs).get('href') or ''
            if href.lower().startswith(('https://', 'http://', 'mailto:')):
                # Preserve meeting-link evidence as text without following it.
                self.parts.append(f' [{href}] ')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'head'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def parse_document(name: str, data: bytes):
    name = PurePosixPath(name.replace('\\', '/')).name
    if not name or len(name) > 240 or any(ord(char) < 32 for char in name):
        raise ValueError('Choose a file with a plain filename under 241 characters')
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError('Choose a nonempty file no larger than 10 MiB')
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in {'.png', '.jpg', '.jpeg'}:
        width = height = 0
        if suffix == '.png' and data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24 and data[12:16] == b'IHDR':
            width, height = struct.unpack('>II', data[16:24])
            media_type = 'image/png'
        elif suffix in {'.jpg', '.jpeg'} and data.startswith(b'\xff\xd8'):
            media_type = 'image/jpeg'
            position = 2
            while position + 4 <= len(data):
                if data[position] != 255:
                    break
                marker = data[position + 1]
                if marker == 255:
                    position += 1; continue
                if marker in {0xd8, 0xd9}:
                    position += 2; continue
                size = int.from_bytes(data[position+2:position+4], 'big')
                if size < 2 or position + 2 + size > len(data):
                    break
                if marker in {0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf} and size >= 7:
                    height, width = struct.unpack('>HH', data[position+5:position+9]); break
                position += size + 2
        if not width or not height or width * height > 20_000_000:
            raise ValueError('Choose a valid PNG or JPEG no larger than 20 megapixels')
        return {'name': name, 'media_type': media_type, 'width': width, 'height': height, 'needs_transcription': True, 'revision': 1}, ''
    if suffix == '.pdf':
        if not data.startswith(b'%PDF-'):
            raise ValueError('This file does not have a PDF signature')
        try:
            import fitz
        except ImportError:
            raise ValueError('PDF reading is unavailable on this host; install Dear Diane with the pdf extra') from None
        try:
            with fitz.open(stream=data, filetype='pdf') as document:
                if document.needs_pass:
                    raise ValueError('Encrypted PDFs are not supported; upload an unlocked copy')
                if not 1 <= document.page_count <= 20:
                    raise ValueError('Choose a PDF with 1–20 pages')
                texts = []
                for number, page in enumerate(document, 1):
                    text = page.get_text().strip()
                    if not text:
                        raise ValueError(f'PDF page {number} has no readable text. Scanned pages need a transcription')
                    texts.append(f'[Page {number}]\n{text}')
                    bounded('\n\n'.join(texts))
                return {'name': name, 'media_type': 'application/pdf', 'pages': document.page_count}, bounded('\n\n'.join(texts))
        except ValueError:
            raise
        except Exception:
            raise ValueError('This PDF could not be read; try another copy') from None
    if suffix == '.eml':
        try:
            message = BytesParser(policy=policy.default).parsebytes(data)
            if not any(message.get(key) for key in ('From', 'To', 'Subject', 'Date')):
                raise ValueError('This file does not contain recognizable email headers')
            if sum(1 for _ in message.walk()) > 100:
                raise ValueError('This email contains too many MIME parts')
            body = message.get_body(preferencelist=('plain', 'html'))
            if body is None:
                raise ValueError('This email has no readable message body')
            text = body.get_content()
            if not isinstance(text, str):
                raise ValueError('This email body is not text')
            if body.get_content_type() == 'text/html':
                parser = PlainHTML(); parser.feed(text); text = ''.join(parser.parts)
            headers = '\n'.join(f'{key}: {message[key]}' for key in ('From', 'To', 'Subject', 'Date') if message.get(key))
            return {'name': name, 'media_type': 'message/rfc822'}, bounded(headers + '\n\n' + text)
        except ValueError:
            raise
        except Exception:
            raise ValueError('This email could not be read; export a standard EML file or paste its text') from None
    if suffix == '.txt':
        try:
            return {'name': name, 'media_type': 'text/plain'}, bounded(data.decode('utf-8-sig'))
        except UnicodeDecodeError:
            raise ValueError('Text files must use UTF-8 encoding') from None
    raise ValueError('Choose a text PDF, EML email, UTF-8 TXT, PNG or JPEG file')
