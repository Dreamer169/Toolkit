#!/usr/bin/env python3
"""
patch_pydoll_bypass.py -- Apply OOPIF-aware Cloudflare Turnstile bypass patch to pydoll

Root cause (2025-05):
  pydoll 2.22.1 _bypass_cloudflare uses shadow root traversal:
    page shadow root -> CF iframe -> iframe body -> body.get_shadow_root() -> span.cb-i click
  This FAILS because Chrome runs cross-origin CF iframes as OOPIFs (Out-of-Process IFrames).
  body.get_shadow_root() cannot traverse the OOPIF boundary via the main tab's CDP connection.

Fix:
  Method 1 (OOPIF-aware): find challenges.cloudflare.com in browser's CDP target list,
    connect to it as a separate Tab, and click span.cb-i via JS directly in that context.
  Method 2 (fallback): original shadow root traversal (kept for non-OOPIF environments).

Usage:
  python3 scripts/patch_pydoll_bypass.py
  # Idempotent: safe to re-run after pydoll reinstall
"""
import sys
import glob

def find_tab_py():
    candidates = [
        "/data/python3.10-lib/dist-packages/pydoll/browser/tab.py",
        "/usr/lib/python3/dist-packages/pydoll/browser/tab.py",
    ]
    candidates += glob.glob("/usr/local/lib/python3.*/dist-packages/pydoll/browser/tab.py")
    candidates += glob.glob("/root/.local/lib/python3.*/site-packages/pydoll/browser/tab.py")
    for p in candidates:
        try:
            open(p).close()
            return p
        except FileNotFoundError:
            pass
    return None

def main():
    tab_py = find_tab_py()
    if not tab_py:
        print("ERROR: pydoll tab.py not found", file=sys.stderr)
        sys.exit(1)

    with open(tab_py) as f:
        lines = f.readlines()

    # Already patched?
    content = "".join(lines)
    if "Method 1: OOPIF-aware bypass" in content:
        print(f"ALREADY PATCHED: {tab_py}")
        return

    # Find _bypass_cloudflare method bounds
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if "    async def _bypass_cloudflare(" in line:
            start_idx = i
        if start_idx is not None and i > start_idx + 2:
            if line.strip() and (line.startswith("    async def ") or line.startswith("    def ") or line.startswith("class ")):
                end_idx = i
                break

    if start_idx is None or end_idx is None:
        print(f"ERROR: cannot find _bypass_cloudflare in {tab_py}", file=sys.stderr)
        sys.exit(1)

    print(f"Found _bypass_cloudflare at lines {start_idx+1}..{end_idx} in {tab_py}")

    new_block = [
        "    async def _bypass_cloudflare(\n",
        "        self,\n",
        "        event: dict,\n",
        "        time_to_wait_captcha: float = 5,\n",
        "    ) -> None:\n",
        '        """Bypass CF Turnstile (patched v2: OOPIF-aware + shadow-root fallback).\n',
        "\n",
        "        Method 1: OOPIF-aware -- access challenges.cloudflare.com as a CDP target\n",
        "                  and click span.cb-i via JS (handles Chrome OOPIF iframes).\n",
        "        Method 2: original shadow root traversal (fallback for non-OOPIF).\n",
        '        """\n',
        "        try:\n",
        "            timeout_int = int(time_to_wait_captcha)\n",
        "\n",
        "            # Method 1: OOPIF-aware bypass\n",
        "            try:\n",
        "                import asyncio as _aio\n",
        "                _loop = _aio.get_event_loop()\n",
        "                _deadline = _loop.time() + time_to_wait_captcha\n",
        "                _cf_target = None\n",
        "                while _loop.time() < _deadline:\n",
        "                    _targets = await self._browser.get_targets()\n",
        "                    for _t in _targets:\n",
        "                        if 'challenges.cloudflare.com' in _t.get('url', ''):\n",
        "                            _cf_target = _t\n",
        "                            break\n",
        "                    if _cf_target:\n",
        "                        break\n",
        "                    await _aio.sleep(0.5)\n",
        "                if _cf_target:\n",
        "                    _tid = _cf_target['targetId']\n",
        "                    if _tid in self._browser._tabs_opened:\n",
        "                        _cf_tab = self._browser._tabs_opened[_tid]\n",
        "                    else:\n",
        "                        _cf_tab = Tab(\n",
        "                            self._browser,\n",
        "                            target_id=_tid,\n",
        "                            connection_port=self._connection_port,\n",
        "                        )\n",
        "                        self._browser._tabs_opened[_tid] = _cf_tab\n",
        "                    _steps = max(4, int(max(2.0, _deadline - _loop.time()) * 2))\n",
        "                    for _ in range(_steps):\n",
        "                        try:\n",
        "                            _jscode = (\"var cb=document.querySelector('span.cb-i');\"\n",
        "                                       \"if(cb){cb.click();return 'clicked';}\"\n",
        "                                       \"return document.body?'loaded':'wait';\")\n",
        "                            _res = await _cf_tab.execute_script(_jscode, return_by_value=True)\n",
        "                            _r = _res if isinstance(_res, dict) else {}\n",
        "                            _inner = _r.get('result', _r)\n",
        "                            _val = str(_inner.get('value', '')) if isinstance(_inner, dict) else ''\n",
        "                            if _val == 'clicked':\n",
        "                                logger.debug('[bypass] oopif span.cb-i click OK')\n",
        "                                return\n",
        "                        except Exception:\n",
        "                            pass\n",
        "                        await _aio.sleep(0.5)\n",
        "                    logger.warning('[bypass] oopif found but cb-i timed out; trying shadow root')\n",
        "                else:\n",
        "                    logger.warning('[bypass] no CF oopif target; trying shadow root')\n",
        "            except Exception as _e1:\n",
        "                logger.warning(f'[bypass] oopif error: {_e1}; trying shadow root')\n",
        "\n",
        "            # Method 2: original shadow root traversal fallback\n",
        "            shadow_root = await self._find_cloudflare_shadow_root(\n",
        "                timeout=time_to_wait_captcha,\n",
        "            )\n",
        "            iframe = await shadow_root.query(_CLOUDFLARE_IFRAME_SELECTOR, timeout=timeout_int)\n",
        "            body = await iframe.find(tag_name='body', timeout=timeout_int)\n",
        "            inner_shadow = await body.get_shadow_root(timeout=time_to_wait_captcha)\n",
        "            checkbox = await inner_shadow.query(_CLOUDFLARE_CHECKBOX_SELECTOR, timeout=timeout_int)\n",
        "            await checkbox.click()\n",
        "        except Exception as exc:\n",
        "            logger.error(f'Error in cloudflare bypass: {exc}')\n",
        "\n",
    ]

    import shutil
    shutil.copy2(tab_py, tab_py + ".bak_pre_oopif_patch")
    lines[start_idx:end_idx] = new_block
    with open(tab_py, "w") as f:
        f.writelines(lines)

    # Verify
    import py_compile
    try:
        py_compile.compile(tab_py, doraise=True)
        print(f"PATCH OK + SYNTAX OK: {tab_py}")
        print(f"Backup: {tab_py}.bak_pre_oopif_patch")
    except py_compile.PyCompileError as e:
        print(f"SYNTAX ERROR after patch: {e}", file=sys.stderr)
        shutil.copy2(tab_py + ".bak_pre_oopif_patch", tab_py)
        print("REVERTED from backup")
        sys.exit(1)

if __name__ == "__main__":
    main()
