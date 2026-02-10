"""
Comprehensive leak test for nodriver browser sessions.

Runs multiple browser sessions in a Fedora Docker container and checks for:
  1. Pending asyncio tasks ("Task was destroyed but it is pending")
  2. Memory leaks (RSS growth across iterations)
  3. File descriptor leaks
  4. Zombie / orphan Chrome processes
  5. Temp directory leaks

Usage:
    python3 leak_test.py [--iterations N] [--url URL]
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import glob
import os
import signal
import subprocess
import sys
import tempfile
import time
import tracemalloc
import warnings
from io import StringIO
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_rss_kb() -> int:
    """Current process RSS in KB (Linux /proc)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        pass
    try:
        import psutil
        return psutil.Process().memory_info().rss // 1024
    except Exception:
        return 0


def count_open_fds() -> int:
    """Count open file descriptors for this process."""
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except Exception:
        return -1


def reap_zombies():
    """Reap any zombie child processes.

    When running as PID 1 in Docker we inherit orphaned Chrome children
    (e.g. chrome_crashpad_handler). We must call waitpid to clean them up.
    """
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
        except ChildProcessError:
            break


def count_chrome_processes() -> int:
    """Count live (non-zombie) Chrome/Chromium processes."""
    reap_zombies()
    try:
        # Use ps to get only running (non-zombie) chrome processes
        result = subprocess.run(
            ["ps", "-eo", "stat,comm"],
            capture_output=True, text=True
        )
        count = 0
        for line in result.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            stat, comm = parts
            # Skip zombie processes (stat starts with Z)
            if stat.startswith("Z"):
                continue
            if "chromium" in comm.lower() or "chrome" in comm.lower():
                count += 1
        return count
    except Exception:
        return 0


def count_nodriver_temp_dirs() -> int:
    """Count nodriver temp profile dirs in /tmp."""
    pattern = os.path.join(tempfile.gettempdir(), "nodriver_*")
    return len(glob.glob(pattern))


# ---------------------------------------------------------------------------
# Single session run
# ---------------------------------------------------------------------------

