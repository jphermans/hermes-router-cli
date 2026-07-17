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
  * Fetches a tarball of the whole repo via codeload.github.com so all
    companion files (requirements.txt, config.yaml, smart_router/, ...)
    are present.
  * Verifies the tarball archive SHA-256 against an expected value when
    --sha=<hex> is supplied; otherwise just reports the computed digest.
  * Streams stdout live during the actual install.py run, so the user
    sees the colourised progress.
  * Has a sane default commit (main) but accepts --ref=<branch|tag|sha>
    for pinning.
  * Cleans up the temp directory no matter what.
  * Fails fast with exit-code propagation if anything goes wrong.
"""

import argparse
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

# Default config -- overridable via flags.
DEFAULT_REPO = "jphermans/hermes-router-cli"
DEFAULT_REF = "main"
ARCHIVE_URL = "https://codeload.github.com/{repo}/tar.gz/{ref}"


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
    ap.add_argument("--sha", default=None,
                    help="expected SHA-256 of the tarball bytes (hex, lowercase). "
                         "Skip for the latest commit, or pin to a specific commit.")
    ap.add_argument("--dry-run", action="store_true",
                    help="download + verify only; don't actually run install.py")
    ap.add_argument("--forward-args", action="append", default=[],
                    help="extra args forwarded to install.py (repeatable)")
    args = ap.parse_args(argv)

    url = ARCHIVE_URL.format(ref=args.ref, repo=args.repo)
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

    # Verify SHA-256 if requested. SHA is over the raw tarball bytes
    # so a determined user can compute it independently with sha256sum.
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
            f"⚠️  No --sha given; SHA-256 of tarball: {digest}")
        _print_status("dim",
            "    (to pin to a known version, re-run with --sha=<hex>)")

    # Quick sanity: must be a gzip-compressed tarball.
    if not code[:2] == b"\x1f\x8b":
        _print_status("err", "Fetched content doesn't look like a gzip archive.")
        return 4

    # Extract to a temp dir, find the inner repo directory, run install.py.
    tmp = tempfile.mkdtemp(prefix="hermes-router-bootstrap-")
    try:
        try:
            with tarfile.open(fileobj=io.BytesIO(code), mode="r:gz") as tf:
                # GitHub tarballs have a single top-level dir named
                # 'reponame-<short-sha>'. Extract everything into tmp.
                try:
                    tf.extractall(path=tmp, filter="data")  # Py 3.12+
                except (TypeError, ValueError):
                    # Older Python: filter kwarg not yet a strict string.
                    # Use the numeric equivalent or just no filter (we
                    # trust the source — this is GitHub's signed tarball).
                    try:
                        tf.extractall(path=tmp)
                    except TypeError:
                        # Python 3.12 strict filter rejects non-strings — fall back.
                        tf.extractall(path=tmp, filter=None)
        except Exception as e:
            _print_status("err", f"Failed to extract tarball: {e}")
            return 5

        # Find the extracted directory (top-level only).
        extracted = [p for p in os.listdir(tmp) if not p.startswith(".")]
        if len(extracted) != 1:
            _print_status("err",
                f"Expected single top-level dir in tarball, got: {extracted}")
            return 6
        project_dir = os.path.join(tmp, extracted[0])
        install_py = os.path.join(project_dir, "install.py")
        if not os.path.exists(install_py):
            _print_status("err", f"No install.py found in {project_dir}")
            return 6

        if args.dry_run:
            _print_status("info", "🏁 --dry-run set; not executing.")
            _print_status("info", f"   extracted to: {project_dir}")
            return 0

        # Run install.py live, forwarding stdout/stderr.
        cmd = [sys.executable, install_py, *args.forward_args]
        _print_status("info", f"🚀 Running: {' '.join(cmd)}")
        result = subprocess.run(cmd)
        return result.returncode
    finally:
        try:
            import shutil as _sh
            _sh.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
