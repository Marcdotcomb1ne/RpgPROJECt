"""
Supabase REST client - sem SDK, sem Rust, sem drama.
Usa httpx direto contra a PostgREST API do Supabase.
"""

import httpx
from config import get_settings


class SupabaseTable:
    """Fluent builder para queries na PostgREST API do Supabase."""

    def __init__(self, base_url: str, headers: dict, table: str):
        self._base = base_url
        self._headers = headers
        self._table = table
        self._filters: list[str] = []
        self._select_cols = "*"
        self._order_col: str | None = None
        self._order_desc = False
        self._limit_val: int | None = None
        self._single = False

    def select(self, cols: str = "*"):
        self._select_cols = cols
        return self

    def eq(self, col: str, val):
        self._filters.append(f"{col}=eq.{val}")
        return self

    def order(self, col: str, desc: bool = False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n: int):
        self._limit_val = n
        return self

    def single(self):
        self._single = True
        return self

    def _url(self) -> str:
        return f"{self._base}/rest/v1/{self._table}"

    def _params(self) -> dict:
        p: dict = {"select": self._select_cols}
        for f in self._filters:
            k, v = f.split("=", 1)
            p[k] = v
        if self._order_col:
            direction = "desc" if self._order_desc else "asc"
            p["order"] = f"{self._order_col}.{direction}"
        if self._limit_val is not None:
            p["limit"] = self._limit_val
        return p

    def _headers_for(self, single=False) -> dict:
        h = dict(self._headers)
        if single or self._single:
            h["Accept"] = "application/vnd.pgrst.object+json"
        else:
            h["Accept"] = "application/json"
        return h

    def execute(self) -> "Result":
        params = self._params()
        with httpx.Client(timeout=15) as client:
            r = client.get(
                self._url(),
                params=params,
                headers=self._headers_for(),
            )
        return Result(r)

    def insert(self, data: dict) -> "MutationBuilder":
        return MutationBuilder(self._base, self._headers, self._table, "INSERT", data)

    def update(self, data: dict) -> "MutationBuilder":
        return MutationBuilder(
            self._base, self._headers, self._table, "UPDATE", data,
            filters=list(self._filters)
        )

    def delete(self) -> "MutationBuilder":
        return MutationBuilder(
            self._base, self._headers, self._table, "DELETE", {},
            filters=list(self._filters)
        )


class MutationBuilder:
    def __init__(self, base_url, headers, table, method, data, filters=None):
        self._base = base_url
        self._headers = headers
        self._table = table
        self._method = method
        self._data = data
        self._filters: list[str] = filters or []

    def eq(self, col: str, val):
        self._filters.append(f"{col}=eq.{val}")
        return self

    def _url(self) -> str:
        return f"{self._base}/rest/v1/{self._table}"

    def _params(self) -> dict:
        p = {}
        for f in self._filters:
            k, v = f.split("=", 1)
            p[k] = v
        return p

    def execute(self) -> "Result":
        h = dict(self._headers)
        h["Content-Type"] = "application/json"
        h["Prefer"] = "return=representation"

        with httpx.Client(timeout=15) as client:
            if self._method == "INSERT":
                r = client.post(self._url(), json=self._data, headers=h)
            elif self._method == "UPDATE":
                r = client.patch(
                    self._url(), json=self._data,
                    params=self._params(), headers=h
                )
            elif self._method == "DELETE":
                r = client.delete(
                    self._url(), params=self._params(), headers=h
                )
            else:
                raise ValueError(f"Metodo desconhecido: {self._method}")

        return Result(r)


class Result:
    def __init__(self, response: httpx.Response):
        self._response = response
        try:
            self.data = response.json()
        except Exception:
            self.data = None

        if response.status_code >= 400:
            msg = self.data if isinstance(self.data, str) else str(self.data)
            raise RuntimeError(f"Supabase error {response.status_code}: {msg}")

        # Single object endpoint returns a dict, wrap in list for consistency
        # unless caller used Accept: application/vnd.pgrst.object+json
        if isinstance(self.data, dict) and "data" not in self.data:
            # could be a single row result
            pass


class SupabaseClient:
    def __init__(self, url: str, service_key: str):
        self._url = url.rstrip("/")
        self._headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
        }

    def table(self, name: str) -> SupabaseTable:
        return SupabaseTable(self._url, self._headers, name)


_client: SupabaseClient | None = None


def get_db() -> SupabaseClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = SupabaseClient(s.supabase_url, s.supabase_service_key)
    return _client
