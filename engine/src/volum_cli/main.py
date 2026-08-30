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

from volum_core.config import get_data_dir, load_settings
from volum_core.hardware import run_doctor
from volum_core.hardware.detect import detect_hardware
from volum_core.hardware.types import RuntimeKind
from volum_core.jobs import JobManager, JobStore
from volum_core.jobs.types import JobStatus
from volum_core.models import (
    InstallState,
    ModelInstallError,
    ModelManager,
    all_models,
    get_model,
)
from volum_core.pipeline import run_pipeline
from volum_core.providers import Verdict
from volum_core.providers.triposr import TripoSRProvider
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


def _pick_device() -> str:
    """Choose the runtime for a generation.

    Uses the same preference order as the doctor, so what the doctor reports and
    what a job actually runs on cannot drift apart.
    """
    recommended = detect_hardware().recommended_runtime
    return (
        "mps"
        if recommended is RuntimeKind.MPS
        else ("cuda" if recommended is RuntimeKind.CUDA else "cpu")
    )


def _manager() -> ModelManager:
    return ModelManager(get_data_dir() / "models")


def _gib(value: int) -> str:
    return f"{value / 1024**3:.2f} GB"


@models_app.command("install")
def models_install(
    model_id: Annotated[str, typer.Argument(help="Model id, e.g. triposr")],
    allow_marginal: Annotated[
        bool,
        typer.Option(
            "--allow-marginal",
            help="Install even though this machine is below the model's estimated peak. "
            "It may swap heavily or fail.",
        ),
    ] = False,
    token: Annotated[
        str | None,
        typer.Option("--hf-token", help="Hugging Face token, for gated weights."),
    ] = None,
) -> None:
    """Download and install a model. Nothing is downloaded without this command."""
    manager = _manager()
    if manager.state(model_id) is InstallState.INSTALLED:
        console.print(f"[green]{model_id} is already installed.[/green]")
        return

    resolved_token = token or load_settings().hugging_face_token
    try:
        with console.status(f"Installing {model_id}...") as status:

            def report(step: str, message: str) -> None:
                status.update(f"[{step}] {message}")
                console.print(f"  [dim]{step}[/dim]  {message}")

            manifest = manager.install(
                model_id,
                on_progress=report,
                allow_marginal=allow_marginal,
                hf_token=resolved_token,
            )
    except ModelInstallError as error:
        err_console.print(f"\n[red]{error.message}[/red]")
        for suggestion in error.suggestions:
            err_console.print(f"  - {suggestion}")
        if error.technical:
            err_console.print(f"\n[dim]{error.technical}[/dim]")
        raise typer.Exit(1) from error

    usage = manager.disk_usage(model_id).get(model_id, 0)
    console.print(
        f"\n[green]{model_id} installed[/green] ({_gib(usage)} on disk, "
        f"source {manifest.source_commit[:12] if manifest.source_commit else 'n/a'})"
    )


@models_app.command("verify")
def models_verify(
    model_id: Annotated[str, typer.Argument(help="Model id")],
) -> None:
    """Check that an installed model is intact."""
    problems = _manager().verify(model_id)
    if not problems:
        console.print(f"[green]{model_id} looks sound.[/green]")
        return
    err_console.print(f"[red]{model_id} has problems:[/red]")
    for problem in problems:
        err_console.print(f"  - {problem}")
    raise typer.Exit(1)


