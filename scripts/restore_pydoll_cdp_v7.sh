#!/bin/bash
# Restore pydoll tab.py to CDP v7 patch (bypass_v7_applied)
TAB=/usr/local/lib/python3.10/dist-packages/pydoll/browser/tab.py
BAK="./pydoll_tab_cdp_v7_snapshot.py"
cp -f "$BAK" "$TAB" && echo "restored CDP v7"
python3 -c "import py_compile; py_compile.compile(, doraise=True) ; print(syntax OK)"
find /usr/local/lib/python3.10/dist-packages/pydoll/browser/__pycache__/ -name "tab*.pyc" -delete
echo "pyc cleared"
