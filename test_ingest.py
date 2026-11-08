"""Tests for ingest.py, built only from rules.md.

Every test runs ingest.py inside its own temporary folder, against a database
named test.db and a log in that folder. The folder, test.db, the log and any CSV
the test wrote are removed when the test ends, whether it passed or failed.
The books.db and ingestion-log.json in the project folder are never touched.

ingest.py is not changed: the tests load it as a module, point its DB_PATH at
test.db and its LOG_PATH at the temporary log, and call main() with the CSV
file name as the command-line argument.
"""
import csv
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile

import pytest

PROJECT = os.path.dirname(os.path.abspath(__file__))
INGEST_PY = os.path.join(PROJECT, "ingest.py")
BOOKS_CSV = os.path.join(PROJECT, "books.csv")
BATCH2_CSV = os.path.join(PROJECT, "books-batch2.csv")

TEST_DB = "test.db"
TEST_LOG = "ingestion-log.json"

# The header of the goodbooks-10k files (books.csv and books-batch2.csv).
HEADER = [
    "book_id", "goodreads_book_id", "best_book_id", "work_id", "books_count",
    "isbn", "isbn13", "authors", "original_publication_year", "original_title",
    "title", "language_code", "average_rating", "ratings_count",
    "work_ratings_count", "work_text_reviews_count", "ratings_1", "ratings_2",
    "ratings_3", "ratings_4", "ratings_5", "image_url", "small_image_url",
]


def make_row(book_id, title="A Test Book", authors="Test Author",
             isbn="", isbn13="", **other):
    """One CSV row with every column filled in; isbn and isbn13 as given."""
    row = {
        "book_id": str(book_id), "goodreads_book_id": "1", "best_book_id": "1",
        "work_id": "1", "books_count": "1", "isbn": isbn, "isbn13": isbn13,
        "authors": authors, "original_publication_year": "2000.0",
        "original_title": title, "title": title, "language_code": "eng",
        "average_rating": "4.0", "ratings_count": "100",
        "work_ratings_count": "100", "work_text_reviews_count": "10",
        "ratings_1": "1", "ratings_2": "2", "ratings_3": "3", "ratings_4": "4",
        "ratings_5": "90", "image_url": "https://example.com/m.jpg",
        "small_image_url": "https://example.com/s.jpg",
    }
    row.update({k: str(v) for k, v in other.items()})
    return row


class Workspace:
    """A temporary folder where ingest.py runs against test.db."""

    def __init__(self, folder, monkeypatch):
        self.folder = folder
        self.monkeypatch = monkeypatch
        self.counter = 0

    def write_csv(self, rows):
        self.counter += 1
        name = "input-%d.csv" % self.counter
        with open(os.path.join(self.folder, name), "w", newline="",
                  encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HEADER)
            writer.writeheader()
            writer.writerows(rows)
        return name

    def copy_project_file(self, path):
        name = os.path.basename(path)
        shutil.copyfile(path, os.path.join(self.folder, name))
        return name

    def run(self, csv_name):
        """Run ingest.py on csv_name; return the log entry this run added."""
        spec = importlib.util.spec_from_file_location("ingest_under_test",
                                                      INGEST_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.DB_PATH = TEST_DB
        module.LOG_PATH = TEST_LOG
        self.monkeypatch.setattr(sys, "argv", ["ingest.py", csv_name])
        module.main()
        return self.log()[-1]

    def log(self):
        with open(os.path.join(self.folder, TEST_LOG), encoding="utf-8") as f:
            return json.load(f)

    def book(self, book_id):
        """The stored row for book_id as a dict, or None."""
        conn = sqlite3.connect(os.path.join(self.folder, TEST_DB))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM books WHERE CAST(book_id AS TEXT) = ?",
                (str(book_id),)).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def count_books(self, book_id=None):
        conn = sqlite3.connect(os.path.join(self.folder, TEST_DB))
        try:
            if book_id is None:
                return conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM books WHERE CAST(book_id AS TEXT) = ?",
                (str(book_id),)).fetchone()[0]
        finally:
            conn.close()

    def snapshot(self):
        """Every stored row except last_loaded_at (rule 8: the same data)."""
        conn = sqlite3.connect(os.path.join(self.folder, TEST_DB))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM books").fetchall()
        finally:
            conn.close()
        data = []
        for r in rows:
            d = dict(r)
            d.pop("last_loaded_at", None)
            data.append(d)
        return sorted(data, key=lambda d: str(d["book_id"]))

    def reset(self):
        """Delete test.db and the log, leaving an empty database to start from."""
        for name in (TEST_DB, TEST_LOG):
            path = os.path.join(self.folder, name)
            if os.path.exists(path):
                os.remove(path)


