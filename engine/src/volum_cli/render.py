"""Rendering for the CLI.

Separate from ``volum_core`` on purpose: the core produces data, the CLI decides
how it looks, and the same report is served over the API unchanged.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from volum_core.hardware import DoctorReport
from volum_core.hardware.types import Availability
from volum_core.providers import Verdict
from volum_core.providers.types import CommercialUse, ProviderMetadata

_VERDICT_STYLE = {
    Verdict.RUNNABLE: ("runnable", "green"),
    Verdict.MARGINAL: ("marginal", "yellow"),
    Verdict.BLOCKED: ("blocked", "red"),
    Verdict.UNKNOWN: ("unknown", "dim"),
}

_COMMERCIAL_STYLE = {
    CommercialUse.ALLOWED: ("allowed", "green"),
    CommercialUse.CONDITIONAL: ("conditional", "yellow"),
    CommercialUse.NOT_ALLOWED: ("not allowed", "red"),
    CommercialUse.UNKNOWN: ("unknown", "yellow"),
}


def _gib(value: int | None) -> str:
    return "unknown" if value is None else f"{value / (1024**3):.1f} GB"


def _availability(value: Availability) -> Text:
    if value is Availability.AVAILABLE:
        return Text("available", style="green")
    if value is Availability.EXPECTED:
        return Text("expected", style="cyan")
    if value is Availability.UNAVAILABLE:
        return Text("unavailable", style="red")
    return Text("unknown", style="dim")


def _render_facts(report: DoctorReport, console: Console) -> None:
    hardware = report.hardware
    facts = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    facts.add_column(style="dim", width=18)
    facts.add_column()

    facts.add_row("Platform", f"{hardware.os} {hardware.os_version}")
    facts.add_row("Architecture", hardware.arch)
    if hardware.is_apple_silicon:
        facts.add_row("Apple Silicon", Text("detected", style="green"))
    facts.add_row("CPU", hardware.cpu_name or "unknown")
    cores = (
        f"{hardware.cpu_cores_physical} physical / {hardware.cpu_cores_logical} logical"
        if hardware.cpu_cores_physical
        else "unknown"
    )
    facts.add_row("CPU cores", cores)

    memory_label = "Unified memory" if hardware.unified_memory else "Memory"
    facts.add_row(
        memory_label,
        f"{_gib(hardware.ram_total_bytes)} total, {_gib(hardware.ram_available_bytes)} available",
    )

    for gpu in hardware.gpus:
        detail = gpu.name
        if gpu.cores:
            detail += f" ({gpu.cores} GPU cores)"
        if gpu.vram_bytes:
            detail += f", {_gib(gpu.vram_bytes)} VRAM"
        elif gpu.unified_memory:
            detail += ", shares system memory"
        facts.add_row("GPU", detail)

    facts.add_row("Metal", _availability(hardware.metal))
    facts.add_row(
        "Disk", f"{_gib(hardware.disk_free_bytes)} free of {_gib(hardware.disk_total_bytes)}"
    )
    console.print(facts)


def _render_runtimes(report: DoctorReport, console: Console) -> None:
    table = Table(
        title="Runtimes",
        title_justify="left",
        title_style="bold",
        box=None,
        padding=(0, 2, 0, 0),
    )
    table.add_column("Runtime", style="dim", width=16)
    table.add_column("Status", width=13)
    table.add_column("Detail", overflow="fold")
    for status in report.hardware.runtimes:
        table.add_row(
            status.kind.value,
            _availability(status.availability),
            status.detail or (status.version or ""),
        )
    console.print(table)


def _render_model_assessments(report: DoctorReport, console: Console) -> None:
    table = Table(
        title="Models",
        title_justify="left",
        title_style="bold",
        box=None,
        padding=(0, 2, 0, 0),
    )
    table.add_column("Model", style="dim", width=16)
    table.add_column("Status", width=11)
    table.add_column("Runtime", width=9)
    table.add_column("Notes", overflow="fold")
    for assessment in report.assessments:
        label, style = _VERDICT_STYLE[assessment.verdict]
        note = " ".join(reason.message for reason in assessment.reasons) or "Ready to install."
        if assessment.overridable and assessment.verdict is not Verdict.RUNNABLE:
            note += " You can proceed anyway at your own risk."
        table.add_row(
            assessment.provider_id,
            Text(label, style=style),
            assessment.runtime.value if assessment.runtime else "-",
            note,
        )
    console.print(table)


def render_doctor(report: DoctorReport, console: Console) -> None:
    console.print()
    console.print(Text("VOLUM Doctor", style="bold"), f"[dim]v{report.volum_version}[/dim]")
    console.print()
    _render_facts(report, console)
    console.print()
    _render_runtimes(report, console)
    console.print()
    console.print(
        Text("Recommended runtime: ", style="bold"),
        Text(report.recommended_runtime.value, style="cyan"),
    )
    console.print()
    _render_model_assessments(report, console)

    if report.warnings:
        console.print()
        for warning in report.warnings:
            console.print(Text("! ", style="yellow"), Text(warning, style="yellow"), sep="")
    console.print()


def render_models(entries: list[tuple[ProviderMetadata, str, str]], console: Console) -> None:
    """``entries`` is ``(metadata, status_label, status_style)``."""
    console.print()
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("Model", style="bold", width=14)
    table.add_column("Status", width=11)
    table.add_column("Size", width=9, justify="right")
    table.add_column("Needs", width=11, justify="right")
    table.add_column("Licence", width=10)
    table.add_column("Commercial", width=12)

    for metadata, label, style in entries:
        commercial_label, commercial_style = _COMMERCIAL_STYLE[metadata.license.commercial_use]
        table.add_row(
            metadata.id,
            Text(label, style=style),
            _gib(metadata.requirements.disk_bytes),
            _gib(metadata.requirements.estimated_peak_memory_bytes),
            metadata.license.weights_license,
            Text(commercial_label, style=commercial_style),
        )
    console.print(table)
    console.print()
    console.print(
        "[dim]'Needs' is estimated peak memory, not file size — it is the number that "
        "decides whether a model runs. Run [/dim][bold]volum models show <id>[/bold]"
        "[dim] for licence detail.[/dim]"
    )
    console.print()
