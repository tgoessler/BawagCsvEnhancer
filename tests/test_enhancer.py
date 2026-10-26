import csv
import io
import re
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import enhancer


VALID_IBAN = "AT611904300234573201"
VALID_BIC = "BKAUATWW"
ACCOUNT = "AT123456789012345678"


def transaction_line(
    description: str = "Payment",
    transaction_type: str = "ZA",
    reference: str = "123456789",
    extra: str = "",
    booking_date: str = "01.01.2025",
    value_date: str = "02.01.2025",
    amount: str = "+1,00",
    currency: str = "EUR",
    account: str = ACCOUNT,
) -> str:
    suffix = f"|{extra}" if extra else ""
    return (
        f"{account};{description} {transaction_type}/{reference}{suffix};"
        f"{booking_date};{value_date};{amount};{currency}"
    )


def counterparty_row(
    description: str,
    transaction_type: str,
    extra: str,
) -> dict[str, str]:
    return {
        "description": description,
        "transaction_type": transaction_type,
        "extra": extra,
    }


def output_row(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in enhancer.OUTPUT_COLUMNS}
    row.update(overrides)
    return row


class TextAndAccountTests(unittest.TestCase):
    def test_is_valid_iban_accepts_valid_and_rejects_invalid_values(self) -> None:
        self.assertTrue(enhancer.is_valid_iban(VALID_IBAN))
        self.assertFalse(enhancer.is_valid_iban("AT12"))
        self.assertFalse(enhancer.is_valid_iban("AT611904300234573202"))

    def test_find_account_skips_invalid_iban_and_returns_valid_iban_with_bic(self) -> None:
        value = (
            "AT611904300234573202 invalid | "
            f"{VALID_BIC} {VALID_IBAN}|Alice Example"
        )

        match = enhancer.find_account(value)

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("iban"), VALID_IBAN)
        self.assertEqual(match.group("bic"), VALID_BIC)

    def test_find_account_returns_none_without_valid_iban(self) -> None:
        self.assertIsNone(enhancer.find_account("No account data"))

    def test_clean_text_normalizes_backslashes_and_whitespace(self) -> None:
        self.assertEqual(
            enhancer.clean_text("  Alice\\\\Example\t GmbH  "),
            "Alice Example GmbH",
        )

    def test_clean_extra_removes_valid_account_but_preserves_invalid_account(self) -> None:
        self.assertEqual(
            enhancer.clean_extra(f"{VALID_BIC} {VALID_IBAN}|Alice Example"),
            "Alice Example",
        )
        invalid = "AT611904300234573202"
        self.assertEqual(enhancer.clean_extra(f"{invalid}|Reference"), f"{invalid} Reference")

    def test_clean_merchant_removes_marker_and_location(self) -> None:
        self.assertEqual(
            enhancer.clean_merchant("00STO Example Shop\\\\Vienna"),
            "Example Shop",
        )


