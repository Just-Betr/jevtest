"""Runs the tests in a spec, step by step, against one device."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from .brain import Brain, Decision
from .drivers.base import Driver, DriverError
from .jev import JevError
from .screen import Element, Screen
from .spec import Spec, Step, Test

POLL = 0.5          # seconds between retries while waiting for a check or element
FOCUS_DELAY = 0.4   # seconds after tapping a text field before typing
# Actions after which the app is allowed to be closed or in the background.
APP_MAY_LEAVE = {"stop", "clear_data", "reinstall", "home", "open_url"}
# Actions that do their own waiting, or change nothing on screen.
NO_SETTLE = {"stop", "clear_data", "reinstall", "home", "wait", "screenshot", "scroll_to", "do", "use"}


class StepFailed(Exception):
    pass


class Clock:
    """Real time. Tests pass a fake one so runs are instant and repeatable."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float):
        time.sleep(seconds)


class Runner:
    def __init__(self, spec: Spec, driver: Driver, brain: Brain, out_dir: Path,
                 verbose: bool = False, clock: Clock | None = None, out=None):
        self.spec = spec
        self.s = spec.settings
        self.driver = driver
        self.brain = brain
        self.out_dir = out_dir
        self.verbose = verbose
        self.clock = clock or Clock()
        self.out = out or sys.stdout
        self.expect_running = False
        self.shot_n = 0
        self.lines: list[str] = []

    # --- output ----------------------------------------------------------------
    def log(self, msg: str):
        print(msg, file=self.out, flush=True)
        self.lines.append(msg)

    def log_jev(self, pad: str, since: int):
        """-v: print every Jev call made since index `since`."""
        if not self.verbose:
            return
        for call in self.brain.jev.calls[since:]:
            for qid, ans in call["answers"].items():
                if ans["type"] == "noul":
                    got = f"yes={ans['noul']:.2f}"
                else:
                    top = sorted(ans["probabilities"].items(), key=lambda kv: (-kv[1], kv[0]))[:3]
                    got = f"{ans['choice']}  [" + ", ".join(f"{k} {v:.2f}" for k, v in top) + "]"
                self.log(f"{pad}    jev {qid}: {got}")
            source = "from lockfile" if call.get("cached") else f"{call['ms']} ms"
            self.log(f"{pad}    jev call {source}, {len(call['questions'])} question(s)")

    # --- helpers ---------------------------------------------------------------
    def settle(self):
        self.clock.sleep(self.s.settle)

    def screen(self) -> Screen:
        return self.driver.screen()

    def screenshot(self, name: str) -> str:
        self.shot_n += 1
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name)[:60].strip("_") or "screen"
        path = self.out_dir / f"{self.shot_n:03d}_{safe}.png"
        try:
            self.driver.screenshot(path)
        except DriverError as e:
            return f"(screenshot failed: {e})"
        return path.name

    def poll(self, attempt, timeout: float, failure: str):
        """Call `attempt()` until it returns something other than None, or time runs out."""
        deadline = self.clock.now() + timeout
        while True:
            result = attempt()
            if result is not None:
                return result
            if self.clock.now() >= deadline:
                raise StepFailed(failure)
            self.clock.sleep(POLL)

    def locate(self, target: str, timeout: float, editable: bool = False) -> Element:
        def attempt():
            screen = self.screen()
            return self.brain.locate(target, screen, screen.editable if editable else None)
        what = "text field" if editable else "element"
        return self.poll(attempt, timeout, f"Could not find {what} '{target}' on screen")

    # --- tests -------------------------------------------------------------------
    def run(self) -> dict:
        results = [self.run_test(t) for t in self.spec.tests]
        passed = sum(r["status"] == "pass" for r in results)
        return {"passed": passed, "failed": len(results) - passed, "tests": results}

    def run_test(self, test: Test) -> dict:
        self.lines = []
        self.log(f"\n▶ {test.name}")
        started = self.clock.now()
        steps: list[dict] = []
        try:
            self.start_app(test.fresh)
            status = self.run_steps(test.steps, steps, "  ")
        except DriverError as e:
            status = "fail"
            steps.append({"step": "(start app)", "status": "fail", "detail": str(e)})
            self.log(f"  ✗ could not start app: {e}")
        if status != "pass":
            steps[-1]["screenshot"] = self.screenshot(f"FAIL_{test.name}")
        took = round(self.clock.now() - started, 1)
        self.log(f"  {'PASS' if status == 'pass' else 'FAIL'} {test.name} ({took}s)")
        return {"name": test.name, "status": status, "seconds": took,
                "failure": failure_of(steps) if status != "pass" else None,
                "steps": steps, "log": list(self.lines)}

    def start_app(self, fresh: bool):
        if fresh:
            self.driver.stop()
            self.driver.clear_data()
        if fresh or self.driver.app_state() != "foreground":
            self.driver.launch()
            self.settle()
        self.expect_running = True

    def run_steps(self, steps: list[Step], results: list, pad: str) -> str:
        for step in steps:
            result = self.run_step(step, pad)
            results.append(result)
            if result["status"] != "pass":
                return "fail"
        return "pass"

    def run_step(self, step: Step, pad: str) -> dict:
        started = self.clock.now()
        result: dict = {"step": step.raw, "status": "pass"}
        if step.kind == "use":
            self.log(f"{pad}▸ use: {step.used.name}")
            result["steps"] = []
            result["status"] = self.run_steps(step.used.steps, result["steps"], pad + "  ")
        elif step.kind is not None:
            self.run_action(step, result, pad, started)
        if result["status"] == "pass" and step.checks:
            pad = pad + "    " if step.kind is not None else pad
            result["checks"] = []
            for kind, text in step.checks:
                check = self.run_check(kind, text, step.opts.get("timeout", self.s.timeout), pad)
                result["checks"].append(check)
                if check["status"] != "pass":
                    result["status"] = "fail"
                    break
        result["seconds"] = round(self.clock.now() - started, 1)
        return result

    def run_action(self, step: Step, result: dict, pad: str, started: float):
        mark = len(self.brain.jev.calls)
        decisions: list[dict] = []
        try:
            detail = getattr(self, f"act_{step.kind}")(step, decisions)
            if step.kind not in NO_SETTLE:
                self.settle()
            self.check_app(step)
            if detail:
                result["detail"] = detail
        except (StepFailed, DriverError, JevError) as e:
            result["status"] = "fail"
            result["detail"] = str(e)
        if decisions:
            result["decisions"] = decisions
        took = round(self.clock.now() - started, 1)
        extra = f" — {result['detail']}" if result.get("detail") else ""
        self.log(f"{pad}{'✓' if result['status'] == 'pass' else '✗'} {step.title()} ({took}s){extra}")
        for d in decisions:
            self.log(f"{pad}    → {d['did']}  (confidence {d['confidence']:.2f})")
        self.log_jev(pad, mark)

    def run_check(self, kind: str, text: str, timeout: float, pad: str) -> dict:
        mark = len(self.brain.jev.calls)
        check = {"check": kind, "text": text, "status": "pass"}
        try:
            check["detail"] = self.check(kind, text, timeout)
        except (StepFailed, DriverError, JevError) as e:
            check["status"], check["detail"] = "fail", str(e)
        extra = f" — {check['detail']}" if check["detail"] else ""
        self.log(f"{pad}{'✓' if check['status'] == 'pass' else '✗'} {kind}: {text}{extra}")
        self.log_jev(pad, mark)
        return check

    def check_app(self, step: Step):
        """Fail if the app crashed or left the foreground during the action."""
        if not self.expect_running or step.kind in APP_MAY_LEAVE:
            return
        state = self.driver.app_state()
        if state == "not_running":
            raise StepFailed("The app is no longer running (crashed or closed)")
        if state == "background":
            raise StepFailed("The app left the foreground")

    def check(self, kind: str, text: str, timeout: float) -> str | None:
        """expect: Jev judges the statement. see / not_see: exact text. Retries until timeout."""
        last = {}

        def attempt():
            if kind == "expect":
                p = last["p"] = self.brain.check(text, self.screen())
                return f"Jev {p:.2f}" if p > self.s.threshold else None
            found = any(text.lower() in t.lower() for t in self.screen().texts())
            return "" if found == (kind == "see") else None

        failure = {"see": "not on screen", "not_see": "still on screen"}.get(kind, "")
        try:
            return self.poll(attempt, timeout, failure) or None
        except StepFailed:
            if kind == "expect":
                raise StepFailed(f"Jev says false ({last['p']:.2f})") from None
            raise

    # --- actions: one method per step kind (act_<kind>) ---------------------------
    def act_launch(self, step, _):
        self.driver.launch()
        self.expect_running = True

    def act_stop(self, step, _):
        self.driver.stop()
        self.expect_running = False

    def act_restart(self, step, _):
        self.driver.stop()
        self.driver.launch()
        self.expect_running = True

    def act_clear_data(self, step, _):
        self.driver.stop()
        self.driver.clear_data()
        self.expect_running = False

    def act_reinstall(self, step, _):
        self.driver.reinstall()
        self.expect_running = False

    def act_back(self, step, _):
        self.driver.back()

    def act_home(self, step, _):
        self.driver.home()

    def act_hide_keyboard(self, step, _):
        self.driver.hide_keyboard()

    def act_wait(self, step, _):
        self.clock.sleep(step.value)

    def act_key(self, step, _):
        self.driver.key(step.value)

    def act_scroll(self, step, _):
        self.driver.scroll(step.value)

    def act_swipe(self, step, _):
        timeout = step.opts.get("timeout", self.s.timeout)
        el = self.locate(step.opts["target"], timeout) if "target" in step.opts else None
        self.driver.swipe(step.value, el=el)
        return f"on {el.label()}" if el else None

    def act_scroll_to(self, step, _):
        direction = step.opts.get("direction", "down")
        for _i in range(step.opts.get("max_scrolls", 15)):
            screen = self.screen()
            if self.brain.locate(step.value, screen) is not None:
                return None
            self.driver.scroll(direction, screen=screen)
            self.settle()
        raise StepFailed(f"Scrolled {direction} but never found '{step.value}'")

    def _touch(self, step) -> str:
        el = self.locate(step.value, step.opts.get("timeout", self.s.timeout))
        getattr(self.driver, step.kind)(*el.center)
        return f"on {el.label()}"

    def act_tap(self, step, _):
        return self._touch(step)

    def act_double_tap(self, step, _):
        return self._touch(step)

    def act_long_press(self, step, _):
        return self._touch(step)

    def act_clear(self, step, _):
        el = self.locate(step.value, step.opts.get("timeout", self.s.timeout), editable=True)
        self.driver.clear_text(el)
        return f"on {el.label()}"

    def act_type(self, step, _):
        if "into" not in step.opts:
            self.driver.type_text(step.value)
            return None
        el = self.locate(step.opts["into"], step.opts.get("timeout", self.s.timeout), editable=True)
        self.driver.tap(*el.center)
        self.clock.sleep(FOCUS_DELAY)
        self.driver.type_text(step.value, at=el.center)
        return f"into {el.label()}"

    def act_rotate(self, step, _):
        self.driver.rotate(step.value)

    def act_location(self, step, _):
        self.driver.set_location(*step.value)

    def act_open_url(self, step, _):
        self.driver.open_url(step.value)

    def act_screenshot(self, step, _):
        return f"saved {self.screenshot(step.value)}"

    def act_background(self, step, _):
        self.driver.home()
        self.clock.sleep(step.value)
        self.driver.resume()

    def act_dark_mode(self, step, _):
        self.driver.dark_mode(step.value)

    def act_grant(self, step, _):
        self.driver.grant(step.value)

    def act_network(self, step, _):
        self.driver.network(step.value)

    def act_do(self, step, decisions):
        return self.achieve(step.value, step.opts.get("max_actions", self.s.max_actions), decisions)

    # --- the Jev loop --------------------------------------------------------------
    def achieve(self, goal: str, max_actions: int, decisions: list) -> str:
        """Look at the screen, let Jev pick the next action, do it, repeat until done."""
        taken: list[str] = []
        while True:
            screen = self.screen()
            dec = self.brain.next_action(goal, screen, taken)
            decisions.append({"did": dec.describe(), "confidence": dec.confidence,
                              "probabilities": dec.probabilities})
            if dec.action == "done":
                return f"{len(taken)} action(s)"
            if dec.action == "impossible":
                raise StepFailed("Jev says the goal is impossible from this screen")
            if len(taken) == max_actions:
                raise StepFailed(f"Goal not reached after {max_actions} actions")
            if taken[-2:] == [dec.describe()] * 2:
                raise StepFailed(f"Stuck repeating: {dec.describe()}")
            self.perform(dec, screen)
            taken.append(dec.describe())
            self.settle()

    def perform(self, dec: Decision, screen: Screen):
        d, el, a = self.driver, dec.element, dec.action
        if a in ("tap", "double_tap", "long_press"):
            getattr(d, a)(*el.center)
        elif a in ("swipe_left_on", "swipe_right_on"):
            d.swipe(a.split("_")[1], el=el)
        elif a == "type":
            if not el.focused:
                d.tap(*el.center)
                self.clock.sleep(FOCUS_DELAY)
            d.type_text(dec.text, at=el.center)
        elif a == "clear":
            d.clear_text(el)
        elif a.startswith("scroll_"):
            d.scroll(a.split("_", 1)[1], screen=screen)
        elif a == "back":
            d.back()
        elif a == "press_enter":
            d.key("enter")
        elif a == "hide_keyboard":
            d.hide_keyboard()
        else:  # wait
            self.clock.sleep(1.0)


