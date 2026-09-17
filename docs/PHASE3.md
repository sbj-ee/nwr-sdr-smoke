# Phase 3 — Notify + minimal query UI (implementer spec)

**Status:** implemented (2026-09-17), disabled pending a hub token. See **As-built**. Web UI not implemented (spec marked it optional).

**Design brief:** [NOAA SDR Weather Alert Pipeline Design v2](https://drive.google.com/file/d/16R4MZVRLa44BPlTqiuuWO2KOPG7w5Sc9) (§9 Alerting, Phase 3 exit: webhook on severe + search by event/date/FIPS; config `notify.webhook_url` + `min_class`).

---

## As-built (2026-09-17)

Implemented per the spec's suggested pattern: `phase3/notify_worker.py` is
a **standalone poller**, same isolation as `phase2/worker.py` — it never
imports or calls into `phase1/listen.py`.

**Files:**
- `phase1/db.py` — added `notified_at`/`notify_topic` columns (SCHEMA for new DBs + idempotent `ALTER TABLE` migration for existing ones), `find_pending_notifications()`, `mark_notified()`, `counts_since()` (for digest)
- `phase3/notify.py` — eligibility gate (`check_eligibility`), payload builder (`build_payload`), hub HTTP publish (`publish`, stdlib `urllib`, no new dependency)
- `phase3/notify_worker.py` — poller; `--once`/`--dry-run` for offline testing; optional digest mode (`--digest`, `--no-immediate`)
- `phase3/query.py` — CLI search by `--event`/`--fips`/`--since`/`--until`, parameterized SQL
- `scripts/query_alerts.sh` — now delegates to `phase3/query.py` (kept the old no-args "last 20 rows" behavior and the optional positional DB-path argument)
- `phase3/test_notify.py` — unit tests for the severity gate, test/unknown suppression, topic selection, payload shape (no network)
- `phase1/test_db.py` — extended with Phase 3 DB method checks + a migration-idempotency check
- `scripts/run_phase3_notify.sh`, `systemd/user/nwr-phase3-notify.service`
- `config/phase3.env` — created locally (gitignored), `NOTIFY_ENABLED=0`, `NOTIFY_TOKEN` empty

**Duplicate handling — no extra code needed:** the spec asks to "only
notify on the first stored row (`duplicate=False` path)." That's already
guaranteed by the schema: Phase 1's `find_recent_dedup` never creates a
second `alerts` row for a repeat broadcast within the purge window, so
every row the notify poller sees is already a first-time insert.
`notified_at` alone is enough to prevent re-notifying.

**"Already notified" marker — spec deviation:** used `notified_at` for
both an actual send *and* a suppression decision (`notify_topic =
suppressed:test` / `suppressed:min_class<X` / `suppressed:unknown`),
rather than only marking rows that were actually POSTed. Rationale: with
this marker, `find_pending_notifications()` shrinks over time instead of
re-evaluating every RWT/watch forever as the table grows. If that
overloads `notify_topic` in a way that's a problem for the (unbuilt) web
UI later, split it into a separate `notify_status` column.

**Blocked on:** a publish-scoped bearer token. Tailscale is up and
`debian199` is reachable
(`curl http://debian199.tailade1d3.ts.net:8787/health` → `{"ok":true}`),
but this implementer has no SSH access to `debian199` to run the
`ts-notify-hub` admin CLI and mint one — needs Stephen. Proved the
publish contract anyway: `POST /v1/publish/house-nwr` with a bogus token
returned `401 {"error":"unauthorized"}`, matching the spec's contract
exactly, and the worker correctly left that row unnotified for retry
rather than marking it sent. `NOTIFY_ENABLED=0` until a real token is set.

**Verification performed (no real alert or token needed):**
- `phase1/test_db.py`, `phase3/test_notify.py` pass.
- Migrated the **live production** `data/alerts.db` (adds the two new
  columns) while `nwr-alerts.service` and `nwr-phase2-worker.service`
  were both running; both stayed `active` throughout, row count
  unchanged (0 rows — no real alert has landed since Phase 2's fix).
- Built a synthetic warning/watch/test row set in a **scratch** DB
  (`/tmp`, not `alerts.db`) and ran `--dry-run`: warning-class produced a
  correct dual `[house-nwr, house-urgent]` payload with the Dane FIPS
  flag and transcript preview; watch was suppressed under
  `NOTIFY_MIN_CLASS=warning` (default); RWT was suppressed as a test.
  Dry-run is idempotent — repeated runs produced byte-identical output,
  confirming dry-run never marks a row notified.
- `scripts/query_alerts.sh` / `phase3/query.py --event`/`--fips`/`--since`/`--until`
  all verified against that same scratch DB.

**Not implemented (optional per spec):** the tiny web UI. The required
CLI search exit criterion is met by `phase3/query.py`.

**Not rigorously verified (needs the token + a real event):** exit
criteria 2 and 3 (live smoke test, severe-path hub POST + no-duplicate-on-
second-poll) and test plan items 3, 4, 6 — all require either a minted
token or a real/injected eligible alert, neither of which existed at
implementation time.

**Exit criteria:**

1. On a new **non-duplicate** alert at or above the configured severity threshold, Protectli **POSTs** a notification to **ts-notify-hub** (topic `house-nwr`, and optionally `house-urgent` for the highest class).
2. Operator can **search** stored alerts by **event**, **date range**, and **FIPS** via CLI (required) and optionally a tiny LAN/Tailscale web UI.

This system is an **archive / secondary** path. It is **not** a substitute for phone WEA / Wireless Emergency Alerts or a battery SAME radio.

---

## Goals

- Notify Stephen’s Tailscale clients when meaningful NWR SAME alerts land in SQLite.
- Prefer the existing house hub over inventing a new public webhook SaaS.
- Keep capture / STT / DB on **Protectli**; notify is a thin publisher over Tailscale.
- Provide searchable local query (event / date / FIPS) without depending on Slack or Notion.

## Non-goals

- No notify / UI application code in the docs PR (this file is the contract).
- Not life-safety primary alerting; do not claim WEA replacement.
- No public internet publish of NWR audio or SAME-derived feeds.
- No requirement to stand up Slack for MVP (optional secondary only).
- Do not modify `phase1/listen.py` realtime path for notify fan-out (mirror Phase 2: standalone poller).
- Do not move NWR off Protectli; do not touch home-security SDR.

---

## Preferred architecture (hub-first)

**Primary:** [ts-notify-hub](https://github.com/sbj-ee/ts-notify-hub) on **debian199**

| Item | Value |
|------|--------|
| MagicDNS | `debian199.tailade1d3.ts.net` |
| Listen | `http://100.112.153.39:8787` (Tailscale-only) |
| Topic | **`house-nwr`** — NOAA SAME from Protectli |
| Urgent | **`house-urgent`** — optional second POST for warning-class / selected codes |
| Auth | Bearer token with **publish** scope (mint on hub; store only in gitignored `config/phase3.env`) |

**Publish contract** (hub MVP):

```http
POST /v1/publish/house-nwr
Authorization: Bearer <token>
Content-Type: application/json

{
  "title": "TOR — Tornado Warning",
  "body": "FIPS 055025 … received_at=… alert_id=42\ntranscript preview…",
  "priority": 5,
  "tags": ["nwr", "TOR", "warning"]
}
```

Also acceptable if hub still accepts ntfy-ish `POST /house-nwr` with the same JSON — prefer `/v1/publish/{topic}`.

**Publisher location:** Protectli (has `alerts.db`). Call hub over Tailscale; no WAN port, no pfSense publish.

**Suggested implementation pattern (like Phase 2):** standalone poller `phase3/notify_worker.py` + optional `systemd/user/nwr-phase3-notify.service`, **not** hooked inside `listen.py`.

- Poll (or react to) rows that are eligible and not yet notified.
- Need a durable “already notified” marker: prefer new nullable columns e.g. `notified_at TEXT`, `notify_topic TEXT` (document `ALTER TABLE` migration) **or** a side table `alert_notifications(alert_id, topic, sent_at, status)`. Do not re-notify on every poll.
- Phase 1 dedup already collapses purge-window duplicates at insert; **only notify on the first stored row** (`duplicate=False` path). Never notify on dedup hits.

**Optional secondary:** Slack workspace (sgiab) via Incoming Webhook or bot — only if Stephen asks; must not block hub MVP.

---

## Severity mapping + thresholds

Reuse `phase1/same.py` `EVENT_CODES` → `event_class`: `test` | `advisory` | `watch` | `warning` | `other`.

Design default: `notify.min_class: warning`.

| `min_class` | Notify when `event_class` is… |
|-------------|-------------------------------|
| `warning` (default) | `warning` only |
| `watch` | `watch` or `warning` |
| `advisory` | `advisory`, `watch`, or `warning` |
| `all` | any non-suppressed event (still respects test suppress) |

**Warning-class examples** (non-exhaustive; table in `same.py` is source of truth): `TOR`, `SVR`, `FFW`, `FLW`, `BZW`, `WSW`, `EWW`, `CEM`, `EVI`, …

**Unknown codes** (`other`): default **do not notify** unless `NOTIFY_UNKNOWN=1`.

**Hub priority sketch** (map into JSON `priority` if hub uses 1–5):

| Class | Topic(s) | priority |
|-------|----------|----------|
| warning | `house-nwr` + `house-urgent` | 5 |
| watch | `house-nwr` | 4 |
| advisory | `house-nwr` | 3 |

Config should allow disabling the dual-publish to `house-urgent`.

---

## Suppress tests / digests / noise

- **Always suppress** RWT / RMT / NPT / DMO / `event_class == test` **unless** `NOTIFY_TESTS=1` (`notify_tests: true` in design).
- Phase 1 may still **store** tests (`STORE_TESTS=1`); Phase 3 notify is independent.
- **Digest mode (optional):** hourly summary of new rows (counts by event/class) to `house-nwr` or `house-home` to cut noise. Immediate notify still used for ≥ warning when digest is on, unless `NOTIFY_IMMEDIATE=0`.
- Cap: if many alerts in a short window, coalesce or rate-limit (document e.g. max 1 notify per `dedup_key` ever; max N/hour).

---

## Payload content

Minimum useful body:

- `alert_id`, `received_at`, `event` + `event_label`, `event_class` / severity
- FIPS list (highlight Dane `055025` if present)
- `audio_path` basename (not a public URL)
- Transcript preview if Phase 2 filled `transcript` (first ~200 chars); else `(no transcript yet)`
- Explicit footer: `Secondary NWR archive — not a WEA replacement`

Do **not** attach full WAV over the hub in MVP.

---

## Minimal UI / query

### CLI (required for exit)

Extend or wrap `scripts/query_alerts.sh` (today: last 20 rows). Add filters, e.g.:

```bash
./scripts/query_alerts.sh --event TOR
./scripts/query_alerts.sh --fips 055025
./scripts/query_alerts.sh --since 2026-09-01 --until 2026-09-17
./scripts/query_alerts.sh --event SVR --fips 055025 --since 2026-09-01
```

Implementation may be bash+`sqlite3` or a small `phase3/query.py`. Output: id, received_at, event, fips, severity/class, notified_at, transcript preview.

### Tiny web (optional)

- LAN or Tailscale-only bind (Protectli or debian199).
- Read-only search by event / date / FIPS; link/path to local audio if authenticated on LAN.
- No public ingress; no rebroadcast of audio off-LAN.

---

## Config

See `config/phase3.env.example`.

Key knobs:

| Key | Intent |
|-----|--------|
| `NOTIFY_ENABLED` | `0` until ready (worker exits clean like Phase 2) |
| `NOTIFY_HUB_URL` | e.g. `http://debian199.tailade1d3.ts.net:8787` |
| `NOTIFY_TOKEN` | publish bearer (gitignored env only) |
| `NOTIFY_TOPIC` | `house-nwr` |
| `NOTIFY_URGENT_TOPIC` | `house-urgent` or empty to disable |
| `NOTIFY_MIN_CLASS` | `warning` (default) |
| `NOTIFY_TESTS` | `0` default |
| `NOTIFY_DIGEST` | `0` / `1` + interval |
| `DATA_DIR` | same DB as Phase 1/2 |

Slack keys only if secondary path is explicitly enabled later.

---

## Suggested file layout when implementing (later)

```
phase3/
  notify_worker.py       # poller: eligible rows → hub POST
  query.py               # optional richer CLI
  test_notify_*.py
scripts/run_phase3_notify.sh
scripts/query_alerts.sh  # extend filters
systemd/user/nwr-phase3-notify.service
config/phase3.env        # gitignored
docs/PHASE3.md           # this file
```

DB: add `notified_at` (and optional notify audit table) via migration documented in the PR that implements code.

**As-built:** matches, plus `phase3/notify.py` (eligibility/payload/HTTP,
split out of `notify_worker.py` so it's unit-testable without a running
poller) and `phase1/test_db.py` (extended, not a new file). No separate
audit table — `notified_at`/`notify_topic` on `alerts` covers it.

---

## Dependencies

- Protectli: Tailscale up; reach debian199:8787 (`curl -sS "$NOTIFY_HUB_URL/health"`).
- Publish-scoped token minted on hub (`ts-notify-hub` admin CLI); never commit the secret.
- Python 3 + `urllib`/`requests` (prefer stdlib if enough).
- No new cloud SaaS accounts for MVP.

---

## Success / exit criteria (checklist)

1. ✅ **Dry-run:** worker with `NOTIFY_DRY_RUN=1` logs the JSON it would POST; no hub traffic. Verified against a scratch DB (see As-built).
2. ⏳ **Live smoke:** force-notify a **test** row only with `NOTIFY_TESTS=1` → message appears on a subscribed client (vertex/Mac) on `house-nwr` (or use `house-test` topic for smoke if preferred — document which). Blocked on a publish token.
3. ⏳ **Severe path:** insert or replay a warning-class row (or temporary lower `NOTIFY_MIN_CLASS` in dry-run) → hub POST; row marked notified; second poll does not duplicate. Logic verified in dry-run + a deliberate-401 real-hub call (row correctly left unnotified on failure); the actual successful-POST + no-duplicate-on-retry path needs a real token.
4. ✅ **Suppress:** RWT with `NOTIFY_TESTS=0` never POSTs. Verified (scratch DB, dry-run).
5. ✅ **Query:** CLI filters by event, date, FIPS return expected rows from Protectli `data/alerts.db`. Verified against a scratch DB; `data/alerts.db` itself has 0 rows so far.
6. ✅ Phase 1 `nwr-alerts.service` and Phase 2 worker unaffected if notify worker crashes. By construction — `notify_worker.py` never imports `listen.py`; confirmed both services stayed `active` through the real DB migration.

---

## Test plan

1. ✅ Unit: severity gate + test suppress + "already notified" skip (no network). `phase3/test_notify.py`, `phase1/test_db.py`.
2. ✅ Dry-run against real DB rows on Protectli. Ran against the real (empty) `data/alerts.db` and a scratch DB with synthetic rows.
3. 🟡 Hub smoke: `curl` publish to `house-test` with Protectli token; confirm SSE/toast. No token to do the real publish, but confirmed the hub is reachable (`/health` → `{"ok":true}`) and the auth contract (`401` on a bad token, matching spec).
4. ⏳ Enable worker; inject or wait for eligible alert; confirm single notify. Blocked on a token; no real alert has fired yet either.
5. ✅ CLI: `--event`, `--fips`, `--since`/`--until` against known ids. Verified against a scratch DB.
6. — Optional web: bind Tailscale-only; search smoke. Not implemented (optional).

---

## Ops / ownership

- Host capture stays Protectli; hub stays debian199.
- Label UX copy as secondary archive.
- Ownership: **Software** owns nwr Phase 3 publisher + query; hub auth tokens via existing ts-notify-hub ops; **CoS** for Notion; merge only with Stephen / CoS yes.
- Do not start coding until this spec PR is merged (or Stephen says implement from the branch).

---

Until implementation, this document + `config/phase3.env.example` are the Phase 3 deliverable.
