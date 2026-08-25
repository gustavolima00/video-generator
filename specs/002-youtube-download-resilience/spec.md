# Feature Specification: Resilient YouTube Background Downloads

**Feature Branch**: `002-youtube-download-resilience`

**Created**: 2026-08-25

**Status**: Draft

**Input**: User description: "YouTube background downloads are failing with HTTP 429 on the innertube player endpoint. The throttle is per-IP and my server + laptop share one public IP, so all downloads fail while channel listing still works. I want two things: (1) po_token support — pytubefix's supported workaround for 429/bot detection, configurable, with the token stored outside git and a documented way to refresh it when it expires; must degrade gracefully when absent. (2) A background cache keyed by YouTube video id — a folder holding downloaded mp4s. Before downloading, check the cache and reuse the file; only hit the network on a miss. Configurable path and a bound on disk growth. Goal: daily runs stop depending on the network for clips already fetched, and still work when the IP is throttled."

## Context

The daily video-generation pipeline builds each output video on top of a background clip pulled from a fixed pool of YouTube videos (~144 candidates drawn from three configured channels). Every run currently re-downloads its chosen clips from YouTube, even though the pool changes slowly and the same clips are fetched again and again. YouTube has begun throttling the download endpoint per-IP (HTTP 429), and the operator's server and laptop share one public IP — so once the address is throttled, every run on both machines fails, even though listing channel contents still works.

Prior work (out of scope, already shipped): the download library is pinned, bot-detected default clients were replaced with explicitly configured ones, and a 429 now aborts the run immediately instead of walking the whole pool.

This feature has two parts, in priority order:

1. **Local background cache** (the main fix): downloaded clips are kept on disk keyed by YouTube video id and reused on later runs, so a run only touches the network for clips it has never fetched.
2. **Access-token support** (po_token): an operator-supplied proof-of-origin token that YouTube's bot detection accepts, reducing the chance of being throttled when a network fetch is required.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reuse previously downloaded backgrounds (Priority: P1)

As the pipeline operator, when the daily run needs a background clip it has downloaded before, it uses the locally stored copy instead of contacting YouTube, so the run completes even when YouTube is throttling the IP.

**Why this priority**: This is the main fix. The pool is effectively fixed, so after a few successful runs nearly every needed clip is already on disk — daily runs then no longer depend on YouTube's goodwill at download time. It also delivers value on its own even if the token work never ships.

**Independent Test**: Run the compilation twice against the same pool. The second run must produce its video without any download traffic to YouTube for clips fetched in the first run, and must succeed even if downloads are artificially made to fail.

**Acceptance Scenarios**:

1. **Given** a clip was downloaded in a previous run, **When** a new run selects that clip, **Then** the stored copy is used and no download request for it is sent to YouTube.
2. **Given** a clip is not yet stored locally, **When** a run selects it, **Then** it is downloaded once, used for the video, and kept for future runs.
3. **Given** every clip a run needs is already stored, **When** YouTube is throttling the IP (all downloads would fail), **Then** the run still completes successfully.
4. **Given** a stored copy exists but is unreadable or truncated, **When** a run selects that clip, **Then** the bad copy is discarded and the clip is re-downloaded as if it were a miss.

---

### User Story 2 - Bounded disk usage (Priority: P2)

As the pipeline operator, I can rely on the stored clips not growing without bound: storage stays under a configured limit, and when the limit is reached the least recently used clips are removed first.

**Why this priority**: Without a bound the cache eventually fills the disk on a small server. It protects the P1 value but is not itself the fix.

**Independent Test**: Configure a small size limit, store clips past it, and verify total size stays at or under the limit with the least recently used clips evicted first.

**Acceptance Scenarios**:

1. **Given** the store is at its configured size limit, **When** a new clip is added, **Then** least recently used clips are removed until the new clip fits, and total size remains at or under the limit.
2. **Given** a clip was evicted, **When** a later run selects it, **Then** it is simply re-downloaded (eviction is invisible apart from the extra download).
3. **Given** a clip is reused from the store, **When** eviction later runs, **Then** that clip counts as recently used and is evicted after clips that have not been touched.

---

### User Story 3 - Operator-supplied access token (Priority: P3)

As the pipeline operator, I can supply a proof-of-origin token that YouTube accepts as evidence the requests are legitimate, so fresh downloads (cache misses) succeed even from an IP that bot detection would otherwise throttle. The token lives outside the repository, and there is a documented procedure for obtaining and refreshing it when it expires.

**Why this priority**: It only matters on cache misses, which become rare once the store is warm. It is the fallback for genuinely new clips while throttled.

**Independent Test**: Configure a valid token and verify downloads use it; remove the token and verify downloads still work exactly as today.

**Acceptance Scenarios**:

1. **Given** a token is configured, **When** a clip must be downloaded, **Then** the download presents the token to YouTube.
2. **Given** no token is configured, **When** a clip must be downloaded, **Then** the download proceeds exactly as it does today (no error, no behavior change).
3. **Given** a configured token has expired or is rejected, **When** a download fails for that reason, **Then** the failure message tells the operator the token needs refreshing and points at the documented refresh procedure.
4. **Given** the repository is inspected, **Then** no token value appears in any tracked file.

