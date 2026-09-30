"""Transactional single-operator capture/review state with replay-safe mutations."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .models import CaptureInput, ReviewInput


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PersonalStore:
    """Each request opens its own connection; SQLite serializes competing writers."""
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.path = directory / "state.sqlite3"
        # Create privately before sqlite opens it; journal is in the private dir.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with sqlite3.connect(self.path) as probe:
            previous_version = probe.execute('PRAGMA user_version').fetchone()[0]
            if previous_version in (1, 2, 3, 4, 5, 6):
                backup = directory / (f'state.v{previous_version}.' + uuid.uuid4().hex + '.backup.sqlite3')
                fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                os.close(fd)
                with sqlite3.connect(backup) as target:
                    probe.backup(target)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 7:
                raise RuntimeError("Personal state uses a newer schema; update Dear Diane")
            if version == 0:
                for sql in (
                    "CREATE TABLE captures (id TEXT PRIMARY KEY, owner TEXT NOT NULL, sha256 TEXT NOT NULL, body TEXT NOT NULL)",
                    "CREATE INDEX capture_hash ON captures(owner, sha256)",
                    "CREATE TABLE commitments (id TEXT PRIMARY KEY, owner TEXT NOT NULL, capture_id TEXT NOT NULL REFERENCES captures(id), revision INTEGER NOT NULL, body TEXT NOT NULL)",
                    "CREATE TABLE operations (owner TEXT NOT NULL, id TEXT NOT NULL, hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(owner,id))",
                    "CREATE TABLE business_events (cursor INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, entity_id TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL)",
                ):
                    db.execute(sql)
                db.execute("PRAGMA user_version=1")
            if version < 2:
                db.execute("CREATE TABLE extraction_jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, commitment_id TEXT NOT NULL REFERENCES commitments(id), state TEXT NOT NULL, lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT NOT NULL DEFAULT '', body TEXT NOT NULL)")
                db.execute("PRAGMA user_version=2")
            if version < 3:
                db.execute("CREATE TABLE reminders (id TEXT PRIMARY KEY, owner TEXT NOT NULL, commitment_id TEXT NOT NULL REFERENCES commitments(id), state TEXT NOT NULL, due REAL NOT NULL, body TEXT NOT NULL)")
                db.execute("CREATE INDEX reminders_due ON reminders(state,due)")
                db.execute("CREATE TABLE notifications (id TEXT PRIMARY KEY, owner TEXT NOT NULL, reminder_id TEXT NOT NULL UNIQUE REFERENCES reminders(id), body TEXT NOT NULL)")
                db.execute("PRAGMA user_version=3")
            if version < 4:
                db.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, owner TEXT NOT NULL, text TEXT NOT NULL, body TEXT NOT NULL, data BLOB NOT NULL)")
                db.execute("PRAGMA user_version=4")

            if version < 5:
                db.execute("CREATE TABLE personal_settings (owner TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)")
                db.execute("PRAGMA user_version=5")
            if version < 6:
                db.execute("CREATE TABLE conversation_jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL, lease_until REAL NOT NULL, body TEXT NOT NULL)")
                db.execute("PRAGMA user_version=6")
            if version < 7:
                db.execute("CREATE TABLE voice_operations (owner TEXT NOT NULL, id TEXT NOT NULL, hash TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(owner,id))")
                db.execute("PRAGMA user_version=7")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _operation(self, db, owner, operation_id, request, apply):
        digest = hashlib.sha256(canonical(request).encode()).hexdigest()
        previous = db.execute("SELECT hash,result FROM operations WHERE owner=? AND id=?", (owner, operation_id)).fetchone()
        if previous:
            if previous["hash"] != digest:
                raise Conflict("This operation ID was already used for different input")
            return json.loads(previous["result"])
        result = apply()
        db.execute("INSERT INTO operations VALUES (?,?,?,?)", (owner, operation_id, digest, canonical(result)))
        return result

    def capture(self, owner: str, body: CaptureInput):
        with self.connection() as db:
            def apply():
                timestamp = now()
                attachments, parts = [], [body.text] if body.text else []
                for identifier in body.source_ids:
                    source = db.execute('SELECT text,body FROM sources WHERE id=? AND owner=?', (identifier, owner)).fetchone()
                    if source is None:
                        raise ValueError('An attached source is unavailable; attach it again')
                    metadata = json.loads(source['body'])
                    if metadata.get('deleted'):
                        raise ValueError('An attached source was deleted; upload it again')
                    if metadata.get('needs_transcription'):
                        raise ValueError('An image still needs text recognition before saving')
                    attachments.append(metadata)
                    parts.append(f"[File: {metadata['name']} · source {identifier}]\n{source['text']}")
                from .intake import bounded
                text = bounded('\n\n'.join(parts))
                digest = hashlib.sha256(canonical({'text': body.text, 'sources': [item['sha256'] for item in attachments]}).encode()).hexdigest() if attachments else hashlib.sha256(text.encode()).hexdigest()
                duplicates = [row[0] for row in db.execute("SELECT id FROM captures WHERE owner=? AND sha256=? LIMIT 10", (owner, digest))]
                capture = {"id": str(uuid.uuid4()), "owner_id": owner, "schema_version": 1, "revision": 1,
                           "kind": "files" if attachments else "paste", "text": text, "attachments": attachments, "locale": body.locale, "sha256": digest,
                           "created_at": timestamp, "updated_at": timestamp, "extraction_status": "manual_review",
                           "duplicate_candidates": duplicates}
                record = {"id": str(uuid.uuid4()), "owner_id": owner, "schema_version": 1, "revision": 1,
                          "capture_id": capture["id"], "title": (body.text.splitlines()[0] if body.text else attachments[0]['name'])[:240],
                          "date": None, "time": None, "timezone": None, "all_day": False, "location": "",
                          "lifecycle": "draft", "attention": "needs-input", "task_id": None,
                          "created_at": timestamp, "updated_at": timestamp}
                db.execute("INSERT INTO captures VALUES (?,?,?,?)", (capture["id"], owner, digest, canonical(capture)))
                db.execute("INSERT INTO commitments VALUES (?,?,?,?,?)", (record["id"], owner, capture["id"], 1, canonical(record)))
                db.execute("INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)", (owner, record["id"], "captured", timestamp))
                return {"capture": capture, "commitment": record}
            return self._operation(db, owner, body.operation_id, {"action": "capture", **body.model_dump(mode="json", exclude={"source_ids"} if not body.source_ids else set())}, apply)

    def add_source(self, owner, operation_id, name, data):
        from .intake import parse_document
        metadata, text = parse_document(name, data)
        digest = hashlib.sha256(data).hexdigest()
        with self.connection() as db:
            def apply():
                result = {**metadata, 'id': str(uuid.uuid4()), 'sha256': digest, 'size': len(data), 'created_at': now()}
                db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', (result['id'], owner, text, canonical(result), data))
                return result
            return self._operation(db, owner, operation_id, {'action': 'source', 'name': metadata['name'], 'sha256': digest}, apply)

    def original_source(self, owner, record_id, source_id):
        with self.connection() as db:
            detail = self._detail(db, owner, record_id)
            if source_id not in {item['id'] for item in detail['capture'].get('attachments', [])}:
                raise KeyError(source_id)
            row = db.execute('SELECT data FROM sources WHERE owner=? AND id=?', (owner, source_id)).fetchone()
            if row is None:
                raise KeyError(source_id)
            if not row['data']:
                raise KeyError(source_id)
            return bytes(row['data']), next(item['name'] for item in detail['capture']['attachments'] if item['id'] == source_id)

    def list_sources(self, owner):
        with self.connection() as db:
            values = [json.loads(row[0]) for row in db.execute('SELECT body FROM sources WHERE owner=? ORDER BY rowid DESC', (owner,))]
            return {'sources': [value for value in values if not value.get('deleted')]}

    def delete_original(self, owner, source_id, operation_id, revision):
        with self.connection() as db:
            def apply():
                row = db.execute('SELECT body FROM sources WHERE id=? AND owner=?', (source_id, owner)).fetchone()
                if row is None:
                    raise KeyError(source_id)
                metadata = json.loads(row[0])
                if metadata.get('revision', 1) != revision:
                    raise Conflict('This source changed; reload before deleting it')
                captures = [row['id'] for row in db.execute('SELECT id,body FROM captures WHERE owner=?', (owner,)) if source_id in {item['id'] for item in json.loads(row['body']).get('attachments', [])}]
                metadata.update(revision=revision + 1, original_deleted=True, deleted=not bool(captures), retained_text=bool(captures))
                db.execute('UPDATE sources SET data=?,text=CASE WHEN ? THEN text ELSE ? END,body=? WHERE id=? AND owner=?', (b'', bool(captures), '', canonical(metadata), source_id, owner))
                for capture_id in captures:
                    for record in db.execute('SELECT id FROM commitments WHERE owner=? AND capture_id=?', (owner, capture_id)):
                        db.execute('INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)', (owner, record['id'], 'original_deleted', now()))
                return metadata
            return self._operation(db, owner, operation_id, {'action': 'delete_original', 'id': source_id, 'revision': revision}, apply)

    def transcribe_source(self, owner, source_id, operation_id, revision, text):
        from .intake import bounded
        text = bounded(text)
        with self.connection() as db:
            def apply():
                row = db.execute('SELECT body FROM sources WHERE owner=? AND id=?', (owner, source_id)).fetchone()
                if row is None:
                    raise KeyError(source_id)
                metadata = json.loads(row[0])
                if metadata.get('revision', 1) != revision or not metadata.get('needs_transcription'):
                    raise Conflict('This source already has a transcription; upload a new source to change it')
                metadata.update(needs_transcription=False, revision=revision + 1, transcription='browser_ocr_unverified')
                db.execute('UPDATE sources SET text=?,body=? WHERE owner=? AND id=?', (text, canonical(metadata), owner, source_id))
                return metadata
            return self._operation(db, owner, operation_id, {'action': 'transcribe', 'id': source_id, 'revision': revision, 'text': text}, apply)

    def snapshot(self, owner: str):
        with self.connection() as db:
            records = [json.loads(row[0]) for row in db.execute("SELECT body FROM commitments WHERE owner=? ORDER BY rowid DESC LIMIT 501", (owner,))]
            cursor = db.execute("SELECT COALESCE(MAX(cursor),0) FROM business_events WHERE owner=?", (owner,)).fetchone()[0]
            return {"commitments": records[:500], "cursor": cursor, "has_more": len(records) > 500}

    def detail(self, owner: str, record_id: str):
        with self.connection() as db:
            return self._detail(db, owner, record_id)

    def _detail(self, db, owner, record_id):
        row = db.execute("SELECT body,capture_id FROM commitments WHERE owner=? AND id=?", (owner, record_id)).fetchone()
        if not row:
            raise KeyError(record_id)
        capture = db.execute("SELECT body FROM captures WHERE owner=? AND id=?", (owner, row["capture_id"])).fetchone()
        history = [dict(item) for item in db.execute("SELECT cursor,kind,at FROM business_events WHERE owner=? AND entity_id=? ORDER BY cursor", (owner, record_id))]
        captured = json.loads(capture[0])
        for index, source in enumerate(captured.get('attachments', [])):
            current = db.execute('SELECT body FROM sources WHERE id=? AND owner=?', (source['id'], owner)).fetchone()
            if current:
                captured['attachments'][index] = json.loads(current[0])
        return {"commitment": json.loads(row["body"]), "capture": captured, "history": history}

    def review(self, owner: str, record_id: str, body: ReviewInput):
        with self.connection() as db:
            def apply():
                record = self._detail(db, owner, record_id)["commitment"]
                if record["revision"] != body.expected_revision:
                    raise Conflict("This commitment changed on another device. Reload before saving")
                if record["lifecycle"] in {"cancelled", "fulfilled"}:
                    raise Conflict("This commitment is closed")
                if record.get('attention') == 'paused':
                    raise Conflict('Resume this commitment before editing it')
                intent = (record.get('extraction', {}).get('draft') or {}).get('intent')
                if body.decision == 'confirm' and intent in {'update', 'cancellation', 'none', 'unknown'} and not body.confirm_as_new:
                    raise ValueError('This source does not describe a clear new commitment. Explicitly choose to save it as a new commitment, or leave it for review')
                values = body.model_dump(mode="json", exclude={"operation_id", "expected_revision", "decision", "confirm_as_new"})
                # Editing always returns to review; only explicit confirmation activates.
                state = "active" if body.decision == "confirm" else "cancelled" if body.decision == "dismiss" else "draft"
                record.update(**values, lifecycle=state, attention="needs-input" if state == "draft" else None,
                              revision=record["revision"] + 1, updated_at=now())
                # Changes invalidate the old timing. Rescheduling requires an explicit decision.
                db.execute("UPDATE reminders SET state='stopped' WHERE owner=? AND commitment_id=? AND state IN ('scheduled','paused')", (owner, record_id))
                if record.get('reminder') and record['reminder']['state'] in {'scheduled', 'paused'}:
                    record['reminder']['state'] = 'stopped'
                if state in {'active', 'cancelled'}:
                    stopped = db.execute("UPDATE extraction_jobs SET state='stopped',lease_until=0 WHERE owner=? AND commitment_id=? AND state IN ('queued','running')", (owner, record_id)).rowcount
                    if stopped:
                        record['extraction'] = {**record.get('extraction', {}), 'status': 'stopped'}
                if body.decision == 'confirm' and body.confirm_as_new:
                    db.execute("INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)", (owner, record_id, 'explicit_new_commitment', record['updated_at']))
                db.execute("UPDATE commitments SET revision=?,body=? WHERE owner=? AND id=?", (record["revision"], canonical(record), owner, record_id))
                db.execute("INSERT INTO business_events(owner,entity_id,kind,at) VALUES (?,?,?,?)", (owner, record_id, body.decision, record["updated_at"]))
                return record
            return self._operation(db, owner, body.operation_id, {"action": "review", "id": record_id, **body.model_dump(mode="json", exclude={"confirm_as_new"} if not body.confirm_as_new else set())}, apply)

    def events(self, owner, after, limit=100):
        with self.connection() as db:
            maximum = db.execute('SELECT COALESCE(MAX(cursor),0) FROM business_events WHERE owner=?', (owner,)).fetchone()[0]
            if after > maximum:
                return {'events': [], 'cursor': maximum, 'reset': True}
            rows = db.execute('SELECT cursor,entity_id,kind,at FROM business_events WHERE owner=? AND cursor>? ORDER BY cursor LIMIT ?', (owner, after, limit)).fetchall()
            return {'events': [dict(row) for row in rows], 'cursor': rows[-1]['cursor'] if rows else after, 'reset': False}
