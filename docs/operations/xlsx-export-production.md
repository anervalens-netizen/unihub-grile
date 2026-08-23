# XLSX production artifact contract

Status: operational contract for tracker items `XLSX-001`..`XLSX-004`.

## Durable renderer

Worker export jobs use `ugrile.services.revisioned_xlsx_export` and pin one
calendar-derived revision before rendering. A durable artifact must therefore
satisfy all of the following:

- per-store export contains exactly `Grila` + `Pontaj`;
- Pontaj and Grila are rendered from the same pinned revision;
- bulk ZIP contains one deterministic per-store workbook plus `manifest.json`;
- each manifest checksum/size matches the exact embedded workbook bytes;
- pontaj-only respects the explicit requested store set;
- workbook bytes contain no external-link package parts;
- workbook cells contain no formulas/external dependencies on Retail or Google;
- identical logical input produces identical XLSX/ZIP bytes and SHA-256.

OpenPyXL normally writes wall-clock OOXML core timestamps and ZIP member
timestamps. Durable rendering canonicalizes both before checksumming. This is
required for retry/idempotency evidence: checksum drift must mean content drift,
not a different serialization time.

## Local artifact root

Canonical API-created export targets are isolated by operation below:

```text
${UGR_S5_EXPORT_DIR:-<system-temp>}/ugrile-s5-exports/<operation-id>/<filename>
```

`<operation-id>` is derived from tenant + export kind + idempotency key. The
retention cleaner only operates on immediate entries below
`ugrile-s5-exports`; it never deletes arbitrary paths stored in database rows.

## Bounded retention

Two independent limits apply:

- `UGRILE_EXPORT_RETENTION_HOURS` — default `168` hours / 7 days;
- `UGRILE_EXPORT_MAX_OPERATIONS` — default `500` managed operation entries.

Age cleanup runs first, then the count cap removes the oldest remaining
unprotected entries until the configured maximum is reached.

Safety rules:

- a symlinked managed root is rejected;
- child symlinks are skipped and never followed;
- cleanup never walks outside the managed root;
- `--dry-run` reports the exact deletion set without mutation;
- a programmatic caller may protect the top-level operation containing a current
  artifact from age/count cleanup.

## Maintenance command

Preview:

```bash
cd backend
python -m ugrile.services.export_retention --dry-run
```

Apply configured policy:

```bash
cd backend
python -m ugrile.services.export_retention
```

Optional one-off overrides:

```bash
python -m ugrile.services.export_retention \
  --retention-hours 168 \
  --max-operations 500
```

For server testing, run the apply command from the same environment as the
worker at least daily (systemd timer/cron or equivalent). The repository does
not install host timers implicitly.

## Verification

Permanent automated tests parse the durable worker renderer with OpenPyXL and
raw ZIP inspection. They prove:

1. repeated per-store renders separated in wall-clock time are byte-identical;
2. the workbook reports the pinned revision and expected Grila/Pontaj layout;
3. bulk manifest size/checksum equals the embedded workbook;
4. scoped pontaj-only output contains only the requested store set;
5. no external-link parts or formula cells are present;
6. retention deletes expired/overflow operation entries while preserving
   filesystem boundaries and symlink safety.

No Retail mutation or live Google request is part of this contract.
