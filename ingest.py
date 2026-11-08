#!/usr/bin/env python3
"""ingest.py - load a books CSV into books.db, following rules.md.

Deterministic and offline: no AI and no network, so the same file on the same
database always gives the same result. book_id is the key (rule 5). Each run
appends one entry to ingestion-log.json and never overwrites earlier entries
(rule 9).

Usage:
    python3 ingest.py <books_csv_file>
"""
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

DB_PATH = "books.db"
LOG_PATH = "ingestion-log.json"

# Columns that hold what the file had under the isbn / isbn13 headers (rule 8).
RAW_COLUMNS = {"isbn": "isbn_raw", "isbn13": "isbn13_raw"}
# Columns that only record the load; never compared on re-delivery (rule 7).
LOAD_COLUMNS = ("last_source_file", "last_loaded_at")
FLOAT_ISBN13 = re.compile(r"^\d\.\d+e\+12$", re.IGNORECASE)


# --------------------------------------------------------------------------
# Rule 1: valid ISBNs, and deriving one form from the other
# --------------------------------------------------------------------------
def valid_isbn10(s):
    if len(s) != 10 or not s[:9].isdigit() or not (s[9].isdigit() or s[9] == "X"):
        return False
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
    return total % 11 == 0


def valid_isbn13(s):
    if len(s) != 13 or not s.isdigit() or s[:3] not in ("978", "979"):
        return False
    return sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(s)) % 10 == 0


def isbn10_to_13(s):
    core = "978" + s[:9]
    total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(core))
    return core + str((10 - total % 10) % 10)


def isbn13_to_10(s):
    """ISBN-10 for a 978 ISBN-13; None for 979 (no ISBN-10 exists)."""
    if not s.startswith("978"):
        return None
    core = s[3:12]
    check = (11 - sum((10 - i) * int(c) for i, c in enumerate(core)) % 11) % 11
    return core + ("X" if check == 10 else str(check))


# --------------------------------------------------------------------------
# Rule 2: normalization (formatting only, never logged)
# --------------------------------------------------------------------------
def normalize(value):
    return (value or "").strip().replace(" ", "").replace("-", "").replace("x", "X")


def why_bad_isbn10(v):
    """Reason an isbn value is not a valid ISBN-10 (rule 4)."""
    core = v[:-1] if v.endswith("X") else v
    if not core.isdigit():
        return "a letter or other character that is not a digit"
    if len(v) < 9:
        return "the wrong length (%d digits; only one lost leading zero may be added)" % len(v)
    if len(v) == 9:
        return "9 digits, and adding one leading zero fails the checksum"
    if len(v) == 10:
        return "a checksum that fails"
    return "the wrong length (%d digits)" % len(v)


def why_bad_isbn13(v):
    if not v.isdigit():
        return "not a 13-digit value or an e+12 float"
    if len(v) != 13:
        return "the wrong length (%d digits)" % len(v)
    if v[:3] not in ("978", "979"):
        return "13 digits not starting 978 or 979"
    return "a checksum that fails"