@pytest.fixture
def ws(monkeypatch):
    project_db = os.path.join(PROJECT, "books.db")
    project_log = os.path.join(PROJECT, "ingestion-log.json")
    before = {p: os.path.getmtime(p) if os.path.exists(p) else None
              for p in (project_db, project_log)}

    folder = tempfile.mkdtemp(prefix="ingest-test-")
    monkeypatch.chdir(folder)
    try:
        yield Workspace(folder, monkeypatch)
    finally:
        monkeypatch.undo()
        shutil.rmtree(folder, ignore_errors=True)
    for path, mtime in before.items():
        now = os.path.getmtime(path) if os.path.exists(path) else None
        assert now == mtime, "a test touched %s" % path


def warnings_for(entry, book_id):
    return [w for w in entry["warnings"] if str(w["book_id"]) == str(book_id)]


def rejections_for(entry, book_id):
    return [r for r in entry["rejected_rows"] if str(r["book_id"]) == str(book_id)]


# --------------------------------------------------------------------------
# Rule 10, examples 1-17: one row, checked for its ISBNs and its warning.
# (number, isbn, isbn13, expected isbn10, expected isbn13, warning?, text)
# --------------------------------------------------------------------------
SINGLE_ROW_EXAMPLES = [
    (1, "0439139600", "9780439139601", "0439139600", "9780439139601", False, None),
    (2, "439023483", "9.78043902348e+12", "0439023483", "9780439023481", True, None),
    (3, "0-450-19827-8", "9780450198274", "0450198278", "9780450198274", False, None),
    (4, " 162 001 4750 ", "9781620014752", "1620014750", "9781620014752", False, None),
    (5, "031200009x", "9780312000097", "031200009X", "9780312000097", False, None),
    (6, "043965548X", "", "043965548X", "9780439655484", True, None),
    (7, "9781982147600", "", "1982147601", "9781982147600", True, None),
    (8, "", "9780062398123", "0062398121", "9780062398123", True, None),
    (9, "", "9.78043902348e+12", None, None, True, None),
    (10, "04390A3483", "", None, None, True, None),
    (11, "1854433017", "", None, None, True, None),
    (12, "15287463097", "", None, None, True, None),
    (13, "7442912", "", None, None, True, None),
    (14, "", "", None, None, True, "no ISBN"),
    (15, "1555550126", "9781555550134", "1555550134", "9781555550134", True, "1555550126"),
    (16, "", "76783609419.0", None, None, True, None),
    (17, "", "9790000000001", None, "9790000000001", False, None),
]


@pytest.mark.parametrize(
    "number, isbn, isbn13, want10, want13, warned, text", SINGLE_ROW_EXAMPLES,
    ids=["example_%d" % e[0] for e in SINGLE_ROW_EXAMPLES])
def test_single_row_example(ws, number, isbn, isbn13, want10, want13, warned, text):
    book_id = 5000 + number
    entry = ws.run(ws.write_csv([make_row(book_id, isbn=isbn, isbn13=isbn13)]))

    assert entry["loaded"] == 1 and entry["rejected"] == 0
    stored = ws.book(book_id)
    assert stored is not None, "the row must be loaded"
    assert stored["isbn10"] == want10
    assert stored["isbn13"] == want13
    assert stored["isbn_raw"] == isbn
    assert stored["isbn13_raw"] == isbn13

    found = warnings_for(entry, book_id)
    if warned:
        assert found, "expected a warning for example %d" % number
        if text:
            assert any(text in json.dumps(w) for w in found)
    else:
        assert not found, "expected no warning for example %d" % number


def test_example_18_isbn_already_assigned(ws):
    first = make_row(9, title="Angels & Demons", isbn="1416524797",
                     isbn13="9.78141652479e+12")
    second = make_row(10013, title="Another Book", isbn="1416524797")
    entry = ws.run(ws.write_csv([first, second]))

    assert ws.book(9)["isbn13"] == "9781416524793"
    stored = ws.book(10013)
    assert stored is not None, "book 10013 must still be loaded"
    assert stored["isbn10"] is None and stored["isbn13"] is None
    assert any("ISBN already assigned to book 9" in w["reason"]
               for w in warnings_for(entry, 10013))


