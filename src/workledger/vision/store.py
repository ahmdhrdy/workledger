import json
import os
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet

FACE_EMBEDDING_MODEL = "adaface_ir50_ms1mv2:bgr112:127.5:v1"


class LocalStore:
    """Separate on-premise vault. Never returned by the cloud business API."""

    def __init__(self, directory, model_id=FACE_EMBEDDING_MODEL):
        self.model_id = model_id
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name == "nt" and not (self.directory / "vault.key").exists():
            import csv
            import io

            identity = subprocess.run(
                ["whoami", "/user", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            sid = next(csv.reader(io.StringIO(identity.stdout.strip())))[1]
            secured = subprocess.run(
                [
                    "icacls",
                    str(self.directory.resolve()),
                    "/inheritance:r",
                    "/grant:r",
                    f"*{sid}:(OI)(CI)F",
                    "/grant:r",
                    "*S-1-5-18:(OI)(CI)F",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if secured.returncode:
                raise RuntimeError("Unable to protect the local biometric vault directory")
        elif os.name != "nt":
            self.directory.chmod(0o700)
        key_path = self.directory / "vault.key"
        try:
            descriptor = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes())
        self.lock = threading.RLock()
        self.path = self.directory / "local.db"
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA secure_delete=ON;
                CREATE TABLE IF NOT EXISTS cameras(id TEXT PRIMARY KEY, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY, company TEXT NOT NULL,
                    employee TEXT NOT NULL, variant TEXT NOT NULL, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS observations(employee TEXT NOT NULL, camera TEXT NOT NULL,
                    at REAL NOT NULL, zone TEXT NOT NULL, method TEXT NOT NULL, score REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS obs_employee_at ON observations(employee, at);
                CREATE INDEX IF NOT EXISTS obs_camera_at ON observations(camera, at);
                CREATE TABLE IF NOT EXISTS evidence(event_id TEXT PRIMARY KEY, company TEXT NOT NULL,
                    camera TEXT NOT NULL, at REAL NOT NULL, jpeg BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS zones(camera TEXT PRIMARY KEY, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS daily_profiles(site TEXT NOT NULL, employee TEXT NOT NULL,
                    day TEXT NOT NULL, expires REAL NOT NULL, payload BLOB NOT NULL,
                    PRIMARY KEY(site,employee,day));
                CREATE TABLE IF NOT EXISTS evidence_context(event_id TEXT PRIMARY KEY,
                    expires REAL NOT NULL, payload BLOB NOT NULL);
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(observations)")}
            if "counts_work" not in columns:
                db.execute(
                    "ALTER TABLE observations ADD COLUMN counts_work INTEGER NOT NULL DEFAULT 0"
                )
            if "zone_kind" not in columns:
                db.execute(
                    "ALTER TABLE observations ADD COLUMN zone_kind TEXT NOT NULL DEFAULT 'unassigned'"
                )

    @contextmanager
    def db(self):
        with self.lock:
            connection = sqlite3.connect(self.path, timeout=10)
            connection.execute("PRAGMA secure_delete=ON")
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value).encode())

    def open(self, value):
        return json.loads(self.cipher.decrypt(value))

    def put_camera(self, identifier, value):
        with self.db() as db:
            db.execute(
                "INSERT INTO cameras VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (identifier, self.seal(value)),
            )

    def camera(self, identifier):
        with self.db() as db:
            row = db.execute("SELECT payload FROM cameras WHERE id=?", (identifier,)).fetchone()
        return self.open(row[0]) if row else None

    def camera_ids(self):
        with self.db() as db:
            return [r[0] for r in db.execute("SELECT id FROM cameras")]

    def add_template(self, identifier, company, employee, variant, vector, metadata=None):
        metadata = dict(metadata or {})
        augmentations = metadata.pop("augmentations", [])
        with self.db() as db:
            rows = db.execute(
                "SELECT id, payload FROM templates WHERE company=? AND employee=?",
                (company, employee),
            ).fetchall()
            active = [
                (row[0], value)
                for row in rows
                if isinstance(value := self.open(row[1]), dict)
                and value.get("model") == self.model_id
            ]
            if metadata and metadata.get("sha256"):
                for sample_id, value in active:
                    if value.get("metadata", {}).get("sha256") == metadata["sha256"]:
                        variants = {item["kind"]: item for item in value.get("augmentations", [])}
                        variants.update({item["kind"]: item for item in augmentations})
                        value["augmentations"] = list(variants.values())
                        db.execute(
                            "UPDATE templates SET payload=? WHERE id=?",
                            (self.seal(value), sample_id),
                        )
                        return sample_id
            count = len(active)
            if count >= 20:
                raise ValueError(
                    "Maximum 20 enrollment samples per employee; remove old samples first"
                )
            db.execute(
                "INSERT INTO templates VALUES (?,?,?,?,?)",
                (
                    identifier,
                    company,
                    employee,
                    variant,
                    self.seal(
                        {
                            "model": self.model_id,
                            "vector": vector,
                            "metadata": metadata,
                            "augmentations": augmentations,
                        }
                    ),
                ),
            )
            return identifier

    def templates(self, company, include_legacy=False):
        with self.db() as db:
            rows = db.execute(
                "SELECT id, employee, variant, payload FROM templates WHERE company=?", (company,)
            ).fetchall()
        result = []
        for row in rows:
            value = self.open(row[3])
            if isinstance(value, dict) and (include_legacy or value.get("model") == self.model_id):
                result.append(
                    {
                        "id": row[0],
                        "employee_id": row[1],
                        "variant": row[2],
                        "vector": value["vector"],
                        "source_name": value.get("metadata", {}).get("source_name"),
                        "quality": value.get("metadata", {}).get("quality"),
                        "detection_score": value.get("metadata", {}).get("detection_score"),
                        "created_at": value.get("metadata", {}).get("created_at"),
                        "has_preview": bool(value.get("metadata", {}).get("thumbnail")),
                        "model": value["model"],
                        "augmentations": [
                            {"kind": variant["kind"], "vector": variant["vector"]}
                            for variant in value.get("augmentations", [])
                        ],
                    }
                )
        return result

    def template_preview(self, company, employee, identifier, augmentation=None):
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM templates WHERE id=? AND company=? AND employee=?",
                (identifier, company, employee),
            ).fetchone()
        if row is None:
            return None
        value = self.open(row[0])
        if not isinstance(value, dict):
            return None
        if augmentation:
            return next(
                (
                    variant.get("thumbnail")
                    for variant in value.get("augmentations", [])
                    if variant["kind"] == augmentation
                ),
                None,
            )
        return value.get("metadata", {}).get("thumbnail")

    def remove_template(self, company, employee, identifier):
        with self.db() as db:
            return (
                db.execute(
                    "DELETE FROM templates WHERE id=? AND company=? AND employee=?",
                    (identifier, company, employee),
                ).rowcount
                > 0
            )

    def remove_templates(self, company, employee):
        with self.db() as db:
            db.execute("DELETE FROM templates WHERE company=? AND employee=?", (company, employee))

    def put_evidence(self, event_id, company, camera, at, jpeg):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?)",
                (event_id, company, camera, at, self.cipher.encrypt(jpeg)),
            )

    def evidence(self, event_id, company):
        with self.db() as db:
            row = db.execute(
                "SELECT jpeg FROM evidence WHERE event_id=? AND company=?", (event_id, company)
            ).fetchone()
        return self.cipher.decrypt(row[0]) if row else None

    def purge_evidence(self, cameras, before):
        if cameras:
            placeholders = ",".join("?" for _ in cameras)
            with self.db() as db:
                db.execute(
                    f"DELETE FROM evidence WHERE camera IN ({placeholders}) AND at<?",
                    (*cameras, before),
                )

    def observe(
        self, employee, camera, at, zone, method, score, counts_work=False, zone_kind="unassigned"
    ):
        with self.db() as db:
            db.execute(
                "INSERT INTO observations(employee,camera,at,zone,method,score,counts_work,zone_kind) VALUES (?,?,?,?,?,?,?,?)",
                (employee, camera, at, zone, method, score, int(counts_work), zone_kind),
            )

    def purge_observations(self, cameras, before):
        if cameras:
            placeholders = ",".join("?" for _ in cameras)
            with self.db() as db:
                db.execute(
                    f"DELETE FROM observations WHERE camera IN ({placeholders}) AND at<?",
                    (*cameras, before),
                )

    def observations(self, employee_ids, start, end):
        if not employee_ids:
            return []
        placeholders = ",".join("?" for _ in employee_ids)
        with self.db() as db:
            result = db.execute(
                f"SELECT employee,camera,at,zone,method,score,counts_work,zone_kind FROM observations WHERE employee IN ({placeholders}) AND at>=? AND at<? ORDER BY at",
                (*employee_ids, start, end),
            ).fetchall()
        return [
            dict(
                zip(
                    (
                        "employee_id",
                        "camera_id",
                        "at",
                        "zone",
                        "method",
                        "score",
                        "counts_work",
                        "zone_kind",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in result
        ]

    def zones(self, camera):
        with self.db() as db:
            row = db.execute("SELECT payload FROM zones WHERE camera=?", (camera,)).fetchone()
        return self.open(row[0]) if row else []

    def put_zones(self, camera, zones):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO zones VALUES (?,?)", (camera, self.seal(zones)))

    def purge_daily(self, at):
        with self.db() as db:
            db.execute("DELETE FROM daily_profiles WHERE expires<=?", (at,))
            db.execute("DELETE FROM evidence_context WHERE expires<=?", (at,))
        # Truncate old encrypted database pages in the WAL after daily expiry.
        with self.db() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def daily_profiles(self, site, at):
        with self.db() as db:
            rows = db.execute(
                "SELECT payload FROM daily_profiles WHERE site=? AND expires>?", (site, at)
            ).fetchall()
        return [self.open(row[0]) for row in rows]

    def put_daily_profile(self, site, employee, day, expires, payload):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO daily_profiles VALUES (?,?,?,?,?)",
                (site, employee, day, expires, self.seal(payload)),
            )

    def remove_daily_profile(self, site, employee):
        with self.db() as db:
            db.execute("DELETE FROM daily_profiles WHERE site=? AND employee=?", (site, employee))

    def put_context(self, event_id, expires, payload):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO evidence_context VALUES (?,?,?)",
                (event_id, expires, self.seal(payload)),
            )

    def context(self, event_id, at):
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM evidence_context WHERE event_id=? AND expires>?",
                (event_id, at),
            ).fetchone()
        return self.open(row[0]) if row else None
