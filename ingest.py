#!/usr/bin/env python3
"""ingest.py - load a books CSV into books.db, following rules.md.

Deterministic and offline: it uses no AI and no network, so the same file
always produces the same result. It checks every row on every run, and running
the same file twice never creates duplicates. Each run appends one entry to
ingestion-log.json and never overwrites earlier runs.

Usage:
    python3 ingest.py <books_csv_file>
"""
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime

DB_PATH = "books.db"
LOG_PATH = "ingestion-log.json"


# --------------------------------------------------------------------------
# ISBN validation (check-digit based, per rules.md Section 1)
# --------------------------------------------------------------------------
def valid_isbn10(s):
    if len(s) != 10:
        return False
    total = 0
    for i, ch in enumerate(s):
        if i == 9 and ch in "Xx":
            v = 10
        elif ch.isdigit():
            v = int(ch)
        else:
            return False
        total += (10 - i) * v
    return total % 11 == 0


def valid_isbn13(s):
    if len(s) != 13 or not s.isdigit() or s[:3] not in ("978", "979"):
        return False
    total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(s))
    return total % 10 == 0


def isbn10_to_13(s):
    core = "978" + s[:9]
    total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(core))
    return core + str((10 - total % 10) % 10)


def clean(value):
    """Strip hyphens and spaces - the only cosmetic repairs (rules.md Section 2)."""
    return (value or "").replace("-", "").replace(" ", "").strip()


# --------------------------------------------------------------------------
# Canonical ISBN-13 and disposition (rules.md Sections 2 and 3)
# --------------------------------------------------------------------------
def resolve_canonical(raw_isbn, raw_isbn13):
    """Return (canonical_isbn13, reject_reason); exactly one is non-None."""
    c13 = clean(raw_isbn13)
    c10 = clean(raw_isbn)

    # 1. The isbn13 column wins when it is a valid ISBN-13 (this also makes any
    #    scientific-notation or otherwise-unusable value in that column be ignored).
    if valid_isbn13(c13):
        return c13, None

    # 2. Otherwise derive the key from the isbn column.
    if c10 == "":
        return None, "no usable ISBN (both fields empty or unusable)"

    if len(c10) == 13:
        # A 13-digit value sitting in the isbn column: move to isbn13, then validate.
        if valid_isbn13(c10):
            return c10, None
        return None, "13-digit value in isbn column is not a valid ISBN-13 (%r)" % raw_isbn

    # Treat as an ISBN-10, repairing a dropped leading zero (8 or 9 digits).
    rep = c10.upper()
    if len(rep) in (8, 9) and rep[:-1].isdigit() and (rep[-1].isdigit() or rep[-1] == "X"):
        rep = rep.rjust(10, "0")
    if len(rep) == 10 and valid_isbn10(rep):
        return isbn10_to_13(rep), None

    # Name why it was rejected.
    core = rep[:-1] if rep and rep[-1] == "X" else rep
    if not core.isdigit():
        return None, "ISBN contains a letter or invalid character (%r)" % raw_isbn
    if len(rep) not in (8, 9, 10):
        return None, "ISBN is the wrong length (%r)" % raw_isbn
    return None, "ISBN fails its check digit (%r)" % raw_isbn


def year_of(raw):
    """Normalize the year, which arrives as a float string like '2008.0'."""
    raw = (raw or "").strip()
    if raw == "":
        return ""
    try:
        return str(int(float(raw)))
    except ValueError:
        return raw


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
def ensure_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS books ("
        "  canonical_isbn13 TEXT PRIMARY KEY,"
        "  title            TEXT,"
        "  authors          TEXT,"
        "  publication_year TEXT,"
        "  book_id          TEXT"
        ")"
    )


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 ingest.py <books_csv_file>")
        sys.exit(1)
    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        print("File not found: %s" % csv_path)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)
    cur = conn.cursor()

    counts = {"read": 0, "loaded": 0, "updated": 0, "skipped_existing": 0, "rejected": 0}
    not_loaded = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            counts["read"] += 1
            book_id = (row.get("book_id") or "").strip()
            title = (row.get("title") or "").strip()
            authors = (row.get("authors") or "").strip()
            year = year_of(row.get("original_publication_year"))
            raw_isbn = row.get("isbn") or ""
            raw_isbn13 = row.get("isbn13") or ""

            canonical, reason = resolve_canonical(raw_isbn, raw_isbn13)
            if canonical is None:
                counts["rejected"] += 1
                not_loaded.append({
                    "book_id": book_id, "title": title,
                    "isbn": raw_isbn, "isbn13": raw_isbn13, "reason": reason,
                })
                continue

            existing = cur.execute(
                "SELECT title, authors, publication_year FROM books "
                "WHERE canonical_isbn13 = ?",
                (canonical,),
            ).fetchone()

            if existing is None:
                cur.execute(
                    "INSERT INTO books "
                    "(canonical_isbn13, title, authors, publication_year, book_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (canonical, title, authors, year, book_id),
                )
                counts["loaded"] += 1
                continue

            ex_title, ex_authors, ex_year = existing
            if title == ex_title and authors == ex_authors:
                if year == ex_year:
                    counts["skipped_existing"] += 1          # identical re-arrival
                else:
                    cur.execute(
                        "UPDATE books SET publication_year = ?, book_id = ? "
                        "WHERE canonical_isbn13 = ?",
                        (year, book_id, canonical),
                    )
                    counts["updated"] += 1                   # year corrected
            else:
                # Same ISBN but a different title/author: collision, not a merge.
                counts["rejected"] += 1
                not_loaded.append({
                    "book_id": book_id, "title": title,
                    "isbn": raw_isbn, "isbn13": raw_isbn13,
                    "reason": ("collision - ISBN %s already belongs to a different "
                               "title (%r)" % (canonical, ex_title)),
                })

    conn.commit()
    conn.close()

    entry = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "file": os.path.basename(csv_path),
        "counts": counts,
        "not_loaded": not_loaded,
    }

    log = []
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                log = json.load(f)
            if not isinstance(log, list):
                log = [log]
        except (ValueError, OSError):
            log = []
    log.append(entry)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)

    print("%s: read %d, loaded %d, updated %d, already-exists %d, rejected %d" % (
        entry["file"], counts["read"], counts["loaded"], counts["updated"],
        counts["skipped_existing"], counts["rejected"]))
    print("Logged to %s; database %s" % (LOG_PATH, DB_PATH))


if __name__ == "__main__":
    main()
