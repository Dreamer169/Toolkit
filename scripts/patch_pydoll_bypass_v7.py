#!/usr/bin/env python3
"""
patch_pydoll_bypass_v7.py — Fix CF bypass core bug:
  Bug: Method 1 OOPIF click immediately returns, never waits for token
       Method 2 shadow root waits blind 8s then returns regardless of token
  Fix: After any click, poll cf-turnstile-response up to 25s before returning
"""
import shutil, sys, py_compile, glob, os

TAB_PY = '/usr/local/lib/python3.10/dist-packages/pydoll/browser/tab.py'

def apply():
    with open(TAB_PY) as f:
        src = f.read()

    if 'bypass_v7_applied' in src:
        print('[patch] v7 already applied.')
        return

    if 'bypass_v6_applied' not in src:
        print('[patch] v6 not found — must apply v6 first')
        sys.exit(1)

    shutil.copy2(TAB_PY, TAB_PY + '.bak_pre_v7')
    changed = 0

    # Fix 1: Method 1 OOPIF — wait for token after click
    OLD1 = "                            if _val and _val.startswith('clicked'):\n                                logger.warning(f'[bypass] oopif v4 click OK: {_val}')\n                                return\n"
    NEW1 = ("                            if _val and _val.startswith('clicked'):\n"
            "                                logger.warning(f'[bypass] oopif v4 click OK: {_val}')\n"
            "                                # bypass_v7_applied\n"
            "                                for _tw in range(25):\n"
            "                                    await _aio.sleep(1.0)\n"
            "                                    try:\n"
            "                                        _tk_res = await self.execute_script(\n"
            "                                            \"(document.querySelector('[name=\\\"cf-turnstile-response\\\"]')||{value:''}).value.length\",\n"
            "                                            return_by_value=True)\n"
            "                                        _tk_r = _tk_res if isinstance(_tk_res, dict) else {}\n"
            "                                        _tk_i = _tk_r.get('result', _tk_r)\n"
            "                                        _tk_v = int(_tk_i.get('value', 0) if isinstance(_tk_i, dict) else 0)\n"
            "                                        if _tk_v > 20:\n"
            "                                            logger.warning(f'[bypass] oopif v7: token OK {_tw+1}s len={_tk_v}')\n"
            "                                            return\n"
            "                                    except Exception:\n"
            "                                        pass\n"
            "                                    if _tw % 5 == 4:\n"
            "                                        logger.warning(f'[bypass] oopif v7: waiting token {_tw+1}s')\n"
            "                                logger.warning('[bypass] oopif v7: click OK but token timeout 25s')\n"
            "                                return\n")
    if OLD1 in src:
        src = src.replace(OLD1, NEW1, 1)
        print('[patch] Fix1: Method1 OOPIF post-click poll 25s OK')
        changed += 1
    else:
        print('[patch] Fix1: marker not found — aborting')
        sys.exit(1)

    # Fix 2: Method 2 shadow root — replace blind 8s sleep with poll
    OLD2 = ("                            logger.warning('[bypass] deep-sr: clicked span.cb-i OK \xe2\x80\x94 waiting 8s for CF token')\n"
            "                            _m2_clicked = True\n"
            "                            # bypass_v6_applied\n"
            "                            await _aio.sleep(8.0)  # v6: let CF validate click and set token\n"
            "                            return\n")
    NEW2 = ("                            logger.warning('[bypass] deep-sr: clicked span.cb-i OK \xe2\x80\x94 polling 25s for token')\n"
            "                            _m2_clicked = True\n"
            "                            # bypass_v7_applied\n"
            "                            for _m2w in range(25):\n"
            "                                await _aio.sleep(1.0)\n"
            "                                try:\n"
            "                                    _m2t = await self.execute_script(\n"
            "                                        \"(document.querySelector('[name=\\\"cf-turnstile-response\\\"]')||{value:''}).value.length\",\n"
            "                                        return_by_value=True)\n"
            "                                    _m2tr = _m2t if isinstance(_m2t, dict) else {}\n"
            "                                    _m2ti = _m2tr.get('result', _m2tr)\n"
            "                                    _m2tv = int(_m2ti.get('value', 0) if isinstance(_m2ti, dict) else 0)\n"
            "                                    if _m2tv > 20:\n"
            "                                        logger.warning(f'[bypass] deep-sr v7: token OK {_m2w+1}s len={_m2tv}')\n"
            "                                        return\n"
            "                                except Exception:\n"
            "                                    pass\n"
            "                                if _m2w % 5 == 4:\n"
            "                                    logger.warning(f'[bypass] deep-sr v7: waiting token {_m2w+1}s')\n"
            "                            logger.warning('[bypass] deep-sr v7: click OK but token timeout 25s')\n"
            "                            return\n")
    if OLD2 in src:
        src = src.replace(OLD2, NEW2, 1)
        print('[patch] Fix2: Method2 shadow root poll 25s OK')
        changed += 1
    else:
        print('[patch] Fix2: Method2 marker not found — aborting')
        sys.exit(1)

    with open(TAB_PY, 'w') as f:
        f.write(src)

    try:
        py_compile.compile(TAB_PY, doraise=True)
        print('[patch] Syntax OK — v7 applied')
    except py_compile.PyCompileError as e:
        print(f'[patch] SYNTAX ERROR: {e}')
        shutil.copy2(TAB_PY + '.bak_pre_v7', TAB_PY)
        sys.exit(1)

    for pyc in glob.glob(TAB_PY.replace('tab.py', '__pycache__/tab*.pyc')):
        try:
            os.remove(pyc)
        except OSError:
            pass
    print(f'[patch] Done. {changed} fixes applied.')

if __name__ == '__main__':
    apply()
