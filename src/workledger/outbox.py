"""Durable metadata transport. Camera/inference adapters are intentionally separate.

An adapter persists a validated event before trying the network. A crash after the
server commits but before acknowledgment is safe: the same IDs are retried.
Only acknowledged rows are removed. This module never reads video or embeddings.
"""

import argparse
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request

from .schemas import EventInput


class Outbox:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS outbox "
                "(ordinal INTEGER PRIMARY KEY AUTOINCREMENT, "
                "id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)"
            )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def enqueue(self, event: dict):
        payload = EventInput.model_validate(event).model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True)
        with self.connect() as db:
            existing = db.execute(
                "SELECT payload FROM outbox WHERE id=?", (payload["event_id"],)
            ).fetchone()
            if existing:
                if existing[0] != serialized:
                    raise ValueError("Event ID is already associated with another payload")
                return
            db.execute(
                "INSERT INTO outbox (id,payload) VALUES (?,?)", (payload["event_id"], serialized)
            )

    def pending(self, limit=100):
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM outbox ORDER BY ordinal LIMIT ?", (min(max(limit, 1), 100),)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def flush(self, send):
        events = self.pending()
        if not events:
            return 0
        result = send({"events": events})
        accepted = result.get("accepted", [])
        duplicates = result.get("duplicates", [])
        if not isinstance(accepted, list) or not isinstance(duplicates, list):
            raise ValueError("Malformed event acknowledgment")
        acknowledged = set(accepted + duplicates)
        sent = {event["event_id"] for event in events}
        if not acknowledged <= sent:
            raise ValueError("Acknowledgment contains IDs outside the submitted batch")
        with self.connect() as db:
            db.executemany(
                "DELETE FROM outbox WHERE id=?", [(identifier,) for identifier in acknowledged]
            )
        return len(acknowledged)


def http_sender(base_url: str, token: str):
    parsed = urlsplit(base_url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if not parsed.hostname:
        raise ValueError("A server hostname is required")
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise ValueError("Event transport requires HTTPS; HTTP is allowed only on loopback")
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Supply a server origin without credentials, path, query, or fragment")
    if not token:
        raise ValueError("A site agent token is required")

    def send(payload):
        request = Request(
            base_url.rstrip("/") + "/agent/v1/events:batch",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST",
        )
        # Disable redirects: a redirect must never forward an agent credential.
        from urllib.request import HTTPRedirectHandler, build_opener

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        with build_opener(NoRedirect).open(request, timeout=15) as response:
            result = json.loads(response.read(1_000_000))
        if not isinstance(result, dict):
            raise ValueError("Malformed event acknowledgment")
        return result

    return send


def main():
    parser = argparse.ArgumentParser(description="Development metadata outbox; no camera inference")
    parser.add_argument("--db", default=".local/agent-outbox.db")
    parser.add_argument(
        "--enqueue", type=Path, help="Validated metadata event JSON file to queue locally"
    )
    parser.add_argument(
        "--flush", action="store_true", help="Attempt one batch; failures retain pending events"
    )
    args = parser.parse_args()
    outbox = Outbox(args.db)
    if args.enqueue:
        outbox.enqueue(json.loads(args.enqueue.read_text(encoding="utf-8")))
    if args.flush:
        sender = http_sender(
            os.environ.get("WORKLEDGER_SERVER", "http://127.0.0.1:8000"),
            os.environ.get("WORKLEDGER_AGENT_TOKEN", ""),
        )
        print(f"Acknowledged {outbox.flush(sender)} events")
    print(f"Pending in next batch: {len(outbox.pending())}")


if __name__ == "__main__":
    main()
