"""``insyte semantic`` — generate suggested metrics and validate the semantic layer."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.metadata.repository import MetadataRepository
from insyte.nl.llm import (
    available_backends,
    resolve_semantic_proposals,
    resolve_starter_questions,
)
from insyte.semantic.catalog import SemanticCatalog
from insyte.semantic.generator import generate_semantic
from insyte.semantic.proposals import apply_metric_proposal
from insyte.semantic.repository import SemanticRepository
from insyte.semantic.validator import SchemaIndex, validate_semantic

console = Console()

semantic_app = typer.Typer(
    help="Generate and validate the semantic layer.",
    no_args_is_help=True,
    add_completion=False,
)


@semantic_app.command("enrich")
def enrich(
    project: str | None = typer.Option(None, "--project", "-p", help="Project to update."),
    backend: str = typer.Option("auto", "--backend", help="claude, codex, auto, or off."),
) -> None:
    """Propose grounded derived metrics that remain blocked until explicitly approved."""

    config = resolve_config(project)
    semantic_repo = SemanticRepository(paths.semantic_path(config.project.name))
    layer = semantic_repo.load()
    metadata = MetadataRepository(paths.metadata_path(config.project.name))
    try:
        profiles = metadata.list_column_profiles() if metadata.has_profiles() else []
    finally:
        metadata.dispose()
    if not layer.metrics or not profiles:
        console.print("[yellow]Metrics and column profiles are required for enrichment.[/yellow]")
        raise typer.Exit(1)

    proposals = []
    used_backend = ""
    for candidate in available_backends(backend):
        proposals = resolve_semantic_proposals(layer, profiles, candidate)
        if proposals:
            used_backend = candidate.name
            break
    if not proposals:
        console.print("[dim]No valid derived metric proposals were generated.[/dim]")
        raise typer.Exit(0)
    for proposal in proposals:
        layer = apply_metric_proposal(proposal, layer)
    semantic_repo.save(layer)
    console.print(
        f"Added [green]{len(proposals)}[/green] confirmation-required metric proposals with "
        f"[bold]{used_backend}[/bold]."
    )
    console.print("Review with [bold]insyte metrics[/bold] and approve definitions you accept.")


@semantic_app.command("questions")
def questions(
    project: str | None = typer.Option(None, "--project", "-p", help="Project to update."),
    backend: str = typer.Option("auto", "--backend", help="claude, codex, auto, or off."),
) -> None:
    """Generate concise, schema-grounded Studio starter questions with a local AI CLI."""

    config = resolve_config(project)
    repo = SemanticRepository(paths.semantic_path(config.project.name))
    layer = repo.load()
    if not layer.metrics:
        console.print(
            "[yellow]No metrics available.[/yellow] Run [bold]insyte semantic generate[/bold]."
        )
        raise typer.Exit(1)

    metadata = MetadataRepository(paths.metadata_path(config.project.name))
    try:
        catalog = SemanticCatalog(
            layer,
            profiles=metadata.list_column_profiles() if metadata.has_profiles() else [],
            relationships=metadata.list_relationships() if metadata.has_metadata() else [],
        )
    finally:
        metadata.dispose()

    generated = []
    used_backend = ""
    for candidate in available_backends(backend):
        generated = resolve_starter_questions(layer, candidate, catalog=catalog)
        if generated:
            used_backend = candidate.name
            break
    if not generated:
        console.print(
            "[yellow]No valid starter questions were generated.[/yellow] "
            "Existing questions were preserved."
        )
        raise typer.Exit(0)

    layer.starter_questions = generated
    repo.save(layer)
    console.print(
        f"Added [green]{len(generated)}[/green] grounded starter questions with "
        f"[bold]{used_backend}[/bold]."
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

        details = metadata.list_table_details()
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
