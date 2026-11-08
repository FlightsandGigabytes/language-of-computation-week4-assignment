# Comparison: my books.db vs. my partner's books.db

**The two databases hold the same data.**

| | Books | With an ISBN-13 | Without any ISBN |
|---|---|---|---|
| Mine (`books.db`) | 10,014 | 8,262 | 1,752 |
| Alex Bertelli's (`partner/books.db`) | 10,014 | 8,262 | 1,752 |

## How the comparison was done

- **Same book:** two rows are the same book when they have the same `book_id` (rules.md, rule 5).
  My `book_id` is stored as text and Alex's as an integer, so they were matched by value.
- **Columns compared (25):** `goodreads_book_id`, `best_book_id`, `work_id`, `books_count`, `isbn_raw`, `isbn13_raw`, `authors`, `original_publication_year`, `original_title`, `title`, `language_code`, `average_rating`, `ratings_count`, `work_ratings_count`, `work_text_reviews_count`, `ratings_1`, `ratings_2`, `ratings_3`, `ratings_4`, `ratings_5`, `image_url`, `small_image_url`, `isbn10`, `isbn13`, `last_source_file`.
- **Left out:** `last_loaded_at`, which only records when a row was loaded (rules.md, rule 8).
- Every other value was compared as text, and an empty (NULL) value matches only another NULL.
- Both databases have one table, `books`, with the same 27 columns in a different order.
- Files compared: `books.db` (sha256 `63601d595d2f1ae8…`) and `partner/books.db`
  (sha256 `224dbc386327a2a5…`), downloaded from
  https://github.com/havenlock/language-of-computation-week4-assignment/raw/assignment/books.db

## Differences

- Books only in mine: **0**
- Books only in my partner's: **0**
- Books in both with a different value: **0**

None of the 10,014 books differs in any compared column.

## Why our data differs

Our data does not differ, and in many ways that outcome was decided before either of us wrote a single test. In Module 3 every script followed its own rules and the results scattered; this time Alex and I started from one agreed rules.md, and whenever Claude stopped on a question the rules did not answer (what counts as the same data, which columns matter when a book arrives a second time, where the log records the columns that changed, and where the counts belong) we settled it together and added the identical line to both copies before either of us moved on. It reminded me of mission planning in the Army, where two crews can fly the same route and arrive at the same objective only if they briefed from the same plan and agreed on the contingencies ahead of time.

We also ran our code against exactly the same input files, byte for byte, which mattered more than I expected. My original books-batch2.csv had lost its Windows line endings, and had I not downloaded it again from Canvas our databases could have drifted apart for reasons that had nothing to do with the rules. The two databases do store the data a little differently (the columns are in a different order, and my book_id is saved as text while Alex's is saved as a number); however, those are choices each script made about storage, not differences in the data itself, and once the books were matched by value all 10,014 of them lined up in every compared column. In short, the rules, the tests and the code all held, and the agreement we reached up front is what allowed them to hold.
