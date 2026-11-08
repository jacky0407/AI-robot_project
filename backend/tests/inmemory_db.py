"""
測試用的記憶體版 Supabase：資料真的會被新增、修改、刪除，
適合要驗證「操作後資料變成什麼樣子」的測試（模組與步驟管理、解鎖規則）。

只支援測試用到的鏈式方法：select / insert / update / delete / eq / in_ / order / limit / execute，
以及 rpc()。select 一律回傳整列（不做欄位投影），巢狀關聯直接寫在列資料裡。

模擬錯誤：db.errors[(table, op)] = Exception，該操作執行時拋出。
"""

import uuid


class FakeResult:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db: "InMemoryDB", table: str):
        self.db = db
        self.table = table
        self.op = None
        self.payload = None
        self.selected = ""
        self.filters: list = []
        self.order_by: list[tuple[str, bool]] = []
        self.limit_n: int | None = None

    def select(self, columns="*", **kwargs):
        self.op, self.selected = "select", columns
        return self

    def insert(self, payload, **kwargs):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload, **kwargs):
        self.op, self.payload = "update", payload
        return self

    def delete(self, **kwargs):
        self.op = "delete"
        return self

    def eq(self, column, value):
        self.filters.append(lambda r, c=column, v=value: r.get(c) == v)
        return self

    def in_(self, column, values):
        values = list(values)
        self.filters.append(lambda r, c=column, v=values: r.get(c) in v)
        return self

    def order(self, column, desc=False, **kwargs):
        self.order_by.append((column, desc))
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def _matches(self, row):
        return all(f(row) for f in self.filters)

    def execute(self):
        self.db.calls.append((self.table, self.op, self.selected))
        error = self.db.errors.get((self.table, self.op))
        if error:
            raise error

        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            result = [dict(r) for r in rows if self._matches(r)]
            for column, desc in reversed(self.order_by):
                result.sort(key=lambda r: (r.get(column) is None, r.get(column)), reverse=desc)
            return FakeResult(result[: self.limit_n] if self.limit_n is not None else result)

        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            inserted = []
            for item in items:
                row = {"id": str(uuid.uuid4()), **self.db.defaults.get(self.table, {}), **item}
                rows.append(row)
                inserted.append(dict(row))
            return FakeResult(inserted)

        if self.op == "update":
            updated = []
            for row in rows:
                if self._matches(row):
                    row.update(self.payload)
                    updated.append(dict(row))
            return FakeResult(updated)

        if self.op == "delete":
            removed = [dict(r) for r in rows if self._matches(r)]
            self.db.tables[self.table] = [r for r in rows if not self._matches(r)]
            return FakeResult(removed)

        raise AssertionError(f"unsupported op {self.op}")


class _Rpc:
    def __init__(self, db: "InMemoryDB", name: str, params: dict):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        self.db.rpc_calls.append((self.name, self.params))
        error = self.db.errors.get(("rpc", self.name))
        if error:
            raise error
        handler = self.db.rpc_handlers.get(self.name)
        if handler is None:
            raise AssertionError(f"no rpc handler for {self.name}")
        return FakeResult(handler(self.db, **self.params))


def reorder_module_steps(db: "InMemoryDB", p_module_id: str, p_step_ids: list[str]):
    """與 migration 010 的 reorder_module_steps() 相同的效果"""
    by_id = {r["id"]: r for r in db.tables.get("module_steps", []) if r["module_id"] == p_module_id}
    assert set(by_id) == set(p_step_ids), "step ids do not match module"
    for order, step_id in enumerate(p_step_ids, start=1):
        by_id[step_id]["step_order"] = order
    return None


class InMemoryDB:
    def __init__(self, **tables):
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.calls: list[tuple] = []
        self.rpc_calls: list[tuple] = []
        self.errors: dict[tuple, Exception] = {}
        self.defaults: dict[str, dict] = {"module_steps": {"is_active": True}}
        self.rpc_handlers = {"reorder_module_steps": reorder_module_steps}

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def rpc(self, name: str, params: dict) -> _Rpc:
        return _Rpc(self, name, params)

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table, [])
