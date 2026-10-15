# Evidence attachments

- Who can upload: pentesters/admins (optionally **internal**, hidden from the
  client) and client users (always client-visible).
- Who can download: members of the same project; client users only
  non-internal files. Downloads are audited (`attachment_downloaded`).
- Allowed types: PNG, JPEG, GIF, PDF (content must match the file
  signature), TXT, LOG, MD, CSV, JSON (must be UTF-8 text without NUL bytes;
  JSON must parse). HTML, SVG, XML, archives and executables are refused.
- Size limit: `THRENDA_ATTACHMENT_MAX_BYTES` (default 10 MiB, max 20 MiB),
  also bounded by `THRENDA_MAX_UPLOAD_BYTES` for the whole request.
- Filenames are sanitised (directory parts and unusual characters removed)
  and only used as a display/download name, never as a filesystem path.
- Downloads are always `Content-Disposition: attachment`, with
  `X-Content-Type-Options: nosniff`, `Cache-Control: no-store` and a
  sandboxing CSP; the content type comes from the verified allowlist, never
  from the uploader.
- Storage: inside the database (`attachments.content`), so a database
  backup contains all evidence. Size the database accordingly; there is no
  per-project quota.
- Attachments and comments cannot be deleted or edited in this version (they
  are part of the record). See the known limitations in
  [pilot/atadas.md](pilot/atadas.md).
