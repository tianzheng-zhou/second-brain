"""
cli.py — Command-line interface for PersonalBrain.
Usage: python -m personal_brain.cli <command>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click


@click.group()
def cli():
    """PersonalBrain CLI — personal knowledge base management."""


@cli.command()
def init():
    """Initialize database and storage directories."""
    from . import database as db
    db.init_db()
    click.echo("Database initialized.")


@cli.command()
def reset():
    """Delete and recreate the database (with confirmation)."""
    click.confirm(
        "This will DELETE all data. Are you sure?",
        abort=True,
    )
    click.confirm("Are you really sure? This cannot be undone.", abort=True)
    from . import database as db
    db.reset_db()
    click.echo("Database reset complete.")


@cli.command()
@click.argument("path")
def ingest(path: str):
    """Ingest a file or directory."""
    from . import database as db
    from .ingestion import process_directory, process_file

    db.init_db()
    p = Path(path)
    if not p.exists():
        click.echo(f"Error: path not found: {path}", err=True)
        sys.exit(1)

    if p.is_dir():
        result = process_directory(p)
        click.echo(
            f"Directory ingest complete: "
            f"{result['success']} success, {result['skip']} skip, {result['fail']} fail"
        )
        if result["failures"]:
            for f in result["failures"]:
                click.echo(f"  FAIL: {f['path']} — {f['error']}", err=True)
    else:
        try:
            result = process_file(p)
            click.echo(json.dumps(result, indent=2))
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=5, show_default=True, help="Number of results")
@click.option(
    "--mode", "-m",
    default="hybrid",
    type=click.Choice(["hybrid", "semantic", "keyword", "notes"]),
    show_default=True,
    help="Search mode",
)
def search(query: str, limit: int, mode: str):
    """Search the knowledge base."""
    from . import database as db
    from .search import (
        search_hybrid, search_keyword, search_notes, search_semantic,
    )

    db.init_db()

    fn = {
        "hybrid": search_hybrid,
        "semantic": search_semantic,
        "keyword": search_keyword,
        "notes": search_notes,
    }[mode]

    results = fn(query, limit)
    if not results:
        click.echo("No results found.")
        return

    for i, r in enumerate(results, 1):
        click.echo(f"\n[{i}] score={r.score:.4f} type={r.source_type}")
        if r.source_filename:
            click.echo(f"    File: {r.source_filename} (chunk {r.chunk_index})")
        if r.entry_id:
            click.echo(f"    Entry: {r.entry_id}")
        click.echo(f"    {r.content[:200]}{'...' if len(r.content) > 200 else ''}")


@cli.command()
@click.option("--transport", default="stdio", type=click.Choice(["stdio", "sse", "http"]))
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8765, show_default=True)
def serve(transport: str, host: str, port: int):
    """Start the MCP server."""
    from .mcp_server import run_server
    run_server(transport, host, port)


def main():
    cli()


if __name__ == "__main__":
    main()
