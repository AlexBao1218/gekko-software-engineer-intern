# Evidence privacy and provenance

The evidence directories contain recorded test output, including failed runs.
Some files are now **path-redacted views**, not byte-for-byte original logs.
[EVIDENCE_REDACTION.json](EVIDENCE_REDACTION.json) identifies every changed file,
its original SHA-256 and its redacted SHA-256 at local cleanup commit `ff03e2f`.
Later editorial updates to a document can change its hash again; historical log
views must remain frozen. New verification output has a separate receipt.

Only path prefixes were replaced:

| Placeholder | Meaning |
|---|---|
| `<WORKSPACE>` | Original local repository root |
| `<USER_HOME>` | Original macOS user home |
| `<TEMP>` | Per-user macOS temporary root |
| `<CI_WORKSPACE>` / `<CI_TEMP>` / `<CI_HOME>` | GitHub runner paths |

These are labels in historical evidence, not literal shell commands. Use
[START_HERE.zh-CN.md](START_HERE.zh-CN.md) for portable, current commands.
No pass/fail result, duration, exception, case/export identity, receipt, CSV
content, business row or source hash was rewritten to make an experiment pass.
JSON files remain valid JSON. Old source manifests describe the source revision
used by the corresponding historical run, not today's modified source.

Raw originals are preserved locally in an ignored archive and per-file backup
under `.gekko-setup/delivery-fixes-20261014/`. Do not upload that directory.
Original hashes identify these private raw bytes; redacted hashes identify the
corresponding tracked views. Documentation claiming a historical run recorded
"raw output" describes how it was captured, not a claim that its current
path-redacted view has the same bytes.

This cleanup changes the current working tree through ordinary new commits.
It does **not** rewrite Git history or remove data previously published through
GitHub's fork network. The repository stays private and no push is authorized.
Candidate reflection is left untouched. All input/application identities in the
supplied sample are synthetic; repository URLs and required project identity
remain readable.
