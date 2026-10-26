# BAWAG CSV Enhancer

A dependency-free Python CLI that cleans BAWAG/PSK transaction exports and adds detected counterparty information.

## Requirements and usage

Requires Python 3.10 or newer.

```bash
python enhancer.py <input.csv> [<input.csv> ...] <output.csv>
```

Examples:

```bash
python enhancer.py bawag-export.csv cleaned-transactions.csv
python enhancer.py january.csv february.csv march.csv cleaned-transactions.csv
python enhancer.py "exports/*.csv" cleaned-transactions.csv
```

Input arguments may contain wildcard patterns such as `*`, `?`, and `**`. Quoting a pattern lets the program expand it consistently across shells. Matches are processed in sorted order; a pattern without matching files causes an error.

Each input is read as `cp1252`. Duplicate transactions with the same account and reference are written only once; the first occurrence in input order is retained. The resulting transactions are sorted by `reference` in descending order and written with one output header. The semicolon-delimited output is encoded as UTF-8 with BOM (`utf-8-sig`). No input may be the same file as the output.

## Input format

Each non-empty line must match the BAWAG export layout:

```text
AT##################;Description TT/123456789|Extra data;DD.MM.YYYY;DD.MM.YYYY;+1.234,56;EUR
```

The parser expects an Austrian account number, a two-letter transaction type, a nine-digit reference, two dates, a signed European-format amount, and a three-letter currency code. Header rows and unsupported layouts cause an error containing the line number.

## Output columns

```text
account;description;transaction_type;reference;booking_date;value_date;
amount;currency;counterparty;counterparty_iban;counterparty_bic
```

The program normalizes text, merges supplementary data into the description, validates detected IBANs with modulo 97, and recognizes counterparties from IBAN/BIC data, card payments, POS transactions, and legacy formats. Unavailable counterparty fields remain empty.

German values such as `Bezahlung Karte` and `Gutschrift POS` are intentionally used internally because they appear in BAWAG exports.

## Safety and privacy

Output is written to a temporary file and replaces the target only after all input files have been processed successfully. On failure, existing output remains unchanged.

Bank exports contain sensitive data. Use synthetic test data and do not commit real CSV files. CSV files are not globally excluded by `.gitignore`.

## License

[MIT](LICENSE)