# --------------------------------------------------------------------------
# Rules 3 and 4: repair what can be rebuilt exactly; otherwise NULL + warning
# --------------------------------------------------------------------------
def resolve_isbns(raw_isbn, raw_isbn13):
    """Return (isbn10, isbn13, notes); notes are (field, before, after, reason)."""
    n10, n13 = normalize(raw_isbn), normalize(raw_isbn13)
    notes = []
    v10 = v13 = None
    v13_from_isbn_column = False

    # The isbn column.
    if n10:
        if len(n10) == 13 and n10.isdigit():
            if valid_isbn13(n10):
                v13_from_isbn_column = True
            else:
                notes.append(("isbn", raw_isbn, None,
                              "isbn %r has %s; discarded" % (raw_isbn, why_bad_isbn13(n10))))
        elif len(n10) == 9 and n10.isdigit() and valid_isbn10("0" + n10):
            v10 = "0" + n10
            notes.append(("isbn10", raw_isbn, v10,
                          "9 digits: one lost leading zero added to %r" % raw_isbn))
        elif valid_isbn10(n10):
            v10 = n10
        else:
            notes.append(("isbn", raw_isbn, None,
                          "isbn %r has %s; discarded" % (raw_isbn, why_bad_isbn10(n10))))

    # The isbn13 column.
    float13 = False
    if n13:
        if FLOAT_ISBN13.match(n13):
            float13 = True
        elif valid_isbn13(n13):
            v13 = n13
        else:
            notes.append(("isbn13", raw_isbn13, None,
                          "isbn13 %r has %s; discarded" % (raw_isbn13, why_bad_isbn13(n13))))

    if v13_from_isbn_column:
        if v13 is None and not n13:
            v13 = n10
            notes.append(("isbn13", raw_isbn, v13,
                          "valid ISBN-13 %r in the isbn column moved to isbn13" % raw_isbn))
        elif v13 is None or v13 != n10:
            notes.append(("isbn", raw_isbn, None,
                          "ISBN-13 %r in the isbn column discarded; isbn13 column used" % raw_isbn))

    # Combine the two.
    if v10 and v13:
        if isbn10_to_13(v10) != v13:
            derived = isbn13_to_10(v13)
            notes.append(("isbn10", v10, derived,
                          "ISBN-10 %r disagrees with isbn13 %r; isbn13 wins, ISBN-10 %r discarded"
                          % (v10, v13, v10)))
            v10 = derived
    elif v10:
        v13 = isbn10_to_13(v10)
        if float13:
            reason = "float isbn13 %r discarded; ISBN-13 derived from ISBN-10 %r" % (raw_isbn13, v10)
        else:
            reason = "ISBN-13 derived from ISBN-10 %r" % v10
        notes.append(("isbn13", raw_isbn13, v13, reason))
    elif v13:
        derived = isbn13_to_10(v13)
        if derived:
            notes.append(("isbn10", raw_isbn if not v13_from_isbn_column else "", derived,
                          "ISBN-10 derived from ISBN-13 %r" % v13))
        v10 = derived
    else:
        if float13:
            notes.append(("isbn13", raw_isbn13, None,
                          "float isbn13 %r with no valid ISBN-10 to derive from" % raw_isbn13))
        if not n10 and not n13:
            notes.append(("isbn", "", None, "no ISBN"))
    return v10, v13, notes


# --------------------------------------------------------------------------
# Rule 8: the database
# --------------------------------------------------------------------------
def data_columns(header):
    """Stored copies of the CSV columns; isbn/isbn13 become *_raw."""
    return [RAW_COLUMNS.get(h, h) for h in header]


def ensure_schema(conn, header):
    cols = data_columns(header)
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='books'").fetchone()
    if not exists:
        others = [c for c in cols if c != "book_id"]
        defs = ["book_id TEXT PRIMARY KEY"] + ['"%s" TEXT' % c for c in others]
        defs += ["isbn10 TEXT", "isbn13 TEXT", "last_source_file TEXT", "last_loaded_at TEXT"]
        conn.execute("CREATE TABLE books (%s)" % ", ".join(defs))
        conn.execute("CREATE UNIQUE INDEX books_isbn13 ON books(isbn13) "
                     "WHERE isbn13 IS NOT NULL")
        return
    have = {r[1] for r in conn.execute("PRAGMA table_info(books)")}
    if "book_id" not in have or "isbn13_raw" not in have:
        sys.exit("%s was made by an older ingest.py; delete it and run again." % DB_PATH)
    for c in cols:
        if c not in have:
            conn.execute('ALTER TABLE books ADD COLUMN "%s" TEXT' % c)


