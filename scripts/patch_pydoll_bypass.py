#!/usr/bin/env python3
"""
patch_pydoll_bypass.py  — v5 (deep shadow root, OOPIF-aware)
Applies/re-applies the Cloudflare Turnstile bypass to pydoll's tab.py.

Root-cause: pydoll _find_cloudflare_shadow_root uses deep=False, which
cannot traverse into Chrome OOPIF (Out-Of-Process IFrame) targets.
The CF Turnstile challenge iframe is an OOPIF on challenges.cloudflare.com,
so span.cb-i is only reachable via find_shadow_roots(deep=True).

Method 1: OOPIF-aware CDP target + JS execute_script (fast path, may fail
          silently on some Tab configurations — logged, not swallowed).
Method 2: find_shadow_roots(deep=True) — pydoll attaches to the OOPIF
          target internally via Target.attachToTarget, returns ShadowRoot
          objects with correct CDP session; query span.cb-i directly.
"""
import shutil, sys, py_compile

TAB_PY = '/usr/local/lib/python3.10/dist-packages/pydoll/browser/tab.py'

def apply():
    with open(TAB_PY) as f:
        src = f.read()

    # ── idempotency check ─────────────────────────────────────────────────────
    if 'deep shadow root traversal — OOPIF-aware (v5)' in src:
        print('[patch] v5 already applied — nothing to do.')
        return

    shutil.copy2(TAB_PY, TAB_PY + '.bak_pre_v5')

    # ── patch 1: log execute_script exceptions instead of swallowing ─────────
    OLD1 = '                        except Exception:\n                            pass\n                        await _aio.sleep(0.8)'
    NEW1 = "                        except Exception as _ejs:\n                            logger.debug(f'[bypass] oopif exec err: {_ejs}')\n                        await _aio.sleep(0.8)"
    if OLD1 in src:
        src = src.replace(OLD1, NEW1, 1)
        print('[patch] patch1 (log exec exceptions) OK')
    else:
        print('[patch] patch1 marker not found — skipping (already applied or different version)')

    # ── patch 2: replace Method 2 with deep shadow root (OOPIF-aware) ────────
    OLD2 = (
        '            # Method 2: original shadow root traversal fallback\n'
        '            shadow_root = await self._find_cloudflare_shadow_root(\n'
        '                timeout=time_to_wait_captcha,\n'
        '            )\n'
        '            iframe = await shadow_root.query(_CLOUDFLARE_IFRAME_SELECTOR, timeout=timeout_int)\n'
        '            body = await iframe.find(tag_name=\'body\', timeout=timeout_int)\n'
        '            inner_shadow = await body.get_shadow_root(timeout=time_to_wait_captcha)\n'
        '            checkbox = await inner_shadow.query(_CLOUDFLARE_CHECKBOX_SELECTOR, timeout=timeout_int)\n'
        '            await checkbox.click()'
    )
    NEW2 = (
        "            # Method 2: deep shadow root traversal — OOPIF-aware (v5)\n"
        "            logger.warning('[bypass] trying deep shadow root (OOPIF-aware)...')\n"
        "            _m2_start = _aio.get_event_loop().time()\n"
        "            _m2_clicked = False\n"
        "            while _aio.get_event_loop().time() - _m2_start < time_to_wait_captcha:\n"
        "                try:\n"
        "                    _deep_roots = await self.find_shadow_roots(deep=True)\n"
        "                    for _dsr in _deep_roots:\n"
        "                        try:\n"
        "                            _cb = await _dsr.query(_CLOUDFLARE_CHECKBOX_SELECTOR, timeout=2)\n"
        "                            await _cb.click()\n"
        "                            logger.warning('[bypass] deep-sr: clicked span.cb-i OK')\n"
        "                            _m2_clicked = True\n"
        "                            return\n"
        "                        except Exception:\n"
        "                            pass\n"
        "                except Exception as _em2:\n"
        "                    logger.warning(f'[bypass] deep-sr error: {_em2}')\n"
        "                await _aio.sleep(1.0)\n"
        "            if not _m2_clicked:\n"
        "                logger.warning('[bypass] deep-sr: span.cb-i not found in any shadow root')"
    )
    if OLD2 in src:
        src = src.replace(OLD2, NEW2, 1)
        print('[patch] patch2 (deep shadow root Method 2) OK')
    else:
        print('[patch] patch2 marker not found — check pydoll version; may need manual review')
        sys.exit(1)

    with open(TAB_PY, 'w') as f:
        f.write(src)

    try:
        py_compile.compile(TAB_PY, doraise=True)
        print(f'[patch] Syntax OK — v5 written to {TAB_PY}')
    except py_compile.PyCompileError as e:
        print(f'[patch] SYNTAX ERROR: {e}')
        shutil.copy2(TAB_PY + '.bak_pre_v5', TAB_PY)
        sys.exit(1)

    import glob, os
    for pyc in glob.glob(TAB_PY.replace('.py', '') + '*.pyc') +                glob.glob(TAB_PY.replace('tab.py', '__pycache__/tab*.pyc')):
        try:
            os.remove(pyc)
            print(f'[patch] removed cache: {pyc}')
        except OSError:
            pass
    print('[patch] Done. Restart unitool_chain_v3 to apply.')


if __name__ == '__main__':
    apply()
