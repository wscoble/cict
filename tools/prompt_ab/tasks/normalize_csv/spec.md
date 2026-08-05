Add a scripts/normalize_csv.py CLI tool that reads a CSV file and prints a normalized version to stdout. Use only the Python standard library (csv, argparse).

Arguments:
- --input <path>   (required) input CSV file
- --key <column>   (required) column name to deduplicate by
- --strict         (optional flag) fail on unparseable values in a detected-numeric column
- --output <path>  (optional) write to this file instead of stdout

Normalization rules:
1. Strip leading/trailing whitespace from every cell.
2. Lowercase all header names (after stripping).
3. For each column, look at its non-empty values: if all parse as integers, render that column as integers; else if all parse as numbers (int or float), render as floats; otherwise keep as strings. An empty cell stays empty.
4. Skip rows that are entirely blank (every cell empty or whitespace).
5. Deduplicate by the --key column, keeping the LAST occurrence of each key value. Emit rows in the order of their last occurrence.

Error handling (write a clear message to stderr and exit with the exact code):
- --input file does not exist  -> exit 2
- --key column not in headers   -> exit 3
- --strict set and a column is inconsistent (at least one non-empty value parses as a number AND at least one does not) -> exit 4
  (a column that is entirely non-numeric is NOT a strict error; without --strict, inconsistent columns are kept as strings)
- empty input file (0 bytes)     -> exit 0 with empty output
- missing required argument      -> non-zero exit (argparse default is fine)

The first output line is the lowercased, stripped header row. Then one line per surviving data row.
