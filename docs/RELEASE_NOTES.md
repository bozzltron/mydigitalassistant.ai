# MyDigitalAssistant.ai — release notes

## v0.11.2-alpha

**Voice mode listens like it should, and read-aloud actually reads.**

Three voice-output defects and one structural listening gap, all found by
tracing the settings and audio paths end to end.

- **Read-aloud plays.** The spoken utterance was never retained, so a
  garbage-collected utterance could stop mid-sentence and never fire
  `onend` — which left the "agent is speaking" gate stuck true, keeping the
  microphone shut. The utterance is now held until it finishes, and gets a
  `lang` fallback for engines that drop one with neither voice nor lang set.
- **Turning read-aloud on works.** A reply was marked "spoken" even when
  read-aloud was off, so switching it on could not read the answer that
  prompted the switch. It now marks only what it speaks.
- **The voice setting sticks.** The voice `<select>` set its value before
  Chrome's asynchronous `voiceschanged` populated the options, so the
  browser dropped it to "" and the panel showed the wrong voice — looking
  exactly like the setting had not persisted. It now re-applies the saved
  value once the options exist. (Persistence itself was already correct.)
- **Voice mode listens through transcription.** The microphone used to be
  shut for the entire `/transcribe` round trip, so anything said between
  utterances was dropped. It now stays open whenever voice mode is on and
  the agent is not speaking; captures are queued and transcribed one at a
  time, so order is preserved. A busy agent still queues the transcript,
  and queued items remain removable while you wait.

## v0.11.1-alpha

**Following through on v0.11.0: the task loop's edges are cleaned up.**

Running tasks through the real loop was right, and it surfaced three rough edges
at the seams — each a small correctness issue rather than a redesign.

- **An alert no longer swallows the sources footer.** Because a task's report now
  carries the loop's own footers, a `**Sources:**` list (or the "answered from
  memory" marker) landed *after* the `ALERT:` line — and the parser treats
  everything from that line on as the alert body. The sources became the alert's
  "reason" and dropped out of the stored summary. Footers are now stripped before
  the alert is parsed.
- **Scheduling a task is consent.** A scheduled task used to hit Brave's
  sensitivity gate and, finding no one present to answer, would replace its
  output with a "do you want to proceed?" prompt. The user asked for the search
  when they created the task, so the task's request now carries `search_consent`
  and the gate is skipped.
- **Run-now has room to finish.** The `run_scheduled_task` tool had a 60s cap,
  but a run-now executes the whole loop (routing + forced search + tool loop +
  generation) and a single LLM call may take 600s. A big task was cut off
  mid-run. The ceiling is now `SCHEDULED_TASK_TIMEOUT_SECONDS` (default 900s);
  the scheduler's own firing path still has no cap.

## v0.11.0-alpha

**Scheduled tasks run the real loop now.** The daily list was executing through a
third, partial copy of the pipeline — and it showed: the job-postings monitor
searched with its own instruction as the query, so Brave returned documentation
for job-search *tools* instead of postings.

- **One loop, three consumers.** `run_scheduled_task` is now a thin wrapper over
  the same loop as chat and the UI stream. A task's script drives routing and
  retrieval, is delivered as the explicit user turn, and search is forced. The
  ~160-line duplicate pipeline is gone.
- **Tasks can search properly, and use tools.** Because it is the full loop, the
  model can `recall` the user's criteria and `web_search` a targeted query while
  a task runs — it had **no tools at all** before. The search query is the
  router's, not the instruction.
- **The agent's cry for help was correct.** "Search results surface tool
  documentation" was the presence rule working: it could not settle the task and
  said so. It can now do better — and still alerts when it genuinely cannot.

### Behaviour changes

- A task run logs its script as a **user** turn as well as the answer (the full
  loop logs both turns). The answer is still the assistant episode the scheduler
  links to the daily-run frame.
- Facts are **no longer mined from the task's own report**
  (`extract_facts_from_document` on the output). That was self-referential and
  redundant with search extraction; the report is already an episode.

## v0.10.1-alpha

**Editing an existing file works, and failing to edit tells you why.**

