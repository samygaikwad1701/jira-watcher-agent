from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime

from rich.align import Align
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .config import SUPPORTED_PROVIDERS, load_config
from .integrations import INTEGRATIONS, IntegrationStatus
from .integrations.bitbucket import from_env as bitbucket_from_env
from .integrations.eks import from_env as eks_from_env
from .integrations.github import from_env as github_from_env
from .integrations.jenkins import from_env as jenkins_from_env
from .investigator import (
    InvestigationResult,
    investigate,
    resolve_keys_for_category,
)
from .jira_client import JiraClient, Ticket
from .memory import Memory, load_memory
from .providers import ProviderError
from .summarizer import rule_based_digest, summarize

CHECK_TARGETS = ("all", "jira", "bitbucket", "jenkins", "github", "eks")


console = Console()


# Almond-eye motif — mirrors the project's real logo (a watching eye with a
# cyan iris). Rendered as a stack of coloured rows: dim outer almond, cyan
# iris ring, bright pupil in the middle.
_LOGO_ROWS = [
    ("bright_black", r"         ▄▄▓▓█████████████████▓▓▄▄         "),
    ("bright_black", r"      ▄▓█▀▀░░░░░░░░░░░░░░░░░░░░▀▀█▓▄      "),
    ("bright_black", r"   ▄██▀░░░░░░░░  ▄▄█████▄▄  ░░░░░░░░▀██▄   "),
    ("cyan", r" ▄█▀░░░░░░░░   ▄██▀░░░░░▀██▄   ░░░░░░░░▀█▄ "),
    ("cyan", r"██░░░░░░░░    ██▀  ▄███▄  ▀██    ░░░░░░░░██"),
    ("bright_cyan", r"█░░░░░░░░    ██   ██▓▓▓██   ██    ░░░░░░░░█"),
    ("cyan", r"██░░░░░░░░    ██▄  ▀███▀  ▄██    ░░░░░░░░██"),
    ("cyan", r" ▀█▄░░░░░░░░   ▀██▄░░░░░▄██▀   ░░░░░░░░▄█▀ "),
    ("bright_black", r"   ▀██▄░░░░░░░░  ▀▀█████▀▀  ░░░░░░░░▄██▀   "),
    ("bright_black", r"      ▀▓█▄▄░░░░░░░░░░░░░░░░░░░░▄▄█▓▀      "),
    ("bright_black", r"         ▀▀▓▓█████████████████▓▓▀▀         "),
]


def _integration_pills() -> str:
    """Show a check/dot per integration so users see at a glance what's wired."""
    pills = []
    labels = [
        ("Bitbucket", bitbucket_from_env),
        ("Jenkins", jenkins_from_env),
        ("GitHub", github_from_env),
        ("EKS", eks_from_env),
    ]
    for name, factory in labels:
        try:
            configured = factory() is not None
        except Exception:  # noqa: BLE001 — never fail the banner
            configured = False
        if configured:
            pills.append(f"[green]✓[/green] {name}")
        else:
            pills.append(f"[dim]· {name}[/dim]")
    return "   ".join(pills)


def print_banner(cfg=None) -> None:
    """Startup banner. Skipped when stdout isn't a TTY (piped, cron, CI)."""
    if not sys.stdout.isatty():
        return
    from . import __version__

    console.print()
    for style, row in _LOGO_ROWS:
        console.print(Align.center(Text(row, style=style)))
    console.print()

    # Two-tone title: "Jira Watcher" in silver-y white, "Agent" in cyan —
    # mirrors the project logo.
    title = Text()
    title.append("Jira Watcher Agent ", style="bold bright_cyan")
    console.print(Align.center(title))
    console.print(Align.center(f"[dim]v{__version__}[/dim]"))
    console.print(Align.center("[dim]AI-powered JIRA planner, organizer & triage CLI[/dim]"))
    console.print()

    provider = getattr(cfg, "llm_provider", None) if cfg else None
    if provider:
        console.print(Align.center(f"provider [cyan]{provider}[/cyan]     {_integration_pills()}"))
    else:
        console.print(Align.center(_integration_pills()))
    console.print()


