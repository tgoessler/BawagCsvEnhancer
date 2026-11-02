# BAWAG CSV Enhancer

A dependency-free Python CLI that cleans BAWAG/PSK giro and credit card exports and adds counterparty information.

## Requirements and usage

Requires Python 3.10 or newer.

```bash
python enhancer.py <input.csv> [<input.csv> ...] <output.csv>
```

Multiple files and quoted wildcard patterns are supported, for example:

```bash
python enhancer.py "exports/*.csv" cleaned-transactions.csv
```

Patterns support `*`, `?`, and recursive `**`. Arguments retain their order; wildcard matches are sorted by path. Unmatched patterns cause an error.

## Input format

Input is read as `cp1252`, without a header. Both layouts can occur in the same file or invocation.

**Giro account:**

```text
AT##################;Description TT/123456789|Extra data;DD.MM.YYYY;DD.MM.YYYY;+1.234,56;EUR
```

**Credit card:**

```text
AT##################;Example Shop|01234567890123456789012;DD.MM.YYYY;DD.MM.YYYY;-12,34;EUR
AT##################;EINZUGSBETRAG;DD.MM.YYYY;DD.MM.YYYY;+12,34;EUR
```

Both require an Austrian account number, `DD.MM.YYYY` dates, a signed European-format amount, and a three-letter currency. Giro rows also require a two-letter transaction type and nine-digit reference; their supplementary data is optional. Credit card booking dates must be valid calendar dates.

For credit cards, text before `|` becomes both `description` and `counterparty`; the optional numeric suffix becomes `counterparty_iban`. This identifier is **not an IBAN** and retains its full length and leading zeros. Without `|`, the text remains in `description`, while `counterparty` and `counterparty_iban` stay empty. `transaction_type`, `reference`, and `counterparty_bic` stay empty.

Empty lines are skipped. Headers, malformed non-empty lines, and unsupported layouts fail with a line number; malformed giro markers are not treated as credit card descriptions.

## Output

```text
account;description;transaction_type;reference;booking_date;value_date;
amount;currency;counterparty;counterparty_iban;counterparty_bic
```

Output uses semicolons, one header, and UTF-8 with BOM (`utf-8-sig`). Dates and amounts remain strings. Missing fields are empty.

- **Cleaning:** normalize whitespace and backslashes, merge supplementary text into descriptions, and collapse consecutive periods only in `description`.
- **Counterparties:** giro rules recognize IBAN/BIC, card, POS, and legacy formats; the first successful rule wins. Detected IBANs require a valid modulo-97 checksum; numeric credit card identifiers are exempt.
- **Duplicates:** match by account plus giro reference or credit card identifier, keeping the first occurrence. The two identifier types are separate; rows without identifiers are all retained.
- **Sorting:** giro rows first, by reference descending; credit card rows next, by booking date newest first, including rows without identifiers. Equal sort keys retain input order.
- **Logging:** only after a successful export, stderr reports the total removed, including zero: `Info: removed 0 duplicate transactions.`

## Safety and privacy

Input and output must be different files. Output is written to a temporary file and replaces the target only on success; failures preserve existing output and remove temporary files.

Bank exports contain sensitive data. Use synthetic test fixtures and never commit real CSV files; `.gitignore` does not globally exclude them.

## License

[MIT](LICENSE)
