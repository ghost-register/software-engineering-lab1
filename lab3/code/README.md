# 校园二手交易系统

Python 3.11及以上，Flask与SQLite实现，电脑浏览器本地使用。管理员、卖家和买家共享同一应用，权限由服务端检查。

## 安装与运行

在本目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m campus_market.cli --data-dir instance demo
.venv\Scripts\python.exe -m campus_market.cli --data-dir instance run
```

访问 http://127.0.0.1:5053 。演示账号为admin_demo、seller_demo、buyer_demo、other_demo，密码均为Campus2026。`demo`是显式演示数据命令；普通注册只产生普通账号。用于自己的数据时用`init`初始化，再用`admin 用户名`在终端预置管理员。

Windows也可双击“启动应用.cmd”。首次运行创建虚拟环境并安装依赖，需要联网；数据库保存在本目录instance，关闭窗口不会删除数据。应用仅绑定本机回环地址。演示密码不是生产部署凭据。

## 构建与检查

```powershell
.venv\Scripts\python.exe -m compileall -q src
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m pylint src/campus_market
.venv\Scripts\python.exe -m black --check src tests tools --line-length 100
.venv\Scripts\python.exe -m build
.venv\Scripts\python.exe tools/count_lines.py
```

`pyproject.toml`声明构建后端、入口、依赖和包内资源；构建输出在dist，包括wheel和源代码分发包。requirements-lock.txt记录本次验证环境的确切依赖版本。不是将GUI生成文件计入代码行数；统计工具只统计应用Python源码，另列空白与文档字符串排除后的行数。

## 主要功能

注册登录退出；商品草稿和图片维护；提交审核及驳回原因；公开组合查询与独立推荐；预约与请求结果恢复；买家取消、卖家拒绝和确认、买家完成；异常关闭申请；举报、违规下架、账号停用；审计和统计。

请求重发返回原业务结果和当前状态。旧预约取消后重发不会恢复预约。SQLite使用BEGIN IMMEDIATE串行写事务和部分唯一索引，替代实验二PostgreSQL行锁；代码未声称具备PostgreSQL的写并行能力。

## 运维命令

```powershell
.venv\Scripts\python.exe -m campus_market.cli --data-dir instance check-state
.venv\Scripts\python.exe -m campus_market.cli --data-dir instance backup backup.sqlite3
.venv\Scripts\python.exe -m campus_market.cli --data-dir instance retention
```

备份命令只备份数据库，恢复时还需保留instance/uploads图片目录。retention默认预览，只有添加`--apply`才清理过期会话、请求结果及超过保留期的日志；日志保留期不得少于180天。

## 后续验证

自动测试和浏览器脚本属于工具验证。五名真人首次使用测试、规定4核8GB机器与局域网客户端性能验收尚待执行。报告阅读确认也需由本人实际完成。