The agent could create files but not reliably change them — found when it tried
to update a release document and reported "the edit didn't find the exact text
to replace."

- **A binary document could not be edited at all.** `read_file` shows a `.docx`'s
  text, but `edit_file` replaces bytes on disk — and a `.docx` is a zip — so the
  text could never match, and the error was a misleading "old_text not found".
  `edit_file` now refuses a binary document and says what to do instead: read it,
  then rewrite the whole file with `write_file` (which re-renders a real document
  and refreshes memory).
- **A near-miss edit dead-ended.** A collapsed blank line or a trailing space was
  enough to defeat an otherwise-correct edit, with nothing to correct it with.
  Matching is now whitespace-tolerant (words must still match exactly), and a
  failure names the file size and the closest region so the model can retry
  precisely.
- **The guidance was wrong.** The tool description told the model to use
  `edit_file` for "updating documents", which is the path that cannot work. It
  now says `edit_file` is for text files, and documents are read and rewritten.

## v0.10.0-alpha

**The agent knows what day it is, and there is one loop instead of two.**

### It knows the date and time

The model had no clock, so "what's today's date?" was answered from its training
cutoff. A current date/time line is now injected into the system prompt — in the
user's timezone, resolved the same way the scheduler resolves it
(`DAILY_TASKS_TZ` > `TZ` > host), so a US user asking at 19:00 is not told it is
tomorrow. It is injected rather than exposed as a tool, so it costs no
round-trip. The `.ics` writer now stamps the user's date too, not the container's
UTC date.

### One cognitive loop

`chat()` and `chat_stream()` were two ~600-line copies of the same loop, kept in
step by hand. They had drifted **six** times, each a user-visible bug: a stalled
search gate that hung the live stream, a generation failure that escaped instead
of degrading, a `compute` result shown and never stored, a missing sources
footer, a missing learning summary, and a correction turn logged twice.
`chat_stream()` is now a pass-through to one loop (`_run_turn`) and `chat()`
drains it, so a change reaches both by construction. The best-tested path is now
the one production runs.

Closing the drift also fixed three parity gaps: the stream now carries
`citations` and `memory_context` (so the CLI and `/chat` adapter lose nothing),
scheduled-task and correction turns emit their metadata like every other turn,
and a streamed turn's reasoning trace is persisted instead of dropped.

### Known limits

- **The date is a snapshot of the turn.** It is read when the prompt is built,
  not per-token; a turn spanning midnight reports the day it started.
- **`run_scheduled_task` is still its own path.** Deliberately simpler
  (always-search, no routing/extraction); not folded into the one loop.

## v0.9.1-alpha

**The suite and the docs stop drifting.** A maintenance release — no feature
changes.

- **Test audit.** Removed eight tests that could not fail (empty bodies), fixed
  one that always skipped, renamed a fixture that was collected as a test, and
  strengthened several whose only check was "does not raise". A new
  `test_suite_hygiene.py` fails the suite if any test has no assertion, so this
  cannot regrow.
- **Docs reconciled with the code.** The prod port (8444, not 8443), two
  `assistant db` commands that do not exist, the backup schedule's real name and
  interval, and the max/math model defaults (opt-in, not `qwen3.8:27b`).
- **Changelog tightened** from 701 to 254 lines, with its duplicated README tail
  removed.

## v0.9.0-alpha

**When you ask it to save something, you get a file — and you can find it.**

`write_file` wrote the text with a document extension: a `.docx` was the literal
string and the owning library called it corrupt (`PackageNotFoundError`;
`BadZipFile` for xlsx/odt/ods/odp; `.xls` found the raw CSV text where a
spreadsheet header belongs). The agent now renders a real file for all 13
supported formats and refuses one it cannot produce. Separately, the Files page
listed only *uploaded* files, so everything the agent wrote was invisible though
it was on disk and in memory; both kinds are listed now.

- **One path, not two.** An upload recorded extracted entities and rows; an
  agent-written file recorded only its name and size. Both now run the same
  memory step.
