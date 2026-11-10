import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from getpass import getpass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DB_PATH = Path(os.environ.get("CAMPUS_FIND_DB", ROOT / "campus_find.sqlite3"))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
CAMPUS_NAME = "Chinhoyi University Of Technology"
MAX_BODY_SIZE = 64 * 1024
PASSWORD_ITERATIONS = 310_000
STOP_WORDS = {
    "a", "an", "and", "are", "at", "be", "been", "by", "for", "from",
    "has", "have", "i", "in", "is", "it", "its", "of", "on", "or", "the",
    "this", "to", "was", "with", "you", "your",
}
SCHEMA_LOCK = threading.Lock()


class ForbiddenError(Exception):
    pass


@contextmanager
def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_db():
    with SCHEMA_LOCK, connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                university TEXT NOT NULL,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN ('lost', 'found')),
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                location TEXT NOT NULL,
                happened_on TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY,
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                requester_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'declined')),
                created_at TEXT NOT NULL,
                UNIQUE(item_id, requester_id)
            );
            CREATE TABLE IF NOT EXISTS handovers (
                id INTEGER PRIMARY KEY,
                item_id INTEGER NOT NULL UNIQUE REFERENCES items(id) ON DELETE RESTRICT,
                recipient_id TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                recorded_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                recorded_by_name TEXT NOT NULL,
                recorded_by_email TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS items_status_kind_idx ON items(status, kind);
            CREATE INDEX IF NOT EXISTS claims_item_status_idx ON claims(item_id, status);
            CREATE INDEX IF NOT EXISTS handovers_collected_at_idx ON handovers(collected_at DESC);
            """
        )
        db.execute("UPDATE users SET university = ?", (CAMPUS_NAME,))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def manager_emails():
    return {
        email.strip().lower()
        for email in os.environ.get("CUT_MANAGER_EMAILS", "").split(",")
        if email.strip()
    }


def clean_text(value, field, minimum, maximum):
    if not isinstance(value, str):
        raise ValueError(f"{field} is required.")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum} characters.")
    return value


def tokenize(text):
    return [
        word for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) > 1 and word not in STOP_WORDS
    ]


def match_score(first, second, document_frequency, document_count):
    first_words = Counter(tokenize(f"{first['title']} {first['description']}"))
    second_words = Counter(tokenize(f"{second['title']} {second['description']}"))
    shared = first_words.keys() & second_words.keys()

    def vector(words):
        return {
            word: (1 + math.log(count)) *
            (math.log((document_count + 1) / (document_frequency[word] + 1)) + 1)
            for word, count in words.items()
        }

    first_vector = vector(first_words)
    second_vector = vector(second_words)
    dot = sum(first_vector[word] * second_vector[word] for word in shared)
    first_norm = math.sqrt(sum(weight * weight for weight in first_vector.values()))
    second_norm = math.sqrt(sum(weight * weight for weight in second_vector.values()))
    lexical = dot / (first_norm * second_norm) if first_norm and second_norm else 0

    category_match = first["category"] == second["category"]
    first_location = set(tokenize(first["location"]))
    second_location = set(tokenize(second["location"]))
    location_match = bool(first_location & second_location)
    try:
        days_apart = abs(
            (datetime.fromisoformat(first["happened_on"]) -
             datetime.fromisoformat(second["happened_on"])).days
        )
    except ValueError:
        days_apart = 365
    date_score = max(0, 5 - min(days_apart, 30) / 6)
    score = min(100, round(lexical * 55 + category_match * 22 + location_match * 18 + date_score))

    reasons = []
    if category_match:
        reasons.append(f"Same category: {first['category']}")
    if location_match:
        reasons.append("Similar campus location")
    if shared:
        reasons.append("Shared details: " + ", ".join(sorted(shared)[:3]))
    if days_apart <= 7:
        reasons.append("Reported within a week")
    return score, reasons


class CampusFindHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request body.") from exc
        if length <= 0 or length > MAX_BODY_SIZE:
            raise ValueError("Request body must be between 1 byte and 64 KB.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def current_user(self, required=True):
        authorization = self.headers.get("Authorization", "")
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            if required:
                raise PermissionError("Please sign in to continue.")
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with connect_db() as db:
            row = db.execute(
                """
                SELECT users.id, users.name, users.email, users.university
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (token_hash, utc_now()),
            ).fetchone()
        if not row and required:
            raise PermissionError("Your session has expired. Please sign in again.")
        if not row:
            return None
        user = dict(row)
        user["is_manager"] = user["email"].lower() in manager_emails()
        return user

    def require_manager(self, user):
        if user["email"].lower() not in manager_emails():
            raise ForbiddenError("This action is restricted to authorised CUT managers.")

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
        with connect_db() as db:
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )
        return token

    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return super().do_GET()
        try:
            if parsed.path == "/api/health":
                return self.send_json(200, {"ok": True})
            user = self.current_user()
            if parsed.path == "/api/me":
                return self.send_json(200, {"user": user})
            if parsed.path == "/api/items":
                return self.list_items(user, parse_qs(parsed.query))
            if parsed.path == "/api/matches":
                return self.list_matches(user)
            if parsed.path == "/api/claims":
                return self.list_claims(user)
            if parsed.path == "/api/management/handovers":
                self.require_manager(user)
                return self.list_handovers()
            return self.send_json(404, {"error": "That endpoint was not found."})
        except PermissionError as exc:
            return self.send_json(401, {"error": str(exc)})
        except ForbiddenError as exc:
            return self.send_json(403, {"error": str(exc)})
        except Exception:
            self.log_error("GET request failed for %s", parsed.path)
            return self.send_json(500, {"error": "Something went wrong. Please try again."})

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/auth/register":
                return self.register(payload)
            if parsed.path == "/api/auth/login":
                return self.login(payload)
            if parsed.path == "/api/auth/logout":
                user = self.current_user()
                token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                with connect_db() as db:
                    db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return self.send_json(200, {"ok": True})
            user = self.current_user()
            if parsed.path == "/api/items":
                return self.create_item(user, payload)
            if parsed.path == "/api/management/handovers":
                self.require_manager(user)
                return self.create_handover(user, payload)
            match = re.fullmatch(r"/api/items/(\d+)/claims", parsed.path)
            if match:
                return self.create_claim(user, int(match.group(1)), payload)
            return self.send_json(404, {"error": "That endpoint was not found."})
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        except PermissionError as exc:
            return self.send_json(401, {"error": str(exc)})
        except ForbiddenError as exc:
            return self.send_json(403, {"error": str(exc)})
        except sqlite3.IntegrityError:
            if parsed.path == "/api/auth/register":
                return self.send_json(409, {"error": "An account with that email already exists."})
            if parsed.path == "/api/management/handovers":
                return self.send_json(409, {"error": "A collection record already exists for this item."})
            return self.send_json(409, {"error": "You have already contacted the poster about this post."})
        except Exception:
            self.log_error("POST request failed for %s", parsed.path)
            return self.send_json(500, {"error": "Something went wrong. Please try again."})

    def do_PATCH(self):
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            user = self.current_user()
            claim_match = re.fullmatch(r"/api/claims/(\d+)", parsed.path)
            if claim_match:
                return self.update_claim(user, int(claim_match.group(1)), payload)
            item_match = re.fullmatch(r"/api/items/(\d+)", parsed.path)
            if item_match:
                return self.resolve_item(user, int(item_match.group(1)), payload)
            return self.send_json(404, {"error": "That endpoint was not found."})
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        except PermissionError as exc:
            return self.send_json(401, {"error": str(exc)})
        except Exception:
            self.log_error("PATCH request failed for %s", parsed.path)
            return self.send_json(500, {"error": "Something went wrong. Please try again."})

    def register(self, payload):
        name = clean_text(payload.get("name"), "Name", 2, 80)
        email = clean_text(payload.get("email"), "Email", 3, 254).lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address.")
        if email in manager_emails():
            raise ForbiddenError("CUT manager accounts must be provisioned by the system operator.")
        password = clean_text(payload.get("password"), "Password", 10, 128)
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
        )
        with connect_db() as db:
            cursor = db.execute(
                """
                INSERT INTO users (name, email, university, password_salt, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, email, CAMPUS_NAME, salt, password_hash, utc_now()),
            )
            user_id = cursor.lastrowid
        token = self.create_session(user_id)
        return self.send_json(201, {
            "token": token,
            "user": {
                "id": user_id, "name": name, "email": email,
                "university": CAMPUS_NAME, "is_manager": email in manager_emails(),
            },
        })

    def login(self, payload):
        email = clean_text(payload.get("email"), "Email", 3, 254).lower()
        password = clean_text(payload.get("password"), "Password", 1, 128)
        with connect_db() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            raise PermissionError("Email or password is incorrect.")
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), row["password_salt"], PASSWORD_ITERATIONS
        )
        if not secrets.compare_digest(candidate, row["password_hash"]):
            raise PermissionError("Email or password is incorrect.")
        token = self.create_session(row["id"])
        return self.send_json(200, {
            "token": token,
            "user": {
                "id": row["id"], "name": row["name"], "email": row["email"],
                "university": row["university"],
                "is_manager": row["email"].lower() in manager_emails(),
            },
        })

    def list_items(self, user, query):
        mine_only = query.get("mine", [""])[0] == "1"
        filters = ["lower(users.university) = lower(?)"]
        values = [user["university"]]
        if mine_only:
            filters.append("items.user_id = ?")
            values.append(user["id"])
        else:
            filters.append("items.status = 'open'")
        kind = query.get("kind", [""])[0]
        category = query.get("category", [""])[0]
        search = query.get("q", [""])[0].strip()[:100]
        if kind in ("lost", "found"):
            filters.append("items.kind = ?")
            values.append(kind)
        if category and category != "All":
            filters.append("items.category = ?")
            values.append(category)
        if search:
            filters.append(
                "(items.title LIKE ? OR items.description LIKE ? OR items.location LIKE ?)"
            )
            values.extend([f"%{search}%"] * 3)
        with connect_db() as db:
            rows = db.execute(
                f"""
                SELECT items.id, items.user_id, items.kind, items.title, items.description,
                       items.category, items.location, items.happened_on, items.status,
                       items.created_at, users.name AS owner_name,
                       EXISTS(SELECT 1 FROM claims WHERE claims.item_id = items.id
                              AND claims.requester_id = ?) AS has_contacted
                FROM items JOIN users ON users.id = items.user_id
                WHERE {' AND '.join(filters)}
                ORDER BY items.created_at DESC LIMIT 100
                """,
                [user["id"], *values],
            ).fetchall()
        return self.send_json(200, {"items": [dict(row) for row in rows]})

    def create_item(self, user, payload):
        kind = payload.get("kind")
        if kind not in ("lost", "found"):
            raise ValueError("Choose whether the item was lost or found.")
        title = clean_text(payload.get("title"), "Title", 3, 100)
        description = clean_text(payload.get("description"), "Description", 10, 1000)
        category = clean_text(payload.get("category"), "Category", 2, 40)
        location = clean_text(payload.get("location"), "Location", 2, 120)
        happened_on = clean_text(payload.get("happened_on"), "Date", 10, 10)
        try:
            date_value = datetime.strptime(happened_on, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("Enter a valid date.") from exc
        if date_value > datetime.now(timezone.utc).date():
            raise ValueError("Date cannot be in the future.")
        with connect_db() as db:
            cursor = db.execute(
                """
                INSERT INTO items
                    (user_id, kind, title, description, category, location, happened_on, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user["id"], kind, title, description, category, location, happened_on, utc_now()),
            )
            item_id = cursor.lastrowid
        return self.send_json(201, {"id": item_id, "message": "Your post is live."})

    def list_matches(self, user):
        with connect_db() as db:
            rows = db.execute(
                """
                SELECT items.id, items.user_id, items.kind, items.title, items.description,
                       items.category, items.location, items.happened_on, items.created_at,
                       users.name AS owner_name,
                       EXISTS(SELECT 1 FROM claims WHERE claims.item_id = items.id
                              AND claims.requester_id = ?) AS has_contacted
                FROM items JOIN users ON users.id = items.user_id
                WHERE items.status = 'open' AND lower(users.university) = lower(?)
                ORDER BY items.created_at DESC LIMIT 500
                """,
                (user["id"], user["university"]),
            ).fetchall()
        items = [dict(row) for row in rows]
        own_items = [item for item in items if item["user_id"] == user["id"]]
        if not own_items:
            return self.send_json(200, {"matches": []})
        document_frequency = Counter()
        for item in items:
            document_frequency.update(set(tokenize(f"{item['title']} {item['description']}")))
        matches = []
        for own_item in own_items:
            for candidate in items:
                if candidate["user_id"] == user["id"] or candidate["kind"] == own_item["kind"]:
                    continue
                score, reasons = match_score(
                    own_item, candidate, document_frequency, max(1, len(items))
                )
                if score >= 15:
                    matches.append({
                        "item": candidate,
                        "your_item": {"id": own_item["id"], "title": own_item["title"], "kind": own_item["kind"]},
                        "score": score,
                        "reasons": reasons,
                    })
        matches.sort(key=lambda result: result["score"], reverse=True)
        return self.send_json(200, {"matches": matches[:30]})

    def create_claim(self, user, item_id, payload):
        message = clean_text(payload.get("message"), "Message", 10, 500)
        with connect_db() as db:
            item = db.execute(
                """
                SELECT items.id, items.user_id, items.status
                FROM items JOIN users ON users.id = items.user_id
                WHERE items.id = ? AND lower(users.university) = lower(?)
                """,
                (item_id, user["university"]),
            ).fetchone()
            if not item or item["status"] != "open":
                return self.send_json(404, {"error": "This post is no longer available."})
            if item["user_id"] == user["id"]:
                return self.send_json(400, {"error": "You cannot contact yourself about your own post."})
            cursor = db.execute(
                """
                INSERT INTO claims (item_id, requester_id, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (item_id, user["id"], message, utc_now()),
            )
        return self.send_json(201, {"id": cursor.lastrowid, "message": "Your message was sent privately."})

    def list_claims(self, user):
        with connect_db() as db:
            rows = db.execute(
                """
                SELECT claims.id, claims.item_id, claims.requester_id, claims.message,
                       claims.status, claims.created_at, items.title AS item_title,
                       items.kind AS item_kind, items.user_id AS owner_id,
                       CASE WHEN items.user_id = ? THEN requester.name ELSE owner.name END AS other_name,
                       CASE WHEN claims.status = 'accepted' THEN
                           CASE WHEN items.user_id = ? THEN requester.email ELSE owner.email END
                           ELSE NULL END AS other_email,
                       CASE WHEN items.user_id = ? THEN 'incoming' ELSE 'outgoing' END AS direction
                FROM claims
                JOIN items ON items.id = claims.item_id
                JOIN users AS requester ON requester.id = claims.requester_id
                JOIN users AS owner ON owner.id = items.user_id
                WHERE items.user_id = ? OR claims.requester_id = ?
                ORDER BY claims.created_at DESC
                """,
                (user["id"], user["id"], user["id"], user["id"], user["id"]),
            ).fetchall()
        return self.send_json(200, {"claims": [dict(row) for row in rows]})

    def update_claim(self, user, claim_id, payload):
        status = payload.get("status")
        if status not in ("accepted", "declined"):
            raise ValueError("Choose whether to accept or decline this message.")
        with connect_db() as db:
            claim = db.execute(
                """
                SELECT claims.id FROM claims JOIN items ON items.id = claims.item_id
                WHERE claims.id = ? AND items.user_id = ? AND claims.status = 'pending'
                """,
                (claim_id, user["id"]),
            ).fetchone()
            if not claim:
                return self.send_json(404, {"error": "This request was not found or was already answered."})
            db.execute("UPDATE claims SET status = ? WHERE id = ?", (status, claim_id))
        return self.send_json(200, {"ok": True})

    def list_handovers(self):
        with connect_db() as db:
            rows = db.execute(
                """
                SELECT handovers.id, handovers.item_id, items.title AS item_title,
                       items.kind AS item_kind, items.category, items.location,
                       items.happened_on, handovers.recipient_id, handovers.recipient_email,
                       handovers.collected_at, handovers.recorded_by_name,
                       handovers.recorded_by_email, handovers.recorded_at
                FROM handovers
                JOIN items ON items.id = handovers.item_id
                ORDER BY handovers.collected_at DESC, handovers.id DESC
                """
            ).fetchall()
        return self.send_json(200, {"handovers": [dict(row) for row in rows]})

    def create_handover(self, user, payload):
        try:
            item_id = int(payload.get("item_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Choose an item to hand over.") from exc
        recipient_id = clean_text(payload.get("recipient_id"), "CUT student/staff ID", 2, 40)
        recipient_email = clean_text(payload.get("recipient_email"), "CUT email address", 3, 254).lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient_email):
            raise ValueError("Enter a valid recipient email address.")
        collected_value = clean_text(payload.get("collected_at"), "Collection date and time", 20, 40)
        try:
            collected_at = datetime.fromisoformat(collected_value.replace("Z", "+00:00"))
            if collected_at.tzinfo is None:
                raise ValueError("Collection date and time must include a time zone.")
            collected_at = collected_at.astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError as exc:
            raise ValueError("Enter a valid collection date and time.") from exc
        now = datetime.now(timezone.utc)
        if datetime.fromisoformat(collected_at) > now:
            raise ValueError("Collection date and time cannot be in the future.")

        with connect_db() as db:
            item = db.execute(
                """
                SELECT items.id, items.status
                FROM items JOIN users ON users.id = items.user_id
                WHERE items.id = ? AND lower(users.university) = lower(?)
                """,
                (item_id, CAMPUS_NAME),
            ).fetchone()
            if not item or item["status"] != "open":
                return self.send_json(404, {"error": "This item is not available for collection."})
            recorded_at = utc_now()
            db.execute(
                """
                INSERT INTO handovers
                    (item_id, recipient_id, recipient_email, collected_at,
                     recorded_by, recorded_by_name, recorded_by_email, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id, recipient_id, recipient_email, collected_at,
                    user["id"], user["name"], user["email"], recorded_at,
                ),
            )
            db.execute(
                "UPDATE items SET status = 'resolved', resolved_at = ? WHERE id = ?",
                (recorded_at, item_id),
            )
        return self.send_json(201, {"message": "Collection recorded in the CUT handover register."})

    def resolve_item(self, user, item_id, payload):
        return self.send_json(
            403,
            {"error": "Only an authorised CUT manager can complete an item collection record."},
        )


