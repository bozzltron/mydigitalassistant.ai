# Plan E — File support: diagnosis

Phase E.1, completed before any implementation. The plan called for determining what is
actually broken; this is that finding, and it reframes the fix.

## The design question, answered from the code

The user's proposal: *have no memories of files, only work with them directly and
verbatim — that is their value, the concreteness.* The code has already half-agreed,
and the conflict is exactly where it hasn't.

**Measurements (live brain, 2026-10-01):**

```
file frames: 10
slot keys on them:
  file_size              10
  file_safe_name         10
  file_name              10
  file_ext               10
  file_content_preview   10   <- content, in memory
  row_count               1
  columns                 1
```

`file_content_preview` is written on **every** file frame. `retrieval.py:164` excludes
it from the prompt, with this reasoning already in the source:

> *"Content snapshots are truncated on-disk hints. Full content lives in the sandbox
> and is read via `read_file` — never prefill it, or the model answers from a
> snippet."*

So the codebase **already decided** content must not live in memory. The decision is
enforced at render time and **not at write time**. That is the defect.

## Where the conflict actually happens

The content copy is not only dead weight — it is a **fallback that masks failures**.
From `tool_executor.py:845-856`:

```python
except FileNotFoundError:
    pass  # Not on disk — fall back to memory slots.
...
if not content:
    content = (
        slots_dict.get("file_content")
        or slots_dict.get("file_content_preview")
        or ""
    )
```

When the file is not on disk, `read_file` returns a **200-character preview from
memory** rather than reporting the file is missing. The model then answers from a
truncated, possibly stale copy, believing it read the file.

That is "memory of a file conflicting with the file" precisely: two representations,
and the stale one wins when the good one is unavailable. It also explains the reported
symptom — tasks saying *"I currently don't have any files available to access"* — as
the inverse case: memory and disk disagreeing about existence.

## The rule the finding implies

**Memory holds what a file *is*; it never holds what a file *contains*.**

| Kind | Home | Why |
|---|---|---|
| Bytes | Filesystem, verbatim | Source of truth. Never copied. |
| Content / preview | **Nowhere** | Read on demand. A copy goes stale silently and, worse, substitutes for the original. |
| Identity — name, path, ext, size, owner | Memory | Cheap, stable, and how the file is found at all. |
| Meaning — entities, what the file is about | Memory | The abstraction worth keeping; derived once, needs no bytes. |

Two consequences:

1. **Stop writing `file_content_preview` / `file_content`.** Three writers do:
   `tool_executor.py:1025` (create), `:1138` (edit), `main.py:2196` (upload).
2. **Remove the disk-failure fallback.** A missing file must report missing, not
   silently serve a preview. That is the difference between the agent knowing it
   failed and believing it succeeded.

## The memory layer keeps its place

The proposal's strongest form — no file memory at all — would remove things the memory
layer is good at and currently relies on:

- **Retrieval is how a file is found.** "Read my subscriber list" needs a searchable
  representation; listing a directory every turn is neither semantic nor scalable.
- **Ownership.** `owner_user_id` lives on the frame. The sandbox has no such concept,
  so dropping it would let household members read each other's files.
- **Uploads already extract meaning** (entities, questions) from content. That is the
  memory layer doing useful work on a file, not duplicating it.

So the fix is not *remove file memory*. It is *enforce the boundary the code already
drew*: identity and meaning in memory, bytes only on disk.

## What is still unknown

All three questions answered from the same live probe:

- **`file_content` (the other content key) is used nowhere.** 0 slots; only
  `file_content_preview` (10) exists. So the write-side fix touches one key, not two,
  though both stay in the hint list as a guard.
- **The 4 orphaned sandbox files are expected.** `list_files` has an explicit branch
  for files on disk with no frame, so it is designed behaviour rather than an accident.
- **`row_count` / `columns` are identity, not content.** One frame carries them and
  they are stable properties of the file (like size), so they stay. They are not
  derived from a preview and do not drift with the bytes.

The preview values confirm the drift risk concretely: `file_subscribers_active.csv`
holds a truncated CSV header row in memory while the file itself is on disk. If the
file were edited, that string would not change — and the `read_file` fallback would
serve it as though it were the content.


## Recommended shape for Plan E

**E.1 — this diagnosis.** Done.

**E.2 — enforce the boundary.**
- Stop writing content slots at the three write sites.
- Delete the disk-failure fallback; report a missing file as missing.
- Backfill: remove existing `file_content_preview` values (they are excluded from the
  prompt today, so nothing user-visible depends on them; the tools' `content_preview`
  field becomes empty and the UI already has a preview path).
- Correct `FILE_CONTENT_HINT_SLOTS` from a render-time skip to a write-time refusal,
  so a future writer cannot reintroduce it.

**E.3 — the journal** (the plan's original Phase 3), unchanged: a scheduled task writes
a dated file. Not a memory object — a file, read verbatim.
