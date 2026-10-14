# Local delivery fixes evidence

These are **path-redacted views**, not untouched original bytes. See
[privacy/provenance](../../../EVIDENCE_PRIVACY.md) and [REDACTION.json](REDACTION.json).

- `backup-clock-red-confirmed.txt`: two regression cases failed before the fix.
- `backup-regression.txt`: all 33 backup tests passed after the fix.
- `pytest-full.txt`: first full run, 482 passed / one shell subprocess timeout.
- `pytest-serial.txt`: subsequent serial run, all 483 passed, including that case.
- `backup-runtime.txt`: real Docker backup/restore experiment stage output.
- `backup-runtime-summary.json`: derived summary plus the 57-file source hash
  manifest. This is a summary; complete untouched evidence is kept privately.

No failed output was overwritten or presented as passing. This verification used
the working source before the fix commit; the final local receipt checks that
the committed runtime sources have exactly these hashes. It does not assert a
GitHub CI run for these unpushed commits.