- **Format set:** thirteen, the same for reading and writing. `.md` added;
  `.rtf`/`.eml`/`.tsv`/`.html`/`.xml` dropped from upload (files already saved
  still read).

**Known limits:** PDF is generated text, not layout; an `.ics` write is a single
event; prose written to `.json` is wrapped as `{"content": ...}` to stay valid.

## v0.8.2-alpha

**The names are not swapped any more.** v0.8.1 fixed the extractor's routing but
not the correction pipeline, which took the model's frame and key verbatim and was
the other road a name travels. *"I'm not Carl. You are Carl."* was applied as
`user_identity.name = "Carl"` — the assistant's name on the user's frame, under a
key nothing reads — while *"My name is not Carl. I go by Boz."* left the user's
name on `identity_name`. Both were stored, in each other's place.

- **The correction model says who.** `CorrectionResult` gains `subject`
  (`user` | `assistant` | `topic`); the prompt asks for it.
- **The code maps that answer to a frame.** `route_correction` sends
  `user`/`assistant` to the reserved frame, normalises any name key to
  `full_name`, and leaves `topic` alone. No frame-name list, no value guessing.
- **Routing runs before validation**, so a correction is checked against the
  frame it will be written to.

**Known limits:** existing memory is not rewritten (a rename corrects it); the
subject is still the model's judgment, and the correction path has no cross-check.

## v0.8.1-alpha

**The assistant stopped taking your name.** v0.8.0 made the assistant's name
yours to set; the first thing it did was adopt the name you gave yourself. The
extraction guard proves *where* a name came from, not *who* it is for, and the
naming rules said nothing about the user's own name — so "my name is not Carl, I
go by Boz" renamed the assistant to Boz. The same gap had already let "Jay Miles"
(from a pasted email) and the pronoun "you" onto the assistant's name.

- **The user's own name has its own frame:** first-person self-identification
  extracts to `user_identity.full_name`; the assistant's name only when you
  assign it to the assistant.
- **Names in pasted content are not identity facts.**
- **A name cannot belong to both:** a same-turn claim for user and assistant
  keeps the user's and drops the assistant's.
- **The two frames resolve by exact name only**, so canonicalization cannot merge
  them; "what is my name?" retrieves `user_identity`.

**Known limits:** existing memory is not rewritten; extraction remains the model's
judgment.

## v0.8.0-alpha

**A name that sticks.** You could tell the assistant its name, watch it
acknowledge you, and still find the old name on the tab. The name was never
really yours to set: a conversational fact carries source reliability 0.5, the
stored name had been left at 0.99 by an earlier correction, and the confidence
ladder keeps the higher-reliability value. The attempt was recorded as an
auto-resolved conflict, so the brain looked like it had chosen otherwise.

- **The assistant's name is authoritative.** A user-stated `full_name` is written
  at reliability 1.0 and always supersedes the stored name — still through
  `revise`, `slot_history`, and the conflicts table, not around them.
- **The tab follows memory.** The name endpoint was right; the UI asked once on
  mount. A turn that reports `identity_name.full_name` now makes the app re-read
  it, so the header and tab update in the same turn.
- **A name made only of function words is rejected** (the live brain had mined
  "you" as the name).

**Known limits:** a mis-extracted name now sticks harder — the guard filters
self-description and pronouns but is not infallible, by design (the project stays
model-first). Only `identity_name.full_name` is affected.

## v0.7.0-alpha

**One picture instead of six.** Search answers came with a strip of up to six
images behind a lightbox, and the pictures were often wrong. The gallery is gone:
one hero at the top — the video when you asked for video, the lead image
otherwise — with other videos as links. It also turns out the image vanished on
every reload.

- **The gallery was 300 lines of component and 635 of stylesheet** for pictures
  not good enough to earn the space. Net −720 lines.
- **The hero never survived a refresh** because the schema parsing a restored
  message declared three fields and `z.object()` strips undeclared keys — so
  `search_info` was deleted after the backend had sent it, taking the "Searched
  via Brave" badge with it. The fix carries a test checked to fail without it.
- **Dangling doc citations:** fifteen comments cited deleted planning documents;
  they now point at the rule's real home, and a test fails if a citation dangles.

