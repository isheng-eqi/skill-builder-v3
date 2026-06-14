# Skill Builder V3 → V4

Claude 驱动自进化引擎 — v4.2

## 核心哲学

> Claude 是决策者，不是执行者。每一步做独立判断，不跳过、不妥协。

## 决策树

```
CONTEXT → DEEP_AUDIT → JUDGE → FIX_THE_FIX → EXECUTE → VERIFY → RECORD → REFLECT
```

## v4.2 闭合修复循环

- **伪修复检测** → 三步强制执行 → 重写 fix 函数 → 重新验证
- Deep Audit 发现 regex scanner 永久检测不到的语义缺陷（伪修复/同义反复验证/硬编码指标/重复代码/伪因果）

## 目录结构

```
skill-builder-v3/
├── skill.md              # Skill 定义
├── manifest.json         # 版本清单
├── constitution.md       # 宪法约束
├── engine/               # 引擎模块
│   ├── loop.py           # 主决策循环
│   ├── reflect.py        # 元反思
│   ├── anchor.py         # 宪法锚定
│   ├── deep_audit.py     # 深度语义审计
│   ├── system1/          # 快速模式匹配
│   └── system2/          # 深度 LLM 推理
├── skills/               # Skill 图管理
└── references/           # 参考文档
```

## 许可

MIT License
