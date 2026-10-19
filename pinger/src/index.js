// The thing that actually triggers collection on GitHub Actions, because GitHub's own
// scheduler does not.
//
// Evidence, 2026-10-13, after the band crons were split into their own file to test
// whether an hourly neighbour was starving them:
//
//   20 3 * * *    band shift A     fired 10:46Z   7h26m late
//   10 6 * * *    band shift B     fired 14:03Z   7h53m late
//   30 10 * * *   resolutions      fired 17:41Z   7h11m late   <- the control, same file
//   5 * * * *     GEFS hourly      fired 3 of 24 slots
//
// The control falsified the file-splitting diagnosis: the specific-time cron left beside
// the hourly one fired too. Everything fires, hours late. For a band that is the same as
// not firing, because `--until` is an absolute instant: shift A started 4 hours after its
// own deadline and wrote nothing, green.
//
// `workflow_dispatch` through the REST API is not on that scheduler and is not throttled
// -- every hand dispatch of these workflows has started within seconds, including a full
// 12m29s band on 2026-10-11 that collected 161 markets and pushed first try. So the fix is
// to stop asking GitHub to keep time and keep it here instead.
//
// WHY NOT THE DESKTOP. Task Scheduler is the one timer in this project that has never
// failed to fire, and it already drives every local collector. But Actions exists to cover
// the days the desktop is asleep -- which are the days a desktop-hosted pinger would also
// be asleep. A redundant collector triggered by the host it is redundant *for* is not
// redundant. Hence an outside host.
//
// WHAT THIS DELIBERATELY DOES NOT DO. It does not check whether the run it triggered
// collected anything. That question is answered where the data lands, by
// scripts/band_check.ps1 against the committed Parquet, and by the deadline guard in
// band.yml which fails the run outright if this pinger ever fires past a shift's end.
// Amendment 15 retired a watchdog for watching the wrong host from the wrong trigger; a
// watchdog bolted onto the trigger it is meant to be independent of repeats that mistake.

// Cloudflare cron triggers are UTC, and so are the shift deadlines. Keyed by the exact
// cron string, so a trigger added to wrangler.toml without a mapping here fails loudly
// rather than silently doing nothing.
const DISPATCH = {
  // The two band shifts. 40 min of margin ahead of the 04:00Z-09:00Z window the US
  // ladders' T-24h instants fall in, and 40 min of deliberate overlap between them.
  '20 3 * * *': { workflow: 'band.yml', inputs: { until: '06:50' } },
  '10 6 * * *': { workflow: 'band.yml', inputs: { until: '09:10' } },
  // GEFS, hourly. The cadence is the point: a prediction uses the newest ensemble that
  // existed before it, so this bounds how stale that can be. The desktop covers the band
  // hourly on its own, but on a day the desktop is asleep an Actions band would otherwise
  // emit against whatever GitHub's 3-of-24 cron last happened to fetch.
  '0 * * * *': { workflow: 'feeds.yml', inputs: { job: 'gefs' } },
};

export default {
  async scheduled(event, env) {
    const job = DISPATCH[event.cron];
    if (!job) throw new Error(`no dispatch mapped for cron '${event.cron}'`);

    const url = `https://api.github.com/repos/${env.REPO}/actions/workflows/${job.workflow}/dispatches`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        // Required, and its absence is why this Worker had never once dispatched. Workers'
        // fetch stamps a string body `text/plain;charset=UTF-8`, which GitHub refuses to
        // parse: every invocation from the 2026-10-14 deploy onward returned 400. The dead
        // GH_TOKEN hid it until 2026-10-15 -- a 401 is decided before the body is read, so
        // fixing the token did not introduce this fault, it exposed it. Hand dispatches
        // always worked because `gh` sets this header itself.
        'Content-Type': 'application/json',
        // api.github.com rejects a request without one.
        'User-Agent': 'pmv-pinger',
      },
      body: JSON.stringify({ ref: env.REF, inputs: job.inputs }),
    });

    // 204 No Content is success for this endpoint; anything else is a failure worth
    // raising, because a swallowed 401 from an expired token would look exactly like the
    // silence this whole arrangement exists to remove. Throwing marks the cron invocation
    // failed in `wrangler tail` and in the Workers dashboard.
    if (res.status !== 204) {
      // statusText and the response headers are logged because the 400 this has returned
      // since the 2026-10-14 deploy has an EMPTY body, while GitHub's API-layer 400s carry a
      // JSON one -- so the Content-Type diagnosis never matched the symptom, and GitHub may
      // not be what is answering. `x-github-request-id` is the discriminator: present means
      // the request reached GitHub and was rejected there, absent means something in front of
      // it replied. No request header is logged -- one of them is the PAT.
      throw new Error(
        `dispatch ${job.workflow} (${JSON.stringify(job.inputs)}) -> ` +
          `${res.status} ${res.statusText}: ${(await res.text()).slice(0, 300)} ` +
          `| headers ${JSON.stringify(Object.fromEntries(res.headers))}`,
      );
    }
    console.log(`dispatched ${job.workflow} ${JSON.stringify(job.inputs)} on ${event.cron}`);
  },
};
