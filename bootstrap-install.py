#!/usr/bin/env python3
"""Bootstrap installer for hermes-router.

Why this exists:
  A single, copy-pasteable one-liner to install hermes-router without
  needing git clone first:

    python3 -c "$(curl -fsSL https://raw.githubusercontent.com/jphermans/hermes-router-cli/main/bootstrap-install.py)"

  Then run:
    python3 install.py install

What's special about this bootstrap:
  * Pure Python (stdlib only) -- no shell, no `curl | bash`.
  * Streams stdout live during the actual install.py run, so the user
    sees the colourised progress.
  * Verifies the downloaded install.py against an expected SHA-256
    when --sha=<hex> is supplied.
  * Has a sane default commit (main) but accepts --ref=<branch|tag|sha>
    for pinning.
  * Cleans up the temp file no matter what.
  * Fails fast with exit-code propagation if anything goes wrong.
"""

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

# Default config -- overridable via flags.
DEFAULT_REPO = "jphermans/hermes-router-cli"
DEFAULT_REF = "main"
DEFAULT_PATH = "install.py"
RAW_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"


def _fetch(url):
    """Stream a URL to bytes. Raises urllib.error on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-router-bootstrap/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _print_status(symbol: str, text: str):
    """Cheap coloured stderr status (stderr only — doesn't pollute curl output)."""
    colours = {
        "ok": "\033[32m",        # green
        "warn": "\033[33m",      # yellow
        "err": "\033[31m",        # red
        "info": "\033[36m",       # cyan
        "dim": "\033[2m",
    }
    reset = "\033[0m"
    sys.stderr.write(f"  {colours.get(symbol, '')}{text}{reset}\n")


def main(argv):
    ap = argparse.ArgumentParser(
        prog="bootstrap-install",
        description="Download + verify + run hermes-router's install.py.",
    )
    ap.add_argument("--ref", default=DEFAULT_REF,
                    help="git ref to fetch from (default: main)")
    ap.add_argument("--repo", default=DEFAULT_REPO,
                    help=f"github 'owner/repo' (default: {DEFAULT_REPO})")
    ap.add_argument("--path", default=DEFAULT_PATH,
                    help=f"path inside repo (default: {DEFAULT_PATH})")
    ap.add_argument("--sha", default=None,
                    help="expected SHA-256 of install.py (hex, lowercase). "
                         "Skip for the latest commit, or pin to a specific commit.")
    ap.add_argument("--dry-run", action="store_true",
                    help="download + verify only; don't actually run install.py")
    args = ap.parse_args(argv)

    url = RAW_URL.format(ref=args.ref, repo=args.repo, path=args.path,
                          owner=args.repo.split("/")[0])
    _print_status("info", f"📡 Fetching {url}")

    try:
        code = _fetch(url)
    except urllib.error.HTTPError as e:
        _print_status("err", f"HTTP {e.code} fetching {url}: {e.reason}")
        return 2
    except urllib.error.URLError as e:
        _print_status("err", f"Network error: {e.reason}")
        return 2
    except Exception as e:
        _print_status("err", f"Failed to fetch: {type(e).__name__}: {e}")
        return 2

    _print_status("info", f"📦 Downloaded {len(code):,} bytes")

    # Verify SHA-256 if requested.
    digest = hashlib.sha256(code).hexdigest()
    if args.sha:
        if digest.lower() != args.sha.lower():
            _print_status("err",
                f"SHA-256 mismatch!\n"
                f"   expected: {args.sha}\n"
                f"   actual:   {digest}")
            return 3
        _print_status("ok", f"🔒 SHA-256 verified ({digest[:12]}…)")
    else:
        _print_status("warn",
            f"⚠️  No --sha given; SHA-256 of fetched file: {digest}")
        _print_status("dim",
            "    (to pin to a known version, re-run with --sha=<hex>)")

    # Quick sanity: must look like Python.
    head = code[:200].decode("utf-8", errors="replace")
    if "python" not in head and "import" not in head:
        _print_status("err", "Fetched content doesn't look like a Python file.")
        return 4

    # Run from a temp file so Python's argv[0] is meaningful.
    tmp = tempfile.NamedTemporaryFile(
        prefix="hermes-router-install-",
        suffix=".py", mode="wb", delete=False,
    )
    try:
        tmp.write(code)
        tmp.flush()
        tmp.close()
        if args.dry_run:
            _print_status("info", "🏁 --dry-run set; not executing.")
            _print_status("info", f"   saved to: {tmp.name}")
            return 0
        _print_status("info", f"🚀 Running {tmp.name} install (forwarding your flags)…")
        # Forward every argv after the script path as the install's argv.
        proc_argv = [sys.executable, tmp.name] + argv
        # Don't capture stdout: let the colourised install output stream live.
        result = subprocess.run(proc_argv, stdin=sys.stdin)
        return result.returncode
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
