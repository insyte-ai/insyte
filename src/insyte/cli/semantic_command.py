"""``insyte semantic`` — generate suggested metrics and validate the semantic layer."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.metadata.repository import MetadataRepository
from insyte.semantic.generator import generate_semantic
from insyte.semantic.repository import SemanticRepository
from insyte.semantic.validator import SchemaIndex, validate_semantic

console = Console()

semantic_app = typer.Typer(
    help="Generate and validate the semantic layer.",
    no_args_is_help=True,
    add_completion=False,
)


@semantic_app.command("generate")
def generate(
    project: str | None = typer.Option(None, "--project", "-p", help="Project to update."),
) -> None:
    """Suggest entities, metrics and dimensions from the scanned schema and profiles."""

    config = resolve_config(project)
    metadata = MetadataRepository(paths.metadata_path(config.project.name))
    try:
        if not metadata.has_metadata():
            console.print(
                "[yellow]No schema metadata yet.[/yellow] Run [bold]insyte scan[/bold] first."
            )
            raise typer.Exit(1)

        details = [
            detail
            for summary in metadata.list_tables()
            if (detail := metadata.get_table(summary.schema, summary.name)) is not None
        ]
        profiles = {p.qualified_column: p for p in metadata.list_column_profiles()}

        semantic_repo = SemanticRepository(paths.semantic_path(config.project.name))
        existing = semantic_repo.load()
        result = generate_semantic(details, profiles, existing)
        semantic_repo.save(result.layer)
    finally:
        metadata.dispose()

    console.print(
        f"Added [green]{len(result.added_metrics)}[/green] metrics, "
        f"[green]{len(result.added_dimensions)}[/green] dimensions, "
        f"[green]{len(result.added_entities)}[/green] entities, "
        f"[green]{len(result.added_aliases)}[/green] aliases "
        "([yellow]suggested[/yellow])."
    )
    if result.added_metrics:
        console.print("[dim]New metrics:[/dim] " + ", ".join(result.added_metrics))
    if result.added_aliases:
        console.print("[dim]New aliases:[/dim] " + ", ".join(result.added_aliases[:12]))
    console.print(
        "\nReview with [bold]insyte metrics[/bold], then "
        "[bold]insyte metrics approve <name>[/bold]."
    )


@semantic_app.command("validate")
def validate(
    project: str | None = typer.Option(None, "--project", "-p", help="Project to validate."),
) -> None:
    """Validate the semantic layer against the scanned schema."""

    config = resolve_config(project)
    metadata = MetadataRepository(paths.metadata_path(config.project.name))
    try:
        layer = SemanticRepository(paths.semantic_path(config.project.name)).load()
        index = SchemaIndex.from_repository(metadata)
    finally:
        metadata.dispose()

    if layer.is_empty():
        console.print("[dim]Semantic layer is empty.[/dim]")
        raise typer.Exit(0)

    issues = validate_semantic(layer, index)
    if not issues:
        console.print("[green]Semantic layer is valid.[/green]")
        raise typer.Exit(0)

    table = Table(title="Semantic issues", title_justify="left")
    table.add_column("Level")
    table.add_column("Target")
    table.add_column("Message", overflow="fold")
    for issue in issues:
        colour = "red" if issue.level == "error" else "yellow"
        table.add_row(f"[{colour}]{issue.level}[/{colour}]", issue.target, issue.message)
    console.print(table)

    if any(issue.level == "error" for issue in issues):
        raise typer.Exit(1)
