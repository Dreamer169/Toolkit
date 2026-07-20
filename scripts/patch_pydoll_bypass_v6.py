#!/usr/bin/env python3
"""
patch_pydoll_bypass_v6.py — 修复CF bypass两个核心问题:
  1. Method 1 初始sleep从5s减到1s，节省4s
  2. Method 2 点击span.cb-i后不立即return，改为等待8s让CF生成token
     同时尝试CDP Input.dispatchMouseEvent做真实鼠标点击
"""
import shutil, sys, py_compile

TAB_PY = '/usr/local/lib/python3.10/dist-packages/pydoll/browser/tab.py'

def apply():
    with open(TAB_PY) as f:
        src = f.read()

    if 'bypass_v6_applied' in src:
        print('[patch] v6 already applied.')
        return

    shutil.copy2(TAB_PY, TAB_PY + '.bak_pre_v6')
    changed = False

    # ── Fix 1: reduce initial sleep from 5.0 to 1.0 ─────────────────────────
    OLD_SLEEP = "                    # v4: strict selectors only; detect invisible Turnstile\n                    await _aio.sleep(5.0)"
    NEW_SLEEP = "                    # v4: strict selectors only; detect invisible Turnstile\n                    await _aio.sleep(1.0)  # v6: reduced from 5.0"
    if OLD_SLEEP in src:
        src = src.replace(OLD_SLEEP, NEW_SLEEP, 1)
        print('[patch] Fix1: Method1 sleep 5s→1s OK')
        changed = True
    else:
        print('[patch] Fix1: marker not found — skipping (may differ)')

    # ── Fix 2: after deep-sr click, wait for CF to process instead of return ─
    OLD_M2 = (
        "                        try:\n"
        "                            _cb = await _dsr.query(_CLOUDFLARE_CHECKBOX_SELECTOR, timeout=2)\n"
        "                            await _cb.click()\n"
        "                            logger.warning('[bypass] deep-sr: clicked span.cb-i OK')\n"
        "                            _m2_clicked = True\n"
        "                            return\n"
        "                        except Exception:\n"
        "                            pass"
    )
    NEW_M2 = (
        "                        try:\n"
        "                            _cb = await _dsr.query(_CLOUDFLARE_CHECKBOX_SELECTOR, timeout=2)\n"
        "                            # v6: get bounding box for real CDP mouse click\n"
        "                            try:\n"
        "                                _bbox_js = (\n"
        "                                    \"(function(){\"\n"
        "                                    \"var el=document.querySelector('span.cb-i');\"\n"
        "                                    \"if(!el)return null;\"\n"
        "                                    \"var r=el.getBoundingClientRect();\"\n"
        "                                    \"return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});\"\n"
        "                                    \"})();\"\n"
        "                                )\n"
        "                                _bbox_res = await self.execute_script(_bbox_js, return_by_value=True)\n"
        "                                _bbox_r = _bbox_res if isinstance(_bbox_res, dict) else {}\n"
        "                                _bbox_inner = _bbox_r.get('result', _bbox_r)\n"
        "                                _bbox_val = _bbox_inner.get('value', '') if isinstance(_bbox_inner, dict) else ''\n"
        "                                if _bbox_val and _bbox_val != 'null':\n"
        "                                    import json as _json\n"
        "                                    _pos = _json.loads(_bbox_val)\n"
        "                                    _cx, _cy = int(_pos['x']), int(_pos['y'])\n"
        "                                    from pydoll.commands.input_commands import InputCommands\n"
        "                                    from pydoll.constants import MouseEventType, MouseButton\n"
        "                                    await self._execute_command(InputCommands.dispatch_mouse_event(MouseEventType.MOUSE_MOVED, _cx, _cy))\n"
        "                                    await _aio.sleep(0.1)\n"
        "                                    await self._execute_command(InputCommands.dispatch_mouse_event(MouseEventType.MOUSE_PRESSED, _cx, _cy, button=MouseButton.LEFT, click_count=1))\n"
        "                                    await _aio.sleep(0.08)\n"
        "                                    await self._execute_command(InputCommands.dispatch_mouse_event(MouseEventType.MOUSE_RELEASED, _cx, _cy, button=MouseButton.LEFT, click_count=1))\n"
        "                                    logger.warning(f'[bypass] deep-sr: CDP mouse click at ({_cx},{_cy})')\n"
        "                                else:\n"
        "                                    await _cb.click()\n"
        "                                    logger.warning('[bypass] deep-sr: pydoll click (no bbox)')\n"
        "                            except Exception as _cbdp:\n"
        "                                logger.warning(f'[bypass] deep-sr cdp click err: {_cbdp}')\n"
        "                                await _cb.click()\n"
        "                                logger.warning('[bypass] deep-sr: pydoll click (fallback)')\n"
        "                            logger.warning('[bypass] deep-sr: clicked span.cb-i OK — waiting 8s for CF token')\n"
        "                            _m2_clicked = True\n"
        "                            # bypass_v6_applied\n"
        "                            await _aio.sleep(8.0)  # v6: let CF validate click and set token\n"
        "                            return\n"
        "                        except Exception:\n"
        "                            pass"
    )
    if OLD_M2 in src:
        src = src.replace(OLD_M2, NEW_M2, 1)
        print('[patch] Fix2: Method2 post-click wait 8s + CDP mouse OK')
        changed = True
    else:
        print('[patch] Fix2: Method2 marker not found — check v5 patch state')
        sys.exit(1)

    if not changed:
        print('[patch] No changes made.')
        return

    with open(TAB_PY, 'w') as f:
        f.write(src)

    try:
        py_compile.compile(TAB_PY, doraise=True)
        print(f'[patch] Syntax OK — v6 written to {TAB_PY}')
    except py_compile.PyCompileError as e:
        print(f'[patch] SYNTAX ERROR: {e}')
        shutil.copy2(TAB_PY + '.bak_pre_v6', TAB_PY)
        sys.exit(1)

    import glob, os
    for pyc in glob.glob(TAB_PY.replace('tab.py', '__pycache__/tab*.pyc')):
        try:
            os.remove(pyc)
            print(f'[patch] removed cache: {pyc}')
        except OSError:
            pass
    print('[patch] Done. Run: pm2 restart unitool_chain_v3 unitool_verify_rescue')

if __name__ == '__main__':
    apply()