def _render(cfg, tickets: list[Ticket], summary: str, memory: Memory) -> None:
    header = (
        f"[bold]watcher[/bold] · provider=[cyan]{cfg.llm_provider}[/cyan] · "
        f"{len(tickets)} ticket{'s' if len(tickets) != 1 else ''} · "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    console.print(Rule(header))
    console.print(Panel(Markdown(summary), border_style="cyan"))

    counts = memory.category_counts(t.key for t in tickets)
    if counts:
        table = Table(
            title="Category memory (this run)",
            show_edge=False,
            header_style="dim",
        )
        table.add_column("Category")
        table.add_column("Count", justify="right")
        for cat, n in sorted(counts.items(), key=lambda x: -x[1]):
            table.add_row(cat, str(n))
        console.print(table)


def _produce(cfg, tickets: list[Ticket], memory: Memory, use_llm: bool) -> str:
    if not use_llm:
        return rule_based_digest(tickets, memory)
    try:
        digest, _ = summarize(tickets, memory, cfg.llm_provider, cli_override=cfg.llm_cli_path)
        return digest
    except ProviderError as exc:
        console.print(f"[yellow]LLM ({cfg.llm_provider}) failed: {exc}[/yellow]")
        console.print("[yellow]Falling back to rule-based digest.[/yellow]")
        return rule_based_digest(tickets, memory)


def _fingerprint(tickets: list[Ticket]) -> tuple:
    return tuple(t.signature for t in tickets)


def run_once(
    cfg,
    client: JiraClient,
    memory: Memory,
    use_llm: bool,
    interactive: bool = True,
    kube_context_override: str | None = None,
) -> None:
    tickets = client.assigned_to_me(cfg.jira_jql)
    summary = _produce(cfg, tickets, memory, use_llm)
    _render(cfg, tickets, summary, memory)

    # Post-digest interactive resolve — reuses the already-fetched tickets
    # and in-memory Memory, so no extra JIRA calls happen for queue selection.
    if interactive and use_llm and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            _interactive_resolve_loop(cfg, tickets, memory, client, kube_context_override)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]interactive resolve cancelled.[/dim]")


