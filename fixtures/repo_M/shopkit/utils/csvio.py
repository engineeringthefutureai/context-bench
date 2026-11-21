"""CSV writing for exports and reports (stdlib csv, always \n line endings, header first)."""
import csv
import io
from collections.abc import Iterable, Sequence


def to_csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def read_csv(text: str) -> list[dict[str, str]]:
    """Parse CSV text with a header row into dicts; surrounding whitespace in values is stripped."""
    reader = csv.DictReader(io.StringIO(text))
    return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in reader]