class CounterpartyExtractionTests(unittest.TestCase):
    def test_extracts_counterparty_from_iban_and_bic(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row(
                "Transfer",
                "ZA",
                f"{VALID_BIC} {VALID_IBAN}|Alice Example|Reference",
            )
        )

        self.assertEqual(
            result,
            {
                "counterparty": "Alice Example",
                "counterparty_iban": VALID_IBAN,
                "counterparty_bic": VALID_BIC,
            },
        )

    def test_keeps_account_data_when_counterparty_name_is_missing(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row("Transfer", "ZA", f"{VALID_BIC} {VALID_IBAN}")
        )

        self.assertEqual(result["counterparty"], "")
        self.assertEqual(result["counterparty_iban"], VALID_IBAN)
        self.assertEqual(result["counterparty_bic"], VALID_BIC)

    def test_extracts_name_after_iban_without_bic_or_pipe_separator(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row("Transfer", "FE", f"{VALID_IBAN} Synthetic Person")
        )

        self.assertEqual(result["counterparty"], "Synthetic Person")
        self.assertEqual(result["counterparty_iban"], VALID_IBAN)
        self.assertEqual(result["counterparty_bic"], "")

    def test_extracts_eleven_character_bic(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row(
                "Transfer",
                "OG",
                f"{VALID_BIC}XXX {VALID_IBAN}|Synthetic Company",
            )
        )

        self.assertEqual(result["counterparty"], "Synthetic Company")
        self.assertEqual(result["counterparty_bic"], f"{VALID_BIC}XXX")

    def test_iban_rule_has_precedence_over_card_rule(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row(
                "Bezahlung Karte",
                "MC",
                f"{VALID_BIC} {VALID_IBAN}|Bank Party|Card Merchant",
            )
        )

        self.assertEqual(result["counterparty"], "Bank Party")
        self.assertEqual(result["counterparty_iban"], VALID_IBAN)

    def test_extracts_new_card_payment_and_credit_merchants(self) -> None:
        for description in ("Bezahlung Karte", "Gutschrift Karte"):
            with self.subTest(description=description):
                result = enhancer.extract_counterparty(
                    counterparty_row(
                        description,
                        "MC",
                        "Terminal|00STO Card Shop\\\\Vienna",
                    )
                )
                self.assertEqual(result["counterparty"], "Card Shop")

    def test_extracts_old_pos_credit_merchant(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row(
                "Gutschrift POS Terminal",
                "VD",
                "Terminal|POS Credit Shop\\\\Graz",
            )
        )

        self.assertEqual(result["counterparty"], "POS Credit Shop")

    def test_extracts_pos_merchant_from_first_extra_segment(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row("POS Einkauf", "VD", "POS Shop\\\\Linz|Reference")
        )

        self.assertEqual(result["counterparty"], "POS Shop")

    def test_extracts_old_card_merchant_from_description(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row(
                "Synthetic Card Shop 0027N K4   29.11. 13:12|",
                "MC",
                "",
            )
        )

        self.assertEqual(result["counterparty"], "Synthetic Card Shop")
        self.assertEqual(result["counterparty_iban"], "")
        self.assertEqual(result["counterparty_bic"], "")

    def test_extracts_legacy_counterparty(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row(
                "Legacy transfer",
                "ZA",
                "Synthetic Person 60000 00516172428",
            )
        )

        self.assertEqual(result["counterparty"], "Synthetic Person")

    def test_ignores_cash_withdrawals_and_card_statements(self) -> None:
        cases = (
            ("Auszahlung Karte", "AUTOMAT   00001234 K010 01.01. 12:00"),
            ("Mastercard GOLD 202601", "Mastercard GOLD 202601|1"),
        )
        for description, extra in cases:
            with self.subTest(description=description):
                result = enhancer.extract_counterparty(
                    counterparty_row(description, "MC", extra)
                )
                self.assertEqual(result["counterparty"], "")

    def test_incomplete_special_formats_do_not_create_counterparty(self) -> None:
        cases = (
            counterparty_row("Bezahlung Karte", "MC", "Terminal"),
            counterparty_row("Bezahlung Karte", "MC", r"Terminal|\\Location"),
            counterparty_row("Gutschrift POS", "VD", "Terminal"),
            counterparty_row("Gutschrift POS", "VD", r"Terminal|\\Location"),
            counterparty_row("POS transaction", "VD", r"\\Location"),
            counterparty_row(r"\\Location 0027N K4", "MC", ""),
            counterparty_row("Legacy transfer", "ZA", r"\\ 60000 00516172428"),
        )
        for row in cases:
            with self.subTest(row=row):
                result = enhancer.extract_counterparty(row)
                self.assertEqual(result["counterparty"], "")

    def test_returns_empty_counterparty_when_no_rule_matches(self) -> None:
        result = enhancer.extract_counterparty(
            counterparty_row("Service fee", "SG", "Unrecognized data")
        )

        self.assertEqual(
            result,
            {
                "counterparty": "",
                "counterparty_iban": "",
                "counterparty_bic": "",
            },
        )


