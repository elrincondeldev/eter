"""`eter` command line: the same operations as the MCP server, printed as JSON."""

import json
from typing import Annotated, Any

import typer

from eter_core import NodeError, open_eter

app = typer.Typer(
    help="eter: radio spectrum access for humans and AI agents.", no_args_is_help=True
)


def _print(result: dict[str, Any]) -> None:
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


def _run(operation: str, *args: Any) -> None:
    eter = open_eter()
    try:
        _print(getattr(eter, operation)(*args))
    except NodeError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error


@app.command()
def status() -> None:
    """Connected node, firmware and radios."""
    _run("status")


@app.command()
def scan(
    start: Annotated[float, typer.Option(help="MHz")] = 430.0,
    end: Annotated[float, typer.Option(help="MHz")] = 440.0,
    step: Annotated[float, typer.Option(help="kHz")] = 25.0,
) -> None:
    """Sweep a range: noise floor and strongest peaks."""
    _run("scan", start, end, step)


@app.command()
def listen(
    freq: Annotated[float, typer.Option(help="MHz")] = 433.92,
    seconds: Annotated[int, typer.Option(help="1-60")] = 10,
    threshold: Annotated[float | None, typer.Option(help="dBm; default noise floor + 15")] = None,
) -> None:
    """Capture on/off transmissions on one frequency."""
    _run("listen", freq, seconds, threshold)


@app.command()
def identify(freq: Annotated[float, typer.Argument(help="MHz")]) -> None:
    """Typical uses of a frequency."""
    _run("identify", freq)


@app.command()
def events(
    kind: Annotated[str | None, typer.Option(help="scan or listen")] = None,
    since: Annotated[int | None, typer.Option(help="minutes")] = None,
    limit: Annotated[int, typer.Option()] = 20,
) -> None:
    """History of scans and listens, newest first."""
    _run("events", kind, since, limit)
