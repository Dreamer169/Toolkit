import cycronet as _cycronet


class _MultiHeaders:
    def __init__(self, raw):
        self._raw = raw

    def multi_items(self):
        for k, vals in self._raw.items():
            for v in (vals if isinstance(vals, list) else [vals]):
                yield (k, v)

    def get(self, key, default=None):
        key_lower = key.lower()
        for k, vals in self._raw.items():
            if k.lower() == key_lower:
                if isinstance(vals, list):
                    return vals[0] if vals else default
                return vals
        return default

    def __contains__(self, key):
        key_lower = key.lower()
        return any(k.lower() == key_lower for k in self._raw)

    def __getitem__(self, key):
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v

    def __iter__(self):
        return iter(self._raw)

    def items(self):
        return {k: (v[0] if isinstance(v, list) and v else v) for k, v in self._raw.items()}.items()


class _ResponseShim:
    def __init__(self, resp):
        self._resp = resp
        self.status_code = resp.status_code
        self.content = resp.content
        self.url = resp.url
        raw = getattr(resp, "_headers", None) or {}
        if raw and isinstance(next(iter(raw.values()), None), str):
            raw = {k: [v] for k, v in raw.items()}
        self._multi_headers = _MultiHeaders(raw)

    @property
    def text(self):
        return self._resp.text

    def json(self):
        return self._resp.json()

    @property
    def headers(self):
        return self._multi_headers

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _CookieJarShim:
    def __init__(self, jar):
        self._jar = jar

    @property
    def jar(self):
        return list(self._jar.iter_cookies())

    def set(self, name, value, domain=None, path="/"):
        self._jar.set(name, value, domain=domain, path=path)

    def delete(self, name, domain=None, path=None):
        try:
            self._jar.delete(name=name, domain=domain)
        except Exception:
            pass

    def get(self, name, default=None, domain=None):
        return self._jar.get(name, default=default, domain=domain)

    def __iter__(self):
        return self._jar.iter_cookies()

    def __len__(self):
        return len(self._jar)

    def __contains__(self, name):
        return name in self._jar


class Session:
    def __init__(self, impersonate=None, **kwargs):
        self._proxy = None
        self._session = None
        self._cookies_shim = None

    def _ensure_session(self):
        if self._session is None:
            proxies = None
            if self._proxy:
                proxies = {"https": self._proxy, "http": self._proxy}
            self._session = _cycronet.CronetClient(
                verify=False,
                proxies=proxies,
                timeout_ms=30000,
            )
            self._cookies_shim = _CookieJarShim(self._session.cookies)
        return self._session

    @property
    def proxies(self):
        return {"https": self._proxy, "http": self._proxy} if self._proxy else {}

    @proxies.setter
    def proxies(self, value):
        proxy = None
        if isinstance(value, dict):
            proxy = value.get("https") or value.get("http")
        elif isinstance(value, str):
            proxy = value
        if proxy != self._proxy:
            self._proxy = proxy
            self._session = None
            self._cookies_shim = None

    @property
    def cookies(self):
        self._ensure_session()
        return self._cookies_shim

    def get(self, url, **kwargs):
        kwargs.pop("timeout", None)
        resp = self._ensure_session().get(url, **kwargs)
        return _ResponseShim(resp)

    def post(self, url, **kwargs):
        kwargs.pop("timeout", None)
        resp = self._ensure_session().post(url, **kwargs)
        return _ResponseShim(resp)

    def request(self, method, url, **kwargs):
        kwargs.pop("timeout", None)
        resp = self._ensure_session().request(method, url, **kwargs)
        return _ResponseShim(resp)

    def close(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
