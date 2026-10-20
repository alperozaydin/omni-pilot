# Technical Specification: Store the App's Data in S3 (BAR-83)

**Document Version:** 1.3 (AWS setup moved to CDK in `infra/`; export uploaded before the data files; S3 settings from environment variables for cloud sessions)
**Date:** 2026-10-18
**Status:** Approved
**Linear Issue:** [BAR-83](https://linear.app/knaak/issue/BAR-83/store-the-apps-data-in-s3-instead-of-icloud) (part 2 of [BAR-81](https://linear.app/knaak/issue/BAR-81/automate-the-custom-food-skill))
**Unblocks:** BAR-84 (daily routine that reviews new foods unattended)
**Builds on:** BAR-78 (timeouts, IPv4 first), BAR-82 (review mark written by the custom-foods skill)

---

## 1. Executive Summary & Objective

### The Problem

The personal data (food database, mappings, recipes, MacroFactor exports) lives in iCloud Drive, in the a-Shell copy of the repo (`~/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/omni-pilot/`). The Mac reaches it through the paths in `settings.yaml`; the iPhone runs from that folder. BAR-84 adds a daily cloud routine that runs while the Mac is off, and a cloud routine cannot reach iCloud. The access check on BAR-81 confirmed a routine can read and write an S3 bucket.

### The Solution

- S3 becomes the only home of the personal data: bucket `omni-pilot-data` in `eu-central-1`.
- Each device keeps a local working copy. Two new commands move it: **pull** (S3 → local, S3 always wins) and **push** (local → S3, refused when S3 changed since the pull).
- `analyze` pulls before it starts and pushes when it is done, and uploads the export it analysed.
- The custom-foods skill, run by hand on the Mac, pulls (with exports) before it starts and pushes when it is done.
- iCloud keeps carrying only the code to the iPhone (`make sync-iphone`, unchanged).

Requests to S3 are signed by hand (AWS Signature Version 4) with `requests`, so the app stays pure Python and runs in a-Shell.

### Decisions Made During Design

Settled with the user (on the issue, 2026-10-11, and in this design session):

| Decision | Choice |
| --- | --- |
| What moves to S3 | `food_db.json`, `food_mappings.yaml`, `custom_foods.yaml`, the MacroFactor exports |
| Conflicts | An upload is refused (`If-Match`, S3 answers 412) when the file in S3 changed since it was downloaded; the command stops with a clear message instead of overwriting |
| No network | The run stops and says the network is down |
| Upload fails after a good run | S3 always wins. The run says the results were not saved; the user runs it again. The next pull replaces the local copy and the failed run's lookups are done again. No code for a local copy newer than S3 |
| Switching over | Both devices switch together; no code for a device still on the old version. The existing iCloud data is copied to the bucket once |
| Bucket | New bucket `omni-pilot-data`, region `eu-central-1`, private, versioning on, old versions expire. `omni-pilot-data-test` was only for the access check |
| Credentials | An IAM user limited to this bucket; its key in `settings.yaml` (gitignored) |
| Exports | Kept in S3, all of them. Each MacroFactor export covers only the window picked (7, 14, 30 days…), so a food eaten a while ago has its logged macros only in an older export. The custom-foods skill and the BAR-84 routine need them for the macro check |
| Custom-foods skill | Pulls (with exports) before it starts and pushes when it is done, like `analyze` |
| Cloud sessions | The S3 settings can also come from environment variables, so the custom-foods skill can run in a Claude cloud session with the same `pull`/`push` (§3.1) |
| Hand edits to the yaml files | Not protected. Editing `custom_foods.yaml` or `food_mappings.yaml` by hand means pull, edit, push; forgetting the push loses the edit at the next pull. The user takes this on: they rarely edit by hand |
| AWS setup | Bucket and IAM user defined with AWS CDK (Python) in `infra/`, deployed from the Mac by Claude during implementation after the user has seen `cdk diff`. Secrets only in `settings.yaml` (gitignored) or, for cloud sessions, the cloud environment's variables (§3.1); never in the repo. `cdk.context.json` gitignored |

Calls made by the designer (the user can overrule any of them):

| Decision | Choice | Why |
| --- | --- | --- |
| Old versions | Expire 30 days after they are replaced | Long enough to recover from a bad push noticed within a few weeks; the data is small |
| Two commands, not one hidden step | `pull` and `push` are CLI commands; `analyze` calls the same functions | The skill edits files between its two steps, so it needs them separately |
| Export names in S3 | `exports/<first date>_<last date>_<hash8>.xlsx` | The iPhone Shortcut always saves the export as `data/data.xlsx`, so the original name would overwrite every earlier iPhone export. The content hash makes a re-upload of the same file a no-op |
| Reports | Stay local, not uploaded | Not asked for; nothing reads them remotely |
| Access key | Not in the CDK stack; created by the user in the AWS console and pasted into each `settings.yaml` | Keeps the secret out of CloudFormation and out of the conversation transcript; the iPhone's `settings.yaml` has to be edited by hand anyway |
| CDK language and layout | Python, in `infra/`, with `aws-cdk-lib` in an `infra` dependency group | Same language as the app; the group keeps CDK (which needs Node) out of the iPhone install and out of `make test` |
| No `s3` section in settings | The app works on local files only, as today, and says so once | Keeps tests and a fresh clone working; a device is configured once |

### Goals

- Mac, iPhone and the BAR-84 routine all read and write the same data.
- No silent overwrite: a write based on an out-of-date copy is refused.
- Same commands on the Mac (`uv`) and the iPhone (a-Shell, plain `python`).

### Non-goals

- The BAR-84 routine itself and its new skill. BAR-84 planned to reach S3 through the AWS connector's presigned links; with §3.1 it can use the app's `pull`/`push` and the limited `omni-pilot-app` key instead, which would also remove BAR-84's concerns about the connector's root access and expiring sign-in. BAR-84 decides.
- Merging concurrent changes. A refused push is resolved by running again.
- Working offline.
- Protecting unpushed hand edits.
- Moving `settings.yaml`, `supplements.yaml` or `reference_ranges.yaml` to S3. Settings hold per-device secrets; the other two stay where they are today.

### Anti-goals

- Anything that needs `boto3` or another C-extension package.
- An access key in a URL, a log line or an error message.
- A push that quietly overwrites a newer file in S3.

---

## 2. Infrastructure as Code (AWS CDK)

The bucket and the IAM user are defined with AWS CDK in Python, in a new `infra/` folder, and deployed from the Mac. Only the access key is made by hand (§2.1); nothing is created through the AWS connector.

### 2.1 The stack

One stack, `OmniPilotData`, in `eu-central-1`:

**Bucket `omni-pilot-data`:**
- Block Public Access: all four settings on (`BlockPublicAccess.BLOCK_ALL`); `enforce_ssl=True`.
- Versioning: enabled.
- Lifecycle rule (whole bucket): noncurrent versions expire 30 days after they become noncurrent; expired delete markers are removed.
- Encryption: SSE-S3 (`BucketEncryption.S3_MANAGED`).
- `removal_policy=RETAIN`, no `auto_delete_objects`: destroying or replacing the stack never deletes the data.

**IAM user `omni-pilot-app`:** no console access, one policy, written with explicit actions (not `bucket.grant_read_write`, which would add delete):
- `s3:ListBucket` on the bucket (needed to list `exports/`, and so a missing object answers 404 instead of 403).
- `s3:GetObject`, `s3:PutObject` on `bucket/*`.
- No delete, no other bucket, no other service.

This is an IAM **user**, not a role: the iPhone (a-Shell) has no way to assume a role without a long-term key, so the app needs a user's access key.

**No access key in the stack.** A CDK `AccessKey` would put the secret into CloudFormation (stack output or Secrets Manager). The user creates the one access key for `omni-pilot-app` in the AWS console and pastes it into `settings.yaml` on the Mac and on the iPhone (§3). The secret lives only in `settings.yaml`, which is gitignored, and, for cloud sessions, in the cloud environment's `OMNI_PILOT_S3_*` variables (§3.1); never in the repo or in CloudFormation.

**Stack outputs:** the bucket name and the user name only.

### 2.2 The `infra/` folder

```
infra/
  app.py            # CDK app: one OmniPilotData stack
  omni_pilot_infra/
    __init__.py
    data_stack.py   # bucket, lifecycle, IAM user and policy
  cdk.json          # "app": "uv run --group infra python app.py"
  tests/
    test_data_stack.py
```

- **Dependencies:** `aws-cdk-lib` and `constructs` in a new `infra` dependency group in the root `pyproject.toml`. They are not app dependencies: `requirements.txt` (the iPhone install) is compiled from `[project]` dependencies only and stays unchanged. `uv sync` does not install the group; `uv sync --group infra` does.
- **Account and region:** the account comes from the deploying AWS profile (`CDK_DEFAULT_ACCOUNT`), so no account ID is written in the repo; the region is fixed to `eu-central-1` in `app.py`.
- **Gitignored:** `infra/cdk.out/` and `infra/cdk.context.json` (account-specific lookups; nothing in the stack needs them shared).
- **Not synced to the iPhone:** `make sync-iphone` excludes `infra/`.
- **Makefile:** `make infra-diff` (`cdk diff`) and `make infra-deploy` (`cdk deploy`), both run in `infra/`.
- **Tests:** `infra/tests/test_data_stack.py` synthesizes the stack and checks it with `aws_cdk.assertions.Template`: versioning on, public access blocked, the 30-day noncurrent-version rule, retain policy, and a user policy with exactly `ListBucket`, `GetObject`, `PutObject` on this bucket and no `Delete*`. These run with `uv run --group infra pytest infra/tests`, not in the main `make test` (which must not need the CDK packages).

### 2.3 Deploying

From the Mac, with its existing AWS CLI default profile (region `eu-central-1`) and the installed `cdk` CLI:

1. `cdk bootstrap` for the account in `eu-central-1`, if it has not been done before (creates the `CDKToolkit` stack CDK needs to deploy).
2. `make infra-diff`, shown to the user.
3. `make infra-deploy` after the user says go.

Claude runs these during implementation. If the bucket name `omni-pilot-data` is already taken globally, the deploy fails; the bucket then gets another name, and only `settings.yaml` and the stack change.

`omni-pilot-data-test` is not part of the stack and is left alone; deleting it is the user's call.

---

## 3. Settings

New section in `settings.yaml` (and commented in `settings.example.yaml`):

```yaml
s3:
  bucket: "omni-pilot-data"
  region: "eu-central-1"
  access_key_id: "YOUR_ACCESS_KEY_ID"
  secret_access_key: "YOUR_SECRET_ACCESS_KEY"
```

**Environment variables.** Each of the four values can also come from an environment variable, which wins over `settings.yaml`:

| Value | Environment variable |
| --- | --- |
| `bucket` | `OMNI_PILOT_S3_BUCKET` |
| `region` | `OMNI_PILOT_S3_REGION` |
| `access_key_id` | `OMNI_PILOT_S3_ACCESS_KEY_ID` |
| `secret_access_key` | `OMNI_PILOT_S3_SECRET_ACCESS_KEY` |

This is what lets the custom-foods skill (and its `pull`/`push`) run in a Claude cloud session, where the repo is a fresh clone and `settings.yaml` is created from the example without any key (§3.1). On the Mac and the iPhone nothing changes: the values stay in `settings.yaml`. An empty environment variable counts as not set.

- **Not configured** (no `s3` section and none of the four environment variables set): every command works on the local files only, as today. `analyze` prints one line, `S3: not configured — local files only`. `pull` and `push` stop with exit code 1 and say S3 is not configured.
- **Configured but incomplete** (at least one of the four values set, from either source, but another missing or empty, or still a placeholder, i.e. a value starting with `YOUR_`): stop with exit code 1 naming the missing value and both places it can come from. Never fall back to local-only, since that would split the data silently.
- Error messages name the setting or variable, never its value.
- `database_path`, `mappings_path` and `custom_foods_path` keep their meaning: they now point at the device's **local working copy**. On the Mac the three iCloud lines are removed from `settings.yaml`, so the repo defaults (`db/food_db.json`, `config/food_mappings.yaml`, `config/custom_foods.yaml`) apply. On the iPhone the settings already use those relative paths inside the a-Shell folder; nothing changes there.
- The iCloud example lines in `settings.example.yaml` are removed.
- The `s3` section in `settings.example.yaml` is commented out, so a `settings.yaml` copied from the example (as in a cloud session) counts as not configured until the section or the environment variables are filled in.

### 3.1 Running in a Claude cloud session

A cloud session clones the repo from GitHub, so it has no `settings.yaml`, `db/`, `config/*.yaml` or `data/`. To run the custom-foods skill there:

- The cloud environment holds the four `OMNI_PILOT_S3_*` variables, with the same `omni-pilot-app` key the Mac and iPhone use (limited to this bucket, no delete; versioning covers a bad write).
- `settings.yaml` is created from `settings.example.yaml`, with the USDA key as the placeholder the BAR-84 access check already uses (USDA's key is added on the way out as a network secret).
- The environment's network access allows `omni-pilot-data.s3.eu-central-1.amazonaws.com`.
- The skill then runs as on the Mac: `pull --exports` fills the empty working copy, the skill works, `push` uploads.

**To verify during implementation, before relying on it:** the USDA key reaches USDA as a *network* secret, added to requests outside the session. That cannot work for S3: Signature Version 4 is computed from the secret inside the app, so the secret must be readable in the session as a plain environment variable. Check that the cloud environment settings offer this, and that the session can reach the bucket, by running `pull` in a cloud session once the stack is deployed. If either fails, the environment-variable support stays (it is small and harmless) and the gap is written on BAR-84.

Setting up the cloud environment itself (adding the variables and the network rule) is done by the user in the Claude settings; the design only needs the app to read the variables.

---

## 4. Objects in S3

| Key | Local file | Written by |
| --- | --- | --- |
| `food_db.json` | `database_path` | `analyze`, the custom-foods skill (review marks), the BAR-84 routine |
| `food_mappings.yaml` | `mappings_path` | `analyze` (translation sync), hand edits |
| `custom_foods.yaml` | `custom_foods_path` | the custom-foods skill, the BAR-84 routine, hand edits |
| `exports/<first>_<last>_<hash8>.xlsx` | (see §6) | `analyze`, the one-time copy |

**Export key:** `<first>` and `<last>` are the first and last dates in the export's Food Log sheet (`YYYY-MM-DD`, from `parse_food_log` before the partial-last-day check drops anything); `<hash8>` is the first 8 hex characters of the file's SHA-256. Example: `exports/2026-10-01_2026-10-14_3fa2c19b.xlsx`. An export with no Food Log entries is not uploaded.

The three data files are written only with a conditional `PUT` (§5.3); exports only with `If-None-Match: *`. An export is never changed after upload.

---

## 5. The S3 Client and the Sync

### 5.1 Module `s3.py`

A small client with no AWS SDK:

- **Signing:** AWS Signature Version 4, header form (`Authorization` header), built with `hashlib` and `hmac`. The payload hash is the SHA-256 of the body (`x-amz-content-sha256`); bodies are small. Never presigned query strings: the key stays out of the URL (CLAUDE.md HTTP rule).
- **Endpoint:** virtual-hosted style, `https://omni-pilot-data.s3.eu-central-1.amazonaws.com/<key>`.
- **Operations:** `get(key) -> (body, etag) | None` (404 → `None`), `put(key, body, if_match=None, if_none_match=False) -> etag`, `list(prefix) -> list[key]` (ListObjectsV2, following continuation tokens).
- **Timeouts:** an `S3_TIMEOUT = (connect, read)` tuple like `USDA_TIMEOUT`. IPv4-first comes for free from `net.prefer_ipv4_connections()` already called in `cli.main`.
- **No retries** on a failed request: the command stops (the user runs again). This keeps "upload failed" unambiguous.
- **Errors** are raised as one exception type, `S3Error`, carrying a kind and a user-facing message:

| Kind | Cause | Message (gist) |
| --- | --- | --- |
| `network` | connection error or timeout | S3 not reachable — is the network down? |
| `auth` | 403 (`AccessDenied`, `SignatureDoesNotMatch`, `InvalidAccessKeyId`) | S3 refused the key — check `s3` in settings.yaml or the `OMNI_PILOT_S3_*` variables (naming whichever the key came from) |
| `conflict` | 412 on a conditional `PUT`, or 409 `ConditionalRequestConflict` (two conditional writes racing) | `<file>` changed in S3 since it was downloaded |
| `other` | any other non-2xx | S3 answered `<status>` for `<key>` |

No message ever contains the access key, the secret or the signature.

The signing is checked in tests against AWS's published Signature Version 4 example values, so a mistake shows up without a real request.

### 5.2 Pull

`pull(settings, exports=False)`:

1. For each of the three data files: `GET` it from S3.
   - Found: write the body to the local path (create the folder if needed, write to a temp file then rename, so a failure mid-write leaves the old file), and record its ETag and SHA-256.
   - Not found (404): delete the local file if there is one, and record it as absent.
2. If all three are absent in S3, stop with exit code 1 before touching any local file: `S3 bucket <name> has none of the data files — wrong bucket, or the one-time copy was not done`. (Step 1 therefore fetches all three first and writes only after this check.)
3. Write the **sync state** (below).
4. With `exports=True`: list `exports/` and download every export not already in the local exports folder (§6). Existing local files are never fetched again: exports never change.

A pull always replaces the local copy: S3 wins. It does not check for unpushed local changes (user's decision, §1).

**Sync state:** a JSON file `.s3_state.json` in the folder of `database_path`. For each data file: S3 key, ETag (or `null` for absent) and SHA-256 of what was written. It is per device and never uploaded.

### 5.3 Push

`push(settings)`:

1. Read the sync state. No state file → treat all three files as absent in S3 and say so (`no sync state — uploading as new files`). This is how the one-time copy works (§8), and it cannot overwrite anything: every `PUT` then carries `If-None-Match: *`, so a file already in S3 answers 412.
2. For each data file, in this order: `food_db.json`, `food_mappings.yaml`, `custom_foods.yaml`:
   - Unchanged (local SHA-256 equals the recorded one, or absent on both sides) → skip.
   - Changed → `PUT` with `If-Match: <recorded ETag>`, or `If-None-Match: *` if it was absent in S3 at pull time.
   - Present at pull, deleted locally → not supported: stop with a message (there is no delete permission and no use for one).
   - Success → update that file's ETag and SHA-256 in the sync state and save it immediately.
   - 412 → stop. The files after it are not pushed. Message: `<file> changed in S3 since it was downloaded (another device or the daily routine wrote it). Nothing more was uploaded.` Each caller adds its next step: `analyze` ends its red line with `Run analyze again.` (its next run pulls first); `push` adds ``Run `make pull` (it replaces your local changes), redo them, then push again.``
3. Print one line per uploaded file.

The database goes first because it is the file most likely to be written concurrently; a conflict on it stops the others. A partial push (database uploaded, a later file refused) is acceptable: `food_mappings.yaml` mirrors translations that are also in the database and is synced both ways on the next `analyze`; `custom_foods.yaml` is only written by the skill and the routine.

### 5.4 Export upload

`upload_export(settings, path)`: parse the dates, hash the file, `PUT exports/<key>` with `If-None-Match: *`. A 412 here means the same file is already there: treat it as success, print `already in S3`. Used by `analyze` and by the one-time copy.

---

## 6. Local Exports Folder

Pulled exports are written to `data/` relative to the working directory, under their S3 name without the `exports/` prefix (e.g. `data/2026-10-01_2026-10-14_3fa2c19b.xlsx`). `recipe_tool.py` already reads `data/*.xlsx` and drops entries it has already read from an earlier export, so files that overlap (the old `MacroFactor-*.xlsx` copies next to the pulled ones) count once. No change to `recipe_tool.py` is needed for this. "Already local" in §5.2 means a file of that exact name exists in `data/`.

---

## 7. Commands

### 7.1 New CLI commands

```bash
python -m omni_pilot.cli pull [--exports] [--settings PATH]
python -m omni_pilot.cli push [--export FILE ...] [--settings PATH]
```

- `pull --exports` also fetches missing exports (§5.2 step 4).
- `push --export FILE ...` uploads the named exports (§5.4) before the data files, for the same reason as in §7.2. Used by the one-time copy; `analyze` does this itself.
- Exit code 0 on success, 1 on any `S3Error` or settings problem, with the message from §5.1.

Makefile targets `make pull` (passes `--exports`) and `make push`, listed in `make help`.

### 7.2 `analyze`

With S3 configured, `cmd_analyze` becomes:

1. Load settings; check the `s3` section (§3).
2. **Pull** (no exports). On `S3Error`: print the message, exit 1. Nothing else runs; no report is written.
3. The pipeline as today (custom foods, parse, last-day check, translate, enrich, analyze, report). The BAR-43 stop (no food resolved and a lookup failed) still exits 1 before reporting, and then **does not push** or upload the export either.
4. **Upload the export** it analysed (`args.xlsx_file`) first, then **push** the data files. The export goes first because the BAR-84 routine needs it to check the foods it finds new in the database: if the export upload fails, the data files are not pushed (the run counts as not saved, step 5), so the database in S3 never holds foods whose logged macros are missing from S3. The reverse case (export in S3, database push refused) is harmless, since an export never conflicts and is never changed.
5. If any upload failed: after the report, print in red `Results were not saved to S3: <message>. Run analyze again.` and exit 1. The terminal and HTML reports of this run are still written: they are correct for this run.

Without S3 configured, `analyze` behaves exactly as today.

The new prints sit next to the existing `Database:` / `Mappings:` lines: `S3: pulled from omni-pilot-data (database, mappings, recipes)` and, at the end, `S3: uploaded export 2026-10-01_2026-10-14_3fa2c19b.xlsx; database, mappings` (or `nothing changed`).

---

## 8. One-Time Copy (Switching Over)

Done once, during implementation, from the Mac:

1. Deploy the CDK stack (§2.3); the user creates the access key and adds the `s3` section to the Mac's `settings.yaml` (paths still pointing at iCloud).
2. Run `push`. With no sync state it uploads every data file as new (§5.3 step 1). If the bucket already holds a file, S3 answers 412 and nothing is overwritten.
3. `push --export` with every `.xlsx` in the Mac's `data/` and the iCloud `data/`, leaving out files with `example` in the name (same rule as `recipe_tool.py`). Duplicates across the two folders have the same hash and land on the same key.
4. Remove the three iCloud path lines from the Mac's `settings.yaml`, run `make pull`, and check the local copy matches what was uploaded (same SHA-256s).
5. On the iPhone: update the code (`make sync-iphone`), add the `s3` section to its `settings.yaml`, run `analyze` once.

The iCloud copies of the data are left in place, unused; the user can delete them later. Both devices switch together, as decided.

---

## 9. The Custom-Foods Skill

`SKILL.md` lives in the `alperozaydin/my-skills` repo and is installed here from it (as in BAR-82: change there, PR there, reinstall here). The change:

- **Before any work:** run `PYTHONPATH=src uv run python -m omni_pilot.cli pull --exports` (or `make pull`). If it fails, stop and show the message.
- **After the last change to `custom_foods.yaml` or the review marks:** run `push`. If it is refused because S3 changed meanwhile, tell the user; do not retry by itself, since its edits were based on the old copy. The user decides whether to redo them after a new pull.

The skill's text that says the data is "beside the database (iCloud)" or "usually in iCloud" is reworded to the local working copy kept in sync with S3.

The same steps run unchanged in a Claude cloud session (§3.1); the skill says it can run there once the environment holds the `OMNI_PILOT_S3_*` variables.

`recipe_tool.py` needs no change (§6).

---

## 10. Where the Work Happens

| Area | Change |
| --- | --- |
| `src/omni_pilot/s3.py` (new) | SigV4 signing, `get`/`put`/`list`, `S3Error`, `S3_TIMEOUT` |
| `src/omni_pilot/sync.py` (new) | `pull`, `push`, `upload_export`, sync state, export key |
| `src/omni_pilot/config.py` | Read the `s3` section and the `OMNI_PILOT_S3_*` variables, and check them |
| `src/omni_pilot/cli.py` | `pull` and `push` commands; pull/push/upload around `cmd_analyze` |
| `config/settings.example.yaml` | `s3` section; iCloud path examples removed |
| `Makefile` | `pull`, `push`, `infra-diff`, `infra-deploy` targets; `sync-iphone` excludes `infra/` |
| `tests/` | §11 |
| `CLAUDE.md`, `README.md` | §12 |
| `my-skills` repo | `SKILL.md` pull/push steps (§9), separate PR |
| `infra/` (new) | CDK app and stack: bucket, lifecycle, IAM user and policy; stack tests (§2) |
| `pyproject.toml`, `uv.lock` | `infra` dependency group |
| `.gitignore` | `infra/cdk.out/`, `infra/cdk.context.json` |

---

## 11. Testing

No test sends a request to real S3 (like the rule for Gemini): every test fakes the HTTP layer.

- **Signing:** matches AWS's published Signature Version 4 example (canonical request, string to sign, signature).
- **Client:** 404 → `None`; 403/409/412/5xx/connection error → `S3Error` of the right kind; `If-Match` / `If-None-Match` headers sent; the URL never contains the key or the signature; the message never contains the secret; list follows continuation tokens; timeouts passed as the tuple.
- **Pull:** writes files and state; deletes a local file absent in S3; all-absent stops without touching local files; a failed GET leaves the old local files; `--exports` fetches only missing exports.
- **Push:** unchanged files skipped; changed file sent with its recorded ETag; absent-at-pull sent with `If-None-Match: *`; 412 on the database stops before mappings; state updated after each success; no state → all treated as absent.
- **Export key:** dates from the Food Log, 8-character hash; same file → same key; 412 on upload treated as already there.
- **`analyze`:** pull failure exits 1 with no report; export uploaded before the data files, and a failed export upload pushes no data file; upload failure writes the report, prints the red line and exits 1; BAR-43 stop does not push; no `s3` section and no environment variables → no S3 call at all and today's behaviour; incomplete → exit 1.
- **Settings from the environment:** each `OMNI_PILOT_S3_*` variable wins over `settings.yaml`; all four from the environment with no `s3` section works; an empty variable counts as not set; a mix that leaves one value missing stops and names it; no message contains a secret.

---

## 12. Documentation

- **CLAUDE.md:** architecture gets the `s3.py`/`sync.py` step in the data flow (pull before, push after); the TinyDB invariant says S3 holds the shared copy and TinyDB the local working copy; the iCloud wording for `database_path`/`mappings_path`/`custom_foods_path` becomes "local working copy"; the HTTP rules mention S3 (SigV4 header auth, no presigned URLs, `S3_TIMEOUT`); the conflict rule and "S3 always wins on pull" go in Key invariants; the `OMNI_PILOT_S3_*` variables and why they exist (cloud sessions) go next to the `s3` settings.
- **README.md:** setup gets the `s3` section, the `OMNI_PILOT_S3_*` variables and `make pull` / `make push`; the iCloud sync section is replaced; a short section on deploying `infra/` (bootstrap, `make infra-diff`, `make infra-deploy`, creating the access key).
- **CLAUDE.md** also gets the `infra/` folder and its commands, and the rule that the app user's policy never grants delete.

---

## 13. Out of Scope

- The BAR-84 routine, its skill and its access to S3.
- The stale `import` line in the iPhone Shortcut (`shortcuts/Omni-Pilot.shortcut` runs `python -m omni_pilot.cli import`, a command that no longer exists; `analyze` still runs after it). Worth a separate fix.
- Deleting the iCloud data copies or the `omni-pilot-data-test` bucket.
- Uploading reports.
