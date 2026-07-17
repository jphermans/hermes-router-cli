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
    ap.add_argument("--prefix", default=".",
                    help="target directory for the extracted project (default: current dir)")
    # Everything after `--` is forwarded as a single shell-style argv list
    # to install.py. argparse's `nargs='+'` is too clunky for this.
    # Split argv at '--': everything before is for us, everything after
    # goes straight to install.py. If there's no '--', any unknown flags
    # parse_known_args catches are forwarded too.
    if "--" in argv:
        sep = argv.index("--")
        our_args = argv[:sep]
        forward = argv[sep + 1:]
    else:
        our_args = argv
        forward = []

    args, unknown = ap.parse_known_args(our_args)
    # Any `unknown` flags that the bootstrap doesn't recognise are
    # also forwarded to install.py (makes `--no-symlink` work without `--`).
    if unknown and not forward:
        forward = unknown

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

    # Extract to a temp directory, then move the inner repo to --prefix.
    tmp = tempfile.mkdtemp(prefix="hermes-router-bootstrap-")
    try:
        with tarfile.open(fileobj=io.BytesIO(code), mode="r:gz") as tf:
            try:
                tf.extractall(path=tmp, filter="data")  # Py 3.12+
            except (TypeError, ValueError):
                try:
                    tf.extractall(path=tmp)
                except TypeError:
                    tf.extractall(path=tmp, filter=None)
    except Exception as e:
        _print_status("err", f"Failed to extract tarball: {e}")
        try:
            import shutil as _sh; _sh.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        return 5

    # Find the extracted top-level dir (GitHub tarballs have one named
    # 'reponame-<short-sha>').
    extracted = [p for p in os.listdir(tmp) if not p.startswith(".")]
    if len(extracted) != 1:
        _print_status("err", f"Expected single top-level dir, got: {extracted}")
        try:
            import shutil as _sh; _sh.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        return 6
    project_dir = os.path.join(tmp, extracted[0])
    install_py = os.path.join(project_dir, "install.py")
    if not os.path.exists(install_py):
        _print_status("err", f"install.py not found in extracted {project_dir}")
        try: import shutil as _sh; _sh.rmtree(tmp, ignore_errors=True)
        except Exception: pass
        return 6

    # Move the extracted project to the requested prefix (default: current dir).
    prefix = os.path.abspath(args.prefix)
    parent = os.path.dirname(prefix)
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(prefix):
        # Don't blow away an existing directory — let the user manage conflicts.
        _print_status("err",
            f"--prefix directory already exists: {prefix}\n"
            "    Delete it first, or use a different --prefix.")
        try: import shutil as _sh; _sh.rmtree(tmp, ignore_errors=True)
        except Exception: pass
        return 7
    os.rename(project_dir, prefix)
    # Clean up the now-empty tmp (only the top-level dir's parent remains).
    try:
        import shutil as _sh; _sh.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass

    if args.dry_run:
        _print_status("info", "🏁 --dry-run set; not executing.")
        _print_status("info", f"   extracted + moved to: {prefix}")
        return 0

    # Run install.py from the permanent prefix.
    cmd = [sys.executable, os.path.join(prefix, "install.py"), *forward]
    _print_status("info", f"🚀 Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
