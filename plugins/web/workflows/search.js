// web plugin quick research workflow — the middle rung.
// Perplexity-style "read the top results and answer": between the bare
// WebSearch tool call (snippets, seconds) and deep-research.js (adversarial
// verification, 10-25 min). NOT a fork of deep-research.js — a smaller
// sibling pipeline. It shares the URL-normalization and progress-label
// guards with deep-research.js — keep the two in sync when touching them.
//
// All quantities are enforced by the script, not by model discretion:
// exactly 3 queries (schema minItems/maxItems), one WebSearch call per
// searcher, top-7 URL-deduped reads ordered tier-first then round-robin
// across searchers, one synthesis — plus a deterministic snowball round:
// readers report `leads` (other projects/names on the page that bear on the
// question but were not in the search results), the script scores them
// (external link > bare name > self-domain link) and chases at most 3 (one
// WebSearch per lead — empty ones get one simplified retry — top-3 new URLs
// read, same tier + round-robin order).
// Each searcher/reader copies the tool response's `cached` flag into
// its structured output and the script sums them deterministically — no
// model counting.

export const meta = {
  name: 'search',
  description: 'Quick grounded answer: 3 query variants, read the top 7 sources, chase up to 3 leads they name, answer with citations — no fact-checking.',
  whenToUse: "The middle rung between the bare WebSearch tool (snippets only, seconds) and web:deep-research (adversarially verified, 10-25 min). Use when the user needs a substantive answer grounded in sources but does not need the claims verified: it decomposes the question into 3 queries, reads the top 7 pages (relevance tier first, then round-robin across the query angles), chases up to 3 leads found in them (projects/tools named in the sources but missing from the search results — external links first), and answers with per-claim source attribution in the question's language — explicitly marked unverified. Args: the research question.",
  phases: [
    { title: "Queries", detail: "Decompose the question into 3 search queries" },
    { title: "Search", detail: "3 parallel searchers, one WebSearch call each" },
    { title: "Read", detail: "URL-dedup, read top 7 sources (tier + round-robin), extract points + leads" },
    { title: "Snowball", detail: "Chase up to 3 leads: one search per lead (empty retried once), read up to 3 new URLs" },
    { title: "Answer", detail: "Cited answer; contradictions flagged; marked unverified" },
  ],
}

const MAX_QUERIES = 3
const MAX_FETCH = 7
const MAX_LEADS = 3
const MAX_SNOWBALL_FETCH = 3
const DISCLAIMER = "\n\n_Unverified: every claim cites its source, but nothing here passed adversarial verification. For a fact-checked report run /web:deep-research._"

// ─── Schemas ───
const QUERIES_SCHEMA = {
  type: "object", required: ["queries"],
  properties: {
    queries: {
      type: "array", minItems: MAX_QUERIES, maxItems: MAX_QUERIES,
      items: { type: "string" },
      description: "Exactly " + MAX_QUERIES + " distinct search queries",
    },
  },
}
const SEARCH_SCHEMA = {
  type: "object", required: ["query", "cached", "results"],
  properties: {
    query: { type: "string" },
    // Copy the tool response's flag verbatim — the script sums these
    // deterministically into the run's cache stats.
    cached: { type: "boolean", description: "Copy the mcp__websearch__WebSearch response's `cached` field verbatim" },
    results: { type: "array", maxItems: 8, items: {
      type: "object", required: ["url", "title", "relevance"],
      properties: {
        url: { type: "string" },
        title: { type: "string" },
        snippet: { type: "string" },
        relevance: { enum: ["high", "medium", "low"] },
      },
    }},
  },
}
const EXTRACT_SCHEMA = {
  type: "object", required: ["cached", "relevant", "points", "leads"],
  properties: {
    // Copy the tool response's flag verbatim — the script sums these.
    cached: { type: "boolean", description: "Copy the mcp__websearch__WebFetch response's `cached` field verbatim" },
    relevant: { type: "boolean", description: "Does the page actually bear on the question?" },
    points: { type: "array", maxItems: 8, items: {
      type: "object", required: ["point", "quote"],
      properties: {
        point: { type: "string" },
        quote: { type: "string" },
      },
    }},
    // Leads feed the deterministic snowball round: other projects/tools the
    // page names that the search results missed.
    leads: { type: "array", maxItems: 5, items: {
      type: "object", required: ["name"],
      properties: {
        name: { type: "string", description: "What the page calls it — used verbatim as the search query" },
        url: { type: "string" },
        why: { type: "string", description: "One short clause: why it bears on the question" },
      },
    }},
  },
}
const ANSWER_SCHEMA = {
  type: "object", required: ["answer", "contradictions"],
  properties: {
    answer: { type: "string", description: "Markdown answer; every claim carries an [n] source marker" },
    contradictions: { type: "array", items: { type: "string" } },
    gaps: { type: "array", items: { type: "string" } },
  },
}