def _interactive_resolve_loop(
    cfg,
    tickets: list[Ticket],
    memory: Memory,
    client: JiraClient,
    kube_context_override: str | None = None,
) -> None:
    """Ask the user whether to investigate tickets from the current queue.

    Reuses the tickets/memory already in hand — no requeries for queue
    selection. Only the per-ticket `get_issue()` call in the investigator
    hits JIRA again, and only for the specific keys the user picks.
    """
    console.print()
    if not Confirm.ask("[bold]Investigate any tickets from this list?[/bold]", default=False):
        return

    bb_client = bitbucket_from_env()
    gh_client = github_from_env()
    eks_client = eks_from_env()
    jenkins_client = jenkins_from_env()
    # Ask which kubectl context to use for read-only workload probes (once
    # per session). If the user skips or no contexts exist, we drop the EKS
    # client so probing is bypassed entirely — the digest still runs.
    if eks_client is not None:
        if kube_context_override:
            eks_client.set_kube_context(kube_context_override)
        if not _ensure_kube_context(eks_client, interactive=True):
            eks_client = None
    ticket_keys = {t.key for t in tickets}

    key_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9]+-\d+$")

    while True:
        counts = memory.category_counts(ticket_keys)
        if counts:
            shown = ", ".join(
                f"{cat} ({n})" for cat, n in sorted(counts.items(), key=lambda x: -x[1])
            )
            console.print(f"[dim]Available categories: {shown}[/dim]")

        raw = Prompt.ask(
            "[bold]Pick a category name or JIRA keys[/bold] "
            "(e.g. [cyan]bug[/cyan]  or  [cyan]PROJ-1, PROJ-2[/cyan])"
        ).strip()
        if not raw:
            if not Confirm.ask("Try another selection?", default=True):
                return
            continue

        # Auto-detect: any token matching KEY-123 → keys mode; else category.
        tokens = [t for t in re.split(r"[,\s]+", raw) if t]
        looks_like_keys = any(key_pattern.match(t) for t in tokens)

        if looks_like_keys:
            keys = [t.upper() for t in tokens if key_pattern.match(t)]
            invalid = [t for t in tokens if not key_pattern.match(t)]
            if invalid:
                console.print(f"[yellow]Ignoring non-key tokens: {', '.join(invalid)}[/yellow]")
            if not keys:
                console.print("[yellow]No valid keys entered.[/yellow]")
                if not Confirm.ask("Try another selection?", default=True):
                    return
                continue
            unknown = [k for k in keys if k not in ticket_keys]
            if unknown:
                console.print(
                    f"[dim]Note: {', '.join(unknown)} not in the current "
                    f"queue — will still be fetched by key.[/dim]"
                )
        else:
            category = raw.lower()
            if counts and category not in counts:
                console.print(
                    f"[yellow]'{category}' isn't a category in this queue. "
                    f"Try one of: {', '.join(sorted(counts))}.[/yellow]"
                )
                if not Confirm.ask("Try another selection?", default=True):
                    return
                continue
            limit = IntPrompt.ask("Limit", default=3)
            keys = resolve_keys_for_category(memory, tickets, category, max(1, limit))
            if not keys:
                console.print(
                    f"[yellow]No tickets in the current queue are tagged '{category}'.[/yellow]"
                )
                if not Confirm.ask("Try another selection?", default=True):
                    return
                continue

        console.print(
            f"[dim]Investigating {len(keys)} ticket(s): "
            f"{', '.join(keys)} · provider={cfg.llm_provider}[/dim]"
        )
        try:
            results = investigate(
                keys=keys,
                jira=client,
                memory=memory,
                provider=cfg.llm_provider,
                cli_override=cfg.llm_cli_path,
                bb_client=bb_client,
                gh_client=gh_client,
                eks_client=eks_client,
                jenkins_client=jenkins_client,
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Investigation failed: {exc}[/red]")
        else:
            _render_investigation(results)

        if not Confirm.ask("[bold]Investigate more from this list?[/bold]", default=False):
            break


def run_watch(cfg, client: JiraClient, memory: Memory, use_llm: bool, interval: int) -> None:
    console.print(
        f"[dim]Watch mode — polling every {interval}s, provider={cfg.llm_provider}. "
        f"Ctrl+C to stop.[/dim]"
    )
    last_seen: tuple = ()
    while True:
        try:
            tickets = client.assigned_to_me(cfg.jira_jql)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Fetch failed: {exc}[/red]")
            time.sleep(interval)
            continue

        fp = _fingerprint(tickets)
        if fp != last_seen:
            summary = _produce(cfg, tickets, memory, use_llm)
            _render(cfg, tickets, summary, memory)
            last_seen = fp
        else:
            console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')} — no changes[/dim]")
        time.sleep(interval)


def _render_check(statuses: list[IntegrationStatus]) -> None:
    table = Table(title="Connectivity check", show_lines=False)
    table.add_column("Integration", style="bold")
    table.add_column("Status")
    table.add_column("Identity")
    table.add_column("Detail / error")

    for s in statuses:
        badge = "[green]OK[/green]" if s.ok else "[red]FAIL[/red]"
        detail = ""
        if s.ok:
            bits = [f"{k}={v}" for k, v in s.details.items() if v not in (None, "")]
            detail = " · ".join(bits)
        else:
            detail = s.error or ""
            if s.hint:
                detail += f"\n[dim]hint: {s.hint}[/dim]"
        table.add_row(s.name, badge, s.identity or "-", detail or "-")

    console.print(table)


def _check_jira(cfg) -> IntegrationStatus:
    try:
        jc = JiraClient(
            base_url=cfg.jira_base_url,
            flavor=cfg.jira_flavor,
            api_token=cfg.jira_api_token,
            email=cfg.jira_email,
            cloud_id=cfg.jira_cloud_id,
            user_agent=cfg.jira_user_agent,
        )
        info = jc.whoami()
    except Exception as exc:  # noqa: BLE001
        return IntegrationStatus(name="jira", ok=False, error=str(exc)[:400])
    return IntegrationStatus(
        name="jira",
        ok=True,
        identity=str(
            info.get("displayName") or info.get("emailAddress") or info.get("name") or "?"
        ),
        details={
            "flavor": cfg.jira_flavor,
            "site": cfg.jira_base_url,
            "cloud_id": jc.cloud_id or "-",
        },
    )


def run_check(cfg, target: str) -> int:
    target = target or "all"
    names = [target] if target != "all" else ["jira", "bitbucket", "jenkins", "github", "eks"]
    statuses: list[IntegrationStatus] = []
    for name in names:
        if name == "jira":
            statuses.append(_check_jira(cfg))
            continue
        factory = INTEGRATIONS[name]
        client = factory()
        if client is None:
            statuses.append(IntegrationStatus.skipped(name, "no env vars set"))
            continue
        statuses.append(client.verify())
    _render_check(statuses)
    return 0 if all(s.ok for s in statuses) else 1


def _ensure_kube_context(eks_client, interactive: bool) -> bool:
    """Pick the kubectl context to use for read-only workload probes.

    Returns True if the client is ready to probe (context set or user opted
    to skip probes but keep the digest going), False if the caller should
    proceed without EKS entirely.
    """
    if eks_client is None:
        return False
    # Already explicit (KUBE_CONTEXT env or previously chosen this session).
    if eks_client.kube_context:
        console.print(
            f"[dim]EKS probes use context [cyan]{eks_client.kube_context}[/cyan] "
            f"(read-only: kubectl get only).[/dim]"
        )
        return True

    contexts = eks_client.list_contexts()
    current = eks_client.current_context()

    if not contexts:
        console.print(
            "[yellow]No kubectl contexts found — skipping EKS probes for this session. "
            "Run `watcher --eks-login` to add one.[/yellow]"
        )
        return False

    if not interactive:
        # Non-interactive: fall back to current context if there is one; skip otherwise.
        if current:
            eks_client.kube_context = current
            console.print(
                f"[dim]EKS probes use current context [cyan]{current}[/cyan] "
                f"(read-only). Override with --kube-context or KUBE_CONTEXT.[/dim]"
            )
            return True
        console.print(
            "[yellow]No current kubectl context and running non-interactively — "
            "skipping EKS probes. Pass --kube-context or set KUBE_CONTEXT.[/yellow]"
        )
        return False

    # Interactive picker.
    console.print(
        "\n[bold]Available kubectl contexts[/bold]  [dim](read-only probes will use the one you pick)[/dim]"
    )
    table = Table(show_edge=False, header_style="dim")
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Context")
    table.add_column("Note")
    numbered = list(contexts)
    for i, name in enumerate(numbered, start=1):
        marker = "current" if name == current else ""
        table.add_row(str(i), name, marker)
    console.print(table)
    console.print(
        "[dim]Type a number, a full context name, or `skip` to run without EKS probes.[/dim]"
    )
    default = str(numbered.index(current) + 1) if current in numbered else "1"
    raw = Prompt.ask("Use kube context", default=default).strip()

    if raw.lower() in {"skip", "s", "none", ""}:
        console.print("[dim]Skipping EKS probes for this session.[/dim]")
        return False
    chosen: Optional[str] = None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(numbered):
            chosen = numbered[idx]
    elif raw in numbered:
        chosen = raw
    if not chosen:
        console.print(f"[yellow]'{raw}' isn't a listed context — skipping EKS probes.[/yellow]")
        return False

    eks_client.set_kube_context(chosen)
    console.print(
        f"[green]Using kube context[/green] [cyan]{chosen}[/cyan] "
        f"[dim](read-only: kubectl get only — nothing will be modified).[/dim]"
    )
    return True


_VERDICT_STYLES = {
    "REAL": "red",
    "LIKELY_REAL": "yellow",
    "NEEDS_INFO": "cyan",
    "LIKELY_INVALID": "magenta",
    "INVALID": "green",
    "": "dim",
}


def _render_investigation(results: list[InvestigationResult]) -> None:
    for r in results:
        # Ticket key is a clickable OSC 8 hyperlink in supporting terminals.
        title = (
            f"[bold][link={r.url}]{r.key}[/link][/bold] · {r.summary}"
            if r.url
            else f"[bold]{r.key}[/bold] · {r.summary}"
        )
        console.print(Rule(title))
        if r.error:
            console.print(f"[red]{r.error}[/red]")
            continue
        for w in r.warnings:
            console.print(f"[yellow]! {w}[/yellow]")
        style = _VERDICT_STYLES.get(r.verdict_line, "dim")
        # Verdict is itself a clickable link — click to open the ticket.
        if r.verdict_line and r.url:
            console.print(
                f"Verdict: [{style} bold][link={r.url}]{r.verdict_line}[/link][/{style} bold]  "
                f"[dim]{r.url}[/dim]"
            )
        elif r.verdict_line:
            console.print(f"Verdict: [{style} bold]{r.verdict_line}[/{style} bold]")
        else:
            console.print(f"[dim]{r.url}[/dim]")
        console.print(Panel(Markdown(r.body), border_style=style))


def run_resolve(cfg, args) -> int:
    if not (args.keys or args.category):
        console.print("[red]--resolve needs either --keys PROJ-1,PROJ-2 or --category NAME.[/red]")
        return 2

    client = JiraClient(
        base_url=cfg.jira_base_url,
        flavor=cfg.jira_flavor,
        api_token=cfg.jira_api_token,
        email=cfg.jira_email,
        cloud_id=cfg.jira_cloud_id,
        user_agent=cfg.jira_user_agent,
        debug=args.debug,
    )
    memory = load_memory(cfg.memory_path)

    if args.keys:
        # --limit is intentionally ignored when keys are explicit: the caller
        # already chose exactly which tickets they want investigated.
        keys = [k.strip().upper() for k in args.keys.split(",") if k.strip()]
    else:
        console.print(f"[dim]Loading currently-assigned tickets tagged '{args.category}'…[/dim]")
        try:
            tickets = client.assigned_to_me(cfg.jira_jql)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Fetch failed: {exc}[/red]")
            return 1
        keys = resolve_keys_for_category(memory, tickets, args.category, args.limit)
        if not keys:
            console.print(
                f"[yellow]No currently-assigned tickets have category "
                f"'{args.category}' in memory. Run `watcher` first to populate "
                f"memory.[/yellow]"
            )
            return 1
        keys = keys[: max(1, args.limit)]

    console.print(
        f"[dim]Investigating {len(keys)} ticket(s): {', '.join(keys)} · "
        f"provider={cfg.llm_provider}[/dim]"
    )

    bb_client = bitbucket_from_env()
    gh_client = github_from_env()
    eks_client = eks_from_env()
    jenkins_client = jenkins_from_env()
    if eks_client is not None:
        # CLI flag beats env var beats current context.
        if getattr(args, "kube_context", None):
            eks_client.set_kube_context(args.kube_context)
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        if not _ensure_kube_context(eks_client, interactive=interactive):
            eks_client = None

    try:
        results = investigate(
            keys=keys,
            jira=client,
            memory=memory,
            provider=cfg.llm_provider,
            cli_override=cfg.llm_cli_path,
            bb_client=bb_client,
            gh_client=gh_client,
            eks_client=eks_client,
            jenkins_client=jenkins_client,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Investigation failed: {exc}[/red]")
        return 1

    _render_investigation(results)
    return 0 if all(r.error is None for r in results) else 1


def run_eks_login() -> int:
    client = eks_from_env()
    if client is None:
        console.print("[red]EKS not configured — set EKS_CLUSTER_NAME and AWS_REGION.[/red]")
        return 2
    status = client.update_kubeconfig()
    if status.ok:
        console.print(
            f"[green]kubeconfig updated[/green] · "
            f"cluster={status.details.get('cluster')} region={status.details.get('region')}"
        )
        msg = status.details.get("message")
        if msg:
            console.print(f"[dim]{msg}[/dim]")
        return 0
    console.print(f"[red]{status.error}[/red]")
    return 1


def build_parser() -> argparse.ArgumentParser:
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="watcher",
        description="Scan JIRA tickets assigned to you and print an LLM-generated digest.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"jira-watcher-agent {__version__}",
    )
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default=None,
        help="LLM CLI to use (default: from LLM_PROVIDER env, else 'claude').",
    )
    parser.add_argument(
        "--cli-path",
        default=None,
        help="Override the binary invoked for the selected provider.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Re-scan on an interval and print updates when tickets change.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Polling interval in seconds for --watch (default: WATCHER_INTERVAL or 300).",
    )
    parser.add_argument(
        "--jql",
        default=None,
        help="Override the JQL query used to fetch tickets.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the LLM and use the rule-based digest (still uses cached categories).",
    )
    parser.add_argument(
        "--forget",
        action="store_true",
        help="Wipe the category memory before running.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print JIRA request/response diagnostics.",
    )
    parser.add_argument(
        "--whoami",
        action="store_true",
        help="Print the JIRA account the token authenticates as, then exit.",
    )
    parser.add_argument(
        "--check",
        nargs="?",
        const="all",
        choices=CHECK_TARGETS,
        default=None,
        metavar="TARGET",
        help=(
            "Verify connectivity to integrations and exit. "
            "Targets: all, jira, bitbucket, jenkins, github, eks. "
            "With no value, checks 'all'."
        ),
    )
    parser.add_argument(
        "--eks-login",
        action="store_true",
        help="Write a kubeconfig entry for EKS_CLUSTER_NAME via `aws eks update-kubeconfig`, then exit.",
    )
    parser.add_argument(
        "--resolve",
        action="store_true",
        help=(
            "Investigate specific tickets: pull full context (comments, remote "
            "links, linked PRs) and ask the LLM whether the reported problem "
            "is real plus concrete resolution steps. Use with --keys or "
            "--category."
        ),
    )
    parser.add_argument(
        "--keys",
        default=None,
        help="Comma-separated JIRA issue keys to investigate (e.g. PROJ-1,PROJ-2).",
    )
    parser.add_argument(
        "--category",
        default=None,
        help=(
            "Investigate currently-assigned tickets whose cached category "
            "matches (e.g. 'bug'). Requires a prior digest run so memory is "
            "populated."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Cap the number of tickets investigated per run (default: 3).",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help=(
            "Skip the post-digest 'investigate any tickets?' prompt. Also "
            "implied when stdin/stdout aren't a TTY."
        ),
    )
    parser.add_argument(
        "--kube-context",
        default=None,
        help=(
            "kubectl context to use for read-only EKS probes during "
            "investigation. Overrides KUBE_CONTEXT env; skips the interactive "
            "picker."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        cfg = load_config()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    overrides = {}
    if args.provider:
        overrides["llm_provider"] = args.provider
    if args.cli_path:
        overrides["llm_cli_path"] = args.cli_path
    if args.jql:
        overrides["jira_jql"] = args.jql
    if overrides:
        cfg = cfg.with_overrides(**overrides)

    # Diagnostic commands stay noise-free.
    diagnostic = args.check is not None or args.whoami or args.eks_login
    if not diagnostic:
        print_banner(cfg)

    if args.check is not None:
        return run_check(cfg, args.check)

    if args.eks_login:
        return run_eks_login()

    if args.resolve:
        return run_resolve(cfg, args)

    memory = load_memory(cfg.memory_path)
    if args.forget:
        memory.tickets.clear()
        memory.save()
        console.print(f"[dim]Cleared memory at {cfg.memory_path}[/dim]")

    client = JiraClient(
        base_url=cfg.jira_base_url,
        flavor=cfg.jira_flavor,
        api_token=cfg.jira_api_token,
        email=cfg.jira_email,
        cloud_id=cfg.jira_cloud_id,
        user_agent=cfg.jira_user_agent,
        debug=args.debug,
    )
    use_llm = not args.no_llm

    try:
        if args.whoami:
            info = client.whoami()
            ident = info.get("accountId") or info.get("key") or info.get("name") or "?"
            console.print(
                f"[green]Authenticated as[/green] "
                f"{info.get('displayName')} <{info.get('emailAddress') or ''}> "
                f"id={ident} · flavor={cfg.jira_flavor}"
            )
            return 0
        if args.watch:
            interval = args.interval or cfg.watch_interval_seconds
            run_watch(cfg, client, memory, use_llm, interval)
        else:
            console.print(f"[dim]JQL: {cfg.jira_jql}[/dim]")
            run_once(
                cfg,
                client,
                memory,
                use_llm,
                interactive=not args.no_interactive,
                kube_context_override=args.kube_context,
            )
    except KeyboardInterrupt:
        console.print("\n[dim]stopped.[/dim]")
        return 0
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Error: {exc}[/red]")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
