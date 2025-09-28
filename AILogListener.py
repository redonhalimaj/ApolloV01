# AILogListener.py
import os, re, json, platform, subprocess, shutil, traceback
from robot.api import logger
from ai_client import _chat  # your harmony-aware client

class AILogListener:
    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self, model=None, tags="AI_ANALYZE", max_chars="12000", context_file="ai_context.json"):
        self.model = model or os.getenv("OLLAMA_MODEL", "gpt-oss:20b-cloud")
        # tag gating (empty/* => analyze all)
        raw = (tags or "").strip()
        self.enabled_tags = set() if raw in ("", "*", "ALL", "all") else {t.strip().lower() for t in raw.split(",") if t.strip()}
        self.max_chars = int(max_chars)
        self.context_file = context_file

    def _should_analyze(self, result):
        if not self.enabled_tags:
            return True
        return bool({str(t).lower() for t in result.tags} & self.enabled_tags)

    def _parse_versions_from_message(self, message):
        exc = None; chrome = None
        if message:
            first = message.splitlines()[0].strip()
            m = re.match(r"([A-Za-z]+Exception)", first)
            if m: exc = m.group(1)
            m = re.search(r"chrome=([0-9.]+)", message)
            if m: chrome = m.group(1)
        return exc, chrome

    def _major(self, v):
        try: return int(str(v).split(".",1)[0])
        except: return None

    def _load_context(self):
        try:
            if os.path.exists(self.context_file):
                with open(self.context_file, "r") as f:
                    return json.load(f)
        except Exception:
            logger.debug("Could not load ai_context.json")
        return {}

    def end_test(self, data, result):
        try:
            if result.status != "FAIL" or not self._should_analyze(result):
                return

            ctx = self._load_context()
            exc, chrome_from_msg = self._parse_versions_from_message(result.message or "")

            # Extract versions/facts
            v = ctx.get("versions", {})
            chrome = chrome_from_msg or v.get("chrome")
            chromedriver = v.get("chromedriver")
            selenium = v.get("selenium")
            slib = v.get("seleniumlibrary")
            osplat = ctx.get("platform", {})
            caps = (ctx.get("selenium") or {}).get("capabilities")
            url = (ctx.get("selenium") or {}).get("url")
            title = (ctx.get("selenium") or {}).get("title")

            # Build rule flags
            majors_match = False
            mc, md = self._major(chrome), self._major(chromedriver)
            if mc and md and mc == md:
                majors_match = True

            # Deterministic human hint (before AI)
            if majors_match:
                logger.console(f"\n[HINT] Chrome {chrome} and ChromeDriver {chromedriver} majors match -> skip driver mismatch suggestions.\n")

            # Assemble concise FACTS
            facts = {
                "exception": exc,
                "chrome": chrome,
                "chromedriver": chromedriver,
                "majors_match": majors_match,
                "selenium": selenium,
                "seleniumlibrary": slib,
                "os": osplat,
                "url": url,
                "title": title,
                "caps_subset": caps,
                "extra": ctx.get("extra"),
            }

            # Tail the stacktrace
            tail = (result.message or "")
            if len(tail) > self.max_chars:
                tail = tail[-self.max_chars:]

            system_rules = (
                "You are a senior Selenium/Robot triager.\n"
                "- Use the FACTS as ground truth.\n"
                "- If majors_match == true, DO NOT propose Chrome/ChromeDriver mismatch.\n"
                "- Prefer concrete steps that apply to the given OS/versions.\n"
                "- If the wait is short (<=5s) and page is heavy, suggest raising it; "
                "if locator may be brittle, suggest a more robust locator pattern.\n"
                "- Return a short, bullet-pointed analysis."
            )

            messages = [
                {"role": "system", "content": system_rules},
                {"role": "user", "content": f"FACTS:\n{json.dumps(facts, indent=2)}\n\nSTACKTRACE_TAIL:\n{tail}"},
            ]
            analysis = _chat(messages, model=self.model, temperature=0.1)
            logger.console("\n=== AI Failure Analysis ===\n" + analysis + "\n===========================\n")
            logger.warn("AI analysis:\n" + analysis)

        except Exception as e:
            logger.warn("AI listener failed: " + repr(e))
            logger.debug(traceback.format_exc())
