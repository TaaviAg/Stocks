// Throwaway probe: does Yahoo Finance answer a Supabase Edge Function?
//
// The whole proposed architecture rests on this. Yahoo's chart endpoint needs
// no key and works fine from a home connection, but it is known to rate-limit
// and sometimes refuse datacenter ranges -- and Edge Functions run on Deno
// Deploy, which is exactly such a range. Ten minutes here beats discovering it
// after the maths has been ported.
//
// Deploy, call once, read the JSON, delete the function. It writes nothing and
// reads no database.
//
//   supabase functions deploy yahoo-probe --no-verify-jwt --project-ref <ref>
//   curl https://<ref>.supabase.co/functions/v1/yahoo-probe
//   supabase functions delete yahoo-probe --project-ref <ref>

const CHART = (host: string, symbol: string) =>
  `https://${host}/v8/finance/chart/${encodeURIComponent(symbol)}` +
  `?range=6mo&interval=1d`;

// Yahoo returns 4xx to some clients that send no browser-like User-Agent, so
// the probe tests with and without to tell "blocked by IP" from "blocked by UA".
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

type Probe = {
  label: string;
  url: string;
  status?: number;
  contentType?: string | null;
  ms?: number;
  bars?: number;
  lastClose?: number | null;
  lastDate?: string | null;
  bodyHead?: string;
  error?: string;
};

async function probe(label: string, url: string, headers: HeadersInit): Promise<Probe> {
  const started = Date.now();
  try {
    const res = await fetch(url, { headers });
    const ms = Date.now() - started;
    const contentType = res.headers.get("content-type");
    const text = await res.text();
    const out: Probe = { label, url, status: res.status, contentType, ms };

    if (!res.ok || !(contentType || "").includes("json")) {
      // The first 300 characters usually say whether it is an Akamai block
      // page, a consent redirect, or a genuine Yahoo error object.
      out.bodyHead = text.slice(0, 300);
      return out;
    }
    const json = JSON.parse(text);
    const result = json?.chart?.result?.[0];
    const stamps: number[] = result?.timestamp ?? [];
    const closes: (number | null)[] = result?.indicators?.quote?.[0]?.close ?? [];
    out.bars = stamps.length;
    out.lastClose = closes.length ? closes[closes.length - 1] : null;
    out.lastDate = stamps.length
      ? new Date(stamps[stamps.length - 1] * 1000).toISOString().slice(0, 10)
      : null;
    if (json?.chart?.error) out.error = JSON.stringify(json.chart.error);
    return out;
  } catch (err) {
    return { label, url, ms: Date.now() - started, error: String(err) };
  }
}

Deno.serve(async () => {
  // What Yahoo actually sees. If it refuses, this is the address to blame.
  let egress: unknown = null;
  try {
    const r = await fetch("https://api.ipify.org?format=json");
    egress = await r.json();
  } catch (err) {
    egress = { error: String(err) };
  }

  const withUa = { "User-Agent": UA, Accept: "application/json" };
  const bare = { Accept: "application/json" };

  const results = await Promise.all([
    probe("query1 + browser UA", CHART("query1.finance.yahoo.com", "MSFT"), withUa),
    probe("query2 + browser UA", CHART("query2.finance.yahoo.com", "MSFT"), withUa),
    probe("query1, no UA header", CHART("query1.finance.yahoo.com", "MSFT"), bare),
    // A Tallinn listing: non-US symbols are the ones most likely to differ.
    probe("query1 + browser UA, TKM1T.TL",
          CHART("query1.finance.yahoo.com", "TKM1T.TL"), withUa),
    probe("query1 + browser UA, NOKIA.HE",
          CHART("query1.finance.yahoo.com", "NOKIA.HE"), withUa),
  ]);

  // Ten quick repeats say whether a first success survives any rate limiting.
  const burst: number[] = [];
  for (let i = 0; i < 10; i++) {
    try {
      const r = await fetch(CHART("query1.finance.yahoo.com", "AAPL"), { headers: withUa });
      burst.push(r.status);
      await r.body?.cancel();
    } catch {
      burst.push(-1);
    }
  }

  const ok = results.filter((r) => r.status === 200 && (r.bars ?? 0) > 100).length;
  return new Response(
    JSON.stringify({
      verdict: ok >= 3
        ? "USABLE - Yahoo answers this Edge Function with real daily bars."
        : "BLOCKED OR DEGRADED - see the probes below before porting anything.",
      probesReturningData: `${ok} of ${results.length}`,
      burstStatuses: burst,
      burstAll200: burst.every((s) => s === 200),
      egress,
      region: Deno.env.get("SB_REGION") ?? null,
      checkedAt: new Date().toISOString(),
      results,
    }, null, 2),
    { headers: { "content-type": "application/json; charset=utf-8" } },
  );
});
