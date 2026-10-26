#!/usr/bin/env python3
"""Parse and clean a BAWAG/PSK transaction export and save it as a new CSV."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import tempfile
from pathlib import Path


LINE_PATTERN = re.compile(
    r"^(?P<account>AT\d{18});"
    r"(?P<description>.*?)[ \t]*"
    r"(?P<transaction_type>[A-Z]{2})/(?P<reference>\d{9})"
    r"(?:\|(?P<extra>[^;\r\n]*))?;"
    r"(?P<booking_date>\d{2}\.\d{2}\.\d{4});"
    r"(?P<value_date>\d{2}\.\d{2}\.\d{4});"
    r"(?P<amount>[+-](?:\d{1,3}(?:\.\d{3})*|\d+),\d{2});"
    r"(?P<currency>[A-Z]{3})$"
)

ACCOUNT_PATTERN = re.compile(
    r"(?<![A-Z0-9])"
    r"(?:(?P<bic>[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\s+)?"
    r"(?P<iban>[A-Z]{2}\d{2}[A-Z0-9]{11,30})(?=\s|\||$)"
)

# Older card transactions often contain the merchant only in the description,
# followed by a terminal identifier and card number, e.g. "HOFER ... 0236P K3".
OLD_CARD_MERCHANT_PATTERN = re.compile(
    r"^(?P<merchant>.+?)\s+[A-Z0-9]{4}[A-Z]\s+K\d\b"
)

# Older internal transactions sometimes use a bank code and account number
# instead of an IBAN, e.g. "Name 60000 00516172428".
LEGACY_PARTY_PATTERN = re.compile(
    r"^(?P<party>.+?)\s+\d{5}\s+\d{11}(?:\||$)"
)

OUTPUT_COLUMNS = [
    "account",
    "description",
    "transaction_type",
    "reference",
    "booking_date",
    "value_date",
    "amount",
    "currency",
    "counterparty",
    "counterparty_iban",
    "counterparty_bic",
]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse and clean a BAWAG/PSK transaction export, identify "
            "counterparties, and write the result to a new CSV file."
        )
    )
    parser.add_argument("input", type=Path, help="Path to the original CSV file")
    parser.add_argument("output", type=Path, help="Path to the new CSV file")
    return parser.parse_args()


def is_valid_iban(iban: str) -> bool:
    """Validate an IBAN with the official modulo-97 checksum."""
    if not 15 <= len(iban) <= 34:
        return False

    remainder = 0
    for character in iban[4:] + iban[:4]:
        digits = str(ord(character) - 55) if character.isalpha() else character
        for digit in digits:
            remainder = (remainder * 10 + int(digit)) % 97

    return remainder == 1


def find_account(value: str) -> re.Match[str] | None:
    """Find the first valid IBAN and its optional BIC."""
    return next(
        (
            match
            for match in ACCOUNT_PATTERN.finditer(value)
            if is_valid_iban(match.group("iban"))
        ),
        None,
    )


def clean_text(value: str) -> str:
    """Replace backslashes, normalize whitespace, and trim the text."""
    value = re.sub(r"\\+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_extra(value: str) -> str:
    """Remove account data and replace separators in extra text with spaces."""

    def remove_valid_account(match: re.Match[str]) -> str:
        return "" if is_valid_iban(match.group("iban")) else match.group(0)

    value = ACCOUNT_PATTERN.sub(remove_valid_account, value)
    return value.replace("|", " ").strip()


def clean_merchant(value: str) -> str:
    """Remove location data and legacy export markers from a merchant name."""
    merchant = value.split("\\\\", 1)[0]
    merchant = re.sub(r"^00STO\s+", "", merchant, flags=re.IGNORECASE)
    return clean_text(merchant)


def extract_counterparty(row: dict[str, str]) -> dict[str, str]:
    """Identify a counterparty using IBAN, card, and legacy-format rules."""
    description = row["description"].strip()
    transaction_type = row["transaction_type"]
    extra = row["extra"].strip()
    segments = [segment.strip() for segment in extra.split("|")] if extra else []

    result = {
        "counterparty": "",
        "counterparty_iban": "",
        "counterparty_bic": "",
    }

    account_match = find_account(extra)
    if account_match is not None:
        result["counterparty_iban"] = account_match.group("iban")
        result["counterparty_bic"] = account_match.group("bic") or ""

        remainder = extra[account_match.end() :].lstrip()
        if remainder.startswith("|"):
            remainder = remainder[1:]

        party = clean_text(remainder.split("|", 1)[0])
        if party:
            result["counterparty"] = party
            return result

    # For newer card payments and card credits, the merchant is usually in
    # the second pipe-delimited segment.
    if transaction_type == "MC" and (
        description == "Bezahlung Karte" or description.startswith("Gutschrift")
    ):
        if len(segments) >= 2:
            merchant = clean_merchant(segments[1])
            if merchant:
                result["counterparty"] = merchant
                return result

    # Older POS credits also store terminal data and the merchant in separate
    # pipe-delimited segments.
    if transaction_type == "VD" and description.startswith("Gutschrift POS"):
        if len(segments) >= 2:
            merchant = clean_merchant(segments[1])
            if merchant:
                result["counterparty"] = merchant
                return result

    # Some older POS transactions store the merchant in the extra field.
    if transaction_type == "VD" and description.startswith("POS") and segments:
        merchant = clean_merchant(segments[0])
        if merchant:
            result["counterparty"] = merchant
            return result

    # Even older card formats put the merchant at the start of the description,
    # before the terminal identifier and card number.
    old_card_match = OLD_CARD_MERCHANT_PATTERN.search(description)
    if old_card_match is not None:
        merchant = clean_merchant(old_card_match.group("merchant"))
        if merchant:
            result["counterparty"] = merchant
            return result

    legacy_match = LEGACY_PARTY_PATTERN.search(extra)
    if legacy_match is not None:
        party = clean_text(legacy_match.group("party"))
        if party:
            result["counterparty"] = party
            return result

    return result


def parse_file(input_path: Path, output_path: Path) -> int:
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output files must not be identical.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    row_count = 0

    # Write to a temporary file first so an existing output file remains
    # completely intact if processing fails.
    try:
        with input_path.open(
            "r", encoding="cp1252", newline=""
        ) as input_file, tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temporary_path = Path(output_file.name)
            writer = csv.DictWriter(
                output_file,
                fieldnames=OUTPUT_COLUMNS,
                delimiter=";",
                quoting=csv.QUOTE_MINIMAL,
                lineterminator="\n",
            )
            writer.writeheader()

            for line_number, raw_line in enumerate(input_file, start=1):
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue

                match = LINE_PATTERN.fullmatch(line)
                if match is None:
                    raise ValueError(
                        f"Line {line_number} does not match the expected format:\n{line}"
                    )

                row = match.groupdict(default="")
                row.update(extract_counterparty(row))

                row["extra"] = clean_extra(row["extra"])
                row = {key: clean_text(value) for key, value in row.items()}

                description = row["description"].rstrip("|").rstrip()
                row["description"] = " ".join(
                    part for part in (description, row["extra"]) if part
                ).rstrip("|").rstrip()
                del row["extra"]

                writer.writerow(row)
                row_count += 1

        temporary_path.replace(output_path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise

    return row_count


def main() -> int:
    args = parse_arguments()

    try:
        row_count = parse_file(args.input, args.output)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Success: wrote {row_count} transactions to '{args.output}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
