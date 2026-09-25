"""Runs the tests in a spec, step by step, against one device."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .brain import Brain, Decision
from .drivers.base import Driver, DriverError
from .jev import JevError
from .screen import Screen
from .spec import Spec, Step, Test


class StepFailed(Exception):
    pass


def _on_off(value) -> bool:
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ("on", "true", "yes", "1", "dark", "enable", "enabled"):
        return True
    if v in ("off", "false", "no", "0", "light", "disable", "disabled"):
        return False
    raise StepFailed(f"Expected on/off, got {value!r}")


class Runner:
    def __init__(self, spec: Spec, driver: Driver, brain: Brain, out_dir: Path, verbose: bool = False):
        self.spec = spec
        self.s = spec.settings
        self.driver = driver
        self.brain = brain
        self.out = out_dir
        self.verbose = verbose
        self.expect_running = False
        self.shot_n = 0
        self.lines: list[str] = []

    # --- helpers ------------------------------------------------------------
    def log(self, msg: str):
        print(msg, flush=True)
        self.lines.append(msg)

    def log_jev(self, pad: str, since: int):
        """-v: print every Jev call made since index `since` (question, answer, time)."""
        if not self.verbose:
            return
        for call in self.brain.jev.calls[since:]:
            for qid, ans in (call.get("answers") or {}).items():
                if ans.get("type") == "noul":
                    got = f"yes={ans['noul']:.2f}"
                else:
                    top = sorted(ans.get("probabilities", {}).items(), key=lambda kv: -kv[1])[:3]
                    got = f"{ans.get('choice')}  [" + ", ".join(f"{k} {v:.2f}" for k, v in top) + "]"
                self.log(f"{pad}    jev {qid}: {got}")
            self.log(f"{pad}    jev call {call['ms']} ms, {len(call['questions'])} question(s), "
                     f"{(call.get('usage') or {}).get('input_tokens', '?')} tokens")

    def settle(self):
        time.sleep(self.s.settle)

    def screen(self) -> Screen:
        return self.driver.screen()

    def screenshot(self, name: str) -> str:
        self.shot_n += 1
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name)[:60].strip("_") or "screen"
        path = self.out / f"{self.shot_n:03d}_{safe}.png"
        try:
            self.driver.screenshot(path)
        except DriverError as e:
            return f"(screenshot failed: {e})"
        return path.name

    def locate(self, target: str, timeout: float):
        """Poll until Jev finds `target` on screen or time runs out."""
        deadline = time.monotonic() + timeout
        while True:
            screen = self.screen()
            el = self.brain.locate(str(target), screen)
            if el is not None:
                return el, screen
            if time.monotonic() >= deadline:
                raise StepFailed(f"Could not find '{target}' on screen")
            time.sleep(0.5)

    # --- tests ----------------------------------------------------------------
    def run(self) -> dict:
        results = []
        for test in self.spec.tests:
            results.append(self.run_test(test))
        passed = sum(r["status"] == "pass" for r in results)
        return {"passed": passed, "failed": len(results) - passed, "tests": results}

    def run_test(self, test: Test) -> dict:
        self.lines = []
        self.log(f"\n▶ {test.name}")
        started = time.monotonic()
        steps, status = [], "pass"
        try:
            if test.fresh:
                self.driver.stop()
                self.driver.clear_data()
                self.driver.launch()
                self.expect_running = True
                self.settle()
            elif self.driver.app_state() != "foreground":
                self.driver.launch()
                self.expect_running = True
                self.settle()
        except DriverError as e:
            status = "fail"
            steps.append({"step": "(start app)", "status": "fail", "detail": str(e)})
            self.log(f"  ✗ could not start app: {e}")
        if status == "pass":
            status = self.run_steps(test.steps, steps, "  ")
            if status != "pass":
                steps[-1]["screenshot"] = self.screenshot(f"FAIL_{test.name}")
        took = round(time.monotonic() - started, 1)
        self.log(f"  {'PASS' if status == 'pass' else 'FAIL'} {test.name} ({took}s)")
        failure = self.failure_of(steps) if status != "pass" else None
        return {"name": test.name, "status": status, "seconds": took, "failure": failure,
                "steps": steps, "log": list(self.lines)}

    @staticmethod
    def failure_of(steps: list) -> str:
        """Plain-English reason for the first failure (for CI)."""
        for s in steps:
            if s.get("status") == "pass":
                continue
            if "steps" in s:
                return Runner.failure_of(s["steps"])
            for c in s.get("checks", []):
                if c["status"] != "pass":
                    return f"{c['check']}: {c['text']} — {c.get('detail', '')}"
            return f"{s['step']} — {s.get('detail', '')}"
        return "failed"

    def run_steps(self, steps: list[Step], results: list, pad: str) -> str:
        for step in steps:
            result = self.run_step(step, pad)
            results.append(result)
            if result["status"] != "pass":
                return "fail"
        return "pass"

    def run_step(self, step: Step, pad: str) -> dict:
        started = time.monotonic()
        result = {"step": step.raw, "status": "pass"}
        jev_mark = len(self.brain.jev.calls)
        if step.kind == "use":
            self.log(f"{pad}▸ use: {step.used.name}")
            inner = []
            result["steps"] = inner
            result["status"] = self.run_steps(step.used.steps, inner, pad + "  ")
        elif step.kind is not None:
            decisions = []
            try:
                detail = self.do(step, decisions)
                if detail:
                    result["detail"] = detail
                self.check_app(step)
            except (StepFailed, DriverError, JevError) as e:
                result["status"] = "fail"
                result["detail"] = str(e)
            if decisions:
                result["decisions"] = decisions
            secs = round(time.monotonic() - started, 1)
            mark = "✓" if result["status"] == "pass" else "✗"
            extra = f" — {result['detail']}" if result.get("detail") else ""
            self.log(f"{pad}{mark} {step.title()} ({secs}s){extra}")
            for d in decisions:
                self.log(f"{pad}    → {d['did']}  (confidence {d['confidence']:.2f})")
            self.log_jev(pad, jev_mark)

        # Checks run after the action, indented under it.
        check_pad = pad + "    " if step.kind is not None else pad
        result["checks"] = []
        for kind, text in step.checks if result["status"] == "pass" else []:
            jev_mark = len(self.brain.jev.calls)
            c = {"check": kind, "text": text, "status": "pass"}
            try:
                c["detail"] = self.check(kind, text, float(step.opts.get("timeout", self.s.timeout)))
            except (StepFailed, DriverError, JevError) as e:
                c["status"], c["detail"] = "fail", str(e)
            result["checks"].append(c)
            mark = "✓" if c["status"] == "pass" else "✗"
            extra = f" — {c['detail']}" if c.get("detail") else ""
            self.log(f"{check_pad}{mark} {kind}: {text}{extra}")
            self.log_jev(check_pad, jev_mark)
            if c["status"] != "pass":
                result["status"] = "fail"
                break
        if not result["checks"]:
            result.pop("checks")
        result["seconds"] = round(time.monotonic() - started, 1)
        return result

    def check_app(self, step: Step):
        """Fail if the app crashed or left the foreground during the action."""
        if not self.expect_running or step.kind in ("stop", "home", "open_url"):
            return
        state = self.driver.app_state()
        if state == "not_running":
            raise StepFailed("The app is no longer running (crashed or closed)")
        if state == "background":
            raise StepFailed("The app left the foreground")

    def check(self, kind: str, text: str, timeout: float) -> str | None:
        """expect: Jev judges the statement. see/not_see: exact text. Retries until timeout."""
        deadline = time.monotonic() + timeout
        while True:
            if kind == "expect":
                p = self.brain.check(text, self.screen())
                if p > self.s.threshold:
                    return f"Jev {p:.2f}"
                failure = f"Jev says false ({p:.2f})"
            else:
                found = any(text.lower() in t.lower() for t in self.screen().texts())
                if found == (kind == "see"):
                    return None
                failure = "not on screen" if kind == "see" else "still on screen"
            if time.monotonic() >= deadline:
                raise StepFailed(failure)
            time.sleep(0.5)

    # --- steps ----------------------------------------------------------------
    def do(self, step: Step, decisions: list) -> str | None:
        d, v, o = self.driver, step.value, step.opts
        timeout = float(o.get("timeout", self.s.timeout))
        k = step.kind

        if k == "launch":
            d.launch(); self.expect_running = True
        elif k == "stop":
            d.stop(); self.expect_running = False; return None
        elif k == "restart":
            d.stop(); d.launch(); self.expect_running = True
        elif k == "clear_data":
            d.stop(); d.clear_data(); self.expect_running = False; return None
        elif k == "reinstall":
            d.reinstall(); self.expect_running = False; return None
        elif k == "back":
            d.back()
        elif k == "home":
            d.home(); return None
        elif k == "hide_keyboard":
            d.hide_keyboard()
        elif k == "wait":
            time.sleep(float(v)); return None
        elif k == "key":
            d.key(str(v))
        elif k == "scroll":
            d.scroll(str(v))
        elif k == "swipe":
            if "target" in o:
                el, _ = self.locate(o["target"], timeout)
                d.swipe(str(v or o.get("direction", "left")), el=el)
            else:
                d.swipe(str(v))
        elif k == "scroll_to":
            direction = o.get("direction", "down")
            for _ in range(int(o.get("max_scrolls", 15))):
                screen = self.screen()
                if self.brain.locate(str(v), screen) is not None:
                    return None
                d.scroll(direction, screen=screen)
                self.settle()
            raise StepFailed(f"Scrolled {direction} but never found '{v}'")
        elif k in ("tap", "double_tap", "long_press"):
            el, _ = self.locate(v, timeout)
            getattr(d, k)(*el.center)
            self.settle()
            return f"on {el.label()}"
        elif k == "clear":
            el, screen = self.locate(v, timeout)
            d.clear_text(el)
        elif k == "type":
            text = str(o.get("text", v if v is not None else ""))
            if "into" in o:
                deadline = time.monotonic() + timeout
                while True:
                    screen = self.screen()
                    el = self.brain.locate(str(o["into"]), screen, candidates=screen.editable)
                    if el or time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
                if el is None:
                    raise StepFailed(f"Could not find text field '{o['into']}'")
                d.tap(*el.center)
                time.sleep(0.4)
                d.type_text(text, at=el.center)
            else:
                d.type_text(text)
        elif k == "rotate":
            d.rotate(str(v))
        elif k == "location":
            lat, lon = (v if isinstance(v, (list, tuple)) else str(v).split(","))
            d.set_location(float(lat), float(lon))
        elif k == "open_url":
            d.open_url(str(v))
        elif k == "screenshot":
            return f"saved {self.screenshot(str(v))}"
        elif k == "background":
            d.home(); time.sleep(float(v)); d.resume()
        elif k == "dark_mode":
            d.dark_mode(_on_off(v))
        elif k == "grant":
            d.grant(str(v))
        elif k == "network":
            d.network(_on_off(v))
        elif k == "do":
            return self.achieve(str(v), int(o.get("max_actions", self.s.max_actions)), decisions)
        else:
            raise StepFailed(f"Unknown step '{k}'")
        self.settle()
        return None

    def achieve(self, goal: str, max_actions: int, decisions: list) -> str:
        """The Jev loop: look at the screen, pick the next action, do it, repeat."""
        taken: list[str] = []
        for _ in range(max_actions + 1):
            screen = self.screen()
            dec = self.brain.next_action(goal, screen, taken)
            decisions.append({"did": dec.describe(), "confidence": dec.confidence,
                              "probabilities": dec.probabilities})
            if dec.action == "done":
                return f"{len(taken)} action(s)"
            if dec.action == "impossible":
                raise StepFailed("Jev says the goal is impossible from this screen")
            if len(taken) >= max_actions:
                break
            if taken[-2:] == [dec.describe()] * 2:
                raise StepFailed(f"Stuck repeating: {dec.describe()}")
            self.perform(dec, screen)
            taken.append(dec.describe())
            self.settle()
        raise StepFailed(f"Goal not reached after {max_actions} actions")

    def perform(self, dec: Decision, screen: Screen):
        d, el = self.driver, dec.element
        a = dec.action
        if a in ("tap", "double_tap", "long_press"):
            getattr(d, a)(*el.center)
        elif a == "swipe_left_on":
            d.swipe("left", el=el)
        elif a == "swipe_right_on":
            d.swipe("right", el=el)
        elif a == "type":
            if not el.focused:
                d.tap(*el.center)
                time.sleep(0.4)
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
        elif a == "wait":
            time.sleep(1.0)


def write_junit(path: Path, suite: str, results: dict):
    """JUnit XML: the format CI systems (GitHub Actions, GitLab, Jenkins, ...) display."""
    from xml.etree import ElementTree as ET
    tests = results["tests"]
    root = ET.Element("testsuites")
    ts = ET.SubElement(root, "testsuite", name=suite, tests=str(len(tests)),
                       failures=str(results["failed"]), errors="0",
                       time=f"{sum(t['seconds'] for t in tests):.1f}")
    for t in tests:
        tc = ET.SubElement(ts, "testcase", classname=suite, name=t["name"], time=f"{t['seconds']:.1f}")
        if t["status"] != "pass":
            ET.SubElement(tc, "failure", message=t.get("failure") or "failed").text = "\n".join(t["log"])
        ET.SubElement(tc, "system-out").text = "\n".join(t["log"])
    ET.indent(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_report(out_dir: Path, meta: dict, results: dict, jev_calls: list):
    report = {**meta, **results, "jev_calls": jev_calls}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
