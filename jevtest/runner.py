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

    # --- helpers ------------------------------------------------------------
    def log(self, msg: str):
        print(msg, flush=True)

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
            elif not self.screen().app_running:
                self.driver.launch()
                self.expect_running = True
                self.settle()
        except DriverError as e:
            status = "fail"
            steps.append({"step": "(start app)", "status": "fail", "detail": str(e)})
            self.log(f"  ✗ could not start app: {e}")
        for step in test.steps if status == "pass" else []:
            result = self.run_step(step)
            steps.append(result)
            if result["status"] != "pass":
                status = "fail"
                result["screenshot"] = self.screenshot(f"FAIL_{test.name}")
                break
        took = round(time.monotonic() - started, 1)
        self.log(f"  {'PASS' if status == 'pass' else 'FAIL'} {test.name} ({took}s)")
        return {"name": test.name, "status": status, "seconds": took, "steps": steps}

    def run_step(self, step: Step) -> dict:
        started = time.monotonic()
        result = {"step": step.raw, "status": "pass", "decisions": []}
        try:
            detail = self.do(step, result["decisions"])
            if detail:
                result["detail"] = detail
            if self.expect_running and step.kind not in ("stop", "home", "open_url"):
                if not self.driver.screen().app_running:
                    raise StepFailed("The app is no longer running (crashed or closed)")
        except (StepFailed, DriverError, JevError) as e:
            result["status"] = "fail"
            result["detail"] = str(e)
        result["seconds"] = round(time.monotonic() - started, 1)
        mark = "✓" if result["status"] == "pass" else "✗"
        extra = f" — {result['detail']}" if result.get("detail") else ""
        self.log(f"  {mark} {step.title()} ({result['seconds']}s){extra}")
        for d in result["decisions"]:
            self.log(f"      → {d['did']}  (confidence {d['confidence']:.2f})")
        if not result["decisions"]:
            result.pop("decisions")
        return result

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
        elif k in ("see", "not_see"):
            want = k == "see"
            deadline = time.monotonic() + timeout
            while True:
                found = any(str(v).lower() in t.lower() for t in self.screen().texts())
                if found == want:
                    return None
                if time.monotonic() >= deadline:
                    raise StepFailed(f"'{v}' {'not found' if want else 'is still'} on screen")
                time.sleep(0.5)
        elif k == "expect":
            deadline = time.monotonic() + timeout
            while True:
                p = self.brain.check(str(v), self.screen())
                if p > self.s.threshold:
                    return f"Jev: true ({p:.2f})"
                if time.monotonic() >= deadline:
                    raise StepFailed(f"Jev says false ({p:.2f})")
                time.sleep(1.0)
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
            d.type_text(dec.text)
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


def write_report(out_dir: Path, meta: dict, results: dict, jev_calls: list):
    report = {**meta, **results, "jev_calls": jev_calls}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