@models_app.command("remove")
def models_remove(
    model_id: Annotated[str, typer.Argument(help="Model id")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask.")] = False,
) -> None:
    """Delete a model's weights and its environment."""
    manager = _manager()
    usage = manager.disk_usage(model_id).get(model_id, 0)
    if not usage and manager.state(model_id) is InstallState.NOT_INSTALLED:
        console.print(f"{model_id} is not installed.")
        return
    if not yes and not typer.confirm(f"Remove {model_id} and free {_gib(usage)}?"):
        console.print("Cancelled.")
        return
    manager.remove(model_id)
    console.print(f"[green]Removed {model_id}[/green], freeing {_gib(usage)}.")


@models_app.command("disk")
def models_disk() -> None:
    """Show what installed models occupy on disk."""
    usage = _manager().disk_usage()
    if not usage:
        console.print("No models installed.")
        return
    for name, size in sorted(usage.items(), key=lambda item: -item[1]):
        console.print(f"  {name:<16} {_gib(size):>10}")
    console.print(f"  {'total':<16} {_gib(sum(usage.values())):>10}")


@app.command()
def generate(
    images: Annotated[list[Path], typer.Argument(help="One or more input images.", exists=True)],
    model: Annotated[str, typer.Option("--model", "-m", help="Model id.")] = "triposr",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Where to write the asset.")
    ] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="For repeatable runs.")] = None,
    resolution: Annotated[
        int, typer.Option("--resolution", help="Marching-cubes resolution.")
    ] = 256,
    device: Annotated[
        str | None, typer.Option("--device", help="mps, cuda or cpu. Detected by default.")
    ] = None,
) -> None:
    """Generate a 3D asset from one or more images. Runs entirely on this machine."""
    manager = _manager()
    if manager.state(model) is not InstallState.INSTALLED:
        err_console.print(
            f"[red]{model} is not installed.[/red]\n"
            f"  Install it with: [bold]volum models install {model}[/bold]"
        )
        raise typer.Exit(1)

    if model != "triposr":
        err_console.print(f"[red]No provider implementation for '{model}' yet.[/red]")
        raise typer.Exit(1)

    if len(images) > 1:
        # Said out loud rather than quietly ignored: TripoSR is single-image,
        # and silently using the first would be the simulated multi-image
        # support the specification forbids.
        console.print(
            f"[yellow]{model} uses a single image. The first of {len(images)} will be "
            "used; the others are ignored.[/yellow]"
        )

    data_dir = get_data_dir()
    jobs = JobManager(JobStore(data_dir / "jobs"))
    chosen_device = device or _pick_device()

    record = jobs.create(
        model_id=model,
        input_files=images,
        parameters={"mc_resolution": resolution},
        seed=seed,
        runtime=chosen_device,
    )
    output_dir = output or (data_dir / "jobs" / record.id / "output")

    console.print(f"Job [bold]{record.id[:12]}[/bold] on [cyan]{chosen_device}[/cyan]")

    provider = TripoSRProvider(manager, device=chosen_device)
    with console.status("Starting...") as status:
        result = run_pipeline(
            manager=jobs,
            provider=provider,
            record=record,
            images=list(images),
            output_dir=output_dir,
            parameters={"mc_resolution": resolution},
            seed=seed,
            on_stage=lambda stage: status.update(f"[cyan]{stage}[/cyan]..."),
        )

    job = result.job
    if job.status is not JobStatus.COMPLETED:
        err_console.print(f"\n[red]{job.status.value}[/red]")
        if job.error is not None:
            err_console.print(f"  {job.error.message}")
            for suggestion in job.error.suggestions:
                err_console.print(f"  - {suggestion}")
            if job.error.technical:
                err_console.print(f"\n[dim]{job.error.technical[:1500]}[/dim]")
        raise typer.Exit(1)

    report = result.report
    console.print(f"\n[green]Done[/green] in {job.duration_seconds:.1f}s")
    console.print(f"  Asset      {job.artifacts['mesh']}")
    if report is not None:
        console.print(f"  Geometry   {report.vertices:,} vertices, {report.triangles:,} triangles")
        dims = " x ".join(f"{d:.3f}" for d in report.dimensions)
        console.print(f"  Dimensions {dims}")
        console.print(
            f"  Surface    watertight={report.watertight}, "
            f"vertex colours={report.has_vertex_colors}, UV={report.has_uv}"
        )
        for issue in report.issues:
            console.print(f"  [yellow]note[/yellow]  {issue.message}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