**Known limits:** this changes **how many** pictures you see, not **which** one.
The hero is still the search engine's first-ranked result, judged from filename
and host — a sponsored image with a clean filename still wins. Scoring images by
embedding similarity is being measured before it is built on.

## v0.6.0-alpha

**Files actually work now.** The assistant could read a document at upload and not
at read, a large one was refused outright, it printed raw database ids, and its
For You bell filled with things that were never questions.

- **Documents are read, not decoded as text.** `read_file` bypassed the extractor
  and decoded every file as UTF-8 — binary noise for a PDF or `.docx` — and past
  1 MB it was refused first. Every document format now reads through the same
  extractor upload uses; a scanned PDF with no text layer says so; a corrupt file
  is not reported as missing.
- **The input bar uploaded bytes**, not text, so a correctly-extracted PDF no
  longer arrived corrupted. Its format list was six entries against the files
  page's seventeen; both now derive from one list, pinned by a test.
- **No file-size limits.** Removed the 1 MB read cap, the 10 MB write cap, and
  three upload guards. The only bound is the model's context window on a read,
  marked with the true total.
- **No database ids in replies.** Graph edges render the target's name, not
  `frame:4832`; an unnameable edge is omitted.
- **For You can be cleared.** The bell was full of "Correction applied" notices
  fired during the conversation you were in. Those writers are gone; alerts come
  only from scheduled work. The green stripe was not "resolved" — `info` now uses
  a neutral colour — and the never-read `is_read`/`read_at` fields are removed.
- **Container resilience.** The healthcheck probed `/health`, which calls Ollama
  and can block when it is down — so an Ollama outage restarted a container a
  restart cannot fix. It probes `/healthz` (liveness, no I/O) now; `autoheal` is
  label-scoped; logs are rotated.

## v0.5.0-alpha

**The alerts channel stops looking like a warning.** These are things to bring up
and discuss, not errors — so the bell is **For You**, in mint, and resolving one
puts it in the conversation you chose.

- **Resolving posts the alert's question** into the chosen conversation, whether
  or not it had history (it was written only into empty sessions, so picking a
  used conversation showed nothing). One modal, not two; the wall of text is gone.
- **The stale frontend that made rebuilds look broken:** the Dockerfile copied
  the fresh bundle, then `COPY assistant/` re-copied the committed `static/` over
  it, so prod served a pre-top-bar frontend. The build output is excluded now.
- **Class collisions across global stylesheets** (`.empty-state`, `.voice-dot`,
  `.file-icon`) are scoped or removed, with a guard against a bare class defined
  twice.
- **Dev frontend mount:** a file bind mount pins an inode, so any checkout left
  Vite reading a deleted file; it mounts the `frontend/` directory now.

## v0.4.0-alpha

**One top bar, and the small lies it was telling.** The header is a single
coherent control strip, and two places that showed an untrue state are fixed.

- **One shared style** (`.topbar-btn`), an inline SVG icon per control coloured by
  `currentColor`, a deliberate order, and the voice status bar taking Let's talk's
  place while a turn is live.
- **"Transcribing…" now appears during a conversation turn** (the capture hook
  never advanced the global voice status, so it read "Listening…" throughout).
- **Resolving an alert into the conversation you are viewing now shows it** (the
  message-loading effect tracked the active id, so re-opening was a no-op).
- **One dependency source:** `pyproject.toml` omitted `sqlcipher3`,
  `cryptography`, `faster-whisper`, `python-multipart`, so `pip install -e .`
  produced no encrypted brain. Declared now; the Dockerfile builds from `.[dev]`.
- **Icons are inline SVG, not emoji.**

## v0.3.0-alpha

**It reads your documents, and it remembers what it showed you.** The assistant
opens to the files a household actually has — PDF, Word, Excel, PowerPoint and
the legacy formats around them — and stops the chat losing its imagery on reload.

