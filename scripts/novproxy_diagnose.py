#!/usr/bin/env python3
"""
novproxy_diagnose.py  -- standalone diagnosis script
Usage: python3 novproxy_diagnose.py <json_payload>
  payload: {"email":"...","pwd":"..."} or {"email":"...","token":"..."}
Output: JSON to stdout
"""
import sys, json, concurrent.futures

try:
    import requests
except ImportError:
    print(json.dumps({"error": "requests_not_installed"}))
    sys.exit(0)

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://dash.novproxy.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

def post(path, data):
    try:
        r = requests.post(
            "https://api.novproxy.com" + path,
            data=data,
            headers=HEADERS,
            timeout=12,
        )
        return r.json()
    except Exception as e:
        return {"code": -1, "data": {}, "msg": str(e)}

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "no_payload"}))
        return

    try:
        payload = json.loads(sys.argv[1])
    except Exception as e:
        print(json.dumps({"error": f"bad_json: {e}"}))
        return

    results = {}
    try:
        if "pwd" in payload:
            signin = post("/v1/signin", {
                "lang": "en",
                "email": payload["email"],
                "pwd": payload["pwd"],
            })
            results["signin"] = signin
            token = (signin.get("data") or {}).get("token", "")
            if not token:
                print(json.dumps({"error": "login_failed", "signin": signin}))
                return
        else:
            token = payload.get("token", "")

        def fetch_member():   return post("/v1/member",      {"token": token})
        def fetch_traffic():  return post("/v1/trafficInfo", {"token": token})
        def fetch_price():    return post("/v2/priceList",   {"token": token})

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            fm = ex.submit(fetch_member)
            ft = ex.submit(fetch_traffic)
            fp = ex.submit(fetch_price)
            results["token"]       = token
            results["member"]      = fm.result()
            results["trafficInfo"] = ft.result()
            results["priceList"]   = fp.result()

        print(json.dumps(results))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    main()
