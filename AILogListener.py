# AILogListener.py
import os, traceback
from robot.api import logger
from ai_client import _chat

class AILogListener:
    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self, model=None, tags="AI_ANALYZE", max_chars="4000"):
        self.model = model or os.getenv("OLLAMA_MODEL", "gpt-oss:20b-cloud")
        self.enabled_tags = set([t.strip() for t in (tags or "").split(",") if t.strip()])
        self.max_chars = int(max_chars)

    def _should_analyze(self, result):
        if not self.enabled_tags:
            return True
        return any(t in self.enabled_tags for t in result.tags)

    def end_test(self, data, result):
        try:
            if result.status != "FAIL":
                return
            if not self._should_analyze(result):
                return

            # Gather minimal context: test name, message, steps
            steps = []
            for item in getattr(data, "body", []):
                kw = getattr(item, "kwname", None)
                args = getattr(item, "args", [])
                status = getattr(item, "status", "")
                if kw:
                    steps.append(f"{kw}  {'  '.join(map(str, args))}  -> {status}")

            failure_text = f"""
Test: {result.longname}
Error: {result.message}
Elapsed: {result.elapsedtime} ms
Tags: {', '.join(result.tags)}
Steps:
- """ + "\n- ".join(steps)

            failure_text = failure_text[-self.max_chars:]  # keep prompt bounded

            messages = [
                {"role":"system","content":
                 "You are an expert Robot Framework test failure triager. "
                 "Explain the most likely root cause, where to look in the system, and give 3 concrete next actions. "
                 "Be concise, bullet-pointed. Avoid generic advice."},
                {"role":"user","content": failure_text}
            ]
            analysis = _chat(messages, model=self.model, temperature=0.2)
            logger.console("\n=== AI Failure Analysis ===\n" + analysis + "\n===========================\n")
            logger.warn("AI analysis:\n" + analysis)  # also visible in log.html
        except Exception as e:
            logger.warn("AI listener failed: " + repr(e))
            logger.debug(traceback.format_exc())
