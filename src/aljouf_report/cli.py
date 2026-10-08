"""Command line entry points.

    python -m aljouf_report --help
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import PROJECT_ROOT, load_config
from .logging_setup import setup_logging
from .models import SENTIMENT_AR, utcnow
from .pipeline import Pipeline

app = typer.Typer(
    add_completion=False,
    help="Weekly social media reporting pipeline for the Jouf Strategic Development Office.",
)
console = Console()


def _boot():
    config = load_config()
    setup_logging(config.secrets.log_level)
    return config


@app.command()
def run(
    skip_followers: bool = typer.Option(False, help="Skip follower collection."),
    skip_news: bool = typer.Option(False, help="Skip news and trend collection."),
    skip_comments: bool = typer.Option(False, help="Skip comment collection."),
    skip_sentiment: bool = typer.Option(False, help="Skip LLM sentiment analysis."),
    skip_sheets: bool = typer.Option(False, help="Skip the Google Sheets mirror."),
    skip_email: bool = typer.Option(False, help="Skip the weekly email."),
):
    """Run the full weekly pipeline: collect, store, analyse, notify."""
    config = _boot()
    summary = Pipeline(config).run(
        skip_followers=skip_followers,
        skip_news=skip_news,
        skip_comments=skip_comments,
        skip_sentiment=skip_sentiment,
        skip_sheets=skip_sheets,
        skip_email=skip_email,
    )

    table = Table(title=f"Run {summary.run_id}", show_header=False)
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("Followers collected", f"{summary.followers_collected}")
    table.add_row("Followers unavailable", f"{summary.followers_failed}")
    table.add_row("News articles", f"{summary.news_collected}")
    table.add_row("Comments collected", f"{summary.comments_collected}")
    table.add_row("Comments analysed", f"{summary.comments_analyzed}")
    for label, count in summary.sentiment_counts.items():
        table.add_row(f"  {SENTIMENT_AR.get(label, label)} ({label})", f"{count}")
    table.add_row("Warnings", f"{len(summary.errors)}")
    table.add_row("Duration", f"{summary.duration_secs:.1f}s")
    console.print(table)

    if summary.errors:
        console.print("\n[yellow]Warnings:[/yellow]")
        for error in summary.errors[:20]:
            console.print(f"  • {error}")

    # Exit 0 even with warnings: a partial report is a successful weekly run.
    raise typer.Exit(0)


@app.command("init-db")
def init_db():
    """Create the SQLite database and schema without collecting anything."""
    config = _boot()
    Pipeline(config)  # Database() applies the schema on construction.
    console.print(f"[green]Database ready:[/green] {config.db_path}")


@app.command("seed-demo")
def seed_demo(
    weeks: int = typer.Option(8, help="How many weekly runs of history to generate."),
    reset: bool = typer.Option(False, help="Delete the existing database first."),
):
    """Fill the database with realistic synthetic data (no API keys needed)."""
    from . import demo

    config = _boot()
    if reset and config.db_path.exists():
        config.db_path.unlink()
        for suffix in ("-wal", "-shm"):
            extra = Path(str(config.db_path) + suffix)
            if extra.exists():
                extra.unlink()
        console.print("[yellow]Existing database removed.[/yellow]")

    run_id = demo.seed(config, weeks=weeks)
    console.print(
        f"[green]Seeded {weeks} weekly runs[/green] (latest run id {run_id}).\n"
        f"Launch the dashboard with: [bold]python -m aljouf_report dashboard[/bold]"
    )


def _port_is_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        # connect_ex succeeds only when something is already listening.
        return sock.connect_ex((host if host != "0.0.0.0" else "127.0.0.1", port)) != 0


@app.command()
def dashboard(
    port: int = typer.Option(8501, help="Port to serve the dashboard on."),
    host: str = typer.Option("localhost", help="Address to bind."),
    strict_port: bool = typer.Option(
        False, "--strict-port", help="Fail instead of moving to the next free port."
    ),
):
    """Launch the Streamlit dashboard."""
    config = _boot()

    if not config.db_path.exists():
        console.print(
            f"[yellow]No database at[/yellow] {config.db_path}\n"
            "The dashboard will open but have nothing to show. Populate it first:\n"
            "  [bold]python -m aljouf_report run[/bold]        (live data)\n"
            "  [bold]python -m aljouf_report seed-demo[/bold]  (synthetic data)"
        )

    # A stale dashboard left running on this port is the most common cause of a
    # blank page: Streamlit cannot bind, so the browser has no server to talk to.
    requested = port
    if not _port_is_free(host, port):
        if strict_port:
            console.print(
                f"[red]Port {port} is already in use.[/red] "
                f"Stop the process using it, or pass --port with another number."
            )
            raise typer.Exit(1)
        for candidate in range(port + 1, port + 21):
            if _port_is_free(host, candidate):
                port = candidate
                break
        else:
            console.print(f"[red]No free port found in {requested}-{requested + 20}.[/red]")
            raise typer.Exit(1)
        console.print(
            f"[yellow]Port {requested} is already in use[/yellow] "
            f"(another dashboard is probably still running). Using {port} instead."
        )

    url = f"http://{host}:{port}"
    console.print(f"[cyan]Starting dashboard:[/cyan] [bold]{url}[/bold]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    app_path = PROJECT_ROOT / "dashboard" / "app.py"
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(app_path),
                "--server.port",
                str(port),
                "--server.address",
                host,
            ],
            check=False,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/dim]")


@app.command("send-email")
def send_email(
    preview: bool = typer.Option(
        False, "--preview", help="Write the HTML to a file instead of sending."
    ),
):
    """Re-send (or preview) the email for the most recent completed run."""
    from .models import RunSummary, utcnow
    from .notify import WeeklyMailer

    config = _boot()
    pipeline = Pipeline(config)

    run_id = pipeline.db.latest_run_id()
    if run_id is None:
        console.print("[red]No completed runs found. Run the pipeline first.[/red]")
        raise typer.Exit(1)

    window = config.settings.windows.comments_lookback_days
    scored = pipeline.db.scored_comments(window)
    summary = RunSummary(
        run_id=run_id,
        started_at=utcnow(),
        finished_at=utcnow(),
        followers_collected=sum(
            1 for r in pipeline.db.follower_history(1) if r["followers"] is not None
        ),
        news_collected=len(pipeline.db.recent_news(config.settings.windows.news_lookback_days)),
        comments_collected=len(scored),
        comments_analyzed=sum(1 for r in scored if r.get("label")),
        sentiment_counts=pipeline.db.sentiment_counts(window),
    )

    mailer = WeeklyMailer(config)
    html_body = mailer.render_html(
        summary,
        owned_followers=pipeline.owned_follower_deltas(),
        top_negative=pipeline.top_negative_comments(),
        news_count_by_keyword=pipeline.news_counts_by_keyword(),
    )

    if preview:
        out = PROJECT_ROOT / "logs" / "email_preview.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html_body, encoding="utf-8")
        console.print(f"[green]Preview written:[/green] {out}")
        raise typer.Exit(0)

    if not mailer.enabled:
        console.print(
            "[red]SMTP is not configured.[/red] Set SMTP_HOST, SMTP_USERNAME and "
            "SMTP_PASSWORD in .env, or use --preview."
        )
        raise typer.Exit(1)

    recipients = mailer.send(summary, html_body)
    console.print(f"[green]Email sent to:[/green] {', '.join(recipients)}")


@app.command("test-scrape")
def test_scrape(
    platform: str = typer.Option(
        "instagram", help="Platform to probe: instagram, x, or youtube."
    ),
    entity: str = typer.Option("jsdo", help="Entity key from config/accounts.yaml."),
    save: bool = typer.Option(False, "--save", help="Also store what was found."),
    analyze: bool = typer.Option(
        False, "--analyze", help="Run sentiment on the results (needs OPENAI_API_KEY)."
    ),
):
    """Scrape comments for a single account, to validate credentials cheaply.

    A full run starts many Apify actors. This touches one platform for one
    account so you can confirm the token and actor work before spending
    credits on everything.
    """
    from .collectors import ApifyRunner, CommentCollector

    config = _boot()

    if not config.secrets.apify_token:
        console.print(
            "[red]APIFY_TOKEN is not set.[/red]\n\n"
            "1. Sign up at [bold]https://console.apify.com[/bold]\n"
            "2. Copy your token from Settings -> Integrations\n"
            "3. Put it in [bold].env[/bold] as [bold]APIFY_TOKEN=apify_api_...[/bold]"
        )
        raise typer.Exit(1)

    target = config.accounts.by_key(entity)
    if target is None:
        available = ", ".join(e.key for e in config.accounts.entities)
        console.print(f"[red]Unknown entity '{entity}'.[/red] Available: {available}")
        raise typer.Exit(1)

    account = target.account(platform)
    if account is None:
        have = ", ".join(target.platforms.keys()) or "none"
        console.print(
            f"[red]{target.name_en} has no {platform} account configured.[/red] "
            f"It has: {have}"
        )
        raise typer.Exit(1)

    console.print(
        f"[cyan]Probing[/cyan] {target.name_en} · {platform} · @{account.handle}\n"
        f"[dim]{account.url}[/dim]\n"
    )

    runner = ApifyRunner(config.secrets.apify_token, config.settings.apify)
    collector = CommentCollector(config, runner)
    comments = collector._collect_for(target, account, platform)

    if collector.errors:
        console.print("[yellow]Problems reported by the collector:[/yellow]")
        for error in collector.errors:
            console.print(f"  • {error}")
        console.print()

    if not comments:
        console.print(
            "[yellow]No comments returned.[/yellow] Common causes:\n"
            f"  • No posts in the last "
            f"{config.settings.windows.comments_lookback_days} days "
            f"(widen comments_lookback_days in config/settings.yaml)\n"
            "  • The posts genuinely have no comments\n"
            "  • The actor needs a paid Apify plan, or its ID changed\n"
            f"    (current: {config.settings.apify.actors.get(f'{platform}_comments', 'n/a')})"
        )
        raise typer.Exit(0)

    console.print(f"[green]Found {len(comments)} comment(s).[/green]\n")
    table = Table(show_lines=True)
    table.add_column("When", style="dim", no_wrap=True)
    table.add_column("Author", style="cyan")
    table.add_column("Comment")
    for comment in comments[:10]:
        when = (
            comment.published_at.strftime("%Y-%m-%d %H:%M")
            if comment.published_at
            else "unknown"
        )
        table.add_row(when, comment.author or "—", comment.text[:160])
    console.print(table)
    if len(comments) > 10:
        console.print(f"[dim]… and {len(comments) - 10} more[/dim]")

    if save or analyze:
        pipeline = Pipeline(config)
        run_id = pipeline.db.start_run(utcnow()) if save else None
        if run_id is not None:
            pipeline.db.save_comments(run_id, comments)
            pipeline.db.finish_run(run_id, utcnow(), "completed", notes="test-scrape")
            console.print(f"\n[green]Saved to run {run_id}.[/green]")

        if analyze:
            if not config.secrets.openai_api_key:
                console.print(
                    "\n[yellow]Skipping sentiment: OPENAI_API_KEY is not set.[/yellow]"
                )
            else:
                from .analysis import SentimentAnalyzer

                payload = [
                    {"comment_hash": c.comment_hash, "text": c.text} for c in comments
                ]
                results = SentimentAnalyzer(config).analyze(payload)
                pipeline.db.save_sentiments(results)
                console.print(f"\n[green]Analysed {len(results)} comment(s).[/green]")
                stable = Table()
                stable.add_column("Label")
                stable.add_column("Reason (Arabic)")
                stable.add_column("Comment")
                by_hash = {c.comment_hash: c for c in comments}
                for result in results[:10]:
                    stable.add_row(
                        result.label,
                        result.reason_ar,
                        (by_hash[result.comment_hash].text[:90]),
                    )
                console.print(stable)


@app.command()
def doctor():
    """Report which integrations are configured and which are missing."""
    config = _boot()
    secrets = config.secrets

    table = Table(title="Configuration check")
    table.add_column("Component", style="bold")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    def row(name: str, ok: bool, detail: str):
        table.add_row(name, "[green]ready[/green]" if ok else "[yellow]missing[/yellow]", detail)

    row(
        "Apify (followers, comments)",
        bool(secrets.apify_token),
        "APIFY_TOKEN" if not secrets.apify_token else "token present",
    )
    row(
        "OpenAI (sentiment)",
        bool(secrets.openai_api_key),
        f"model: {secrets.openai_model}" if secrets.openai_api_key else "OPENAI_API_KEY",
    )
    row("News (Google News + GDELT)", True, "keyless public endpoints")
    row(
        "Google Sheets mirror",
        secrets.sheets_ready,
        "GOOGLE_SHEETS_ID + service account"
        if not secrets.sheets_ready
        else f"sheet {secrets.google_sheets_id}",
    )
    row(
        "SMTP email",
        secrets.smtp_ready,
        "SMTP_HOST/USERNAME/PASSWORD" if not secrets.smtp_ready else secrets.smtp_host or "",
    )
    row("SQLite", True, str(config.db_path))

    console.print(table)
    console.print(f"\nRecipients: {', '.join(config.recipients) or '(none configured)'}")
    console.print(f"Dashboard URL: {config.dashboard_url}")
    console.print(
        f"Entities: {len(config.accounts.entities)} "
        f"({len(config.accounts.owned)} owned, {len(config.accounts.competitors)} benchmarked)"
    )


def main():
    app()


if __name__ == "__main__":
    main()
