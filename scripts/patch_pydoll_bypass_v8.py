#!/usr/bin/env python3
"""
patch_pydoll_bypass_v8.py — Fix v8: two root-cause bugs after v7

Bug A: Method 1 OOPIF JS only clicks when getBoundingClientRect()>0,
       but in headless/Xvfb the checkbox renders with 0-dims → always skipped.
       Fix: remove dimension gate — try click regardless of dimensions.

Bug B: After Method 1 "oopif found but cb-i timed out", falls to Method 2.
       Fix: inject fixed-coord CDP mouse-click on CF tab first (x25,y32).

Bug C: Method 2 bbox_js runs on MAIN page, span.cb-i is inside OOPIF → null.
       Fix: after pydoll click, also send CDP events to CF tab.
"""
import shutil, sys, py_compile, glob, os, re

TAB_PY = '/usr/local/lib/python3.10/dist-packages/pydoll/browser/tab.py'

def apply():
    with open(TAB_PY) as f:
        src = f.read()

    if 'bypass_v8_applied' in src:
        print('[patch] v8 already applied.')
        return

    if 'bypass_v7_applied' not in src:
        print('[patch] v7 not found — must apply v7 first')
        sys.exit(1)

    shutil.copy2(TAB_PY, TAB_PY + '.bak_pre_v8')
    changed = 0

    # ── Fix A: remove width>0&&height>0 gate in OOPIF JS ────────────────────
    OLD_A = 'if(r.width>0&&r.height>0){'
    if OLD_A in src:
        # Just remove the if gate and its closing brace
        # The block is: if(r.width>0&&r.height>0){...click code...}}}
        # We want: ...click code...}}
        # Simple approach: replace the condition with always-true
        src = src.replace(OLD_A, 'if(true){  /*bypass_v8_applied*/', 1)
        print('[patch] Fix A: zero-dim gate replaced with always-true in OOPIF JS')
        changed += 1
    else:
        print('[patch] Fix A: zero-dim gate not found (already fixed or different code)')

    # ── Fix B: after "oopif found but cb-i timed out", try fixed-coord CDP ──
    OLD_B = "                    logger.warning('[bypass] oopif found but cb-i timed out; trying shadow root')\n"
    NEW_B = (
        "                    # bypass_v8_applied: try fixed-coord CDP on CF tab before shadow root\n"
        "                    logger.warning('[bypass] v8: trying fixed-coord CDP on CF tab before shadow root')\n"
        "                    try:\n"
        "                        from pydoll.commands.input_commands import InputCommands\n"
        "                        from pydoll.constants import MouseEventType, MouseButton\n"
        "                        _v8_done = False\n"
        "                        for _v8x, _v8y in [(25,32),(150,32),(50,32),(25,40)]:\n"
        "                            await _cf_tab._execute_command(InputCommands.dispatch_mouse_event(MouseEventType.MOUSE_MOVED,_v8x,_v8y))\n"
        "                            await _aio.sleep(0.08)\n"
        "                            await _cf_tab._execute_command(InputCommands.dispatch_mouse_event(MouseEventType.MOUSE_PRESSED,_v8x,_v8y,button=MouseButton.LEFT,click_count=1))\n"
        "                            await _aio.sleep(0.06)\n"
        "                            await _cf_tab._execute_command(InputCommands.dispatch_mouse_event(MouseEventType.MOUSE_RELEASED,_v8x,_v8y,button=MouseButton.LEFT,click_count=1))\n"
        "                            logger.warning(f'[bypass] v8 OOPIF CDP click at ({_v8x},{_v8y})')\n"
        "                            await _aio.sleep(0.3)\n"
        "                        for _v8w in range(35):\n"
        "                            await _aio.sleep(1.0)\n"
        "                            try:\n"
        "                                _v8t = await self.execute_script(\n"
        "                                    '(document.querySelector(\\'[name=\"cf-turnstile-response\"]\\') || {value:\\'\\'}).value.length',\n"
        "                                    return_by_value=True)\n"
        "                                _v8r = _v8t if isinstance(_v8t,dict) else {}\n"
        "                                _v8i = _v8r.get('result',_v8r)\n"
        "                                _v8v = int(_v8i.get('value',0) if isinstance(_v8i,dict) else 0)\n"
        "                                if _v8v > 20:\n"
        "                                    logger.warning(f'[bypass] v8 OOPIF CDP: token OK {_v8w+1}s len={_v8v}')\n"
        "                                    return\n"
        "                            except Exception: pass\n"
        "                            if _v8w % 5 == 4: logger.warning(f'[bypass] v8 OOPIF CDP: waiting {_v8w+1}s')\n"
        "                        logger.warning('[bypass] v8 OOPIF CDP: token timeout 35s')\n"
        "                    except Exception as _v8e:\n"
        "                        logger.warning(f'[bypass] v8 OOPIF CDP err: {_v8e}')\n"
        "                    logger.warning('[bypass] oopif found but cb-i timed out; trying shadow root')\n"
    )
    if OLD_B in src:
        src = src.replace(OLD_B, NEW_B, 1)
        print('[patch] Fix B: fixed-coord CDP fallback after OOPIF timeout OK')
        changed += 1
    else:
        print('[patch] Fix B: marker not found — aborting')
        sys.exit(1)

    # ── Fix C: Method 2 — after pydoll click, also try CF tab CDP ────────────
    OLD_C = "                            logger.warning('[bypass] deep-sr: clicked span.cb-i OK \xe2\x80\x94 polling 25s for token')\n                            _m2_clicked = True\n                            # bypass_v7_applied\n"
    NEW_C = (
        "                            logger.warning('[bypass] deep-sr: clicked span.cb-i OK \xe2\x80\x94 polling 25s for token')\n"
        "                            _m2_clicked = True\n"
        "                            # bypass_v8_applied\n"
        "                            try:\n"
        "                                _m2_ts = await self._browser.get_targets()\n"
        "                                _m2_cf = next((_t for _t in _m2_ts if 'challenges.cloudflare.com' in _t.get('url','')),None)\n"
        "                                if _m2_cf:\n"
        "                                    _m2_tid = _m2_cf['targetId']\n"
        "                                    _m2_ctab = self._browser._tabs_opened.get(_m2_tid)\n"
        "                                    if not _m2_ctab:\n"
        "                                        _m2_ctab = Tab(self._browser,target_id=_m2_tid,connection_port=self._connection_port)\n"
        "                                        self._browser._tabs_opened[_m2_tid] = _m2_ctab\n"
        "                                    from pydoll.commands.input_commands import InputCommands\n"
        "                                    from pydoll.constants import MouseEventType, MouseButton\n"
        "                                    for _m2cx,_m2cy in [(25,32),(150,32),(50,32)]:\n"
        "                                        await _m2_ctab._execute_command(InputCommands.dispatch_mouse_event(MouseEventType.MOUSE_MOVED,_m2cx,_m2cy))\n"
        "                                        await _aio.sleep(0.05)\n"
        "                                        await _m2_ctab._execute_command(InputCommands.dispatch_mouse_event(MouseEventType.MOUSE_PRESSED,_m2cx,_m2cy,button=MouseButton.LEFT,click_count=1))\n"
        "                                        await _aio.sleep(0.05)\n"
        "                                        await _m2_ctab._execute_command(InputCommands.dispatch_mouse_event(MouseEventType.MOUSE_RELEASED,_m2cx,_m2cy,button=MouseButton.LEFT,click_count=1))\n"
        "                                    logger.warning('[bypass] deep-sr v8: extra CDP events sent to CF tab')\n"
        "                            except Exception as _m2ce: logger.warning(f'[bypass] deep-sr v8 CF tab CDP (non-fatal): {_m2ce}')\n"
        "                            # bypass_v7_applied\n"
    )
    if OLD_C in src:
        src = src.replace(OLD_C, NEW_C, 1)
        print('[patch] Fix C: Method 2 CF tab CDP backup OK')
        changed += 1
    else:
        print('[patch] Fix C: marker not found (non-fatal)')

    with open(TAB_PY, 'w') as f:
        f.write(src)

    try:
        py_compile.compile(TAB_PY, doraise=True)
        print(f'[patch] Syntax OK — v8 applied ({changed} fixes)')
    except py_compile.PyCompileError as e:
        print(f'[patch] SYNTAX ERROR: {e}')
        shutil.copy2(TAB_PY + '.bak_pre_v8', TAB_PY)
        sys.exit(1)

    for pyc in glob.glob(TAB_PY.replace('tab.py', '__pycache__/tab*.pyc')):
        try: os.remove(pyc)
        except OSError: pass
    print(f'[patch] Done. {changed} fixes applied.')

if __name__ == '__main__':
    apply()
