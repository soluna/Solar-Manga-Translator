#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDS_DIR = REPO_ROOT / "docs" / "evals" / "records"


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned or "eval"


def build_content(record_date: str, title: str, baseline: str) -> str:
    return f"""# 核心流程体验评分卡（{title}）

- 日期：{record_date}
- 版本 / 分支：
- 评估人：
- 对比基线：{baseline}

## 结论

- 总分：`__/100`
- 是否可接受：`是 / 否`
- 是否存在 P0：`是 / 否`
- 与上一轮相比：`变好 / 持平 / 变差`

## 分项评分

| 维度 | 分值 | 得分 | 说明 |
| --- | --- | --- | --- |
| 端到端完成率 | 40 |  |  |
| 审校编辑稳定性 | 30 |  |  |
| 预览与结果一致性 | 20 |  |  |
| 恢复与异常鲁棒性 | 10 |  |  |

## 固定场景结果

| 场景 | 结果 | 备注 |
| --- | --- | --- |
| S1 单页标准流程 | 通过 / 不通过 |  |
| S2 多页压缩包流程 | 通过 / 不通过 |  |
| S3 识别后再审阅流程 | 通过 / 不通过 |  |
| S4 历史项目恢复流程 | 通过 / 不通过 |  |
| S5 异常恢复流程 | 通过 / 不通过 |  |

## 本轮 3 个最影响体验的问题

1. 
2. 
3. 

## 本轮 3 个最明显改善点

1. 
2. 
3. 

## 关键观察

- 
- 
- 

## 判定依据

- 是否出现主链路中断：
- 是否出现选中跳变 / 框回弹 / 输入回滚：
- 预览是否足够可信：
- 历史项目与异常场景是否可恢复：

## 下一步建议

1. 
2. 
3. 
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="生成核心流程体验评分卡记录模板")
    parser.add_argument("--date", default=date.today().isoformat(), help="记录日期，默认今天")
    parser.add_argument("--slug", default="core-flow-iteration", help="文件名 slug")
    parser.add_argument("--title", default="迭代记录", help="文档标题中的记录名")
    parser.add_argument(
        "--baseline",
        default="docs/evals/records/2026-04-03-core-flow-baseline-v1.md",
        help="默认对比基线",
    )
    args = parser.parse_args()

    record_date = args.date.strip() or date.today().isoformat()
    slug = slugify(args.slug)
    filename = f"{record_date}-{slug}.md"
    target = RECORDS_DIR / filename

    if target.exists():
        raise SystemExit(f"记录已存在：{target}")

    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(
        build_content(record_date=record_date, title=args.title.strip() or "迭代记录", baseline=args.baseline.strip()),
        encoding="utf-8",
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
