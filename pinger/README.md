# pmv-pinger

A Cloudflare Worker whose only job is to fire `workflow_dispatch` at `band.yml` and
`feeds.yml` on time, because GitHub's cron scheduler does not.

Why this exists, and why it is not on the desktop, is in the header comment of
`src/index.js`. Short version: on 2026-10-13 every GitHub cron in this repository fired
between 7h11m and 7h53m late, including the one deliberately left in place as a control,
while hand dispatches through the REST API have always started within seconds. A band that
starts after its own deadline writes nothing, so for the band, hours late is the same as
never.

## Deploy

Needs a Cloudflare account (the free plan is enough — cron triggers are included) and a
GitHub token. From this directory:

```sh
npm install -g wrangler        # or prefix each command below with: npx
wrangler login
wrangler deploy                # registers the script and its cron triggers
wrangler secret put GH_TOKEN   # paste the token, see below
```

`deploy` before `secret put`, because `secret put` on a Worker that does not exist yet
stops to ask whether to create one. Secrets apply immediately and need no redeploy, so the
only cost of this order is that a cron firing in the gap throws a 401 — visible in
`wrangler tail`, harmless, and it writes nothing.

Three things the first deploy from this machine tripped over, all one-time:

- **`A permission error occurred while accessing the file system: D:\node_modules\.cache\wrangler`.**
  Wrangler walks up looking for a `node_modules` to cache in and reached the drive root.
  Fixed by `mkdir -p node_modules/.cache` here; it is gitignored.
- **`could not automatically register "pinger" as your workers.dev subdomain`.** Fixed by
  `workers_dev = false` in `wrangler.toml`, which is correct regardless: this Worker has
  only a `scheduled` handler, so a public route would serve nothing.
- **`You need a workers.dev subdomain in order to proceed [code: 10063]`**, when
  registering the cron schedules. This one is an *account* gate, not a per-Worker one, and
  `workers_dev = false` does not get past it: the account needs a workers.dev subdomain to
  exist at all.

  **The URL matters, and the error message's own advice is not enough to find it.** Opening
  `dash.cloudflare.com/<account_id>/workers/onboarding` does *not* provision one — it was
  opened twice here and the error persisted both times. What worked was the Workers & Pages
  overview:

  ```
  https://dash.cloudflare.com/<account_id>/workers-and-pages
  ```

  Visiting that once provisioned the subdomain silently, after which
  `wrangler triggers deploy` registered all three crons in 3.89s. Use
  `wrangler triggers deploy` rather than a full redeploy for this retry — the script is
  already uploaded and only the triggers are missing.

  **The script uploads successfully before this error, with no triggers attached, so "a
  Worker that exists and never runs" is the expected intermediate state**, and
  `Total Upload: 1.74 KiB` in the output is not evidence the pinger is live. Check the
  triggers, not the upload.

### The token

A **fine-grained** personal access token, not a classic one:

- Resource owner: your account, **only** `abelmorales-hub/prediction-market-validation`
- Repository permissions: **Actions: Read and write**, nothing else
- Expiry: set a date and put it in a calendar — an expired token makes the Worker throw,
  which is visible in `wrangler tail` and the dashboard, but produces *no* GitHub run at
  all, so from the GitHub side it looks exactly like a cron that never fired

`Actions: write` on a public repo is the whole blast radius: it can start and cancel
workflow runs here, and read nothing else. It cannot push, cannot read secrets, and cannot
touch another repository.

## Check it

```sh
wrangler tail                              # live log; each dispatch prints one line
wrangler deployments list
gh run list --workflow band.yml --limit 5  # the runs it caused, from the other side
```

An accepted dispatch logs `dispatched band.yml {"until":"06:50"} on 20 3 * * *` and returns
204. Anything else throws, with the status and the first 300 characters of GitHub's
response.

To test without waiting for a cron, dispatch by hand with an `until` still in the future:

```sh
gh workflow run band.yml -f until=23:30
```

## What this does not answer

Whether the run it triggered actually collected anything. Two other things answer that, and
deliberately not this one:

- **`band.yml`'s deadline guard** fails the run outright if the dispatch arrives after the
  shift's own deadline. That is the only place a late pinger is visible, and before the
  guard existed such a run reported success having written zero rows.
- **`scripts/band_check.ps1`** (task `pmv-band-check`) judges the committed Parquet on the
  desktop after the band closes.

Amendment 15 retired a watchdog for watching the wrong host from the wrong trigger. A
watchdog inside the trigger it is supposed to be independent of would repeat that mistake.
