from __future__ import annotations

import json
import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.schemas import (
    IssueCategory,
    IssuePriority,
    IssueSeverity,
    IssueStatus,
)

DB_PATH = Path(__file__).parent / "data" / "issues.db"

STATUSES = [s.value for s in IssueStatus]
PRIORITIES = [p.value for p in IssuePriority]
CATEGORIES = [c.value for c in IssueCategory]
SEVERITIES = [s.value for s in IssueSeverity]

CREATORS = [
    "李娜", "王强", "张伟", "刘芳", "陈明", "杨秀英", "赵军", "黄涛",
    "周杰", "吴敏", "孙丽", "马超", "朱琳", "胡波", "林静", "何磊",
    "高翔", "罗雪", "郑昊", "梁宇",
]

PROJECTS = [
    "支付网关", "用户中心", "订单系统", "商品平台", "营销系统",
    "物流平台", "客服系统", "风控引擎", "数据中台", "消息中心",
]

MODULES = [
    "登录模块", "支付回调", "商品搜索", "购物车", "订单创建",
    "订单结算", "优惠券", "秒杀", "退款", "对账",
]

VERSIONS = ["v1.0.0", "v1.2.3", "v2.0.0", "v2.1.0", "v2.3.1", "v3.0.0-beta"]

ENVIRONMENTS = ["PROD", "STAGING", "UAT", "SIT", "DEV"]

FAULT_CAUSES = [
    "代码逻辑错误", "配置错误", "第三方依赖故障", "数据库性能",
    "网络波动", "缓存失效", "消息积压", "权限不足",
    "数据脏读", "并发冲突",
]

FAULT_COMPONENTS = [
    "API层", "数据库", "消息队列", "Redis缓存", "定时任务",
    "前端页面", "Nginx网关", "外部接口", "认证服务", "文件存储",
]

TITLE_TEMPLATES = [
    "{}偶发{}导致{}",
    "高峰期{}时{}出现{}",
    "{}页面{}操作触发{}",
    "使用{}客户端在{}场景下发生{}",
    "数据同步时{}引起{}",
]

TITLE_TOPICS = [
    "支付回调", "订单创建", "库存扣减", "优惠券核销",
    "用户登录", "商品详情页", "结算页", "退款申请",
    "物流查询", "消息推送",
]

TITLE_PROBLEMS = [
    "偶发丢失", "响应超时", "数据不一致", "页面白屏",
    "异常报错", "重复提交", "状态未更新", "计算错误",
    "权限被拒", "加载缓慢",
]

TAGS_POOL = [
    "bug", "urgent", "regression", "ui", "performance",
    "production", "security", "compatibility", "data", "api",
    "mobile", "ios", "android", "docs", "test",
]

DESCRIPTIONS = [
    "用户反馈在特定操作流程下会触发异常，需复现并定位根因后修复。",
    "该问题影响部分用户正常使用，涉及核心交易链路，优先级较高。",
    "经过初步排查，怀疑为并发场景下的竞态条件，需补充单元测试验证。",
    "服务端日志中存在大量相关错误堆栈，已导出供分析。",
    "该问题可通过调整配置或升级依赖版本进行规避，但仍需彻底解决。",
]


