"""CLI interface for Shopify link checker."""

import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .job_manager import JobManager
from .models import JobConfig, ProductStatus

app = typer.Typer(
    name="shopify-link-checker",
    help="Production-ready Shopify product metafield link checker",
    add_completion=False,
)

console = Console()


def parse_metafield_handle(handle: str) -> tuple[str, str]:
    """Parse namespace.key format into namespace and key."""
    parts = handle.split(".", 1)
    if len(parts) != 2:
        raise ValueError("Metafield handle must be in format: namespace.key")
    return parts[0], parts[1]


@app.command()
def check(
    shop: str = typer.Option(..., "--shop", help="Shop domain (e.g., my-shop.myshopify.com)"),
    token: str = typer.Option(..., "--token", help="Admin API access token", envvar="SHOPIFY_TOKEN"),
    namespace: Optional[str] = typer.Option(None, "--namespace", help="Metafield namespace"),
    key: Optional[str] = typer.Option(None, "--key", help="Metafield key"),
    metafield: Optional[str] = typer.Option(
        None, "--metafield", help="Metafield in format: namespace.key"
    ),
    status: ProductStatus = typer.Option(
        ProductStatus.ACTIVE, "--status", help="Product status filter"
    ),
    collection_ids: Optional[str] = typer.Option(
        None, "--collection-ids", help="Comma-separated collection IDs"
    ),
    batch_size: int = typer.Option(250, "--batch-size", min=1, max=250, help="Batch size (max 250)"),
    concurrency: int = typer.Option(20, "--concurrency", min=1, max=100, help="Concurrent link checks"),
    timeout_ms: int = typer.Option(
        8000, "--timeout-ms", min=1000, max=60000, help="Timeout in milliseconds"
    ),
    follow_redirects: bool = typer.Option(True, "--follow-redirects/--no-follow-redirects", help="Follow HTTP redirects"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run mode (don't draft products)"),
    resume_token: Optional[str] = typer.Option(None, "--resume-token", help="Resume token"),
    api_version: str = typer.Option("2024-10", "--api-version", help="Shopify API version"),
    out: str = typer.Option(
        None, "--out", help="Output CSV file path (default: link_check_<timestamp>.csv)"
    ),
):
    """
    Check product metafield URLs and draft products with broken links.

    Example:
        shopify-link-checker check \\
            --shop my-shop.myshopify.com \\
            --token shpat_xxx \\
            --metafield custom.video_url \\
            --status active
    """
    # Parse metafield
    if metafield:
        ns, k = parse_metafield_handle(metafield)
        namespace = ns
        key = k
    elif not namespace or not key:
        console.print("[red]Error: Must provide either --metafield or both --namespace and --key[/red]")
        raise typer.Exit(1)

    # Parse collection IDs
    coll_ids = None
    if collection_ids:
        try:
            coll_ids = [int(cid.strip()) for cid in collection_ids.split(",")]
        except ValueError:
            console.print("[red]Error: Invalid collection IDs format[/red]")
            raise typer.Exit(1)

    # Create config
    config = JobConfig(
        shop=shop,
        token=token,
        namespace=namespace,
        key=key,
        status=status,
        collection_ids=coll_ids,
        batch_size=batch_size,
        concurrency=concurrency,
        timeout_ms=timeout_ms,
        follow_redirects=follow_redirects,
        dry_run=dry_run,
        resume_token=resume_token,
        api_version=api_version,
    )

    # Set output path
    if not out:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = f"link_check_{timestamp}.csv"

    # Run job
    console.print(f"\n[bold blue]Starting link check for {shop}[/bold blue]")
    console.print(f"Metafield: {namespace}.{key}")
    console.print(f"Status filter: {status.value}")
    if coll_ids:
        console.print(f"Collections: {', '.join(map(str, coll_ids))}")
    console.print(f"Batch size: {batch_size}")
    console.print(f"Dry run: {'Yes' if dry_run else 'No'}")
    console.print(f"Output: {out}\n")

    # Run the job
    try:
        asyncio.run(run_job_cli(config, out))
    except KeyboardInterrupt:
        console.print("\n[yellow]Job cancelled by user[/yellow]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise typer.Exit(1)


async def run_job_cli(config: JobConfig, output_path: str):
    """Run job with CLI progress display."""
    job_manager = JobManager()
    job_id = job_manager.create_job(config)

    # Setup progress display
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        TextColumn("{task.completed}/{task.total} products"),
        TimeElapsedColumn(),
        console=console,
    )

    # Create results table
    results_table = Table(show_header=True, header_style="bold magenta")
    results_table.add_column("Product ID", style="cyan")
    results_table.add_column("Title", style="white", max_width=30)
    results_table.add_column("URL", style="blue", max_width=40)
    results_table.add_column("Status", style="yellow")
    results_table.add_column("Action", style="green")

    task_id = None
    stats = {}

    # Write CSV header
    csv_file = open(output_path, "w", newline="")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow([
        "product_id",
        "product_title",
        "product_status",
        "metafield",
        "url",
        "http_status",
        "is_broken",
        "error",
        "action",
        "checked_at",
    ])

    try:
        with Live(console=console, refresh_per_second=4):
            async for update in job_manager.run_job(job_id):
                stats = update.get("stats", {})
                results = update.get("results", [])

                # Update progress
                if task_id is None and stats.get("total_products", 0) > 0:
                    task_id = progress.add_task(
                        "Checking links...",
                        total=stats["total_products"],
                    )

                if task_id is not None:
                    progress.update(
                        task_id,
                        completed=stats.get("processed", 0),
                        description=f"Batch {stats.get('batch_index', 0)}/{stats.get('total_batches', 0)}",
                    )

                # Add new results to table (show last 10)
                for result in results[-10:]:
                    results_table.add_row(
                        str(result["product_id"]),
                        result["product_title"][:30],
                        result["url"][:40] if result["url"] else "-",
                        "Broken" if result["is_broken"] else "OK",
                        result["action"],
                    )

                    # Write to CSV
                    csv_writer.writerow([
                        result["product_id"],
                        result["product_title"],
                        result["product_status"],
                        result["metafield"],
                        result["url"],
                        result.get("http_status", ""),
                        result["is_broken"],
                        result.get("error", ""),
                        result["action"],
                        result["checked_at"],
                    ])

        # Final summary
        console.print("\n[bold green]Job completed![/bold green]\n")
        console.print(f"Total products scanned: {stats.get('processed', 0)}")
        console.print(f"Products drafted: {stats.get('drafted_count', 0)}")
        console.print(f"Broken URLs found: {stats.get('broken_url_count', 0)}")
        console.print(f"Errors: {stats.get('errors_count', 0)}")
        console.print(f"\nResults saved to: {output_path}")

        # Show resume token if available
        resume_token = update.get("resume_token")
        if resume_token:
            console.print(f"\n[bold]Resume token:[/bold]\n{resume_token}")

    finally:
        csv_file.close()


@app.command()
def server(
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", help="Port to bind to"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
):
    """
    Start the web server.

    Example:
        shopify-link-checker server --port 8000
    """
    import uvicorn

    console.print(f"[bold blue]Starting Shopify Link Checker web server[/bold blue]")
    console.print(f"Server running at: http://{host}:{port}\n")

    uvicorn.run(
        "src.main:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
