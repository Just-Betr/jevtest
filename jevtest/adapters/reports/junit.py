"""JUnit XML: the result format CI systems (GitHub Actions, GitLab, Jenkins, ...) display."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from jevtest.domain.kinds import Status
from jevtest.domain.results import RunResult


@dataclass(frozen=True)
class Suite:
    """One device's run of one test file.

    Attributes:
        name: The suite's name, e.g. ``jevtest.checkout.android.Pixel 8``.
        result: What happened.
        logs: Each test's console log, by test name.
    """

    name: str
    result: RunResult
    logs: Mapping[str, Sequence[str]]


def write_junit(path: Path, suites: Sequence[Suite]) -> None:
    """Write one ``<testsuite>`` per suite, one ``<testcase>`` per test, with each test's log."""
    root = ET.Element("testsuites")
    for suite in suites:
        tests = suite.result.tests
        ts = ET.SubElement(root, "testsuite", name=suite.name, tests=str(len(tests)),
                           failures=str(suite.result.failed), errors="0", time=f"{suite.result.seconds:.1f}")
        for t in tests:
            tc = ET.SubElement(ts, "testcase", classname=suite.name, name=t.name, time=f"{t.seconds:.1f}")
            log = "\n".join(suite.logs.get(t.name, ()))
            if t.status is Status.FAIL:
                ET.SubElement(tc, "failure", message=t.failure or "failed").text = log
            ET.SubElement(tc, "system-out").text = log
    ET.indent(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
