"""Root ``street-lights`` CLI — groups every tool as a subcommand.

    street-lights parse-assessments   # assessment-roll PDFs -> CSV
    street-lights unassessed-apns     # City APNs not in the roll -> CSV
    street-lights build-map           # build all web/data map assets
    street-lights serve               # serve the map site locally
"""

from __future__ import annotations

import typer

from la_street_lights.bin import build_map, parse_assessments, serve, unassessed_apns

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="LA street-light assessment (District No. 5500) tooling.",
)

app.command("parse-assessments")(parse_assessments.main)
app.command("unassessed-apns")(unassessed_apns.main)
app.command("build-map")(build_map.main)
app.command("serve")(serve.main)


if __name__ == "__main__":
    app()
