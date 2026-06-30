"""List City of Los Angeles APNs that are NOT in the street-lighting district.

Cross-references the City of Los Angeles parcel list
(``APNs_in_the_City_of_Los_Angeles_*.csv``) against the parsed Streetlight
Maintenance Assessment District No. 5500 roll (``parsed_assessments.csv``,
produced by ``parse_assessments``) and writes every City APN that does not
appear in the assessment roll.

APN normalization: the City CSV stores the Assessor ID Number as ten digits
with no separators (``2004001003``); the assessment roll stores it dashed
(``2004-001-003``). Both are reduced to the bare ten-digit form for comparison.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    help="Find City APNs absent from the street-lighting assessment roll.",
)

# A normalized Assessor ID Number is exactly ten digits.
AIN_RE = re.compile(r"^\d{10}$")

OUTPUT_FIELDS = ["apn", "apn_dashed", "pin", "pind", "auto_id"]


def _normalize(apn: str) -> str:
    """Reduce an APN/AIN to its bare ten-digit form."""
    return re.sub(r"\D", "", apn or "")


def _dashed(ain: str) -> str:
    """Format a ten-digit AIN as ``####-###-###`` (matching the roll)."""
    return f"{ain[:4]}-{ain[4:7]}-{ain[7:]}"


def _load_roll_apns(roll_csv: Path) -> set[str]:
    """Return the set of normalized APNs present in the assessment roll."""
    apns: set[str] = set()
    with roll_csv.open(newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "apn" not in reader.fieldnames:
            raise typer.BadParameter(f"{roll_csv} has no 'apn' column")
        for row in reader:
            apns.add(_normalize(row["apn"]))
    return apns


@app.command()
def main(
    city_csv: Path = typer.Option(
        Path("sources/APNs_in_the_City_of_Los_Angeles_20260627.csv"),
        "--city-csv",
        help="City of Los Angeles APN list (columns AUTO_ID, PIN, PIND, APN).",
    ),
    roll_csv: Path = typer.Option(
        Path("sources/parsed_assessments.csv"),
        "--roll-csv",
        help="Parsed assessment roll produced by parse_assessments.",
    ),
    output: Path = typer.Option(
        Path("sources/city_apns_not_in_district.csv"),
        "--output",
        "-o",
        help="Destination CSV listing City APNs absent from the roll.",
    ),
) -> None:
    """Write the City APNs that are not in the street-lighting district."""
    for path in (city_csv, roll_csv):
        if not path.exists():
            typer.secho(f"Not found: {path}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

    roll = _load_roll_apns(roll_csv)
    typer.echo(f"Assessment roll: {len(roll):,} unique APNs")

    city_rows = 0
    malformed: list[str] = []
    seen: set[str] = set()  # de-duplicate City APNs (the CSV has repeats)
    missing = 0

    output.parent.mkdir(parents=True, exist_ok=True)
    with city_csv.open(newline="") as fh, output.open("w", newline="") as out:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "APN" not in reader.fieldnames:
            raise typer.BadParameter(f"{city_csv} has no 'APN' column")
        writer = csv.DictWriter(out, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in reader:
            city_rows += 1
            ain = _normalize(row.get("APN", ""))
            if not AIN_RE.match(ain):
                malformed.append(row.get("APN", ""))
                continue
            if ain in seen:
                continue
            seen.add(ain)
            if ain not in roll:
                missing += 1
                writer.writerow(
                    {
                        "apn": ain,
                        "apn_dashed": _dashed(ain),
                        "pin": row.get("PIN", "").strip(),
                        "pind": row.get("PIND", "").strip(),
                        "auto_id": row.get("AUTO_ID", "").strip(),
                    }
                )

    typer.echo(f"City rows read:        {city_rows:,}")
    typer.echo(f"Unique City APNs:      {len(seen):,}")
    typer.echo(f"  in district:         {len(seen) - missing:,}")
    typer.secho(f"  NOT in district:     {missing:,}", fg=typer.colors.YELLOW)
    if malformed:
        typer.secho(
            f"Skipped {len(malformed)} malformed APN(s): "
            + ", ".join(repr(m) for m in malformed[:5]),
            fg=typer.colors.RED,
            err=True,
        )
    typer.secho(f"Wrote {missing:,} rows to {output}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
