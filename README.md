# Module 4 Assignment — MSAI-5523

This is my Module 4 assignment for MSAI-5523.

Partner: Alex Bertelli

## Running the tests

Run these in Terminal on a Mac. Every command starts from this project folder.

### 1. Go to this project folder

```bash
cd "/Users/nandodesktop/Desktop/Cowork_Sandbox/AI_Masters_Projects/language-of-computation/week4-assignment"
```

### 2. Install what the tests need (once)

The tests use pytest. Install it for your user:

```bash
python3 -m pip install --user pytest
```

Check that it is installed:

```bash
python3 -m pytest --version
```

### 3. Run the tests

```bash
python3 -m pytest -v
```

To run the tests and save what the terminal prints at the same time:

```bash
python3 -m pytest -v 2>&1 | tee test-results.txt
```

The tests are in `test_ingest.py`. Each test runs `ingest.py` in its own temporary folder
against a database named `test.db`, and removes that folder, `test.db` and its log when the
test ends. The tests never touch `books.db` or `ingestion-log.json` in this folder.

## Running the ingestion

Start from an empty database and log:

```bash
rm -f books.db ingestion-log.json
```

Load the first delivery:

```bash
python3 ingest.py books.csv
```

Load the same file again (every row should be skipped; nothing is added or updated):

```bash
python3 ingest.py books.csv
```

Load the second delivery:

```bash
python3 ingest.py books-batch2.csv
```

Each run prints a summary and appends one entry to `ingestion-log.json`.

## Opening the report

```bash
open report.html
```

Click **Open ingestion-log.json** and choose `ingestion-log.json` in this folder. The report shows
each run, newest first.
