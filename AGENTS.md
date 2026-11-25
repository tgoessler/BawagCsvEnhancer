# AGENTS.md

## Project context

Read [README.md](README.md) for the project purpose, requirements, CLI usage, input format, output schema, encoding, safety behavior, and privacy rules. Treat that documented behavior as the public contract.

The implementation is contained in `enhancer.py`. Code, identifiers, comments, and CLI messages are English. German literals used to match values from BAWAG exports must remain unchanged unless input support changes.

## Validation commands

```bash
python -m py_compile enhancer.py
python -m unittest discover -v
```

On Windows, use `py` instead of `python`. The `unittest` suite is in `tests/` and requires no third-party dependencies.

## Change guidelines

- Prefer focused, standard-library-only changes with type hints and concise docstrings.
- Preserve strict parsing: malformed non-empty lines and unknown layouts must fail clearly. Match standalone credit card text suffixes explicitly; keep supplementary text separate from identifiers.
- Keep automatic layout detection as the default. An explicit `--format` must reject mismatched rows, not filter them out.
- Preserve `OUTPUT_COLUMNS` names and order unless a schema change is requested.
- Keep dates and amounts as strings.
- Preserve counterparty rule precedence; the first successful rule wins.
- Validate all rows before deduplication. Keep shared-ID card purchases and their foreign-currency fees separate when deduplicating.
- Preserve temporary-file cleanup and atomic output replacement.
- Update `README.md` whenever public behavior changes.

For parser changes, use synthetic fixtures covering valid and malformed input, affected counterparty formats, cleanup behavior, exact output headers, and failure handling. Prefer `unittest` and temporary directories. If local reference CSVs are available, compare new exports byte for byte using temporary outputs without overwriting the references.

Follow the privacy guidance in `README.md`: do not echo raw transactions in diagnostics. Check `git status` before finishing.
