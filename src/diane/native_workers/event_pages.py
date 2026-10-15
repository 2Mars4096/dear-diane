"""Bounded append-aware byte indexes for worker event pagination."""
from collections import OrderedDict
from array import array
import json
from pathlib import Path
from threading import RLock

_lock = RLock()
_indexes = OrderedDict()


def event_page(path: Path, *, before=None, after=None, limit=100):
    limit = max(1, min(limit, 200))
    with _lock:
        try:
            with path.open('rb') as stream:
                import os
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                key = str(path)
                cached = _indexes.pop(key, None)
                if cached is None or cached['identity'] != identity or stat.st_size < cached['size'] or (stat.st_size == cached['size'] and stat.st_mtime_ns != cached['mtime']):
                    cached = dict(identity=identity, offsets=array('Q'), end=0)
                offsets = cached['offsets']
                stream.seek(cached['end'])
                while True:
                    start = stream.tell()
                    line = stream.readline()
                    if not line or not line.endswith(b'\n'):
                        break  # Retry incomplete append on the next request.
                    offsets.append(start)
                    cached['end'] = stream.tell()
                cached.update(size=stat.st_size, mtime=stat.st_mtime_ns)
                _indexes[key] = cached
                while len(_indexes) > 32:
                    _indexes.popitem(last=False)
                stop = min(len(offsets), max(0, before)) if before is not None else len(offsets)
                start = max(0, after + 1) if after is not None else 0
                indices = range(start, stop) if after is not None else range(stop - 1, -1, -1)
                page = []
                for index in indices:
                    stream.seek(offsets[index])
                    try:
                        page.append({'cursor': index, 'event': json.loads(stream.readline())})
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if len(page) == limit:
                        break
                if after is None:
                    page.reverse()
        except FileNotFoundError:
            page = []
    return {'events': [row['event'] for row in page], 'entries': page,
            'has_more': after is not None and len(page) == limit,
            'before': page[0]['cursor'] if page and page[0]['cursor'] else None}