---

### Edge Cases

- First-ever run (empty store) while the IP is throttled: downloads fail and the run aborts as it does today — the feature cannot conjure clips it never fetched. The error should make clear that later runs will recover once clips are stored.
- Two runs (or the server and the laptop's separate stores) downloading the same clip concurrently: both must end with a usable stored copy; neither may read a half-written file.
- A run is killed mid-download: the partial file must not be treated as a valid stored copy on the next run.
- The configured storage location does not exist yet: it is created on first use.
- The configured storage location is not writable: the run proceeds without storing (download-only), with a clear warning, rather than failing.
- Low-quality and normal-quality requests for the same video id must not serve each other's copies.
- The size limit is configured smaller than a single clip: the clip is still usable for the current run; the store simply cannot retain it.
- Token supplied but empty/whitespace: treated as absent.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Before downloading a background clip, the system MUST check a local store keyed by YouTube video id (and quality tier) and use the stored copy when present and valid.
- **FR-002**: On a store miss, the system MUST download the clip once, use it, and retain it in the store for future runs.
- **FR-003**: The store location MUST be configurable, with a sensible default outside the repository working tree; the location is created if missing.
- **FR-004**: Total store size MUST be bounded by a configurable limit; when the limit would be exceeded, least recently used clips are evicted first. Reuse counts as use.
- **FR-005**: Stored copies that are unreadable, truncated, or partially written MUST be detected, discarded, and treated as misses — a bad stored file must never be used in an output video and must never permanently poison its video id.
- **FR-006**: Storing MUST be best-effort: if the store cannot be written (permissions, full disk), the run continues with the downloaded bytes and logs a warning.
- **FR-007**: The system MUST log, per run, how many clips were served from the store versus downloaded, so the operator can see the store working.
- **FR-008**: The system MUST accept an optional operator-supplied proof-of-origin token (and its companion visitor identifier, if the mechanism requires one) and present it on every download request when configured.
- **FR-009**: The token MUST be supplied from outside version control (environment variable or an untracked/ignored file); no token value may live in a tracked file, and configuration examples use placeholders.
- **FR-010**: When no token is configured, downloads MUST behave exactly as they do today — the token path adds capability, never a new requirement.
- **FR-011**: The repository MUST include operator documentation covering: how to obtain a token, where to put it, how to tell it has expired (what the failure looks like), and how to refresh it.
- **FR-012**: When a download fails in a way consistent with an expired or rejected token, the error surfaced to the operator MUST say so and reference the refresh documentation.
- **FR-013**: Existing behavior on per-IP throttling (abort the run immediately rather than retrying the pool) MUST be preserved for downloads that do reach the network.

### Key Entities

- **Stored background clip**: a previously downloaded background video, identified by its YouTube video id plus quality tier; carries the video bytes, total size, and a last-used time that drives eviction.
- **Background store**: the bounded on-disk collection of stored clips at a configured location, with a configured maximum total size.
- **Proof-of-origin token**: an operator-supplied credential (with optional companion visitor identifier) that YouTube accepts as evidence requests come from a real client; optional, sourced outside version control, and refreshable by a documented manual procedure.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After one successful run over the pool, a repeat run over the same pool completes with zero download requests to YouTube for previously fetched clips (100% store hit rate on repeats).
- **SC-002**: With a warm store, the daily run completes successfully even when every YouTube download attempt would fail (simulated full throttle).
- **SC-003**: Store disk usage never exceeds the configured limit, measured after any run.
- **SC-004**: With no token configured, download success rate and behavior are unchanged from today (graceful degradation verified by the existing download tests passing unmodified in behavior).
- **SC-005**: An operator following only the written documentation can install or refresh a token without reading source code.
- **SC-006**: With a warm store, time spent acquiring backgrounds for a run drops by at least 80% compared to an all-network run.

## Assumptions

- The clip pool is drawn from a small fixed set of channels and changes slowly, so a warm store covers nearly all of a run's needs; the ~144-video pool at typical short-video sizes fits comfortably in a few tens of GB.
- Default store size limit is 20 GB unless configured otherwise — large enough for the whole current pool, small enough for a modest server disk. Least-recently-used eviction is the chosen policy (the user asked for "a size cap or eviction"; LRU under a cap gives both).
- The server and the laptop each keep their own local store; sharing one store across machines is out of scope. (They share an IP, not a filesystem.)
- Channel listing is unaffected by the throttle (observed behavior) and needs no caching; only background downloads are cached.
- The token mechanism is the one the download library already supports (proof-of-origin token plus visitor data); obtaining a token is a manual operator step, and automatic token generation/renewal is out of scope for this feature.
- Cached clips are an operational artifact of the operator's own pipeline; retention/redistribution concerns are the operator's responsibility and unchanged by this feature.
- Story-mode videos and other non-YouTube backgrounds are unaffected; scope is the YouTube compilation path only.

## Out of Scope

- Automatic token generation or scheduled token renewal.
- A shared or remote store between machines.
- Caching of channel/playlist listings.
- Proxy rotation, IP rotation, or other throttle-evasion beyond the library-supported token.
- Changing which clips the pipeline selects (pool strategy, pool size).
