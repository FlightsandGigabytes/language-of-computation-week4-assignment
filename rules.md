# Ingestion rules for books CSV files

Agreed by Alex Bertelli and Fernando Martinez (Module 4, MSAI-5523).

These rules are the only source of truth for `ingest.py`. Anything not written here is not allowed. The script uses no AI and no internet. The same file on the same database always gives the same result.

## 1. What a valid ISBN is

- **ISBN-10:** exactly 10 characters. The first nine are digits; the last is a digit or `X`. It is valid when the sum of each character times its weight (weights 10 down to 1, `X` = 10) is divisible by 11.
- **ISBN-13:** exactly 13 digits starting `978` or `979`. It is valid when the sum of each digit times its weight (weights alternate 1, 3, 1, 3…) is divisible by 10.
- A value is valid only if it passes its checksum. The right length alone is not enough.

## 2. Normalization (always first, never logged)

Before any check, both `isbn` and `isbn13` are normalized:

- strip leading and trailing whitespace
- remove internal spaces and hyphens
- upper-case a lower-case `x`

This is formatting, not a change of value. It is not a repair and is not logged.

## 3. Repairs (logged as warnings)

A repair is allowed only when the original value can be rebuilt **exactly** and the result **passes its checksum**. Every repair is logged as a warning on the row, with the value before and after. These are the only repairs:

| Problem | Repair |
|---|---|
| ISBN-10 with exactly **9** digits (one leading zero lost) | Add one leading zero. Keep it only if the checksum passes. |
| `isbn13` written as a float (`9.78043902348e+12`) | Discard the float; its last digits are lost. Derive the ISBN-13 from a valid ISBN-10. |
| `isbn13` empty, ISBN-10 valid | Derive the ISBN-13 from the ISBN-10 (`978` + first nine digits + new check digit). |
| Valid ISBN-13 in the `isbn` column, `isbn13` empty | Move it to `isbn13`. |
| ISBN-10 empty, ISBN-13 valid and starting `978` | Derive the ISBN-10 from the ISBN-13. |
| ISBN-10 and ISBN-13 both valid but they **disagree** | `isbn13` wins. The ISBN-10 is discarded and derived again from the ISBN-13 (no ISBN-10 if it starts `979`). The warning names the discarded value. |

Any fix that means choosing a digit is a guess: correcting a bad checksum, replacing a letter, padding more than one zero, or deciding which of 11 digits is extra. The code never guesses.

## 4. ISBNs that can't be fixed: load the row, no ISBN, warning

If an ISBN value is still invalid after normalization and the repairs in rule 3, the **row is still loaded**. Its ISBN fields are stored as NULL, and a warning is logged with the original value and the reason. This covers:

- a checksum that fails on a 10- or 13-character value
- a letter anywhere except a final `X`
- the wrong length: fewer than 9 digits, 11 or 12 digits, or 13 digits not starting `978`/`979`
- an `isbn13` that is neither a 13-digit value nor an `e+12` float (for example `76783609419.0`)
- a float `isbn13` with no valid ISBN-10 to derive from
- both ISBN fields empty (warning `no ISBN`)

If only one value is unfixable and the other is valid, keep the valid one and derive the other from it (rule 3). The warning names the discarded value.

## 5. Identity: what makes two rows the same book

- **`book_id` is the key.** Two rows with the same `book_id` are the same book. A row with no usable ISBN is still a book.
- **No two books may share an ISBN.** If a row's valid ISBN-13 already belongs to a different `book_id`, either in the database or earlier in the same file, the row is **still loaded**. Its ISBN fields are stored as NULL, with the warning `ISBN already assigned to book <id>`.

## 6. Rejections: the row is never loaded

A row is rejected, counted, and listed in the log with its reason only when:

1. **`book_id` is missing or the title is blank.** There is nothing to key or name the record.
2. **It is an exact duplicate of an earlier row in the same file** (same `book_id` and identical in every column). The first copy is processed; the second is rejected as `duplicate row in file`.
3. **It is a re-delivery that changes the title or author** (rule 7). It is rejected as `collision: title or author changed for book <id>`, and the stored row is left unchanged.

Nothing else is rejected.

## 7. Re-delivery: a book already in the database arrives again

- Compare the incoming row with the stored row column by column, after normalization and repairs.
- **Nothing changed:** the row is `skipped`. This is not an error.
- **Title or author changed:** the row is rejected as a collision (rule 6.3). Nothing is written.
- **Anything else changed:** the stored columns are overwritten and the row is counted as `updated`. The log lists the columns that changed.
- **An empty incoming ISBN never blanks a stored ISBN.** If the incoming row has no usable ISBN and the stored row has one, the stored ISBN is kept. The row is otherwise updated, or skipped if nothing else changed.
- A second row in the same file with the same `book_id` but different content is a re-delivery. It is compared against what the first row just loaded.
- Running the same file twice therefore gives zero new and zero updated rows the second time; every row is `skipped`.