def test_example_19_duplicate_row_in_file(ws):
    row = make_row(10001, isbn="0439139600", isbn13="9780439139601")
    entry = ws.run(ws.write_csv([row, dict(row)]))

    assert entry["loaded"] == 1 and entry["rejected"] == 1
    assert ws.count_books(10001) == 1
    assert any("duplicate row in file" in r["reason"]
               for r in rejections_for(entry, 10001))


def test_example_20_redelivery_updates_changed_column(ws):
    ws.run(ws.write_csv([make_row(132, isbn="1401308589", ratings_count=100)]))
    entry = ws.run(ws.write_csv([make_row(132, isbn="1401308589", ratings_count=200)]))

    assert entry["updated"] == 1
    assert str(ws.book(132)["ratings_count"]) == "200"
    listed = [u for u in entry["updated_rows"] if str(u["book_id"]) == "132"]
    assert listed and "ratings_count" in listed[0]["changed_columns"]


def test_example_21_redelivery_with_new_title_is_rejected(ws):
    ws.run(ws.write_csv([make_row(25, title="Deathly Hallows", isbn="0545010225")]))
    entry = ws.run(ws.write_csv([make_row(25, title="A New Title", isbn="0545010225")]))

    assert entry["rejected"] == 1 and entry["updated"] == 0
    assert any("collision: title or author changed for book 25" in r["reason"]
               for r in rejections_for(entry, 25))
    assert ws.book(25)["title"] == "Deathly Hallows"


def test_example_22_empty_isbns_never_blank_stored_isbns(ws):
    ws.run(ws.write_csv([make_row(25, isbn="0545010225", isbn13="9780545010221")]))
    entry = ws.run(ws.write_csv([make_row(25, isbn="", isbn13="")]))

    assert entry["skipped"] == 1 and entry["updated"] == 0
    stored = ws.book(25)
    assert stored["isbn10"] == "0545010225"
    assert stored["isbn13"] == "9780545010221"


def test_example_23_blank_title_is_rejected(ws):
    entry = ws.run(ws.write_csv([make_row(7777, title="", isbn="0439139600")]))

    assert entry["rejected"] == 1 and entry["loaded"] == 0
    assert ws.book(7777) is None
    found = rejections_for(entry, 7777)
    assert found and "title" in found[0]["reason"].lower()


# --------------------------------------------------------------------------
# The other required tests
# --------------------------------------------------------------------------
def test_same_file_twice_creates_no_duplicates(ws):
    name = ws.copy_project_file(BOOKS_CSV)
    ws.run(name)
    books_after_first = ws.count_books()

    second = ws.run(name)

    assert second["loaded"] == 0 and second["updated"] == 0
    assert ws.count_books() == books_after_first


def test_every_run_appends_to_the_log(ws):
    first = ws.run(ws.write_csv([make_row(1, isbn="0439139600")]))
    ws.run(ws.write_csv([make_row(2, isbn="0450198278")]))
    ws.run(ws.write_csv([make_row(3, isbn="1620014750")]))

    log = ws.log()
    assert len(log) == 3
    assert log[0] == first, "an earlier entry was overwritten"
    for entry in log:
        for key in ("run_at", "source_file", "rows_read"):
            assert key in entry


def test_counts_add_up_to_rows_read(ws):
    for path in (BOOKS_CSV, BOOKS_CSV, BATCH2_CSV):
        name = ws.copy_project_file(path)
        with open(path, newline="", encoding="utf-8") as f:
            data_rows = sum(1 for _ in csv.DictReader(f))
        entry = ws.run(name)

        assert entry["rows_read"] == data_rows
        assert (entry["loaded"] + entry["updated"] + entry["skipped"]
                + entry["rejected"]) == entry["rows_read"]


def test_same_file_on_empty_database_twice_gives_same_database(ws):
    name = ws.copy_project_file(BOOKS_CSV)
    ws.run(name)
    first = ws.snapshot()

    ws.reset()
    ws.run(name)
    second = ws.snapshot()

    assert first == second
