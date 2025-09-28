# AIContext.py
from robot.api.deco import library, keyword
from robot.libraries.BuiltIn import BuiltIn
import json, os, platform, shutil, subprocess, time, re

@library(scope="SUITE")
class AIContext:
    def __init__(self, file: str = "ai_context.json"):
        # default file in the working dir; override via Library args
        self.file = file
        self.extra = {}

    @keyword("Add AI Fact")
    def add_ai_fact(self, key: str, value):
        """Add arbitrary key/value you want the AI to see (e.g., vpn=off)."""
        self.extra[str(key)] = value

    @keyword("Write AI Context")
    def write_ai_context(self):
        sel_bits = self._selenium_bits()
        ctx = {
            "timestamp": time.time(),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "versions": {
                "selenium": self._pip_version("selenium"),
                "robotframework": self._pip_version("robotframework"),
                "seleniumlibrary": self._pip_version("robotframework-seleniumlibrary"),
                # Local host versions (may be None in Docker/remote runs)
                "chrome": self._chrome_version(),
                "chromedriver": self._chromedriver_version_cli(),
            },
            "selenium": sel_bits,  # includes capabilities & versions_from_caps
            "env": self._selected_env(["CI", "APP_ENV", "TZ", "LANG", "LC_ALL"]),
            "extra": self.extra,
        }

        # Prefer authoritative versions from Selenium capabilities (remote container)
        from_caps = (sel_bits or {}).get("versions_from_caps", {}) or {}
        if from_caps:
            ctx["versions"].update(from_caps)

        with open(self.file, "w") as f:
            json.dump(ctx, f, indent=2)
        return os.path.abspath(self.file)

    # -------- helpers --------
    def _pip_version(self, pkg):
        try:
            import importlib.metadata as im
            return im.version(pkg)
        except Exception:
            return None

    def _chromedriver_version_cli(self):
        exe = shutil.which("chromedriver")
        if not exe:
            return None
        try:
            out = subprocess.check_output([exe, "--version"], text=True, timeout=3)
            # "ChromeDriver 140.0.7246.0 (..)"
            for token in out.split():
                if token and token[0].isdigit():
                    return token
            return out.strip()
        except Exception:
            return None

    def _chrome_version(self):
        # macOS app path (host runs). In containers, this will likely be None.
        mac_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        candidates = [
            mac_path,
            shutil.which("google-chrome"),
            shutil.which("chrome"),
            shutil.which("chromium-browser"),
        ]
        for exe in candidates:
            if not exe:
                continue
            try:
                out = subprocess.check_output([exe, "--version"], text=True, timeout=3)
                # "Google Chrome 140.0.7339.208"
                for token in out.split():
                    if token and token[0].isdigit():
                        return token
                return out.strip()
            except Exception:
                continue
        return None

    def _selenium_bits(self):
        """
        Capture current URL/title and a slim subset of capabilities.
        Also derive versions_from_caps for chrome/chromedriver so we don't
        rely on host CLI when using remote Selenium containers.
        """
        info = {
            "url": None,
            "title": None,
            "capabilities": None,
            "versions_from_caps": {},
        }
        try:
            sl = BuiltIn().get_library_instance("SeleniumLibrary")
            drv = sl.driver
            info["url"] = getattr(drv, "current_url", None)
            info["title"] = getattr(drv, "title", None)
            caps = getattr(drv, "capabilities", None)
            # Small + safe subset (capabilities can be huge)
            if isinstance(caps, dict):
                keep = [
                    "browserName",
                    "browserVersion",
                    "platformName",
                    "acceptInsecureCerts",
                    "pageLoadStrategy",
                    "chromedriverVersion",
                    "goog:chromeOptions",
                ]
                info["capabilities"] = {k: caps.get(k) for k in keep if k in caps}

                # Extract versions directly from caps
                ver = {}
                ver["chrome"] = caps.get("browserVersion") or caps.get("version")
                cdv = caps.get("chromedriverVersion")
                if isinstance(cdv, str):
                    m = re.search(r"\d+(?:\.\d+){1,3}", cdv)
                    if m:
                        ver["chromedriver"] = m.group(0)
                info["versions_from_caps"] = {k: v for k, v in ver.items() if v}
        except Exception:
            pass
        return info

    def _selected_env(self, keys):
        """
        Collect a few env vars plus Docker/remote-specific facts so the listener
        can reason about where/how the test ran.
        """
        out = {}
        for k in keys:
            v = os.environ.get(k)
            if v is not None:
                out[k] = v

        # Helpful extras for AI:
        out["SELENIUM_REMOTE_URL"] = os.environ.get("SELENIUM_REMOTE_URL")
        out["OLLAMA_HOST"] = os.environ.get("OLLAMA_HOST")
        out["OLLAMA_MODEL"] = os.environ.get("OLLAMA_MODEL")

        # Docker detection: /.dockerenv is present in most containers
        in_docker = os.path.exists("/.dockerenv")
        # Fallback heuristic via cgroup (older Docker)
        if not in_docker:
            try:
                with open("/proc/1/cgroup", "r") as f:
                    in_docker = "docker" in f.read() or "containerd" in f.read()
            except Exception:
                pass
        out["IN_DOCKER"] = in_docker

        return out
