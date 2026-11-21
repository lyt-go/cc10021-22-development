"""业务路由汇总：观测集合查询在前，通用资源路由在后。

通用资源路由含 /api/{module}/{entry_id}，会把 comparison、ranking-review
这类固定子路径当成整数 id，所以更具体的观测集合路由必须先注册。
"""
from __future__ import annotations

from app.routers import observation, resource

ROUTERS = [observation, resource]