def create_manager_account(email):
    email = email.strip().lower()
    if email not in manager_emails():
        raise ValueError("Add this email to CUT_MANAGER_EMAILS before creating the account.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid manager email address.")
    name = clean_text(input("Manager's full name: "), "Name", 2, 80)
    password = getpass("Set manager password (at least 10 characters): ")
    if not 10 <= len(password) <= 128:
        raise ValueError("Password must be between 10 and 128 characters.")
    confirmation = getpass("Confirm manager password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match.")
    salt = secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO users (name, email, university, password_salt, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, email, CAMPUS_NAME, salt, password_hash, utc_now()),
        )
    print(f"Authorised manager account created for {email}.")


if __name__ == "__main__":
    initialize_db()
    if len(sys.argv) > 1:
        if len(sys.argv) == 3 and sys.argv[1] == "create-manager":
            try:
                create_manager_account(sys.argv[2])
            except (ValueError, sqlite3.IntegrityError) as exc:
                raise SystemExit(str(exc)) from exc
            raise SystemExit(0)
        raise SystemExit("Usage: python server.py [create-manager manager@cut.ac.zw]")
    server = ThreadingHTTPServer((HOST, PORT), CampusFindHandler)
    print(f"CUT Lost & Found is running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CUT Lost & Found.")
    finally:
        server.server_close()
