import os
import traceback
from robot.api import logger
from ai_client import _chat
from robot.libraries.BuiltIn import BuiltIn


class AILogListener:
    """
    A Robot Framework listener that uses a language model to triage test failures.

    When a test ends with status ``FAIL`` and contains one of the configured tags
    (by default ``AI_ANALYZE``), this listener collects a concise context of the
    failure—including the test name, error message, elapsed time, tags, last few
    executed keywords and, if available, the current page URL, title and a
    truncated portion of the page source from Selenium.  It then sends this
    context to the AI client via ``_chat`` and logs the model’s analysis to the
    console and Robot Framework logs.

    Optional context engineering is supported via a history file: if the
    ``history_file`` argument (or the ``AI_HISTORY_FILE`` environment variable)
    is set, the listener reads previous failure contexts from the specified JSON
    file and includes up to ``history_limit`` of them in the prompt.  Each
    historical snippet is truncated to ``history_max_chars`` to preserve the
    prompt budget.  After generating the analysis, the listener appends the
    current failure context to the history file for future runs.  This allows
    the language model to see patterns across multiple executions and tailor
    its recommendations accordingly.

    An experimental OCR feature is also available.  When the ``enable_ocr``
    argument (or the ``AI_ENABLE_OCR`` environment variable) is truthy, the
    listener will attempt to capture a screenshot of the page using Selenium’s
    driver at the time of failure and extract text from it.  OCR is attempted
    via the ``pytesseract`` library if present or ``easyocr`` as a fallback.
    The extracted text (up to ``ocr_max_chars`` characters) is appended to the
    failure context so the language model can reason about what was visually
    displayed.  If no OCR library is available or if extraction fails, this
    feature gracefully degrades and no screenshot text is included.
    """

    ROBOT_LISTENER_API_VERSION = 3

    def __init__(
        self,
        model: str | None = None,
        tags: str = "AI_ANALYZE",
        max_chars: str = "4000",
        history_file: str | None = None,
        history_limit: str | None = None,
        history_max_chars: str | None = None,
        context_file: str | None = None,
        enable_ocr: str | None = None,
        ocr_max_chars: str | None = None,
    ) -> None:
        """
        Construct a new AI log listener.

        :param model: Name of the Ollama model to use.  Falls back to ``OLLAMA_MODEL``.
        :param tags: Comma‑separated list of tags that enable analysis.  If empty, all failures are analysed.
        :param max_chars: Maximum number of characters from the current failure context to send to the model.
        :param history_file: Optional path to a JSON file used to store previous failure contexts.  When provided,
            the listener reads prior failures from this file, injects them into the AI prompt, and appends new
            failures for future runs.  If omitted, history is not used.
        :param history_limit: Maximum number of historical entries to include in the prompt.  Defaults to 3.
        :param history_max_chars: Maximum number of characters from each history entry to include.  Defaults to 1000.
        :param context_file: Ignored.  Reserved for backward compatibility with older versions of this listener.
        :param enable_ocr: If truthy, attempt to extract text from a Selenium screenshot using an OCR library (pytesseract or EasyOCR).  Defaults to off.  The OCR result is added to the failure context.
        :param ocr_max_chars: Maximum number of characters from the OCR result to include.  Defaults to 500.
        """
        # Choose the model for AI analysis.  Fall back to ``OLLAMA_MODEL`` if not provided.
        self.model = model or os.getenv("OLLAMA_MODEL", "gpt-oss:20b-cloud")
        # Parse the enabled tags from a comma‑separated string.
        self.enabled_tags = set([t.strip() for t in (tags or "").split(",") if t.strip()])
        # Bound the length of the prompt sent to the model to avoid exceeding token limits.
        self.max_chars = int(max_chars)
        # History configuration.  If ``history_file`` is provided via the listener argument or environment
        # variable ``AI_HISTORY_FILE``, previous failures will be stored and re‑used.
        self.history_file: str | None = history_file or os.getenv("AI_HISTORY_FILE")
        # Default to including up to 3 previous failure contexts unless overridden.
        self.history_limit: int = int(history_limit or os.getenv("AI_HISTORY_LIMIT", "3"))
        # Bound the length of each historical context snippet.
        self.history_max_chars: int = int(history_max_chars or os.getenv("AI_HISTORY_MAX_CHARS", "1000"))

        # OCR configuration.  ``enable_ocr`` can be passed as any truthy string (e.g. "True", "1") to
        # attempt image-to-text extraction.  ``AI_ENABLE_OCR`` environment variable can also enable it.
        enable_ocr_val = enable_ocr or os.getenv("AI_ENABLE_OCR")
        self.enable_ocr: bool = str(enable_ocr_val).lower() in ("1", "true", "yes", "on") if enable_ocr_val is not None else False
        self.ocr_max_chars: int = int(ocr_max_chars or os.getenv("AI_OCR_MAX_CHARS", "500"))

    def _should_analyze(self, result) -> bool:
        """Return True if at least one of the test tags matches our enabled tags."""
        if not self.enabled_tags:
            return True
        return any(t in self.enabled_tags for t in result.tags)

    def end_test(self, data, result) -> None:
        """
        Invoked by Robot Framework at the end of each test.  If the test failed
        and matches the enabled tags, this method assembles a context string and
        asks the AI to analyse the failure.
        """
        try:
            # Skip if the test did not fail.
            if result.status != "FAIL":
                return
            # Skip if the test does not have a matching tag.
            if not self._should_analyze(result):
                return

            # Collect executed keyword steps.  Only keep the last 10 entries to
            # reduce prompt size and focus on the most recent actions.  Include
            # error messages from failed keywords when available to give the model
            # more detailed context.  For each step, we capture the keyword name,
            # arguments, status, and, if the keyword failed, its failure message.
            steps: list[str] = []
            for item in getattr(data, "body", []):
                kw = getattr(item, "kwname", None)
                args = getattr(item, "args", [])
                status = getattr(item, "status", "")
                if not kw:
                    continue
                # Attempt to get a failure message for the keyword.  Some keyword
                # items have a 'message' attribute (result.model.Keyword) that
                # contains the error text.  Truncate long messages to avoid
                # overwhelming the prompt.
                msg = None
                try:
                    candidate = getattr(item, "message", None)
                    if isinstance(candidate, str) and candidate.strip():
                        msg = candidate.strip()
                        # Only capture messages for failed keywords or if not PASS.
                        if status.upper() == "PASS":
                            msg = None
                except Exception:
                    msg = None
                # Build the step description.
                part = f"{kw}  {'  '.join(map(str, args))}  -> {status}"
                if msg:
                    # Truncate to 200 characters to keep things brief.
                    truncated = msg[:200] + ("..." if len(msg) > 200 else "")
                    part += f" (message: {truncated})"
                steps.append(part)
            steps = steps[-10:]

            # Attempt to retrieve Selenium context: current URL, page title and page source.
            url = None
            title = None
            page_source = None
            ocr_text = None
            try:
                sl = BuiltIn().get_library_instance("SeleniumLibrary")
                drv = sl.driver
                url = getattr(drv, "current_url", None)
                title = getattr(drv, "title", None)
                ps = getattr(drv, "page_source", None)
                # Truncate the page source to avoid sending the entire DOM.  Limit to 500 characters.
                if isinstance(ps, str):
                    page_source = ps[:500] + ("..." if len(ps) > 500 else "")

                # Attempt to capture a screenshot and run OCR if enabled.
                if self.enable_ocr:
                    try:
                        # Capture the screenshot as PNG bytes.  Selenium returns bytes when using get_screenshot_as_png.
                        png_data = drv.get_screenshot_as_png()
                        if png_data:
                            from PIL import Image
                            import io
                            image = Image.open(io.BytesIO(png_data))
                            text = None
                            # Try pytesseract first.
                            try:
                                import pytesseract
                                # pytesseract may throw if Tesseract is not installed.  Catch any exception.
                                try:
                                    text = pytesseract.image_to_string(image)
                                except Exception:
                                    text = None
                            except Exception:
                                # pytesseract not available.  Try easyocr next.
                                try:
                                    import easyocr
                                    # easyocr requires specifying languages.  Use English as default.
                                    reader = easyocr.Reader(["en"], gpu=False)
                                    result = reader.readtext(png_data, detail=0)
                                    # Join the detected strings.
                                    text = " ".join(result)
                                except Exception:
                                    text = None
                            if text:
                                # Truncate the OCR result.
                                ocr_text = text.strip()[: self.ocr_max_chars]
                    except Exception:
                        # Ignore OCR errors.  If OCR fails, ocr_text remains None.
                        ocr_text = None
            except Exception:
                # If Selenium isn't used in this suite or fails, ignore quietly.
                pass

            # Assemble the context lines for the current failure.
            lines: list[str] = [
                f"Test: {result.longname}",
                f"Error: {result.message}",
                f"Elapsed: {result.elapsedtime} ms",
                f"Tags: {', '.join(result.tags)}",
            ]
            if url:
                lines.append(f"URL: {url}")
            if title:
                lines.append(f"Title: {title}")
            if page_source:
                lines.append(f"Page source (truncated): {page_source}")
            if ocr_text:
                lines.append(f"Screenshot text (OCR): {ocr_text}")
            lines.append("Steps:")
            for s in steps:
                lines.append(f"- {s}")
            failure_text = "\n".join(lines)

            # Ensure the prompt stays within the configured character limit.
            failure_text = failure_text[-self.max_chars:]

            # Load previous failures from history if configured.
            history_context = ""
            if self.history_file:
                try:
                    if os.path.exists(self.history_file):
                        import json
                        with open(self.history_file, "r", encoding="utf-8") as f:
                            entries = json.load(f) or []
                        # Keep only the last ``history_limit`` entries.
                        entries = list(entries)[-self.history_limit :]
                        # Build a textual representation of history.  Each entry is separated by a blank line.
                        parts: list[str] = []
                        for entry in entries:
                            if not isinstance(entry, str):
                                continue
                            # Truncate each entry to keep the prompt manageable.
                            snippet = entry[-self.history_max_chars :]
                            parts.append(snippet)
                        if parts:
                            history_context = "\n\n".join(parts)
                except Exception:
                    # If the history file cannot be read or parsed, ignore silently.
                    history_context = ""

            # Prepare messages for the chat completion.  The system prompt instructs the
            # model to be concise and action‑oriented.  The user messages include prior
            # failure contexts (if any) followed by the current failure.
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an expert Robot Framework test failure triager. "
                        "Explain the most likely root cause, where to look in the system, and give 3 concrete next actions. "
                        "Be concise, bullet‑pointed. Avoid generic advice."
                    ),
                },
            ]
            # If we have history, add it as a separate user message to provide background.  We explicitly
            # label this as prior failure contexts so the model can leverage patterns.
            if history_context:
                messages.append(
                    {
                        "role": "user",
                        "content": f"Previous failure contexts (most recent first):\n{history_context}",
                    }
                )
            # Always append the current failure context as the last message.
            messages.append({"role": "user", "content": failure_text})

            # Invoke the AI chat.  Use a low temperature for deterministic output.
            analysis = _chat(messages, model=self.model, temperature=0.2)
            # Log the analysis to the console and Robot's logger.  The console log
            # helps when running tests from the command line; the logger warns highlight the AI output in log.html.
            logger.console("\n=== AI Failure Analysis ===\n" + analysis + "\n===========================\n")
            logger.warn("AI analysis:\n" + analysis)

            # Append the current failure context to the history file for future runs.
            if self.history_file:
                try:
                    import json
                    entries: list[str] = []
                    if os.path.exists(self.history_file):
                        with open(self.history_file, "r", encoding="utf-8") as f:
                            loaded = json.load(f)
                            if isinstance(loaded, list):
                                entries.extend(loaded)
                    # Append the new failure context and retain only the last ``history_limit`` entries for the next read.
                    entries.append(failure_text)
                    # Trim the stored history to avoid unbounded growth.  Keep up to 50 entries for long‑term recall.
                    entries = entries[-50:]
                    with open(self.history_file, "w", encoding="utf-8") as f:
                        json.dump(entries, f, indent=2, ensure_ascii=False)
                except Exception:
                    # Do not disrupt the test run if history persistence fails.
                    pass
        except Exception as e:
            # If anything goes wrong during analysis, log a warning and debug info.
            logger.warn("AI listener failed: " + repr(e))
            logger.debug(traceback.format_exc())