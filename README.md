

# jira-watcher-agent

### An AI-powered JIRA planner, organizer, and triage CLI for engineers.

*Fetch every JIRA ticket assigned to you, get an LLM-generated triage digest by category, and investigate potential resolutions — all from your terminal, powered by the coding CLI you already use ([Claude Code](https://claude.com/claude-code), [OpenAI Codex](https://github.com/openai/codex), or [Cursor Agent](https://cursor.com/cli)).*





**[Quickstart](#quickstart) · [Configuration](#configuration) · [Integrations](#integrations) · [Resolve stage](#resolve-stage) · [How it works](#how-watcher-works) · [Troubleshooting**](#troubleshooting)



---

*Also known as a JIRA planner, JIRA organizer, JIRA triage tool, JIRA AI agent, JIRA CLI, or a Claude / Codex / Cursor JIRA integration.*

> **Public alpha.** Core workflows are usable day-to-day, but flag names, JQL
> defaults, and memory schema may still change.

---



## Before you begin

Make sure you have:

- **Python 3.9+** with `pip` and `venv`.
- A **JIRA account** — either JIRA Cloud (Atlassian-hosted, including custom
vanity domains like `jira.mycompany.com`) or JIRA Server / Data Center.
- **One coding CLI on your** `PATH` — any of `claude`, `codex`, or
`cursor-agent`. Watcher shells out to it; you keep your existing
subscription and auth.
- A **JIRA credential** appropriate to your flavor
(see [Configuration](#configuration)).

You do **not** need a separate Anthropic, OpenAI, or Cursor API key.

---



## Quickstart

```bash
# once published to PyPI
pip install jira-watcher-agent

# or from source
git clone https://github.com/<you>/jira-watcher-agent && cd jira-watcher-agent
python -m venv .venv && source .venv/bin/activate
pip install -e .

# configure
cp .env.example .env
$EDITOR .env                 # fill in JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN

# verify auth, then run
watcher --whoami             # confirms which JIRA account you're hitting
watcher                      # one-shot triage digest
watcher --watch              # keep polling
```

Three console commands ship with the package — pick whichever reads best
in your muscle memory:


| Command              | What it is                                              |
| -------------------- | ------------------------------------------------------- |
| `watcher`            | Short, original name — used everywhere in this doc.     |
| `jira-watcher`       | Longer alias, disambiguates from other "watcher" tools. |
| `jira-watcher-agent` | Full name — useful in scripts and docs.                 |




First run produces an LLM-authored triage digest of every ticket assigned
to you, grouped by category, with a small stats table underneath. Answer
`y` to the follow-up prompt to investigate specific tickets in detail
(see the [Resolve stage](#resolve-stage)).

---



## Table of contents

- [Before you begin](#before-you-begin)
- [Quickstart](#quickstart)
- [Why a JIRA planner CLI?](#why-a-jira-planner-cli)
- [Configuration](#configuration)
- [Usage](#usage)
- [Integrations](#integrations)
- [Resolve stage](#resolve-stage)
- [How watcher works](#how-watcher-works)
- [Capabilities](#capabilities)
- [Supported providers](#supported-providers)
- [Memory & categorization](#memory--categorization)
- [Token optimization](#token-optimization)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [When does watcher connect to what?](#when-does-watcher-connect-to-what)
- [License](#license)

---



## Why a JIRA planner CLI?

Every engineer with a real JIRA queue lives with the same three problems
that a JIRA planner, organizer, or triage tool is supposed to solve:

1. **The queue is noisy.** Twenty tickets, six statuses, three priorities,
  two "urgent" flags — and no single view tells you what actually matters
   this morning.
2. **Reading tickets is slow.** Descriptions, comments, and status hops are
  scattered. You end up context-switching into the browser just to remember
   what a ticket is about.
3. **You lose the shape of the work.** Six months in, you have no honest
  picture of whether you're mostly fixing bugs, shipping features, upgrading
   dependencies, or answering support questions.

`jira-watcher-agent` is a small opinionated JIRA AI agent for exactly this
workflow:

- It **fetches** what's assigned to you (or any JQL you like).
- It **hands the payload to an LLM CLI you already have** — no extra API
keys — and gets back a terse, category-grouped digest optimized for
reading in a terminal.
- It **remembers** the category the LLM assigns to each ticket, so re-runs
skip work that hasn't changed and you can watch the composition of your
backlog evolve over time.

The whole thing is ~1000 lines of Python, one dependency layer, and no
service to host.

---



## Configuration

All settings live in a `.env` file at the project root. Only three variables
are strictly required for JIRA; everything else has sensible defaults.

### Common settings

```env
# Pick your LLM CLI — one of: claude | codex | cursor
LLM_PROVIDER=claude

# Watch-mode polling interval (seconds)
WATCHER_INTERVAL=300

# Where to persist ticket→category memory.
# Default:
#   * from-source install:  <project-root>/memory/memory.json
#   * PyPI install:         ~/.watcher/memory.json
# WATCHER_MEMORY_PATH=/absolute/path/to/memory.json
```



### JIRA Cloud (Atlassian-hosted)

Applies whether your site is `<name>.atlassian.net` **or** a custom vanity
domain (e.g. `prod.jira.yourcompany.com`) that's still an Atlassian Cloud
tenant.

```env
JIRA_FLAVOR=cloud
JIRA_BASE_URL=https://your-tenant.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=ATATT3xFf...
```

- Create the API token at
[https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
- `JIRA_EMAIL` must be the login email of the Atlassian account that
created the token.
- Watcher auto-discovers your tenant's `cloudId` from
`<base>/_edge/tenant_info` and routes API calls through
`https://api.atlassian.com/ex/jira/<cloudId>` — this is required for
scoped `ATATT…` tokens, which do **not** authenticate against the site
URL. If discovery is blocked, set `JIRA_CLOUD_ID=<uuid>` in `.env`.



### JIRA Server / Data Center (self-hosted)

```env
JIRA_FLAVOR=server
JIRA_BASE_URL=https://jira.internal.corp
JIRA_API_TOKEN=<your Personal Access Token>
# JIRA_EMAIL is not needed for server flavor
```

- Create the PAT in JIRA: your avatar → **Profile** → **Personal Access
Tokens** → **Create token**.
- Watcher uses Bearer auth and the v2 REST API.



### Optional overrides

```env
JIRA_JQL=assignee = currentUser() AND sprint in openSprints()
JIRA_USER_AGENT=Mozilla/5.0 (compatible; watcher/0.1)
LLM_CLI_PATH=/opt/homebrew/bin/claude
```

---



## Usage



### One-shot digest

```bash
watcher                       # default provider, default JQL
watcher --provider codex      # pick a provider ad hoc
watcher --jql 'assignee = currentUser() AND priority = Highest'
```



### Watch mode

```bash
watcher --watch                        # poll every WATCHER_INTERVAL seconds
watcher --watch --interval 120         # override interval
```

The LLM is only invoked when tickets actually change (fingerprinted by
`(key, updated)`). Quiet periods print a one-line "no changes" heartbeat.

### All flags


| Flag                               | Purpose                                                                                                                            |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `--provider {claude,codex,cursor}` | Pick the LLM CLI for this run.                                                                                                     |
| `--cli-path PATH`                  | Override the binary invoked for the provider.                                                                                      |
| `--jql "..."`                      | Override the JQL query.                                                                                                            |
| `--watch`                          | Poll on a loop; re-summarize only on change.                                                                                       |
| `--interval N`                     | Polling interval in seconds.                                                                                                       |
| `--no-llm`                         | Skip the LLM; use a rule-based digest (still shows cached categories).                                                             |
| `--forget`                         | Wipe the category memory before running.                                                                                           |
| `--whoami`                         | Print the JIRA account the token authenticates as, then exit.                                                                      |
| `--debug`                          | Print JIRA request/response diagnostics (URL, page counts, cloudId, etc.).                                                         |
| `--check [target]`                 | Verify integration connectivity — see [Integrations](#integrations).                                                               |
| `--eks-login`                      | Write a kubeconfig entry via `aws eks update-kubeconfig`, then exit.                                                               |
| `--resolve`                        | Deep-investigate specific tickets — see [Resolve stage](#resolve-stage).                                                           |
| `--keys A,B,C`                     | With `--resolve`, comma-separated issue keys to investigate.                                                                       |
| `--category NAME`                  | With `--resolve`, pick currently-assigned tickets tagged with this category.                                                       |
| `--limit N`                        | Cap tickets per run (default: 3). Ignored when `--keys` is used — explicit keys always run in full.                                |
| `--no-interactive`                 | Skip the post-digest "investigate any tickets?" prompt.                                                                            |
| `--kube-context NAME`              | kubectl context used for read-only EKS probes during investigation. Overrides `KUBE_CONTEXT` env and skips the interactive picker. |


---



## Integrations

Watcher connects to systems adjacent to your JIRA queue so it can (later)
suggest concrete resolution steps — the linked PR, the failing pipeline,
the pod that's crash-looping. As of this release, connectivity is
verify-only; each integration is optional and activates when its env vars
are present.


| System        | Purpose                       | Auth                                        |
| ------------- | ----------------------------- | ------------------------------------------- |
| **JIRA**      | Fetch assigned tickets        | Cloud API token (via gateway) or Server PAT |
| **Bitbucket** | Repos, PRs, commits           | Atlassian API token (Cloud) or PAT (DC)     |
| **Jenkins**   | Build & pipeline lookups      | Username + API token                        |
| **GitHub**    | Repos, PRs, issues            | PAT or `gh` CLI                             |
| **EKS**       | Cluster & workload inspection | AWS CLI + kubectl                           |




### Configure

Add whichever sections you want to `.env`; unset ones are simply skipped.

```env
# Bitbucket (Cloud shown — Data Center uses BITBUCKET_AUTH_MODE=bearer)
BITBUCKET_HOST=bitbucket.org
BITBUCKET_WORKSPACE=your-workspace
BITBUCKET_EMAIL=you@example.com
BITBUCKET_API_TOKEN=ATATT3xFf...

# Jenkins
JENKINS_BASE_URL=https://jenkins.example.com
JENKINS_USERNAME=your-jenkins-username
JENKINS_API_TOKEN=<api-token-from-jenkins>

# GitHub — either a PAT or an authenticated `gh` CLI on PATH is enough
GITHUB_TOKEN=ghp_xxx
# GITHUB_USE_GH=1     # prefer `gh api` over REST even when a token is set

# EKS — needs AWS CLI v2 and kubectl on PATH; run `aws sso login` first.
# When resolve-stage investigation runs, watcher lists your kubectl
# contexts and asks which to use (unless KUBE_CONTEXT or --kube-context
# is set). Probes are strictly read-only: kubectl get + describe only.
EKS_CLUSTER_NAME=prod-cluster
EKS_REGION=us-east-1
# EKS_NAMESPACE=default
# AWS_PROFILE=my-profile
# KUBE_CONTEXT=arn:aws:eks:us-east-1:123456789012:cluster/prod
```



### Verify

```bash
watcher --check              # every configured integration
watcher --check bitbucket    # just one
watcher --check eks          # aws sts + describe-cluster; no writes
```

The check prints a status table (identity, details, error, hint) and exits
`0` when every checked integration is OK.

### EKS login

Verification is read-only. To *write* a kubeconfig entry so `kubectl` can
talk to the cluster:

```bash
watcher --eks-login          # runs `aws eks update-kubeconfig` under the hood
```



### Where the patterns come from

Bitbucket and Jenkins clients are ported from
`engineering-knowledge-base/skills/{bitbucket-agent,jenkins}/scripts/*_common.py`
so behavior matches the other tooling in the org (User-Agent, auth modes,
keychain fallback via `security find-generic-password`). GitHub and EKS
have no prior KB skill, so watcher defines fresh but conventional patterns.

---



## Resolve stage

The digest tells you *what's* in your queue. The resolve stage tells you
*whether a specific ticket is a real, currently-active defect* — grounded
in **live evidence pulled at investigation time** — and *what to do about
it*.

> **Read-only guarantee.** Investigation only *reads* — JIRA, Bitbucket,
> GitHub, and `kubectl get`/`describe` calls. No writes, no `apply`, no
> `patch`, no `use-context` mutation of your kubeconfig. Enforced at the
> code level by a subcommand guard on every `kubectl` call.



### Invocation

There are two ways in:

**1. Interactive follow-up (default).** After a one-shot `watcher` run
prints the digest, it asks:

```
Investigate any tickets from this list? [y/N]:
```

Answer `y` and watcher shows the categories present in the current
queue, then a single smart prompt that auto-detects category name vs.
JIRA keys:

```
Available categories: bug (5), infra (3), refactor (2)
Pick a category name or JIRA keys (e.g. bug  or  PROJ-1, PROJ-2): bug
Limit (3): 5
```

Or pick keys directly — no limit prompt is shown because the caller has
already committed to N tickets:

```
Pick a category name or JIRA keys (e.g. bug  or  PROJ-1, PROJ-2): PROJ-123, PROJ-456
```

Context is preserved across the digest → prompt → investigation flow —
the already-fetched ticket list and in-memory categories are reused, so
selecting *by category* makes zero extra JIRA calls; only the actual
per-ticket enrichment hits `GET /issue/{key}` per chosen ticket.

After each investigation you're asked whether to investigate more from
the same queue, so you can chain a few passes in one session.

The prompt is skipped automatically when stdin/stdout aren't a TTY
(pipes, cron, CI), or when you pass `--no-interactive`, or when
`--no-llm` is set (no LLM = no investigation).

**2. One-shot flags (scriptable).**

```bash
# investigate two tickets by key (--limit is ignored: keys are explicit)
watcher --resolve --keys PROJ-123,PROJ-456

# investigate the top N cached-'bug' tickets from your queue
watcher --resolve --category bug --limit 5

# swap providers ad hoc
watcher --resolve --keys PROJ-123 --provider codex

# pin the kubectl context used for EKS probes (skips the picker)
watcher --resolve --keys PROJ-123 --kube-context prod-us-east-1
```

`--category` reads from watcher's memory, so run `watcher` at least once
before this mode so tickets have categories assigned.

### Kube context selection

Whenever the investigation is about to hit EKS (i.e. `EKS_CLUSTER_NAME`

- `EKS_REGION` are configured), watcher **enumerates your kubectl
contexts and asks which one to use**. It never mutates your kubeconfig
(no `kubectl config use-context`) — the chosen context is passed on
every call as `--context <name>`.

```
Available kubectl contexts  (read-only probes will use the one you pick)
 # ┃ Context                                              ┃ Note
━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━
 1 │ arn:aws:eks:us-east-1:…:cluster/prod                 │ current
 2 │ arn:aws:eks:us-east-1:…:cluster/staging              │
 3 │ arn:aws:eks:us-east-1:…:cluster/dev                  │
Type a number, a full context name, or `skip` to run without EKS probes.
Use kube context (1): 2
Using kube context arn:aws:eks:us-east-1:…:cluster/staging (read-only:
kubectl get only — nothing will be modified).
```

How the pick is resolved:


| Signal                                     | Effect                                                     |
| ------------------------------------------ | ---------------------------------------------------------- |
| `--kube-context NAME` CLI flag             | Uses it, skips the picker.                                 |
| `KUBE_CONTEXT` env var                     | Uses it, skips the picker.                                 |
| Interactive TTY + contexts available       | Shows the table, defaults to current.                      |
| Non-interactive + a current context exists | Uses current context silently.                             |
| Non-interactive + no current context       | Skips EKS probes; digest still runs.                       |
| Empty kubeconfig                           | Skips EKS probes with a hint to run `watcher --eks-login`. |
| User types `skip`                          | Skips EKS probes for the session.                          |




### What it fetches (live evidence)

For each ticket, watcher probes every downstream system named in the
ticket at investigation time and hands the LLM the answers — the LLM
is instructed to *use* those answers rather than telling you to
"go check yourself." Evidence streams (weighted top-down):

1. **Live workload evidence (kubectl)** — for each candidate deployment
  name extracted from JIRA `components` + `labels`, watcher runs
   read-only calls against the selected context:
  - `kubectl get deployment <name> -n <ns> -o json` → `Available`
  condition, ready/desired replicas, transition time.
  - `kubectl get pods -l app=<name> -n <ns>` → up to 5 pods with
  phase, ready flag, restart count, and `waiting.reason`
  (e.g. `CrashLoopBackOff`, `ImagePullBackOff`).
  - `kubectl get events --field-selector involvedObject.name=<name>` →
  up to 5 most-recent events.
   Ready pods + no Warning events → nudges toward `LIKELY_INVALID`.
   CrashLoop / recent Warning events → nudges toward `REAL` /
   `LIKELY_REAL`. Missing deployment → *no signal*.
2. **Live repo evidence (Bitbucket / GitHub API)** — for each repo URL
  watcher spots in the description, comments, or JIRA remote links, it
   calls the corresponding API to check existence:
  - Bitbucket Cloud: `GET /2.0/repositories/<ws>/<repo>`
  - Bitbucket DC: `GET /rest/api/1.0/projects/<key>/repos/<slug>`
  - GitHub: `GET /repos/<owner>/<repo>` (via `gh api` when available)
   The result lands in the prompt as either **EXISTS** (with default
   branch, `last_activity`, private/public flag) or **NOT FOUND (404) —
   repo already deleted or renamed**. So if the ticket says "delete
   `ms-ccx-verification`" and Bitbucket returns 404, watcher tells the
   LLM "the repo is already gone" and the verdict becomes `INVALID`
   with a "close the ticket" resolution — no more "please confirm in
   Bitbucket" hedging.
3. **Live PR evidence** — same URL scan, but for `/pull-requests/<n>` /
  `/pull/<n>` links. Fetches title + state + author + updated-at.
   **MERGED PRs are flagged as a "possible stale-ticket signal"** so the
   LLM considers whether the fix already shipped but the ticket wasn't
   closed.
4. **Live Jenkins evidence (Jenkins API)** — for each Jenkins job URL
  in the ticket that matches your configured `JENKINS_BASE_URL` host:
  - `GET <base>/<job-path>/api/json` → existence, `buildable` flag,
  `lastCompletedBuild`.
  - Follow-up `GET .../<n>/api/json` → last build `result` and
  timestamp.
   The prompt gets **EXISTS · last build #42 → SUCCESS**, **EXISTS ·
   DISABLED**, or **NOT FOUND (404) — job is not present at that URL**.
   A `FAILURE`/`ABORTED`/`UNSTABLE` last build nudges toward `REAL`; a
   long streak of `SUCCESS` on the referenced job nudges toward
   `LIKELY_INVALID`.
5. **Ticket content** — description + latest ~8 comments. The
  description is treated as background rather than authoritative
   (often stale).

Anything watcher couldn't fetch — because the integration isn't
configured, the host doesn't match, or the API returned an unexpected
error — is passed through as **NO SIGNAL** and the LLM is told not to
use it as evidence either way.

### What the LLM returns

A verdict + reasoning + resolutions block per ticket. The verdict itself
is a **clickable OSC 8 hyperlink** in modern terminals — click it (or
⌘-click on macOS, Ctrl-click on Linux) to open the JIRA ticket in your
browser.

```
──────────── PROJ-123 · Checkout 500s in prod ────────────
Verdict: LIKELY_REAL  https://jira.example.com/browse/PROJ-123
╭─────────────────────────────────────────────────────────╮
│ **Verdict:** LIKELY_REAL                                │
│                                                         │
│ **Reasoning**                                           │
│ Live probe shows `orders-api` has 2/3 ready with 4      │
│ restarts and a recent `BackOff` Warning event (×17,     │
│ last 12min ago). PR #42 is OPEN — fix not yet shipped.  │
│                                                         │
│ **Possible resolutions**                                │
│ - Merge orders-api#42 and roll a redeploy.              │
│ - If rollback is safer, revert commit abc1234.          │
│ - Ping @bob (last reviewer) — flagged this in PR #42.   │
╰─────────────────────────────────────────────────────────╯
```

Verdicts are colored in the terminal:


| Verdict                      | Meaning                                                            |
| ---------------------------- | ------------------------------------------------------------------ |
| **REAL** (red)               | Live evidence + comments strongly support an active defect.        |
| **LIKELY_REAL** (yellow)     | Probably an active defect; some ambiguity remains.                 |
| **NEEDS_INFO** (cyan)        | Not enough info in the ticket or live signals to decide.           |
| **LIKELY_INVALID** (magenta) | Likely stale ticket (fix already shipped, workload healthy, etc.). |
| **INVALID** (green)          | Clearly not a defect based on the evidence.                        |


The full LLM response is rendered as a bordered markdown panel below the
verdict line.

### Cost & limits

Each ticket is one LLM call — the prompt is per-ticket rather than
batched so a failure doesn't wipe out the rest of the run. The default
`--limit 3` keeps ad-hoc invocations cheap; raise it deliberately when
doing a bulk triage sweep. When you pass `--keys`, `--limit` is ignored
— explicit key lists always run in full.

---



## How watcher works

```
   JIRA site                 Watcher                  Your local CLI
  ─────────────           ─────────────             ─────────────────
                          ┌───────────┐
   /_edge/tenant_info ◀───│ discover  │              claude / codex /
                          │ cloudId   │              cursor-agent
                          └─────┬─────┘                    ▲
                                ▼                          │
   /rest/api/…/search  ◀───┤  fetch   │                    │
                          └─────┬─────┘                    │
                                ▼                          │
                          ┌───────────┐   compact prompt   │
                          │  diff vs  │  ────────────────▶ │
                          │  memory   │                    │
                          └─────┬─────┘                    │
                                ▼           markdown       │
                          ┌───────────┐  + category tags  │
                          │  parse &  │ ◀──────────────────┘
                          │  render   │
                          └───────────┘
                                │
                                ▼
                          memory/memory.json  (from source)
                          ~/.watcher/memory.json  (from PyPI)
```

Step by step:

1. **Discover.** For Cloud tenants, watcher hits `<base>/_edge/tenant_info`
  to pick up the `cloudId` and rewrites the API base to Atlassian's
   platform gateway. For Server/DC, it uses your site URL directly.
2. **Fetch.** `jira_client.py` calls the search endpoint (v3 `/search/jql`
  for Cloud, v2 `/search` for Server), pages through all results, and
   flattens ADF descriptions to plain text.
3. **Diff against memory.** `memory.py` loads the memory file — a
  per-ticket record of the last-seen `updated` timestamp and cached
   category. When installed from source it lives at
   `<project-root>/memory/memory.json`; on a plain PyPI install it falls
   back to `~/.watcher/memory.json`. Override with `WATCHER_MEMORY_PATH`. Tickets whose signature is unchanged are shipped compactly
   (no description, cached category attached); new or changed tickets are
   shipped with description + latest comment for re-tagging.
4. **Prompt the LLM.** `providers.py` pipes the prompt to the selected CLI
  over stdin. The prompt asks for a markdown digest followed by a
   `===CATEGORIES===` block with `KEY: category` lines.
5. **Parse.** The digest is extracted and rendered with `rich`; the tag
  block updates memory for the next run.
6. **Render.** You see the digest and a category count table for the
  current run.



### Resolve stage flow

When you accept the "Investigate any tickets?" prompt (or invoke
`--resolve`), the flow above is followed by a per-ticket enrichment
pass:

```
   Ticket keys         Watcher                     Evidence sources
  ─────────────    ──────────────               ────────────────────────
                  ┌──────────────┐
   GET /issue ◀── │ enrich per   │
   GET remotelinks│ ticket       │
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐    Bitbucket / GitHub API  ─── Repo & PR
                  │ extract URLs │ ─▶ /repositories/<w>/<r>       evidence
                  │ + service    │    /pulls/<n>                  (EXISTS
                  │   candidates │                                 / 404)
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐    Jenkins API             ─── Jenkins
                  │ probe        │ ─▶ /<job>/api/json             evidence
                  │ integrations │    /<job>/<n>/api/json         (last build
                  │              │                                 status)
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐    kubectl (read-only)     ─── Workload
                  │ pick kube-   │ ─▶ get deployment / pods /     evidence
                  │ context      │      events
                  │ (once/sess.) │
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐    Coding CLI              ─── Verdict +
                  │ prompt LLM,  │ ─▶ (Claude / Codex /            reasoning +
                  │ per ticket   │      Cursor)                    resolutions
                  └──────────────┘
```

---



## Capabilities


|                                        |                                                                                                       |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 🔍 **JQL-driven queue view**           | Default to your assigned open tickets; override with any JQL.                                         |
| 🧠 **LLM-authored triage digest**      | Category counts, urgent-item callouts, grouped bullets, focus recommendation.                         |
| 🏷️ **Persistent categorization**      | 14 generic buckets remembered per ticket; watch your backlog shape evolve.                            |
| 👁️ **Watch mode**                     | Polls JIRA; only invokes the LLM when a ticket signature changes.                                     |
| ⚡ **Token-optimized prompts**          | Short field names, `days-ago` dates, cached categories skipped from the payload.                      |
| 🔌 **Bring-your-own coding CLI**       | `claude`, `codex`, or `cursor-agent` — no separate API keys.                                          |
| 🛡️ **Diagnostics-first**              | `--whoami`, `--debug`, and auth-failure hints that explain what actually broke.                       |
| 🧩 **Cloud + Server support**          | Handles Atlassian gateway auth, vanity domains, and Server/DC Bearer PATs.                            |
| 🔌 **Adjacent-system connectivity**    | One `--check` command verifies Bitbucket, Jenkins, GitHub, and EKS in one pass.                       |
| 🕵️ **Resolve stage**                  | Deep-investigate a ticket with full context + linked PRs; get a verdict and fixes.                    |
| 📡 **Live workload evidence**          | Reads deployment / pod / event state via `kubectl get` so the verdict reflects reality.               |
| 🗂️ **Live repo evidence**             | Probes Bitbucket / GitHub for repos named in the ticket — EXISTS vs 404 goes straight to the verdict. |
| 🏗️ **Live Jenkins evidence**          | Fetches job existence + last build result for Jenkins URLs in the ticket.                             |
| 🔒 **Read-only investigation**         | Every kubectl call is guarded — no writes, no `apply`, no kubeconfig mutation.                        |
| 🎯 **Interactive kube-context picker** | Lists your contexts, asks which one to use for probes, and never overwrites your `use-context`.       |
| 🔗 **Clickable verdict**               | Verdict labels are OSC 8 hyperlinks — click to open the JIRA ticket.                                  |


---



## Supported providers


| Provider         | CLI binary     | Non-interactive invocation       | Auth                   |
| ---------------- | -------------- | -------------------------------- | ---------------------- |
| **Claude Code**  | `claude`       | `claude -p --output-format text` | Your Claude Code login |
| **OpenAI Codex** | `codex`        | `codex exec -`                   | Your Codex CLI login   |
| **Cursor Agent** | `cursor-agent` | `cursor-agent -p`                | Your Cursor login      |


Whichever you pick must already be installed and authenticated on your
machine. Pipe the prompt over stdin; watcher captures stdout. Missing
binary → a clear error with an install hint.

---



## Memory & categorization

Every ticket the LLM sees is tagged with one of these generic buckets:

```
bug · feature · enhancement · refactor · infra · docs · testing
performance · security · dependency · data · ux · support · other
```

Assignments are persisted next to your install:

- **From source** (`pip install -e .`) → `<project-root>/memory/memory.json`.
Already listed in `.gitignore`; nothing to worry about.
- **From PyPI** → `~/.watcher/memory.json`. Watcher won't write into
`site-packages` — it would get clobbered on the next `pip install --upgrade`.

Override either default with `WATCHER_MEMORY_PATH` in `.env`. On
subsequent runs:

- Tickets whose `(key, updated)` signature is **unchanged** ride along
without description or comments, tagged with the cached category — no
re-categorization needed.
- Only **new or changed** tickets are shipped with description + latest
comment and asked to be tagged.

Over time this gives you an honest picture of what your work actually
consists of. Handy for retrospectives, capacity planning, or noticing
you've become the person who quietly handles every dependency upgrade.

Wipe the memory anytime with:

```bash
watcher --forget
```

---



## Token optimization

The prompt sent to the LLM is aggressively slimmed:

- Short JSON keys (`k`, `t`, `s`, `p`, `u`, `c`, `d`, `lc`) instead of
verbose field names.
- Timestamps collapsed to `days-ago` integers.
- Summaries clipped to 200 chars, descriptions to 300, single latest
comment to 200.
- Cached-category tickets ship **without** body; only new/changed tickets
carry the description + latest comment.
- Watch mode skips the LLM entirely when nothing has changed since the
previous poll.

Net effect: quiet days send only a compact key/status/priority list.

---



## Troubleshooting

Run any command with `--debug` to see actual URLs, HTTP status codes, and
pagination state. Start with `watcher --whoami` — it isolates the auth
question from the search question.

**401 "Client must be authenticated"**

- **Cloud, custom vanity domain** (e.g. `prod.jira.yourcompany.com`): the
scoped `ATATT…` token only authenticates against the platform gateway.
Watcher auto-discovers the `cloudId` from `<base>/_edge/tenant_info`; if
that discovery is blocked, set `JIRA_CLOUD_ID=<uuid>` in `.env`.
- **Cloud, plain email/token**: double-check that `JIRA_EMAIL` is the login
email of the Atlassian account that created the token, and that the
token itself was pasted with no whitespace or quotes.
- **Server / DC**: make sure the token is a Personal Access Token from
your JIRA profile — not your login password.



**403 "Failed to parse Connect Session Auth Token"**

Cloud-only error meaning the token was sent to the site URL instead of the
platform gateway. Confirm `JIRA_FLAVOR=cloud` and that watcher's `--debug`
output shows `cloud gateway resolved: https://api.atlassian.com/ex/jira/…`.
If not, discovery failed — set `JIRA_CLOUD_ID` manually.



**"No tickets currently assigned to you" — but you have tickets**

- Some JIRA setups don't populate the `resolution` field on open tickets.
Watcher's default JQL uses `statusCategory != Done` which is more
portable, but you can override with `--jql "assignee = currentUser()"`
to see everything, resolved or not.
- Confirm `watcher --whoami` shows the account you expect. If a different
account, `assignee = currentUser()` is resolving to someone else.



**LLM CLI errors**

If the selected CLI isn't on `PATH`, watcher prints an install hint. Point
at a specific binary with `--cli-path` or `LLM_CLI_PATH`. If the CLI runs
but returns garbled output, add `--no-llm` to fall back to the rule-based
digest while you investigate.



**Corporate WAF / User-Agent blocks**

Some corporate proxies and Cloudflare fronts block the default
`python-requests` User-Agent. Watcher already sends a browser-like UA; if
your setup needs a specific string, set `JIRA_USER_AGENT` in `.env`.



**EKS probes return "not found" for every candidate**

Watcher extracts service names from JIRA `components` and `labels`, then
runs `kubectl get deployment <candidate> -n <ns>` against the context you
picked. Common causes when everything comes back not-found:

- Deployment lives in a different namespace — set `EKS_NAMESPACE` in
`.env` or pass a JIRA component name that matches the actual
deployment.
- Wrong kubectl context selected — re-run with a different context via
`--kube-context NAME`, or set `KUBE_CONTEXT` in `.env`.
- JIRA Components field is empty and labels don't match k8s naming
conventions — the extractor requires lowercase-alphanumeric-hyphen
names.

Watcher never treats "not found" as evidence of a working service — it's
recorded as *no signal*, and the LLM is instructed to ignore it.



**"Skipping EKS probes" appears even though EKS is configured**

Investigation runs read-only kubectl calls and needs a context. It skips
probes when:

- No kubectl contexts exist (`kubectl config get-contexts` is empty).
- Running non-interactively without a current context.
- You typed `skip` at the picker.

To force a specific context in scripts, use
`watcher --resolve --keys PROJ-1 --kube-context <name>` or set
`KUBE_CONTEXT` in `.env`.



---



## Project layout

```
watcher/
├── cli.py                # argparse entry point + one-shot / watch loops
├── config.py             # loads and validates .env
├── jira_client.py        # Cloud + Server API client with cloudId discovery
├── memory.py             # persistent ticket→category memory
├── providers.py          # wraps claude / codex / cursor-agent CLIs
├── summarizer.py         # compact prompt builder + response parser
├── investigator.py       # resolve: enrich, extract PRs, gather live evidence, prompt
└── integrations/
    ├── _base.py          # IntegrationStatus + macOS keychain fallback
    ├── bitbucket.py      # Cloud (Basic email:token) + DC (Bearer PAT)
    ├── jenkins.py        # Basic username:api_token + CSRF crumb helper
    ├── github.py         # gh CLI when available, else PAT via REST
    └── eks.py            # aws sts + describe-cluster; read-only kubectl probes
                          # (get deployment/pods/events, kube-context picker)
```

---



## When does watcher connect to what?

The digest only talks to JIRA. Every other integration is contacted
**lazily**, on-demand, during `--resolve` (investigation):


| Integration     | When it's called | What triggers a call                                                                             |
| --------------- | ---------------- | ------------------------------------------------------------------------------------------------ |
| JIRA            | Every run        | Search + per-ticket `get_issue` / remote-links during resolve.                                   |
| Bitbucket       | Resolve only     | A `bitbucket.org/<ws>/<repo>[/…]` or DC repo URL in description, comments, or JIRA remote links. |
| GitHub          | Resolve only     | A `github.com/<owner>/<repo>[/…]` URL in the ticket.                                             |
| Jenkins         | Resolve only     | A `/job/…` URL whose host matches `JENKINS_BASE_URL`.                                            |
| EKS (`kubectl`) | Resolve only     | A JIRA `components` or `labels` entry that looks like a valid Kubernetes name.                   |


If an integration isn't configured in `.env`, watcher simply skips its
probes — no warnings, no failures. If a probe fails (host mismatch,
network error, 404), the result is passed through to the LLM as *no
signal* rather than as evidence.

---



## License

Released under the **MIT License** — see [LICENSE](LICENSE) for the full
text.

In plain English:

- ✅ Free for personal and commercial use.
- ✅ Fork, modify, and redistribute.
- ✅ Bundle it inside larger closed-source products.
- ✅ Sell products built on top of it.
- ⚠️ Ship a copy of the license and the copyright notice along with any
redistribution.
- ❌ No warranty, no liability. Use at your own risk.

MIT is the most permissive of the widely-used open-source licenses and
what tools like [jq](https://github.com/jqlang/jq),
[curl](https://github.com/curl/curl), [Rich](https://github.com/Textualize/rich),
and [FastAPI](https://github.com/fastapi/fastapi) ship under. The
copyright stays with the author; anyone else is free to use it.

---

Built for engineers who'd rather stare at a terminal than a JIRA board.