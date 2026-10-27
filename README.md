# 软件工程实验一 校园二手交易系统需求分析

需求分析工程师：马文博（241220052）；甲方：王陈钰（241220055）。

## 项目范围

完成校园闲置商品发布、审核、查询、预约、卖家确认、买家完成、异常处理、举报、日志与统计的需求分析。本实验不实现软件，不接入真实支付与物流。

## 文件

- requirements.md：24条已确认需求，其中20条功能需求、4条非功能需求。
- chat.txt：按本人最终核对的文件原样保存，包含两轮访谈、完整AI输入输出和7项审查记录。
- report.pdf及Word编辑版本：另行提供，报告填写固定后的两个提交编号，因此不参与这两个标签的提交，避免引用自身提交编号。

## 版本

- lab1-ai-draft：从当前chat提取的首轮完整AI需求，以及当时的第一轮访谈和首次交互。初稿不提前包含跟进沟通和审查。
- lab1-final：本人确认后的完整chat及已确认需求。历史输出保持原状态，最终状态仅在requirements.md中更新。

仓库按当前核对材料归档两个阶段；提交时间为实际归档时间，不表示历史访谈或模型交互时间。来源材料最初由AI辅助起草，后由本人和队友核对。标签固定后不移动、不删除、不覆盖。

## 查看与比对

```sh
git show lab1-ai-draft:requirements.md
git diff lab1-ai-draft lab1-final -- requirements.md chat.txt
git rev-parse lab1-ai-draft
git rev-parse lab1-final
```

最终提交包命名为实验1_241220052_马文博.zip，文件夹内仅放report.pdf、requirements.md和chat.txt。后两个文件须与lab1-final中的内容完全相同。公开仓库至少保留到课程结束。