## 8. Database

- SQLite file `books.db` with one table, `books`, whose primary key is `book_id`.
- Columns:
  - every column from the CSV
  - `isbn10` and `isbn13` (normalized, repaired, or NULL)
  - `isbn_raw` and `isbn13_raw`: exactly what the file had under those headers
  - `last_source_file` and `last_loaded_at`
- A UNIQUE index on `isbn13` (where not NULL) enforces rule 5.
- Two databases hold **the same data** when they have the same books and every column matches, except `last_loaded_at`. That column only records when a row was loaded.

## 9. The log and the report

Every run appends one entry to `ingestion-log.json`. The file is created if missing, and earlier entries are never overwritten. Each entry has:

- `run_at` (ISO timestamp), `source_file`, `rows_read`
- the counts `loaded` (new), `updated`, `skipped` and `rejected`, where loaded + updated + skipped + rejected = `rows_read`
- `rejected_rows`: every row not loaded, with `book_id`, `title`, the raw `isbn` and `isbn13`, and a `reason` sentence naming the problem and the value
- `warnings`: every loaded or updated row that had a repair or an unfixable ISBN, with `book_id`, `title`, `field`, `before`, `after` and `reason`
- `database_totals` after the run: total books, books with an ISBN-13, and books without any ISBN

`report.html` is a single file that opens from disk with no server. It reads `ingestion-log.json` and shows the runs newest first: the counts, the rejected rows with reasons, the repairs (before → after), the warnings, and the database totals.

## 10. Examples: what the rules say should happen

| # | `isbn` | `isbn13` | Outcome |
|---|---|---|---|
| 1 | `0439139600` | `9780439139601` | Both valid and agree. **Loaded**, no warning. |
| 2 | `439023483` | `9.78043902348e+12` | 9 digits → padded to `0439023483`, valid. Float discarded; ISBN-13 derived → `9780439023481`. **Loaded**, warning. |
| 3 | `0-450-19827-8` | `9780450198274` | Normalized → `0450198278`, valid, agrees. **Loaded**, no warning. |
| 4 | ` 162 001 4750 ` | `9781620014752` | Normalized → `1620014750`, valid, agrees. **Loaded**, no warning. |
| 5 | `031200009x` | `9780312000097` | Normalized → `031200009X`, valid, agrees. **Loaded**, no warning. |
| 6 | `043965548X` | *(empty)* | ISBN-13 derived → `9780439655484`. **Loaded**, warning. |
| 7 | `9781982147600` | *(empty)* | Valid ISBN-13 moved to `isbn13`; ISBN-10 derived → `1982147601`. **Loaded**, warning. |
| 8 | *(empty)* | `9780062398123` | ISBN-10 derived → `0062398121`. **Loaded**, warning. |
| 9 | *(empty)* | `9.78043902348e+12` | Float with no ISBN-10 to derive from. **Loaded**, both NULL, warning. |
| 10 | `04390A3483` | *(empty)* | Letter in the digits. **Loaded**, both NULL, warning. |
| 11 | `1854433017` | *(empty)* | Checksum fails. **Loaded**, both NULL, warning. |
| 12 | `15287463097` | *(empty)* | 11 digits. **Loaded**, both NULL, warning. |
| 13 | `7442912` | *(empty)* | 7 digits; only one lost zero may be added. **Loaded**, both NULL, warning. |
| 14 | *(empty)* | *(empty)* | No ISBN. **Loaded**, both NULL, warning `no ISBN`. |
| 15 | `1555550126` | `9781555550134` | Both valid but disagree (`1555550126` → `9781555550127`). `isbn13` wins; ISBN-10 derived → `1555550134`. **Loaded**, warning naming `1555550126`. |
| 16 | *(empty)* | `76783609419.0` | Not 13 digits and not an `e+12` float. **Loaded**, both NULL, warning. |
| 17 | *(empty)* | `9790000000001` | Valid 979 ISBN-13; no ISBN-10 exists. **Loaded**, `isbn10` NULL, no warning. |
| 18 | `1416524797` on book 10013 | *(empty)* | ISBN already belongs to book 9. **Loaded**, both NULL, warning `ISBN already assigned to book 9`. |
| 19 | Book 10001, second identical row in the same file | | **Rejected**: `duplicate row in file`. |
| 20 | Book 132 re-delivered with a higher `ratings_count` | | **Updated**; the log lists the changed columns. |
| 21 | Book 25 re-delivered with a new title | | **Rejected**: `collision: title or author changed for book 25`. Stored row unchanged. |
| 22 | Book 25 re-delivered with both ISBN fields empty, otherwise identical | | Stored ISBNs kept; nothing else changed → **skipped**. |
| 23 | Row with a `book_id` but a blank title | | **Rejected**: missing title. |
