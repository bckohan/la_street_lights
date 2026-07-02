"""Serve the static map site with HTTP Range support (Typer command).

PMTiles fetches byte ranges of ``parcels.pmtiles`` via HTTP ``Range`` requests.
Python's stdlib ``http.server`` ignores ``Range`` and returns the whole file,
which breaks PMTiles, so this handler implements single-range responses.
"""

from __future__ import annotations

import os
import re
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Serve the map site locally.")


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):  # noqa: C901 - small, range branch is the only addition
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()

        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()  # let the base class 404/redirect

        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        size = os.path.getsize(path)
        if not m:
            return super().send_head()
        start_s, end_s = m.groups()
        if start_s == "":  # suffix range: last N bytes
            length = int(end_s)
            start, end = max(0, size - length), size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416, "Requested Range Not Satisfiable")
            self.send_header("Content-Range", f"bytes */{size}")
            return None

        f = open(path, "rb")
        f.seek(start)
        self._range_remaining = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self._range_remaining))
        self.end_headers()
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_range_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


@app.command()
def main(
    port: int = typer.Argument(8765, help="Port to listen on."),
    directory: Path = typer.Option(
        Path("web"), "--directory", "-d", help="Directory to serve."
    ),
) -> None:
    """Serve DIRECTORY at http://localhost:PORT with HTTP Range support."""
    web_dir = str(directory.resolve())
    if not os.path.isdir(web_dir):
        typer.secho(f"Not a directory: {web_dir}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    handler = partial(RangeHandler, directory=web_dir)
    typer.echo(f"Serving {web_dir} at http://localhost:{port}  (Ctrl-C to stop)")
    try:
        HTTPServer(("127.0.0.1", port), handler).serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    app()