def run_single_session(url: str, headless: bool = True) -> Tuple[str, List[str]]:
    """
    Run a single browser session and return (html_length, warnings_list).
    Captures stderr to detect pending-task warnings.
    """
    import nodriver as uc

    old_stderr = sys.stderr
    captured = StringIO()
    sys.stderr = captured

    warning_list = []
    old_showwarning = warnings.showwarning

    def capture_warning(message, category, filename, lineno, file=None, line=None):
        warning_list.append(str(message))
        old_showwarning(message, category, filename, lineno, file, line)

    warnings.showwarning = capture_warning

    html_size = 0
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        async def _session():
            nonlocal html_size
            config = uc.Config(
                headless=headless,
                sandbox=False,
                browser_args=["--disable-gpu", "--disable-dev-shm-usage", "--no-first-run"],
            )
            browser = await uc.start(config)
            try:
                page = await browser.get(url)
                await asyncio.sleep(3)
                html = await page.get_content()
                html_size = len(html) if html else 0
            finally:
                await browser.aclose()

        loop.run_until_complete(_session())

        # Drain any remaining tasks
        try:
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
    finally:
        try:
            loop.close()
        except Exception:
            pass
        sys.stderr = old_stderr
        warnings.showwarning = old_showwarning

    # Reap zombies left by Chrome's child processes
    reap_zombies()

    stderr_output = captured.getvalue()
    pending_warnings = [
        line for line in stderr_output.splitlines()
        if "Task was destroyed but it is pending" in line
        or "task:" in line.strip()[:5]
    ]
    return str(html_size), pending_warnings


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="nodriver leak test")
    parser.add_argument("--iterations", "-n", type=int, default=3,
                        help="Number of browser sessions to run (default: 3)")
    parser.add_argument("--url", default="https://example.com",
                        help="URL to fetch (default: https://example.com)")
    args = parser.parse_args()

    print("=" * 70)
    print("  nodriver Leak Test — Fedora Docker")
    print("=" * 70)
    print(f"  URL:        {args.url}")
    print(f"  Iterations: {args.iterations}")
    print(f"  Python:     {sys.version}")
    print()

    # ── Pre-test baseline ────────────────────────────────────────────
    gc.collect()
    tracemalloc.start()

    baseline_rss = get_rss_kb()
    baseline_fds = count_open_fds()
    baseline_chrome = count_chrome_processes()
    baseline_temps = count_nodriver_temp_dirs()

    print(f"  Baseline RSS:      {baseline_rss:>8} KB")
    print(f"  Baseline FDs:      {baseline_fds:>8}")
    print(f"  Baseline Chrome:   {baseline_chrome:>8}")
    print(f"  Baseline TempDirs: {baseline_temps:>8}")
    print("-" * 70)

    # ── Run iterations ────────────────────────────────────────────────
    rss_samples = [baseline_rss]
    total_pending_warnings = []
    all_passed = True

    for i in range(1, args.iterations + 1):
        print(f"\n  ▶ Iteration {i}/{args.iterations}")

        html_size, pending_warnings = run_single_session(args.url)

        gc.collect()
        current_rss = get_rss_kb()
        current_fds = count_open_fds()
        current_chrome = count_chrome_processes()
        current_temps = count_nodriver_temp_dirs()

        rss_samples.append(current_rss)

        print(f"    HTML size:   {html_size:>10} bytes")
        print(f"    RSS:         {current_rss:>8} KB  (Δ {current_rss - baseline_rss:+d} KB)")
        print(f"    FDs:         {current_fds:>8}     (Δ {current_fds - baseline_fds:+d})")
        print(f"    Chrome procs:{current_chrome:>8}")
        print(f"    Temp dirs:   {current_temps:>8}")
        print(f"    Pending task warnings: {len(pending_warnings)}")

        if pending_warnings:
            total_pending_warnings.extend(pending_warnings)
            for w in pending_warnings:
                print(f"      ⚠  {w}")

    # ── Post-test analysis ────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)

    final_rss = get_rss_kb()
    final_fds = count_open_fds()
    final_chrome = count_chrome_processes()
    final_temps = count_nodriver_temp_dirs()

    # tracemalloc snapshot
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics("lineno")

    # 1. Pending task warnings
    print(f"\n  ❶ Pending Task Warnings: {len(total_pending_warnings)}")
    if total_pending_warnings:
        print("    ✘ FAIL — pending tasks detected:")
        for w in total_pending_warnings[:10]:
            print(f"      {w}")
        all_passed = False
    else:
        print("    ✔ PASS — no pending task warnings")

    # 2. Memory growth
    # Use growth between iter 1 and the final iteration to exclude startup overhead.
    if len(rss_samples) >= 3:
        incremental_growth = rss_samples[-1] - rss_samples[1]
        incremental_iters = max(args.iterations - 1, 1)
    else:
        incremental_growth = final_rss - baseline_rss
        incremental_iters = max(args.iterations, 1)
    growth_per_iter = incremental_growth / incremental_iters
    # Allow up to 5 MB growth per iteration (beyond the first)
    mem_threshold_kb = 5 * 1024
    print(f"\n  ❷ Memory (RSS)")
    print(f"    Baseline:           {baseline_rss:>8} KB")
    print(f"    After 1st iter:     {rss_samples[1] if len(rss_samples) > 1 else 'N/A':>8} KB")
    print(f"    Final:              {final_rss:>8} KB")
    print(f"    Incremental growth: {incremental_growth:>+8} KB  (iter 2..{args.iterations})")
    print(f"    Per iteration:      {growth_per_iter:>+8.0f} KB")
    if growth_per_iter > mem_threshold_kb:
        print(f"    ✘ FAIL — growth exceeds {mem_threshold_kb} KB/iter threshold")
        all_passed = False
    else:
        print(f"    ✔ PASS — within {mem_threshold_kb} KB/iter threshold")

    # 3. File descriptor leaks
    fd_growth = final_fds - baseline_fds
    print(f"\n  ❸ File Descriptors")
    print(f"    Baseline: {baseline_fds:>4}    Final: {final_fds:>4}    Δ {fd_growth:+d}")
    if fd_growth > 5:
        print("    ✘ FAIL — FD leak detected")
        all_passed = False
    else:
        print("    ✔ PASS — no significant FD leaks")

    # 4. Orphan Chrome processes
    print(f"\n  ❹ Orphan Chrome Processes: {final_chrome}")
    if final_chrome > 0:
        print("    ✘ FAIL — orphan Chrome processes still running")
        all_passed = False
    else:
        print("    ✔ PASS — no orphan processes")

    # 5. Temp directory leaks
    temp_growth = final_temps - baseline_temps
    print(f"\n  ❺ Temp Dirs")
    print(f"    Baseline: {baseline_temps:>4}    Final: {final_temps:>4}    Δ {temp_growth:+d}")
    if temp_growth > 0:
        print("    ✘ FAIL — temp directories leaked")
        all_passed = False
    else:
        print("    ✔ PASS — no temp directory leaks")

    # 6. Top memory allocations (informational)
    print(f"\n  ❻ Top Memory Allocations (tracemalloc)")
    for stat in top_stats[:5]:
        print(f"    {stat}")

    # ── Final verdict ─────────────────────────────────────────────────
    print()
    print("=" * 70)
    if all_passed:
        print("  ✅  ALL CHECKS PASSED")
    else:
        print("  ❌  SOME CHECKS FAILED — see above")
    print("=" * 70)

    tracemalloc.stop()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