- **It reads documents:** PDF (`pypdf`, first 50 pages), `.docx`, `.xlsx`,
  `.pptx`, plus `.rtf`, OpenDocument (`.odt`/`.ods`/`.odp`), legacy `.xls`,
  `.eml`, `.tsv`, and iCal via `icalendar`. All offline and pure-Python; XML is
  parsed with `defusedxml`; extraction runs off the event loop. Legacy `.doc`/
  `.ppt` say to re-save as `.docx`/`.pdf`.
- **The chat remembers its media:** a search turn stores a compact projection of
  its results (titles, URLs, thumbnails, video fields — never page bodies) so a
  reload restores it.
- **Links ask first** — a link leaving the assistant names the host and confirms,
  including links rendered inside an answer.
- **Two UI defects:** alerts stopped flickering (a 30s poll swapped the list for
  a spinner), and a new conversation is no longer buried below the fold.
- **Accuracy:** the summary frame was never indexed (the summarizer passed an
  `EmbeddingResponse` where a vector was expected); idempotent hops retry while
  side effects do not; the encrypted DB retries a transient key-derivation read.

## v0.2.0-alpha

**The brain stops lying to itself.** Four plans shipped, one closed on
measurement: memory that recorded decisions as undecided, files that duplicated
their own contents, and a bell full of notices nobody could act on.

- **Alerts are memory** — a frame of type `alert`, not a notifications row, so
  retrieval can raise one when it is contextually relevant. The **presence rule**
  decides when: an alert is warranted when the agent learned something and the
  user was **not there to hear it**. A task completion is not an alert; a task
  *failure* is. An alert closes by being answered in a conversation, not by being
  read — there is no read flag, and none is coming back.
- **Files: memory holds what a file *is*, never what it *contains*.** Four sites
  still wrote content, including `read_file` on every read, and a missing file
  returned a stale truncated copy the model answered from. Content keys are now
  refused at write time by `upsert_slot`.
- **Write-path hygiene:** blank frame names / slot keys / values are refused at
  `upsert_slot` (the one funnel); the summarizer writes its counters directly
  instead of "conflicting" with itself; `POST /tasks/run-due` returns
  immediately; embeddings migrated to a single model.
- **Conflicts: the ladder works.** A long-held belief that it never
  discriminated was wrong — a user fact (0.99) beats a search attempt (0.5), and
  the apparent recency-always-wins came from *ties* resolving by recency.
  `EXISTING_WINS` is recorded as decided (`auto_resolved` with `resolved_value`),
  not deferred, and every conflict records its decision inputs. Plan D (a model
  that reasons over conflicts) was closed: it would duplicate a working comparator.
- **Supplied content is registered as memory** (a pasted list used to survive one
  turn); `web_search` stays available on every turn — three static-rule fixes were
  tried and reverted. The model decides.
- **Process:** plans are wiped when shipped; experiments are pre-registered with
  falsification conditions fixed before data.

**Known gaps:** the journal (Plan E phase 3) is not started; conflicts the ladder
declines to settle remain `pending` (now distinguishable from decided ones);
portable brains are implemented and tested but reachable by no endpoint or CLI
command.

## v0.1.1-alpha

**Memory is never forgotten on a timer.** The background garbage collector is
gone, and the maintenance that remains is cheaper and more predictable.

- **Time-based decay is gone.** It only ever lowered priority on rows already
  below the default — data the user had already forgotten — so it re-forgot
  things on a delay. Memory leaves only through an explicit `forget` or deletion.
- **Merging is ad hoc; backups are not.** Duplicates merge as soon as found (the
  6-hour tick is a *look* cadence). The snapshot moved to its own 12-hour clock;
  a merge without a fresh snapshot takes one first.
- **Faster re-indexing:** embedding top-up runs every 6 hours as its own job.
- **Removed:** the `assistant db gc` command and the `/plans/` archive convention.

## v0.1.0-alpha

**The first alpha.** A privacy-first cognitive digital assistant that remembers
what you tell it, learns over time, and corrects itself when it is wrong. All
inference runs locally through Ollama; nothing leaves your machine unless you
explicitly opt into an external search backend.

This is alpha software for a **single household**, not hardened for untrusted
multi-user input or exposure beyond localhost.
