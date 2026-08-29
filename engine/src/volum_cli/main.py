"""VOLUM command line interface.

The CLI imports ``volum_core`` directly rather than talking to the HTTP engine:
one core pipeline, two front doors, no duplicated logic (spec section 32).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, cast

import typer
from rich.console import Console

from volum_core.hardware import run_doctor
from volum_core.models import all_models, get_model
from volum_core.providers import Verdict
from volum_core.providers.types import CommercialUse, ProviderMetadata
from volum_core.version import __version__

from .render import render_doctor, render_models

app = typer.Typer(
    name="volum",
    help="Local-first image-to-3D. Everything runs on your machine.",
    no_args_is_help=True,
    add_completion=False,
)
models_app = typer.Typer(
    name="models", help="Inspect and manage local models.", no_args_is_help=True
)
app.add_typer(models_app)

console = Console()
err_console = Console(stderr=True)

_VERDICT_COLOUR = {
    Verdict.RUNNABLE: "green",
    Verdict.MARGINAL: "yellow",
    Verdict.BLOCKED: "red",
    Verdict.UNKNOWN: "dim",
}

_COMMERCIAL_COLOUR = {
    CommercialUse.ALLOWED: "green",
    CommercialUse.CONDITIONAL: "yellow",
    CommercialUse.NOT_ALLOWED: "red",
    CommercialUse.UNKNOWN: "yellow",
}


def _json_option() -> typer.models.OptionInfo:
    """A fresh ``--json`` option per command.

    Inside ``Annotated`` the positional arguments of ``typer.Option`` are
    parameter declarations, not the default — the default belongs in the
    signature. Passing ``False`` here makes Typer try to parse a bool as a flag
    name and fail at import time. A factory also avoids sharing one mutable
    OptionInfo across commands.
    """
    return cast("typer.models.OptionInfo", typer.Option("--json", help="Machine-readable output."))


@app.callback()
def _root(
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit(0)


@app.command()
def doctor(
    as_json: Annotated[bool, _json_option()] = False,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Check free space for this directory instead of home."),
    ] = None,
) -> None:
    """Report what this machine can actually run."""
    report = run_doctor(data_dir=data_dir)
    if as_json:
        console.print_json(report.model_dump_json())
        return
    render_doctor(report, console)


@models_app.command("list")
def models_list(as_json: Annotated[bool, _json_option()] = False) -> None:
    """List known models and whether they fit this machine."""
    report = run_doctor()
    by_id = {a.provider_id: a for a in report.assessments}

    if as_json:
        console.print_json(
            json.dumps(
                [
                    {
                        "metadata": json.loads(m.model_dump_json()),
                        "assessment": json.loads(by_id[m.id].model_dump_json()),
                    }
                    for m in all_models()
                ]
            )
        )
        return

    entries = []
    for metadata in all_models():
        verdict = by_id[metadata.id].verdict
        entries.append((metadata, verdict.value, _VERDICT_COLOUR[verdict]))
    render_models(entries, console)


def _show_requirements(metadata: ProviderMetadata) -> None:
    requirements = metadata.requirements
    console.print()
    console.print("[bold]Requirements[/bold]")
    console.print(f"  Platforms         {', '.join(sorted(requirements.supported_platforms))}")
    console.print(f"  Runtimes          {', '.join(sorted(requirements.supported_runtimes))}")
    console.print(f"  Minimum memory    {requirements.minimum_memory_bytes / 1024**3:.1f} GB")
    console.print(
        f"  Estimated peak    {requirements.estimated_peak_memory_bytes / 1024**3:.1f} GB"
    )
    console.print(f"  Disk              {requirements.disk_bytes / 1024**3:.1f} GB")
    if requirements.requires_gated_download:
        console.print(
            "  [yellow]Gated download    yes - the model host requires an account "
            "and accepted terms[/yellow]"
        )
    if requirements.numbers_source:
        console.print(f"  [dim]Figures from      {requirements.numbers_source}[/dim]")


def _show_licence(metadata: ProviderMetadata) -> None:
    licence = metadata.license
    console.print()
    console.print("[bold]Licence[/bold]")
    console.print(f"  Code              {licence.code_license}")
    console.print(f"  Weights           {licence.weights_license}")
    colour = _COMMERCIAL_COLOUR[licence.commercial_use]
    console.print(f"  Commercial use    [{colour}]{licence.commercial_use.value}[/]")
    if licence.commercial_use_detail:
        console.print(f"                    [dim]{licence.commercial_use_detail}[/dim]")
    if licence.territorial_restriction:
        console.print(f"  [red]Territory         {licence.territorial_restriction}[/red]")
    if licence.attribution_required:
        console.print(f'  Attribution       must display "{licence.attribution_required}"')
    if licence.dependency_licenses:
        console.print("  Dependencies")
        for name, text in sorted(licence.dependency_licenses.items()):
            flagged = "NON-COMMERCIAL" in text or "UNKNOWN" in text
            console.print(f"    [{'red' if flagged else 'dim'}]{name}: {text}[/]")
    if licence.verified_on:
        console.print(f"  [dim]Verified on       {licence.verified_on}[/dim]")


@models_app.command("show")
def models_show(
    model_id: Annotated[str, typer.Argument(help="Model id, e.g. trellis2")],
    as_json: Annotated[bool, _json_option()] = False,
) -> None:
    """Show a model's capabilities, requirements and full licence chain."""
    metadata = get_model(model_id)
    if metadata is None:
        known = ", ".join(m.id for m in all_models())
        err_console.print(f"[red]Unknown model '{model_id}'.[/red] Known models: {known}")
        raise typer.Exit(1)

    if as_json:
        console.print_json(metadata.model_dump_json())
        return

    console.print()
    console.print(f"[bold]{metadata.name}[/bold] [dim]({metadata.id}, {metadata.version})[/dim]")
    console.print(metadata.description)
    console.print()
    console.print("[bold]Capabilities[/bold]")
    console.print("  " + ", ".join(sorted(c.value for c in metadata.capabilities)))

    _show_requirements(metadata)
    _show_licence(metadata)

    report = run_doctor()
    assessment = next(a for a in report.assessments if a.provider_id == model_id)
    console.print()
    console.print("[bold]On this machine[/bold]")
    console.print(
        f"  Status            [{_VERDICT_COLOUR[assessment.verdict]}]{assessment.verdict.value}[/]"
    )
    for reason in assessment.reasons:
        console.print(f"                    {reason.message}")
    console.print()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