class FileProcessingTests(unittest.TestCase):
    def write_input(self, path: Path, lines: list[str]) -> None:
        path.write_text("\n".join(lines) + "\n", encoding="cp1252")

    def test_parse_input_file_parses_every_output_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.csv"
            self.write_input(
                input_path,
                [
                    transaction_line(
                        description="Invoice payment",
                        extra=f"{VALID_BIC} {VALID_IBAN}|Alice Example",
                        booking_date="31.01.2025",
                        value_date="01.02.2025",
                        amount="-1.234,56",
                    )
                ],
            )

            rows = list(enhancer.parse_input_file(input_path))

            self.assertEqual(len(rows), 1)
            expected = {
                "account": ACCOUNT,
                "description": "Invoice payment Alice Example",
                "transaction_type": "ZA",
                "reference": "123456789",
                "booking_date": "31.01.2025",
                "value_date": "01.02.2025",
                "amount": "-1.234,56",
                "currency": "EUR",
                "counterparty": "Alice Example",
                "counterparty_iban": VALID_IBAN,
                "counterparty_bic": VALID_BIC,
            }
            self.assertEqual(list(rows[0]), enhancer.OUTPUT_COLUMNS)
            for column, expected_value in expected.items():
                with self.subTest(column=column):
                    self.assertEqual(rows[0][column], expected_value)

    def test_parse_input_file_skips_blank_lines_and_reads_cp1252(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.csv"
            input_path.write_text(
                f"\n{transaction_line(description='Café')}\n\n",
                encoding="cp1252",
            )

            rows = list(enhancer.parse_input_file(input_path))

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["description"], "Café")

    def test_parse_input_file_keeps_unavailable_counterparty_columns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.csv"
            self.write_input(
                input_path,
                [
                    transaction_line(
                        description="Service fee",
                        transaction_type="SG",
                        reference="987654321",
                        amount="+12,34",
                        currency="USD",
                    )
                ],
            )

            row = next(enhancer.parse_input_file(input_path))

            self.assertEqual(row["description"], "Service fee")
            self.assertEqual(row["amount"], "+12,34")
            self.assertEqual(row["currency"], "USD")
            self.assertEqual(row["counterparty"], "")
            self.assertEqual(row["counterparty_iban"], "")
            self.assertEqual(row["counterparty_bic"], "")

    def test_parse_input_file_rejects_malformed_line_with_file_and_line_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.csv"
            self.write_input(input_path, [transaction_line(), "malformed"])
            expected_error = re.escape(f"Line 2 in '{input_path}' does not match")

            with self.assertRaisesRegex(ValueError, expected_error):
                list(enhancer.parse_input_file(input_path))

    def test_write_output_file_creates_parent_header_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "output.csv"
            rows = [
                output_row(description="First", amount="+1,00"),
                output_row(description="Second", amount="-2,00"),
            ]

            row_count = enhancer.write_output_file(rows, output)

            self.assertEqual(row_count, 2)
            text = output.read_text(encoding="utf-8-sig")
            self.assertEqual(text.splitlines()[0], ";".join(enhancer.OUTPUT_COLUMNS))
            with output.open("r", encoding="utf-8-sig", newline="") as output_file:
                written_rows = list(csv.DictReader(output_file, delimiter=";"))
            self.assertEqual([row["description"] for row in written_rows], ["First", "Second"])

    def test_write_output_file_preserves_existing_output_when_rows_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output.csv"
            output.write_bytes(b"existing output")

            def failing_rows():
                yield output_row(description="First")
                raise ValueError("row generation failed")

            with self.assertRaisesRegex(ValueError, "row generation failed"):
                enhancer.write_output_file(failing_rows(), output)

            self.assertEqual(output.read_bytes(), b"existing output")
            self.assertEqual(list(root.glob(".output.csv.*.tmp")), [])

    def test_write_output_file_handles_failure_before_temporary_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.csv"
            with patch(
                "enhancer.tempfile.NamedTemporaryFile",
                side_effect=OSError("temporary file failed"),
            ):
                with self.assertRaisesRegex(OSError, "temporary file failed"):
                    enhancer.write_output_file([], output)

            self.assertFalse(output.exists())

    def test_parse_files_combines_files_in_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.csv"
            second = root / "second.csv"
            output = root / "combined.csv"
            self.write_input(first, [transaction_line(description="First")])
            self.write_input(
                second,
                [
                    transaction_line(
                        description="Second",
                        reference="123456788",
                        amount="-2,50",
                    )
                ],
            )

            row_count = enhancer.parse_files([first, second], output)

            self.assertEqual(row_count, 2)
            with output.open("r", encoding="utf-8-sig", newline="") as output_file:
                rows = list(csv.DictReader(output_file, delimiter=";"))
            self.assertEqual([row["description"] for row in rows], ["First", "Second"])

    def test_parse_files_expands_wildcards_in_sorted_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.csv"
            second = root / "b.csv"
            (root / "ignored.csv").mkdir()
            output = root / "combined.out"
            self.write_input(first, [transaction_line(description="First")])
            self.write_input(
                second,
                [transaction_line(description="Second", reference="123456788")],
            )

            row_count = enhancer.parse_files([root / "*.csv"], output)

            self.assertEqual(row_count, 2)
            with output.open("r", encoding="utf-8-sig", newline="") as output_file:
                rows = list(csv.DictReader(output_file, delimiter=";"))
            self.assertEqual([row["description"] for row in rows], ["First", "Second"])

    def test_parse_files_removes_duplicate_transactions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.csv"
            second = root / "second.csv"
            output = root / "combined.csv"
            self.write_input(
                first,
                [
                    transaction_line(description="Original"),
                    transaction_line(description="Unique", reference="123456788"),
                ],
            )
            self.write_input(
                second,
                [
                    transaction_line(description="Changed duplicate", amount="-9,99"),
                    transaction_line(
                        description="Other account",
                        account="AT987654321098765432",
                    ),
                ],
            )

            row_count = enhancer.parse_files([first, second], output)

            self.assertEqual(row_count, 3)
            with output.open("r", encoding="utf-8-sig", newline="") as output_file:
                rows = list(csv.DictReader(output_file, delimiter=";"))
            self.assertEqual(
                [row["description"] for row in rows],
                ["Original", "Unique", "Other account"],
            )

    def test_parse_files_rejects_wildcard_without_matching_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pattern = root / "missing-*.csv"
            output = root / "output.csv"

            with self.assertRaisesRegex(
                FileNotFoundError,
                "Input pattern matched no files",
            ):
                enhancer.parse_files([pattern], output)

            self.assertFalse(output.exists())

    def test_parse_files_rejects_empty_input_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.csv"
            with self.assertRaisesRegex(ValueError, "At least one input file"):
                enhancer.parse_files([], output)

    def test_parse_files_rejects_missing_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.csv"
            output = root / "output.csv"

            with self.assertRaisesRegex(FileNotFoundError, re.escape(str(missing))):
                enhancer.parse_files([missing], output)

            self.assertFalse(output.exists())

    def test_parse_files_rejects_input_equal_to_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transactions.csv"
            self.write_input(path, [transaction_line()])

            with self.assertRaisesRegex(ValueError, "must not be identical"):
                enhancer.parse_files([path], path)

    def test_malformed_later_input_preserves_output_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.csv"
            malformed = root / "malformed.csv"
            output = root / "combined.csv"
            self.write_input(valid, [transaction_line(description="Valid")])
            self.write_input(malformed, ["not a BAWAG transaction"])
            output.write_bytes(b"existing output")

            expected_error = re.escape(f"Line 1 in '{malformed}' does not match")
            with self.assertRaisesRegex(ValueError, expected_error):
                enhancer.parse_files([valid, malformed], output)

            self.assertEqual(output.read_bytes(), b"existing output")
            self.assertEqual(list(root.glob(".combined.csv.*.tmp")), [])


