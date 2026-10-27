"""SQLite is the commit fence; exported files are reproducible projections."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from novel_engine.core.publication_transaction import atomic_write

from .contracts import digest, dumps, generic_role, validate_review
from .timeline import validate_timeline


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "manuscript.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chapters(
                number INTEGER PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL,
                parent_hash TEXT NOT NULL, review TEXT NOT NULL, story_day INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS facts(
                entity TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                chapter INTEGER NOT NULL, PRIMARY KEY(entity,key));
            CREATE TABLE IF NOT EXISTS operations(
                id TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0,
                wall REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS plans(
                start INTEGER PRIMARY KEY, end INTEGER NOT NULL, payload TEXT NOT NULL,
                parent_hash TEXT NOT NULL, review TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exports(number INTEGER PRIMARY KEY, source_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage(run_id TEXT PRIMARY KEY, total_tokens INTEGER NOT NULL,
                                           reported_requests INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS allowances(id TEXT PRIMARY KEY, extra_calls INTEGER NOT NULL,
                                                extra_seconds INTEGER NOT NULL, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS characters(name TEXT PRIMARY KEY, first_chapter INTEGER NOT NULL,
                                                 last_chapter INTEGER NOT NULL, introduction TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chapter_actors(chapter INTEGER NOT NULL, name TEXT NOT NULL,
                                                     PRIMARY KEY(chapter,name));
            CREATE INDEX IF NOT EXISTS actor_history ON chapter_actors(name,chapter);
        """)
        indexed = self.metadata("entity_index_head") or 0
        with self.db:
            for number, raw in self.db.execute("SELECT number,payload FROM chapters WHERE number>? ORDER BY number", (indexed,)):
                self._index_chapter(number, json.loads(raw))

    def close(self):
        self.db.close()

    def metadata(self, key, value=None):
        if value is not None:
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, dumps(value)))
            return value
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def chapter(self, number):
        row = self.db.execute("SELECT payload,hash,parent_hash,review FROM chapters WHERE number=?", (number,)).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        if digest(payload) != row[1]:
            raise ValueError(f"chapter {number} content hash mismatch")
        reviewed = json.loads(row[3])
        plan = payload.get("plan") if reviewed.get("quality_contract") in (2, 3) else None
        if reviewed.get("chapter_hash") != row[1] or not validate_review(reviewed, reviewed.get("input_hash"), payload if plan else None, plan):
            raise ValueError(f"chapter {number} review integrity mismatch")
        return {"payload": payload, "hash": row[1], "parent_hash": row[2], "review": reviewed}

    def prefix(self):
        rows = self.db.execute("SELECT number,payload,hash,parent_hash,review FROM chapters ORDER BY number")
        parent = "GENESIS"
        count = 0
        previous_day = 0
        for number, payload, hashed, previous, raw_review in rows:
            if number != count + 1 or previous != parent or digest(json.loads(payload)) != hashed:
                raise ValueError("committed chapter chain is incomplete or corrupted")
            reviewed = json.loads(raw_review)
            value = json.loads(payload)
            plan = value.get("plan") if reviewed.get("quality_contract") in (2, 3) else None
            if reviewed.get("chapter_hash") != hashed or not validate_review(reviewed, reviewed.get("input_hash"), value if plan else None, plan):
                raise ValueError("committed review is missing or invalid")
            if reviewed.get("quality_contract") == 3 and validate_timeline(value, previous_day):
                raise ValueError("committed timeline is inconsistent with its predecessor")
            previous_day = value["story_day"]
            parent, count = hashed, number
        return count

    def head(self):
        count, maximum = self.db.execute("SELECT COUNT(*), COALESCE(MAX(number),0) FROM chapters").fetchone()
        if count != maximum:
            raise ValueError("committed chapter prefix contains a gap")
        return count

    def state(self):
        return {(entity, key): json.loads(value) for entity, key, value in
                self.db.execute("SELECT entity,key,value FROM facts")}

    def recent_cast(self, recent=4):
        names = set()
        for (raw,) in self.db.execute("SELECT payload FROM chapters ORDER BY number DESC LIMIT ?", (recent,)):
            for scene in json.loads(raw)["scenes"]:
                names.update(scene["cast"])
        return names

    def _index_chapter(self, number, payload):
        for scene in payload["scenes"]:
            for name in scene["cast"]:
                if generic_role(name):
                    continue
                intro = next((beat["action"] for beat in scene["beats"] if name in beat["action"]), scene["start"])
                self.db.execute("INSERT INTO characters VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET first_chapter=MIN(characters.first_chapter,excluded.first_chapter), last_chapter=MAX(characters.last_chapter,excluded.last_chapter), introduction=CASE WHEN excluded.first_chapter<characters.first_chapter THEN excluded.introduction ELSE characters.introduction END",
                                (name, number, number, intro))
                self.db.execute("INSERT OR IGNORE INTO chapter_actors VALUES(?,?)", (number, name))
        self.db.execute("INSERT INTO metadata VALUES('entity_index_head',?) ON CONFLICT(key) DO UPDATE SET value=CASE WHEN CAST(metadata.value AS INTEGER)>CAST(excluded.value AS INTEGER) THEN metadata.value ELSE excluded.value END", (dumps(number),))

    def context(self, cast=None, recent=4):
        rows = self.db.execute("SELECT number,payload FROM chapters ORDER BY number DESC LIMIT ?", (recent,)).fetchall()
        recent_chapters = []
        for number, raw in reversed(rows):
            chapter = json.loads(raw)
            recent_chapters.append({"chapter": number, "title": chapter["title"], "ending": chapter["ending"],
                                    "story_day": chapter["story_day"],
                                    "events": [beat for scene in chapter["scenes"] for beat in scene["beats"]]})
        state = self.state()
        facts = [{"entity": entity, "key": key, "value": value}
                 for (entity, key), value in state.items()
                 if cast is None or entity in cast or entity.startswith("F") or entity in ("world", "milestones", "陆烬")]
        activity = []
        for number, raw in self.db.execute("SELECT number,payload FROM chapters ORDER BY number DESC LIMIT 30"):
            for change in json.loads(raw).get("state_changes", []):
                if change["entity"].startswith("F"):
                    activity.append({"chapter": number, **change})
        registry = [{"name": name, "first_chapter": first, "last_chapter": last}
                    for name, first, last in self.db.execute("SELECT name,first_chapter,last_chapter FROM characters ORDER BY name")]
        related, introductions = [], []
        if cast:
            names = sorted(set(cast))
            placeholders = ",".join("?" for _ in names)
            introductions = [{"name": name, "first_chapter": first, "introduction": intro}
                             for name, first, intro in self.db.execute(
                                 f"SELECT name,first_chapter,introduction FROM characters WHERE name IN ({placeholders})", names)]
            cutoff = min((number for number, _ in rows), default=1)
            appearances = {name: last for name, last in self.db.execute(f"SELECT name,last_chapter FROM characters WHERE name IN ({placeholders})", names)}
            ordered = sorted(appearances, key=lambda name: (appearances[name] >= cutoff, appearances[name], name))
            selected = set()
            for name in ordered:
                selected.update(number for (number,) in self.db.execute(
                    "SELECT chapter FROM chapter_actors WHERE name=? AND chapter<? ORDER BY chapter DESC LIMIT 2", (name, cutoff)))
                if len(selected) >= 12:
                    break
            selected = sorted(selected)[:12]
            matches = self.db.execute(f"SELECT number,payload FROM chapters WHERE number IN ({','.join('?' for _ in selected)}) ORDER BY number", selected) if selected else []
            for number, raw in matches:
                value = json.loads(raw)
                related.append({"chapter": number, "ending": value["ending"],
                                "events": [beat for scene in value["scenes"] for beat in scene["beats"]]})
        return {"recent_chapters": recent_chapters, "facts": facts, "foreshadow_activity_30": activity,
                "character_registry": registry, "introductions": introductions, "related_history": related}

    def commit(self, payload, review):
        chapter = payload["chapter"]
        hashed = digest(payload)
        plan = payload.get("plan") if review.get("quality_contract") in (2, 3) else None
        if review.get("chapter_hash") != hashed or not validate_review(review, review.get("input_hash"), payload if plan else None, plan):
            raise ValueError("commit requires a passing review bound to this chapter")
        existing = self.chapter(chapter)
        if existing:
            if existing["hash"] != hashed:
                raise ValueError("cannot replace a committed chapter")
            return existing["hash"]
        with self.db:
            prefix = self.head()
            if chapter != prefix + 1:
                raise ValueError("predecessor must be committed")
            parent = self.chapter(prefix)["hash"] if prefix else "GENESIS"
            previous_day = self.chapter(prefix)["payload"]["story_day"] if prefix else 0
            if review.get("quality_contract") == 3 and validate_timeline(payload, previous_day):
                raise ValueError("cannot commit a noncontiguous timeline")
            self.db.execute("INSERT INTO chapters VALUES(?,?,?,?,?,?)",
                            (chapter, dumps(payload), hashed, parent, dumps(review), payload["story_day"]))
            for change in payload["state_changes"]:
                current = self.db.execute("SELECT value FROM facts WHERE entity=? AND key=?",
                                          (change["entity"], change["key"])).fetchone()
                before = json.loads(current[0]) if current else None
                if before != change["before"]:
                    raise ValueError("state changed before commit")
                self.db.execute("INSERT OR REPLACE INTO facts VALUES(?,?,?,?)",
                                (change["entity"], change["key"], dumps(change["after"]), chapter))
            for node_id in payload.get("plan", {}).get("milestone_ids", []):
                self.db.execute("INSERT INTO facts VALUES(?,?,?,?)", ("milestones", node_id, dumps(f"completed chapter {chapter}"), chapter))
            self._index_chapter(chapter, payload)
        return hashed

    def plan(self, start):
        row = self.db.execute("SELECT payload FROM plans WHERE start=?", (start,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_plan(self, start, end, payload, review):
        parent = self.chapter(start - 1)["hash"] if start > 1 else "GENESIS"
        with self.db:
            self.db.execute("INSERT INTO plans VALUES(?,?,?,?,?)", (start, end, dumps(payload), parent, dumps(review)))

    def update_plan(self, start, payload):
        old = self.plan(start)
        committed = self.head()
        for before, after in zip(old["chapters"], payload["chapters"], strict=True):
            if before["chapter"] <= committed and before != after:
                raise ValueError("cannot change plans for committed chapters")
        with self.db:
            self.db.execute("UPDATE plans SET payload=? WHERE start=?", (dumps(payload), start))

    def operation(self, name):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO operations(id) VALUES(?)", (name,))
        return self.db.execute("SELECT calls,wall FROM operations WHERE id=?", (name,)).fetchone()

    def charge_request(self, name):
        with self.db:
            self.db.execute("UPDATE operations SET calls=calls+1 WHERE id=?", (name,))

    def save_wall(self, name, wall):
        with self.db:
            self.db.execute("UPDATE operations SET wall=? WHERE id=?", (wall, name))

    def record_trace(self, trace):
        reported = sum(bool(event.get("usage", {}).get("total_tokens"))
                       for event in trace["events"] if event["kind"] == "provider")
        with self.db:
            self.db.execute("INSERT INTO usage VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET total_tokens=MAX(usage.total_tokens,excluded.total_tokens), reported_requests=MAX(usage.reported_requests,excluded.reported_requests)",
                            (trace["run_id"], trace["usage"].get("total_tokens", 0), reported))

    def allowance(self, name):
        row = self.db.execute("SELECT extra_calls,extra_seconds FROM allowances WHERE id=?", (name,)).fetchone()
        return row or (0, 0)

    def extend_budget(self, name, extra_calls, extra_seconds, reason):
        if not reason.strip() or not name or extra_calls < 0 or extra_seconds < 0:
            raise ValueError("budget extension needs a reason and nonnegative increments")
        calls, seconds = self.allowance(name)
        if calls + extra_calls > 16 or seconds + extra_seconds > 1800:
            raise ValueError("explicit extension exceeds the bounded recovery allowance")
        spent = self.db.execute("SELECT calls,wall FROM operations WHERE id=?", (name,)).fetchone()
        atomic_write(self.root / "audit" / "budget_extensions" / f"{uuid.uuid4().hex}.json",
                     dumps({"operation": name, "previous_extra_calls": calls, "previous_extra_seconds": seconds,
                            "added_calls": extra_calls, "added_seconds": extra_seconds,
                            "already_spent": list(spent) if spent else [0, 0], "reason": reason}))
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO allowances VALUES(?,?,?,?)",
                            (name, calls + extra_calls, seconds + extra_seconds, reason))

    def invalidate_from(self, chapter, reason):
        # This operator action archives the original database before any removal.
        if not reason.strip() or not 1 <= chapter <= self.head():
            raise ValueError("invalidation requires an existing chapter and a reason")
        archive = self.root / "invalidated" / uuid.uuid4().hex
        archive.mkdir(parents=True)
        with sqlite3.connect(archive / "manuscript_before.sqlite3") as snapshot:
            self.db.backup(snapshot)
        atomic_write(archive / "reason.json", dumps({"from_chapter": chapter, "reason": reason}))
        with self.db:
            self.db.execute("DELETE FROM chapters WHERE number>=?", (chapter,))
            self.db.execute("DELETE FROM exports WHERE number>=?", (chapter,))
            self.db.execute("DELETE FROM facts")
            self.db.execute("DELETE FROM characters")
            self.db.execute("DELETE FROM chapter_actors")
            self.db.execute("DELETE FROM metadata WHERE key='entity_index_head'")
            self.db.execute("DELETE FROM metadata WHERE key='final_audit'")
            for number, raw in self.db.execute("SELECT number,payload FROM chapters ORDER BY number"):
                payload = json.loads(raw)
                for change in payload["state_changes"]:
                    self.db.execute("INSERT OR REPLACE INTO facts VALUES(?,?,?,?)",
                                    (change["entity"], change["key"], dumps(change["after"]), number))
                for node in payload["plan"].get("milestone_ids", []):
                    self.db.execute("INSERT INTO facts VALUES(?,?,?,?)", ("milestones", node, dumps(f"completed chapter {number}"), number))
                self._index_chapter(number, payload)
        for directory in (self.root / "expansion").glob("chapter_*"):
            if int(directory.name.split("_")[-1]) >= chapter:
                target = archive / "expansion" / directory.name
                target.parent.mkdir(parents=True, exist_ok=True)
                directory.rename(target)
        return archive
