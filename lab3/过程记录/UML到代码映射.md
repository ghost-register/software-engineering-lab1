# UML到代码映射

| UML或模块 | 实际实现 | 主要验证 |
|---|---|---|
| User与权限 | domain.User、AuthService、Repository.actor/admin | 注册、会话撤销、11个越权HTTP请求 |
| Product与审核 | domain.Product、ProductService、products/reviews/images表 | 草稿、真实图片、驳回原因与重审 |
| Reservation与请求结果 | domain.Reservation、TradingService、reservations/request_results表 | 旧键重发、价格快照、唯一有效预约 |
| 商品与预约状态图 | ProductState/ReservationState及应用服务前态检查 | 竞争转换、终态保留、故障回滚 |
| 治理与异常关闭 | GovernanceService、reports/close_requests表 | 举报去重、关闭批准、成交后追加处置 |
| 检索、日志、统计 | QueryService | 组合筛选、权限、时区与快照金额 |
| UI与接口层 | Flask路由、Jinja模板与app.js | 11项自动化浏览器检查 |
| 数据访问与事务 | Database、Repository、Service.perform | BEGIN IMMEDIATE、部分唯一索引、独立失败审计 |

实验二采用PostgreSQL设计；实验三按用户确认改用SQLite，写事务串行核查，不使用SQLite不支持的SELECT FOR UPDATE。金额采用整数分，UUID采用32位十六进制文本。原有实验二资料保留，lab3/UML提供与本次实现对应的类、组件和时序图；行为图继续沿用原业务规则。

主要对象以应用服务与数据表组合实现，不要求将每张表包装成一个只有字段的类。请求结果、审核记录、举报与审计的结构见schema.sql。
