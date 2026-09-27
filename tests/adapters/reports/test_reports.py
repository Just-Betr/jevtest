import json
import xml.etree.ElementTree as ET

from jevtest.adapters.reports.json_report import write_report
from jevtest.adapters.reports.junit import Suite, write_junit
from jevtest.adapters.testfile.steps import parse_step
from jevtest.domain.decisions import Decision, Finished
from jevtest.domain.kinds import Status
from jevtest.domain.model import Choice, ModelCall, Picked, Probability, YesNo
from jevtest.domain.results import CheckResult, RunResult, StepResult, TestResult
from jevtest.domain.steps import See

TAP = parse_step({"tap": "X", "see": "X"})
PASSED = TestResult("A", Status.PASS, 1.25, (StepResult(parse_step("back"), Status.PASS, 1.0),))
FAILED = TestResult("B <x>", Status.FAIL, 2.0, (StepResult(
    TAP, Status.FAIL, 2.0, "on button 'X'", decisions=(Decision(Finished(), 0.9, {"done": 0.9}),),
    checks=(CheckResult(See("X"), Status.FAIL, "not on screen"),),
    steps=(StepResult(parse_step("home"), Status.PASS, 0.1),), screenshot="001_FAIL_B.png"),))
LOGS = {"A": ["ok"], "B <x>": ["bad"]}


def test_junit(tmp_path):
    path = tmp_path / "sub" / "junit.xml"
    write_junit(path, [Suite("jevtest.ios", RunResult((PASSED, FAILED)), LOGS),
                       Suite("jevtest.android", RunResult(()), {})])
    suite, other = ET.parse(path).getroot().findall("testsuite")
    assert other.attrib["name"] == "jevtest.android"
    assert suite.attrib == {"name": "jevtest.ios", "tests": "2", "failures": "1", "errors": "0", "time": "3.2"}
    cases = suite.findall("testcase")
    assert cases[0].find("failure") is None and cases[0].find("system-out").text == "ok"
    assert cases[1].attrib["name"] == "B <x>"
    assert cases[1].find("failure").attrib["message"] == "see: X — not on screen"


def test_json_report(tmp_path):
    calls = [ModelCall({"screen": []}, {"a": Choice({"q": "?"}, {"x": "X"}), "b": YesNo({"q": "?"})},
                       {"a": Picked("x", 1.0, {"x": 1.0}), "b": Probability(0.7)}, recorded=True)]
    start_failed = TestResult("C", Status.FAIL, 0.0, start_failure="locked", screenshot="002_FAIL_C.png")
    write_report(tmp_path / "report.json", {"platform": "ios"}, RunResult((PASSED, FAILED, start_failed)), LOGS,
                 calls)
    report = json.loads((tmp_path / "report.json").read_text())
    assert (report["platform"], report["passed"], report["failed"]) == ("ios", 1, 2)
    step = report["tests"][1]["steps"][0]
    assert step["step"] == "tap: X" and step["detail"] == "on button 'X'"
    assert step["decisions"] == [{"did": "done", "confidence": 0.9, "probabilities": {"done": 0.9}}]
    assert step["checks"] == [{"check": "see", "text": "X", "status": "fail", "detail": "not on screen"}]
    assert step["steps"][0]["step"] == "home" and step["screenshot"] == "001_FAIL_B.png"
    assert report["tests"][2]["failure"] == "(start app) — locked" and report["tests"][2]["screenshot"]
    [call] = report["model_calls"]
    assert call["from_lockfile"] is True and call["answers"] == {
        "a": {"choice": "x", "confidence": 1.0, "probabilities": {"x": 1.0}}, "b": {"yes": 0.7}}
    assert call["questions"]["a"]["choose_from"] == {"x": "X"} and call["questions"]["b"]["yes_or_no"] is True