def _random_time(days_back: int) -> str:
    if days_back <= 0:
        days_back = 1
    dt = datetime.now() - timedelta(
        days=random.randint(0, days_back - 1),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return dt.isoformat(timespec="seconds")


def _email(name: str) -> str:
    pinyin_map = {
        "李娜": "lina", "王强": "wangqiang", "张伟": "zhangwei",
        "刘芳": "liufang", "陈明": "chenming", "杨秀英": "yangxiuying",
        "赵军": "zhaojun", "黄涛": "huangtao", "周杰": "zhoujie",
        "吴敏": "wumin", "孙丽": "sunli", "马超": "machao",
        "朱琳": "zhulin", "胡波": "hubo", "林静": "linjing",
        "何磊": "helei", "高翔": "gaoxiang", "罗雪": "luoxue",
        "郑昊": "zhenghao", "梁宇": "liangyu",
    }
    return f"{pinyin_map.get(name, 'user')}@example.com"


def _title() -> str:
    tpl = random.choice(TITLE_TEMPLATES)
    return tpl.format(
        random.choice(TITLE_TOPICS),
        random.choice(["回调", "同步", "查询", "提交", "更新"]),
        random.choice(TITLE_PROBLEMS),
    )


def _pick_tags() -> str:
    n = random.randint(0, 4)
    chosen = random.sample(TAGS_POOL, n) if n else []
    return json.dumps(chosen, ensure_ascii=False)


def generate_issue(idx: int) -> tuple:
    issue_id = f"ISS-{100000 + idx}"
    status = random.choice(STATUSES)
    created_at = _random_time(180)
    updated_at = _random_time(30)
    resolved_at = None
    due_date = None
    if status in ("RESOLVED", "CLOSED"):
        resolved_at = _random_time(15)
    if random.random() < 0.4:
        due_date = _random_time(60)
    creator = random.choice(CREATORS)
    assignee = random.choice(CREATORS)
    title = _title()
    tags = _pick_tags()
    project = random.choice(PROJECTS)
    module = random.choice(MODULES)
    return (
        issue_id,                    # 0
        title,                       # 1
        random.choice(DESCRIPTIONS), # 2
        status,                      # 3
        random.choice(PRIORITIES),   # 4
        random.choice(CATEGORIES),   # 5
        random.choice(SEVERITIES),   # 6
        creator,                     # 7
        _email(creator),             # 8
        assignee,                    # 9
        _email(assignee),            # 10
        random.choice(CREATORS),     # 11 reporter
        created_at,                  # 12
        updated_at,                  # 13
        resolved_at,                 # 14
        due_date,                    # 15
        project,                     # 16
        module,                      # 17
        random.choice(VERSIONS),     # 18
        random.choice(ENVIRONMENTS), # 19
        random.choice(FAULT_CAUSES), # 20
        random.choice(FAULT_COMPONENTS), # 21
        "1. 打开页面 2. 输入数据 3. 触发异常",        # 22 reproduction_steps
        "系统应当正常处理该请求并返回成功。",          # 23 expected_behavior
        "请求失败，返回错误码或页面显示异常。",        # 24 actual_behavior
        tags,                                           # 25 tags (JSON)
        random.randint(0, 50),                        # 26 comments_count
        random.randint(1, 30),                        # 27 watchers_count
        random.randint(0, 20),                        # 28 votes_count
        random.randint(0, 8),                         # 29 attachments_count
    )


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS issues (
    issue_id           TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    description        TEXT NOT NULL,
    status             TEXT NOT NULL,
    priority           TEXT NOT NULL,
    category           TEXT NOT NULL,
    severity           TEXT NOT NULL,
    creator            TEXT NOT NULL,
    creator_email      TEXT NOT NULL,
    assignee           TEXT NOT NULL,
    assignee_email     TEXT NOT NULL,
    reporter           TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    resolved_at        TEXT,
    due_date           TEXT,
    project            TEXT NOT NULL,
    module             TEXT NOT NULL,
    version            TEXT NOT NULL,
    environment        TEXT NOT NULL,
    fault_cause        TEXT NOT NULL,
    fault_component    TEXT NOT NULL,
    reproduction_steps TEXT NOT NULL,
    expected_behavior  TEXT NOT NULL,
    actual_behavior    TEXT NOT NULL,
    tags               TEXT NOT NULL DEFAULT '[]',
    comments_count     INTEGER NOT NULL DEFAULT 0,
    watchers_count     INTEGER NOT NULL DEFAULT 0,
    votes_count        INTEGER NOT NULL DEFAULT 0,
    attachments_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_issues_status     ON issues(status);
CREATE INDEX IF NOT EXISTS idx_issues_priority   ON issues(priority);
CREATE INDEX IF NOT EXISTS idx_issues_creator    ON issues(creator);
CREATE INDEX IF NOT EXISTS idx_issues_assignee   ON issues(assignee);
CREATE INDEX IF NOT EXISTS idx_issues_project    ON issues(project);
CREATE INDEX IF NOT EXISTS idx_issues_created_at ON issues(created_at);
"""


def seed(n: int = 1000, force: bool = False) -> str:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(CREATE_TABLE_SQL)
        cur = conn.execute("SELECT COUNT(*) FROM issues")
        existing = cur.fetchone()[0]
        if existing > 0 and not force:
            print(f"[seed] 数据库已有 {existing} 条记录，跳过。使用 force=True 可重建。")
            return str(DB_PATH)
        if force:
            conn.execute("DELETE FROM issues")
        rows = [generate_issue(i) for i in range(1, n + 1)]
        conn.executemany(
            "INSERT INTO issues VALUES (" + ",".join(["?"] * 30) + ")",
            rows,
        )
        conn.commit()
        cur = conn.execute("SELECT COUNT(*) FROM issues")
        total = cur.fetchone()[0]
        print(f"[seed] 完成！共写入 {total} 条问题单到 {DB_PATH}")
        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM issues GROUP BY status ORDER BY status"
        ).fetchall()
        print("[seed] 按状态分布:")
        for s, c in by_status:
            print(f"       {s:<12} {c}")
        by_creator = conn.execute(
            "SELECT creator, COUNT(*) c FROM issues GROUP BY creator "
            "ORDER BY c DESC LIMIT 5"
        ).fetchall()
        print("[seed] 创建人 Top5:")
        for name, c in by_creator:
            print(f"       {name:<6} {c}")
        return str(DB_PATH)
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    n = 1000
    for a in sys.argv[1:]:
        if a.isdigit():
            n = int(a)
    seed(n=n, force=force)