def stored_row(conn, book_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM books WHERE book_id = ?", (book_id,)).fetchone()
    conn.row_factory = None
    return dict(row) if row else None


def isbn_owner(conn, isbn13):
    row = conn.execute("SELECT book_id FROM books WHERE isbn13 = ?", (isbn13,)).fetchone()
    return row[0] if row else None


def database_totals(conn):
    total, with13, without = conn.execute(
        "SELECT COUNT(*), COUNT(isbn13), "
        "SUM(CASE WHEN isbn10 IS NULL AND isbn13 IS NULL THEN 1 ELSE 0 END) FROM books"
    ).fetchone()
    return {"total_books": total, "books_with_isbn13": with13,
            "books_without_isbn": without or 0}


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------
def main():
    if len(sys.argv) != 2:
        print("Usage: python3 ingest.py <books_csv_file>")
        sys.exit(1)
    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        print("File not found: %s" % csv_path)
        sys.exit(1)
    source = os.path.basename(csv_path)
    run_at = datetime.now().astimezone().isoformat(timespec="seconds")

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        records = list(reader)

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn, header)
    cols = data_columns(header)
    compare_cols = [c for c in cols if c not in RAW_COLUMNS.values()] + ["isbn10", "isbn13"]

    entry = {"run_at": run_at, "source_file": source, "rows_read": len(records),
             "loaded": 0, "updated": 0, "skipped": 0, "rejected": 0,
             "rejected_rows": [], "warnings": [], "updated_rows": []}
    seen_rows = set()

    for values in records:
        values = values + [""] * (len(header) - len(values))
        row = dict(zip(header, values))
        book_id = (row.get("book_id") or "").strip()
        title = row.get("title") or ""
        raw_isbn, raw_isbn13 = row.get("isbn") or "", row.get("isbn13") or ""

        def reject(reason):
            entry["rejected"] += 1
            entry["rejected_rows"].append({"book_id": book_id, "title": title,
                                           "isbn": raw_isbn, "isbn13": raw_isbn13,
                                           "reason": reason})

        # Rule 6.1: nothing to key or name the record.
        if not book_id:
            reject("missing book_id")
            continue
        if not title.strip():
            reject("missing title for book %s" % book_id)
            continue
        # Rule 6.2: exact duplicate of an earlier row in this file.
        key = tuple(values)
        if key in seen_rows:
            reject("duplicate row in file (book %s)" % book_id)
            continue
        seen_rows.add(key)

        # Rules 2-4.
        isbn10, isbn13, notes = resolve_isbns(raw_isbn, raw_isbn13)
        # Rule 5: no two books share an ISBN.
        if isbn13 is not None:
            owner = isbn_owner(conn, isbn13)
            if owner is not None and owner != book_id:
                notes.append(("isbn13", isbn13, None,
                              "ISBN already assigned to book %s" % owner))
                isbn10 = isbn13 = None

        new = {c: v for c, v in zip(cols, values)}
        new["book_id"] = book_id
        new["isbn10"], new["isbn13"] = isbn10, isbn13
        new["last_source_file"], new["last_loaded_at"] = source, run_at

        old = stored_row(conn, book_id)
        if old is None:
            names = list(new)
            conn.execute("INSERT INTO books (%s) VALUES (%s)" % (
                ", ".join('"%s"' % n for n in names), ", ".join("?" * len(names))),
                [new[n] for n in names])
            entry["loaded"] += 1
        else:
            # Rule 7: re-delivery.
            if old.get("title") != new.get("title") or old.get("authors") != new.get("authors"):
                reject("collision: title or author changed for book %s" % book_id)
                continue
            if isbn13 is None and old.get("isbn13") is not None:
                # An empty incoming ISBN never blanks a stored ISBN.
                for c in ("isbn10", "isbn13", "isbn_raw", "isbn13_raw"):
                    new[c] = old.get(c)
            changed = [c for c in compare_cols if old.get(c) != new.get(c)]
            if not changed:
                entry["skipped"] += 1
                continue
            names = [n for n in new if n != "book_id"]
            conn.execute("UPDATE books SET %s WHERE book_id = ?" % ", ".join(
                '"%s" = ?' % n for n in names), [new[n] for n in names] + [book_id])
            entry["updated"] += 1
            entry["updated_rows"].append({"book_id": book_id, "title": title,
                                          "changed_columns": changed})

        for field, before, after, reason in notes:
            entry["warnings"].append({"book_id": book_id, "title": title, "field": field,
                                      "before": before, "after": after, "reason": reason})

    entry["database_totals"] = database_totals(conn)
    conn.commit()
    conn.close()

    log = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding="utf-8") as f:
            log = json.load(f)
        if not isinstance(log, list):
            log = [log]
    log.append(entry)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

    t = entry["database_totals"]
    print("%s: read %d, loaded %d, updated %d, skipped %d, rejected %d, warnings %d" % (
        source, entry["rows_read"], entry["loaded"], entry["updated"], entry["skipped"],
        entry["rejected"], len(entry["warnings"])))
    print("Database: %d books, %d with an ISBN-13, %d without any ISBN" % (
        t["total_books"], t["books_with_isbn13"], t["books_without_isbn"]))


if __name__ == "__main__":
    main()
