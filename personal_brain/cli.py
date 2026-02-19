import click
from personal_brain.core.database import init_db
from personal_brain.config import ensure_dirs, STORAGE_PATH
from personal_brain.core.ingestion import ingest_path
from personal_brain.core.search import search_files

@click.group()
def cli():
    """PersonalBrain CLI - Your second brain."""
    pass

def _do_init():
    """Shared initialization logic."""
    ensure_dirs()
    init_db()
    click.echo(f"Initialized PersonalBrain at {STORAGE_PATH}")

@cli.command()
def init():
    """Initialize the database and storage directories."""
    _do_init()

@cli.command()
@click.confirmation_option(prompt='Are you sure you want to drop the database? This is irreversible.')
def reset():
    """Reset the database (useful when changing models)."""
    import os
    from personal_brain.config import DB_PATH
    if DB_PATH.exists():
        try:
            os.remove(DB_PATH)
            click.echo("Database deleted.")
        except Exception as e:
            click.echo(f"Error deleting database: {e}")
    else:
        click.echo("Database does not exist.")
    
    # Re-initialize
    _do_init()

@cli.command()
@click.argument('path', type=click.Path(exists=True))
def ingest(path):
    """Ingest files from a path (file or directory)."""
    click.echo(f"Ingesting from {path}...")
    ingest_path(path)
    click.echo("Ingestion completed.")

@cli.command()
@click.argument('query')
@click.option('--limit', default=5, help='Number of results.')
def search(query, limit):
    """Search for files using semantic search."""
    click.echo(f"Searching for: {query}")
    try:
        results = search_files(query, limit)
        if not results:
            click.echo("No results found.")
            return
            
        for res in results:
            score = res.get('trash_score', 0)
            dist = res.get('distance', 0) # Note: dist might be None if vec search failed or different key
            click.echo(f"[{res['type']}] {res['filename']} (Trash: {score:.2f}, Dist: {dist})")
            click.echo(f"   Path: {res['path']}")
            if res['ocr_text']:
                snippet = res['ocr_text'][:100].replace('\n', ' ')
                click.echo(f"   Snippet: {snippet}...")
            click.echo("-" * 20)
    except Exception as e:
        click.echo(f"Error during search: {e}")

@cli.command()
@click.option('--dry-run', is_flag=True, help='Preview changes without deleting.')
def cleanup(dry_run):
    """Cleanup trash files based on score."""
    click.echo(f"Running cleanup (dry_run={dry_run})...")
    click.echo("Cleanup logic is not yet fully implemented.")
    # Future: scan DB for low score files and delete/archive
    
if __name__ == '__main__':
    cli()