// ─── URL normalization + safe progress labels ───
// Web-controlled strings reach the terminal through progress labels.
// HOSTILE_CHARS deletes what must never render there: control characters
// (ESC/CSI included), bidi overrides/isolates, zero-width and other format
// characters, unpaired surrogates, and the double-quote look-alike family —
// any of which could visually close a quoted label early and forge
// host-shaped text after it. PLAIN_HOST_RE is the strict dot-separated LDH
// shape a bare label must prove; labels are capped at LABEL_MAX code points
// (Array.from, so surrogate pairs never split) with the ellipsis INSIDE the
// quotes, so a truncated value can never pass for the whole one.
const HOST_PATH_RE = /^[a-z][a-z0-9+.-]*:\/\/(?:[^/?#\\]*@)?(?:www\.)?([^/:?#@\\]+)(?::\d+)?([^?#]*)/i
const dedupKey = u => {
  const m = String(u).match(HOST_PATH_RE)
  return m ? (m[1] + m[2].replace(/\/$/, "")).toLowerCase() : String(u).toLowerCase()
}
const LABEL_MAX = 40
const HOSTILE_CHARS = /[\p{Cc}\p{Cf}\p{Cs}"\u201c-\u201f\u2033\u2036\u275d\u275e\u301d\u301e\uff02]/gu
const PLAIN_HOST_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$/
const scrubLabel = s => String(s).replace(HOSTILE_CHARS, "")
const quoteLabel = s => {
  const cps = Array.from(scrubLabel(s))
  return '"' + cps.slice(0, LABEL_MAX).join("").trim() + (cps.length > LABEL_MAX ? "\u2026" : "") + '"'
}

const relRank = { high: 0, medium: 1, low: 2 }
// Ordering for both read cuts (main + snowball): relevance tier first — the
// only signal that crosses searchers — then round-robin across searchers
// inside the tier. Each searcher's list is already its own relevance
// ranking, so `ri` is the per-searcher rank; `si` only fixes rotation order.
// Rationale in the comment above the main cut.
const tierThenRoundRobin = (a, b) =>
  (relRank[a.relevance] - relRank[b.relevance]) || (a.ri - b.ri) || (a.si - b.si)

// ─── Phase 0: Queries — exactly 3 distinct search queries ───
phase("Queries")
const QUESTION = (typeof args === "string" && args.trim()) || ""
if (!QUESTION) {
  return { error: "No question provided. Pass it as args: Workflow({name: 'web:search', args: '<question>'})." }
}
const queries = await agent(
  "Decompose this question into exactly " + MAX_QUERIES + " distinct web search queries.\n\n" +
  "## Question\n" + QUESTION + "\n\n" +
  "## Task\nWrite " + MAX_QUERIES + " queries that together cover the question from different angles: " +
  "the direct phrasing, a technical/terminological phrasing, and a phrasing that surfaces discussion or experience. " +
  "Each query must be self-contained (no pronouns referring to the question). " +
  "Do not wrap phrases in quotation marks — the search API matches quotes literally, and a quoted phrase can return zero results. " +
  "Avoid standalone year tokens (a bare year can zero out the results). " +
  "Make them specific enough to surface high-signal results; avoid near-duplicates.\n\n" +
  "## Language\n" +
  "Default to English queries: the hobbyist and game-dev discussion layer (Reddit, itch.io, YouTube, GitHub) is English-speaking; localized queries mostly reach press translations of it, social-media noise, or nothing. " +
  "If the question is not in English, write at most ONE query in the question's language (it can surface local press coverage) and keep the rest — especially the enumeration query — in English. " +
  "Only if the topic itself is language-specific (a local community, a non-English product scene) may the question's language dominate.\n\n" +
  "## Enumeration questions\n" +
  "If the question asks WHAT things exist (\"which games/projects/tools...\", \"cuáles...\"), one of the " + MAX_QUERIES + " queries must target inventories rather than news coverage: directory, catalog, list, collection, or megathread phrasing (e.g. \"<topic> fan projects list\", \"<topic> games directory\"). Small projects surface in listings and community threads, not in press articles.\n\nStructured output only.",
  { label: "queries", schema: QUERIES_SCHEMA }
)
if (!queries) {
  return { error: "Query agent returned no result — cannot search." }
}
log("Queries: " + queries.queries.join(" | ").slice(0, 120))

// ─── Search: 3 parallel searchers, exactly one WebSearch call each ───
phase("Search")
const searchers = (await parallel(
  queries.queries.map((query, i) => () =>
    agent(
      "## Web Searcher (" + (i + 1) + "/" + MAX_QUERIES + ")\n\n" +
      "Question: \"" + QUESTION + "\"\n\n" +
      "## Task\nMake exactly ONE call to the mcp__websearch__WebSearch tool with the query:\n`" + query + "`\n" +
      "Do not refine, repeat, or add searches — one call, then return. " +
      "Copy the tool response's `cached` field into the output verbatim. " +
      "Rank the top 6-8 results by relevance to the ORIGINAL question, not just the query; skip obvious SEO spam. " +
      "Work strictly from this prompt — do not read files or explore the repository.\n\nStructured output only.",
      { label: "search:" + (i + 1), phase: "Search", schema: SEARCH_SCHEMA }
    )
  )
)).filter(Boolean)
// Empty searchers get exactly one deterministic retry with a simplified
// query — a zero-result query is almost always shape damage (quotes, year
// tokens, over-specific phrasing), not a genuinely empty topic.
const emptySearchers = searchers.filter(s => s.results.length === 0)
if (emptySearchers.length > 0) {
  log(emptySearchers.length + " searcher(s) returned zero results — retrying with simplified queries")
  const retries = (await parallel(
    emptySearchers.map((s, i) => () =>
      agent(
        "## Search Retry (" + (i + 1) + "/" + emptySearchers.length + ")\n\n" +
        "Question: \"" + QUESTION + "\"\n\n" +
        "This search query returned ZERO results:\n`" + s.query + "`\n\n" +
        "## Task\nRewrite it into a simpler, more general variant (drop years, quotes, and over-specific terms; keep 2-4 core keywords), then make exactly ONE call to the mcp__websearch__WebSearch tool with the rewritten query. " +
        "Do not make any other calls. Copy the tool response's `cached` field into the output verbatim. " +
        "Rank the top 6-8 results by relevance to the ORIGINAL question; skip obvious SEO spam. " +
        "Work strictly from this prompt — do not read files or explore the repository.\n\nStructured output only.",
        { label: "retry:" + (i + 1), phase: "Search", schema: SEARCH_SCHEMA }
      )
    )
  )).filter(Boolean)
  searchers.push(...retries)
}
const searchCacheHits = searchers.filter(s => s.cached).length
log(searchers.length + "/" + MAX_QUERIES + " searchers done (retries included), " + searchCacheHits + " served from cache")

// ─── URL-dedup + budget (script-side, deterministic) ───
const seen = new Map()
const dupes = []
const bySearcher = []
for (const searcher of searchers) {
  const mine = []
  for (const result of searcher.results) {
    const key = dedupKey(result.url)
    if (seen.has(key)) {
      dupes.push(result)
      continue
    }
    seen.set(key, searcher.query)
    mine.push(result)
  }
  bySearcher.push(mine)
}
// Relevance tier first, then round-robin across searchers inside the tier —
// starvation lives at BOTH levels. The global sort fixed tier starvation
// (one run returned 4 social-media junk results from the RU query that
// filled 4 of 5 slots ahead of another searcher's high-relevance hits); the
// next run exposed the same hazard one level down: all 5 reads came from
// searchers 1-2, and searcher 3's six HIGH results — including the official
// Claude research doc — were all cut, leaving the answer's weakest section
// resting on a contradicted SEO directory post. Within a tier the ratings
// are searcher-relative noise, so the tiebreaker is angle coverage: one
// result per searcher per round. MAX_FETCH=7 because a strict global cut
// cannot cover a multi-entity comparative question at all: 14 HIGH
// candidates for 5 slots, and the later searchers' officials sit behind the
// earlier searchers' tier-mates no matter the ordering.
const candidates = bySearcher.flatMap((results, si) =>
  results.map((r, ri) => ({ ...r, si, ri }))
)
const fetchList = candidates.sort(tierThenRoundRobin).slice(0, MAX_FETCH)
log(candidates.length + " unique results, reading top " + fetchList.length + " (" + dupes.length + " dupes dropped)")

const basicStats = {
  queries: MAX_QUERIES,
  searches: searchers.length,
  searchCacheHits,
  uniqueResults: candidates.length,
  dupes: dupes.length,
}

if (fetchList.length === 0) {
  return {
    question: QUESTION,
    error: "No usable search results. The searchers returned nothing rankable — retry, or use mcp__websearch__WebSearch directly.",
    stats: basicStats,
  }
}

// ─── Read: parallel fetch+extract, one WebFetch call each ───
// Shared reader (first round and snowball both use it): extracts points and
// leads; phaseName marks the progress phase of the caller.
const readSource = (source, phaseName) => {
  const capturedHost = String(source.url).match(HOST_PATH_RE)?.[1] ?? ""
  const host = capturedHost.toLowerCase()
  const cleanHost = scrubLabel(host)
  const isCleanBareHost = cleanHost === host && host !== "" && Array.from(host).length <= LABEL_MAX && PLAIN_HOST_RE.test(host)
  const hostLabel = cleanHost === "" ? "" : isCleanBareHost ? host : quoteLabel(host)
  const sourceLabel = hostLabel || (scrubLabel(source.title).trim() && quoteLabel(source.title)) || "unknown"
  return agent(
    "## Source Reader\n\n" +
    "Question: \"" + QUESTION + "\"\n\n" +
    "**URL:** " + source.url + "\n**Title:** " + source.title + "\n\n" +
    "## Task\n" +
    "1. Call mcp__websearch__WebFetch ONCE with url=\"" + source.url + "\". Do not fetch anything else.\n" +
    "2. Copy the response's `cached` field verbatim.\n" +
    "3. Is the page relevant to the question? (error pages, paywalls, redirects to unrelated content -> relevant: false)\n" +
    "4. Extract up to 8 concrete points that bear on the question; each point carries a direct quote from the page as evidence. Never invent content that is not on the page.\n" +
    "5. Leads: if the page names other concrete projects, tools, or games that bear on the question (and are not this page itself), return up to 5 in `leads`: name = the short plain name the page calls it (a noun phrase like \"Azeroth Survivors\" — never a search query, no OR/grouping), url if the page gives one, why = one short clause. Empty array if none. Do not chase them yourself — just report.\n" +
    "Work strictly from this prompt — do not read files or explore the repository.\n\nStructured output only.",
    { label: "read:" + sourceLabel, phase: phaseName, schema: EXTRACT_SCHEMA }
  ).then(ext => {
    if (!ext) return null
    return { url: source.url, title: source.title, cached: ext.cached, relevant: ext.relevant, points: ext.points, leads: ext.leads || [] }
  }).catch(e => {
    log("read failed: " + source.url + " — " + (e.message || e))
    return null
  })
}

phase("Read")
const pages = (await parallel(
  fetchList.map(source => () => readSource(source, "Read"))
)).filter(Boolean)
const usable = pages.filter(p => p.relevant && p.points.length > 0)
const fetchCacheHits = pages.filter(p => p.cached).length
log("Read: " + pages.length + " pages (" + fetchCacheHits + " from cache), " + usable.length + " usable")

const fullStats = {
  ...basicStats,
  reads: pages.length,
  fetchCacheHits,
  agents: 1 + MAX_QUERIES + fetchList.length + 1,
}

if (usable.length === 0) {
  return {
    question: QUESTION,
    error: "Fetched pages yielded no usable content (irrelevant or failed). Retry, or use mcp__websearch__WebFetch manually.",
    stats: fullStats,
  }
}

// ─── Snowball: chase up to MAX_LEADS leads from the read pages ───
// Single deterministic round: the script dedups leads (by URL against
// everything already seen, by name against already-read titles and each
// other), makes one WebSearch call per chased lead, then reads up to
// MAX_SNOWBALL_FETCH new URLs. Leads found on round-2 pages end here —
// no recursion.
phase("Snowball")
const normName = s => String(s).toLowerCase().replace(/\s+/g, " ").trim()
const hostOf = u => (String(u).match(HOST_PATH_RE)?.[1] ?? "").toLowerCase()
const readTitles = usable.map(p => normName(p.title))
// Failed fetches (relevant:false, zero points — the fetcher got an empty or
// blocked page) contributed nothing; their titles become priority leads so
// the search layer can recover what the fetcher could not read.
const JUNK_TITLE = /^(just a moment|attention required|access denied|error|\d{3} )/i
const failedLeads = []
for (const page of pages) {
  if (page.relevant || page.points.length > 0) continue
  const t = normName(page.title)
  if (t.length < 8 || JUNK_TITLE.test(t)) continue
  if (!failedLeads.some(l => normName(l.name) === t)) failedLeads.push({ name: page.title, url: page.url })
}
const failedNames = failedLeads.map(l => normName(l.name))
const leadMap = new Map()
for (const page of usable) {
  const pageHost = hostOf(page.url)
  for (const lead of page.leads || []) {
    const name = normName(lead.name)
    if (name.length < 4) continue
    const urlKey = lead.url ? dedupKey(lead.url) : ""
    if (urlKey && seen.has(urlKey)) continue
    if (failedNames.includes(name)) continue
    if (readTitles.some(t => t.includes(name))) continue
    // Chase priority: an external link is a concrete, verifiable destination
    // (usually primary material — official docs, papers); a bare name is a
    // generic term that mostly retrieves glossary pages; a link back to the
    // reporting page's own domain is that page advertising its own related
    // content — least new information. Evidence: one run chased two bare
    // names and both lead searches went unread (their results lost the
    // read-cut tie), while an unchased official-docs lead sat right behind
    // the cut.
    const selfRef = !!lead.url && hostOf(lead.url) === pageHost
    const score = selfRef ? 0 : lead.url ? 2 : 1
    const key = urlKey || name
    if (!leadMap.has(key)) leadMap.set(key, { name: lead.name, url: lead.url || "", score })
    else {
      const l = leadMap.get(key)
      l.score = Math.max(l.score, score)
    }
  }
}
// Failed-fetch titles come first: they are known-relevant pages the reader
// could not see, while normal leads are only "mentioned somewhere". The rest
// go out by score, insertion order breaking ties.
const leads = failedLeads
  .concat([...leadMap.values()].sort((a, b) => b.score - a.score))
  .slice(0, MAX_LEADS)
log("Snowball leads: " + leads.length + (leads.length ? " — " + leads.map(l => l.name).join(", ") : ""))

let leadSearches = 0
let leadSearchCacheHits = 0
let leadFetchList = []
if (leads.length > 0) {
  const leadResults = (await parallel(
    leads.map((lead, i) => () =>
      agent(
        "## Lead Searcher (" + (i + 1) + "/" + leads.length + ")\n\n" +
        "Question: \"" + QUESTION + "\"\n\n" +
        "A source read for this question mentioned: \"" + lead.name + "\"" + (lead.url ? " — " + lead.url : "") + "\n\n" +
        "## Task\nMake exactly ONE call to the mcp__websearch__WebSearch tool. If the name is a page title with navigation cruft (Category:, List of, [tag] brackets, site suffixes), strip that cruft in the query; otherwise use the name verbatim:\n`" + lead.name + "`\n" +
        "Do not refine, repeat, or add searches — one call, then return. " +
        "Copy the tool response's `cached` field into the output verbatim. " +
        "Rank the top 6-8 results by relevance to the ORIGINAL question; skip unrelated namesakes. " +
        "Work strictly from this prompt — do not read files or explore the repository.\n\nStructured output only.",
        { label: "lead:" + (i + 1), phase: "Snowball", schema: SEARCH_SCHEMA }
      ).then(r => r ? { ...r, leadName: lead.name, leadUrl: lead.url || "" } : null)
    )
  )).filter(Boolean)
  // Empty lead searches get the same single simplified retry as main
  // searchers — a zero-result lead query is shape damage too ("Google AI
  // features", a chased official-docs lead, returned nothing verbatim and
  // its read slot went to another lead's results).
  const emptyLeadSearchers = leadResults.filter(s => s.results.length === 0)
  if (emptyLeadSearchers.length > 0) {
    log(emptyLeadSearchers.length + " lead search(es) returned zero results — retrying with simplified queries")
    const leadRetries = (await parallel(
      emptyLeadSearchers.map((s, i) => () =>
        agent(
          "## Lead Search Retry (" + (i + 1) + "/" + emptyLeadSearchers.length + ")\n\n" +
          "Question: \"" + QUESTION + "\"\n\n" +
          "A source read for this question mentioned: \"" + s.leadName + "\"" + (s.leadUrl ? " — " + s.leadUrl : "") + "\n" +
          "This search query returned ZERO results:\n`" + s.query + "`\n\n" +
          "## Task\nRewrite it into a simpler, more general variant that names the thing itself (drop site qualifiers, marketing words, and over-specific terms; keep 2-4 core keywords), then make exactly ONE call to the mcp__websearch__WebSearch tool with the rewritten query. " +
          "Do not make any other calls. Copy the tool response's `cached` field into the output verbatim. " +
          "Rank the top 6-8 results by relevance to the ORIGINAL question; skip unrelated namesakes. " +
          "Work strictly from this prompt — do not read files or explore the repository.\n\nStructured output only.",
          { label: "lead-retry:" + (i + 1), phase: "Snowball", schema: SEARCH_SCHEMA }
        )
      )
    )).filter(Boolean)
    leadResults.push(...leadRetries)
  }
  leadSearches = leadResults.length
  leadSearchCacheHits = leadResults.filter(s => s.cached).length
  // Same tier + round-robin ordering as the main read cut: on the previous
  // run both snowball reads went to one lead's results on a relevance tie
  // while the other lead searches returned nothing that got read.
  const byLeadSearcher = leadResults.map(searcher => {
    const mine = []
    for (const result of searcher.results) {
      const key = dedupKey(result.url)
      if (seen.has(key)) {
        dupes.push(result)
        continue
      }
      seen.set(key, "lead")
      mine.push(result)
    }
    return mine
  })
  const leadCandidates = byLeadSearcher.flatMap((results, si) =>
    results.map((r, ri) => ({ ...r, si, ri }))
  )
  leadFetchList = leadCandidates.sort(tierThenRoundRobin).slice(0, MAX_SNOWBALL_FETCH)
}

let leadPages = []
if (leadFetchList.length > 0) {
  leadPages = (await parallel(
    leadFetchList.map(source => () => readSource(source, "Snowball"))
  )).filter(Boolean)
}
const leadUsable = leadPages.filter(p => p.relevant && p.points.length > 0)
const leadFetchCacheHits = leadPages.filter(p => p.cached).length
log("Snowball: " + leadSearches + " lead searches (" + leadSearchCacheHits + " from cache), " + leadUsable.length + "/" + leadPages.length + " pages usable")

fullStats.snowball = {
  leads: leads.length,
  searches: leadSearches,
  searchCacheHits: leadSearchCacheHits,
  reads: leadPages.length,
  fetchCacheHits: leadFetchCacheHits,
}
fullStats.agents += leadSearches + leadPages.length

const allUsable = usable.concat(leadUsable)

// ─── Answer: cited synthesis, marked unverified ───
phase("Answer")
const block = allUsable.map((p, i) =>
  "[" + (i + 1) + "] " + p.title + " — " + p.url + "\n" +
  p.points.map(pt => "  - " + pt.point + " (\"" + pt.quote.slice(0, 200) + "\")").join("\n")
).join("\n")

const answer = await agent(
  "## Synthesis: quick grounded answer\n\n" +
  "**Question:** " + QUESTION + "\n\n" +
  "Sources read:\n" + block + "\n\n" +
  "## Instructions\n" +
  "0. You are a pure synthesis step: do NOT call any tool and do NOT read any file — your only input is the source block above, and every [n] marker must point into it.\n" +
  "1. Write the answer grounded ONLY in the points above; every claim carries an [n] marker naming its source.\n" +
  "2. If sources disagree, list each disagreement in `contradictions` (with the [n] sides).\n" +
  "3. If the sources do not cover part of the question, list it in `gaps` — do not fill gaps from background knowledge.\n" +
  "4. If the question is enumerative, add to `gaps` a note that the enumeration comes from a small source sample and likely misses small projects.\n" +
  "5. Answer in the question's language. Concise: this is the quick rung, not a report.\n\nStructured output only.",
  { label: "answer", schema: ANSWER_SCHEMA }
)

// Deterministic guard: the synthesis may cite only sources it was given.
// A citation outside the block means the agent grounded elsewhere (files,
// its own searches) — fall back to the raw extracted points.
const citedRefs = answer ? [...String(answer.answer).matchAll(/\[(\d+)\]/g)].map(m => parseInt(m[1], 10)) : []
const outOfRange = citedRefs.some(n => n < 1 || n > allUsable.length)
if (!answer || outOfRange) {
  if (outOfRange) log("Synthesis cited sources outside the provided block (" + citedRefs.join(",") + ") — falling back to raw points")
  return {
    question: QUESTION,
    error: "Synthesis step failed" + (outOfRange ? " (cited sources outside the provided block)" : "") + " — returning the extracted source points raw.",
    sources: allUsable.map(p => ({ url: p.url, title: p.title })),
    extractedPoints: allUsable.map(p => ({ url: p.url, points: p.points })),
    stats: fullStats,
  }
}

return {
  question: QUESTION,
  verified: false,
  answer: answer.answer + DISCLAIMER,
  contradictions: answer.contradictions,
  gaps: answer.gaps || [],
  sources: allUsable.map(p => ({ url: p.url, title: p.title })),
  stats: fullStats,
}