class CliTests(unittest.TestCase):
    def test_main_combines_multiple_inputs_and_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.csv"
            second = root / "second.csv"
            output = root / "output.csv"
            first.write_text(transaction_line(description="First") + "\n", encoding="cp1252")
            second.write_text(
                transaction_line(description="Second", reference="123456788") + "\n",
                encoding="cp1252",
            )
            stdout = io.StringIO()

            with patch.object(
                sys,
                "argv",
                ["enhancer.py", str(first), str(second), str(output)],
            ), redirect_stdout(stdout):
                exit_code = enhancer.main()

            self.assertEqual(exit_code, 0)
            self.assertIn("Success: wrote 2 transactions", stdout.getvalue())
            self.assertTrue(output.is_file())

    def test_main_accepts_quoted_wildcard_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.csv").write_text(
                transaction_line(description="First") + "\n",
                encoding="cp1252",
            )
            (root / "b.csv").write_text(
                transaction_line(description="Second", reference="123456788") + "\n",
                encoding="cp1252",
            )
            output = root / "output.out"
            stdout = io.StringIO()

            with patch.object(
                sys,
                "argv",
                ["enhancer.py", str(root / "*.csv"), str(output)],
            ), redirect_stdout(stdout):
                exit_code = enhancer.main()

            self.assertEqual(exit_code, 0)
            self.assertIn("Success: wrote 2 transactions", stdout.getvalue())

    def test_main_reports_error_for_missing_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.csv"
            output = root / "output.csv"
            stderr = io.StringIO()

            with patch.object(
                sys,
                "argv",
                ["enhancer.py", str(missing), str(output)],
            ), redirect_stderr(stderr):
                exit_code = enhancer.main()

            self.assertEqual(exit_code, 1)
            self.assertIn("Error: Input file not found", stderr.getvalue())
            self.assertFalse(output.exists())

    def test_script_entry_point_exits_with_main_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.csv"
            output = root / "output.csv"
            input_path.write_text(transaction_line() + "\n", encoding="cp1252")

            with patch.object(
                sys,
                "argv",
                ["enhancer.py", str(input_path), str(output)],
            ), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(enhancer.__file__, run_name="__main__")

            self.assertEqual(raised.exception.code, 0)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