# --- results -------------------------------------------------------------------------

def failure_of(steps: list) -> str:
    """Plain-English reason for the first failure (for CI)."""
    for s in steps:
        if s["status"] == "pass":
            continue
        if "steps" in s:
            return failure_of(s["steps"])
        for c in s.get("checks", []):
            if c["status"] != "pass":
                return f"{c['check']}: {c['text']} — {c['detail']}"
        return f"{s['step']} — {s.get('detail', '')}"
    return "failed"


def write_junit(path: Path, suite: str, results: dict):
    """JUnit XML: the format CI systems (GitHub Actions, GitLab, Jenkins, ...) display."""
    tests = results["tests"]
    root = ET.Element("testsuites")
    ts = ET.SubElement(root, "testsuite", name=suite, tests=str(len(tests)),
                       failures=str(results["failed"]), errors="0",
                       time=f"{sum(t['seconds'] for t in tests):.1f}")
    for t in tests:
        tc = ET.SubElement(ts, "testcase", classname=suite, name=t["name"], time=f"{t['seconds']:.1f}")
        log = "\n".join(t["log"])
        if t["status"] != "pass":
            ET.SubElement(tc, "failure", message=t["failure"]).text = log
        ET.SubElement(tc, "system-out").text = log
    ET.indent(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_report(out_dir: Path, meta: dict, results: dict, jev_calls: list):
    report = {**meta, **results, "jev_calls": jev_calls}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
