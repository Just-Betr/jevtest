import io

from jevtest.cli.console import Printer, summary
from jevtest.domain.results import RunResult


def test_parallel_output_is_tagged_and_printed_whole():
    out = io.StringIO()
    Printer(parallel=True, out=out).block("android · Pixel", ["\n▶ T", "  PASS T (1.0s)"])
    assert out.getvalue() == "\n[android · Pixel] ▶ T\n[android · Pixel]   PASS T (1.0s)\n"


def test_summary_with_no_time(tmp_path):
    text = "\n".join(summary(RunResult(()), [], tmp_path))
    assert "0/0 passed in 0s" in text and "of run time" not in text
