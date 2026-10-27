# 开发与验证

使用 uv 管理依赖，`pyproject.toml` 为版本、依赖、入口和构建配置的唯一来源。

```bash
uv sync --locked --python 3.13
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest --cov=certbot_dns_esa --cov-report=term-missing
```

测试模拟外部 API，不修改真实 DNS，也不向 ACME 服务申请证书。SDK 请求和响应使用真实 DTO；另通过模拟 HTTP 传输验证 SDK 不会重试写入。

兼容矩阵使用 tox-uv 构建并安装 wheel，从源码目录之外运行测试，校验实际宿主版本、插件入口、导入路径及 `certbot plugins --text`：

```bash
uv python install 3.9 3.10 3.11 3.12 3.13 3.14 3.15
uv run --no-sync tox run-parallel -p 4
uv build
uv run --no-sync twine check --strict dist/*
```

矩阵共 18 组：Python 3.9–3.13 × Certbot 3、Python 3.9–3.15 × Certbot 4、Python 3.10–3.15 × Certbot 5。Certbot 3 测试固定最低版本 3.0.0，并限制 `pyopenssl<25`；该限制不能加入插件运行依赖，否则会与 Certbot 4/5 冲突。Python 3.14 及以后不支持旧 josepy 1.x，限定使用 Certbot 4/5。

修改依赖后执行 `uv lock`；修改认证行为或兼容边界后运行单元测试及完整矩阵；修改发行配置后构建并校验 wheel/sdist。

认证器复用 Certbot 公共生命周期。站点必须通过完整分页发现并按 DNS 标签边界匹配，查询失败不得猜测站点。仅删除本次创建且保存了 ID 的记录；已有记录不得更新或删除。SDK 超时和重试策略应同时通过实际传输层测试验证。

GitHub Actions 对 push/PR 执行检查。发布工作流在 release 发布时复用完整 CI，检查标签为 `v{项目版本}`；预发布版本只验证，正式版本验证成功后使用 PyPI Trusted Publishing 发布。需在仓库配置 `pypi` environment 及 PyPI 的可信发布者。
