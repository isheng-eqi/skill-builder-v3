---
name: skill-builder-v3
description: >-
  v4.2 Claude驱动自进化引擎。闭合修复循环: 伪修复->三步强制执行->重写fix函数->重新验证。
  Claude是决策者——不跳过、不妥协——每一步做独立判断并执行到底。
  七步决策树: CONTEXT→DEEP_AUDIT→JUDGE→FIX_THE_FIX→EXECUTE→VERIFY→RECORD→REFLECT。
version: "4.2.0"
dependencies: "python>=3.10, bash, grep, sed, find"
platforms: "Linux, macOS, Windows (requires Git Bash or WSL)"
when_to_use: >-
  1. 用户发现 skill 在某环境下不工作，需要修复并确保不再复现
  2. 新建 skill 后需要反复调试直到稳定
  3. skill 换到新环境中使用（跨平台迁移）
  4. 任何 skill 相关脚本被修改后，想吸收经验防止下次再踩
  5. 用户想做深度代码分析，让 Claude 独立审查并提出架构改进
constitution: "constitution.md"
---

# Skill-Builder v4.2 — 闭合的进化循环

> **核心准则 v4.2: Python 执行。Claude 决定。不跳过伪修复。**
>
> v4.1 打通了发现和判断。但伪修复被标记后直接 SKIP——
> fix 函数本身从未被改进。v4.2 新增 Step 2.5/2.6:
> **PSEUDO_FIX → 必须重写 fix 函数。SKIP → 必须修 scanner 或记录已知问题。**
> 不允许连续两轮对同一 pattern 跳过而不改变任何代码。

---

## v4.2 决策树

### 完整流程

```
Step 0:  CONTEXT     python engine/loop.py context --target <skill> --json
Step 0.5: PRESCAN    python engine/deep_audit.py <skill> prescan
Step 0.6: GUIDED     python engine/deep_audit.py <skill> guided
Step 1:   SCAN       python engine/loop.py scan --target <skill> --json
Step 2:   JUDGE      Claude 对每个 finding 独立判断 (GENUINE/PSEUDO/SKIP/...)
Step 2.5: FIX_FIX    [PSEUDO_FIX → 重写 fix 函数]
Step 2.6: FIX_SCAN   [SKIP(scanner误报) → 调整 scanner regex]
Step 3:   EXECUTE    python engine/loop.py apply --fixes '<json>'
Step 4:   VERIFY     python engine/loop.py verify --target <skill> --json
Step 5:   RECORD     python engine/verdict.py <skill> record ...
Step 6:   REFLECT    python engine/verdict.py <skill> stats
Step 7:   DECIDE     继续 / 停止
```

---

### Step 0: COLLECT CONTEXT — 收集完整决策上下文

```bash
python engine/loop.py context --target <skill-name> --json
```

Claude 读取: findings, 所有规则的 fix 函数源码, fix 质量历史, 事件日志,
执行轨迹(含 stdout/stderr), GVU 稳定性, fitness 各分量, 历史 Claude verdict。

---

### Step 0.5: DEEP AUDIT PRESCAN — 自动预扫描语义缺陷

```bash
python engine/deep_audit.py <skill> prescan
```

12 个启发式自动扫描。有疑似问题 → Step 0.6。无 → 跳到 Step 1。

---

### Step 0.6: GUIDED SELF-AUDIT — Claude 自主审计

```bash
python engine/deep_audit.py <skill> guided
```

将每个可疑点变成: 精确行号 + 带行号代码片段 + 审查问题 + finding 模板。
Claude 阅读→判断→填写 finding→record 到进化管道。

---

### Step 1: SCAN & MERGE — 合并所有发现

```bash
python engine/loop.py scan --target <skill-name> --json
python engine/deep_audit.py <skill> merge
```

完整 finding 列表 = scanner 表面问题 + deep audit 语义缺陷。

---

### Step 2: JUDGE — Claude 独立判断每个 finding

对每个 finding 问三个问题:

1. **值得修吗？** severity=info/有意保留 → SKIP。影响功能/安全 → WORTH_FIXING。
2. **fix 函数真的改变了行为吗？** 读 `rules[].fix_source` 源码。只加 TODO → PSEUDO_FIX。真正修改代码 → GENUINE_FIX。
3. **如果伪修复 → 必须执行 Step 2.5。不允许跳过。**

---

### Step 2.5: FIX THE FIX — 强制执行规则

**这是 v4.2 闭合进化循环的关键。** v4.1 的失败原因: PSEUDO_FIX 被标记后就直接 SKIP——fix 函数从未被改进。

| 判断 | 强制执行 |
|------|---------|
| **PSEUDO_FIX** | 重写 fix 函数，使其真正改变代码行为。编辑 `engine/system1/rules.py` 或 `data/generated_rules/<id>.py` |
| **NEEDS_IMPROVEMENT** | 改进 fix 函数——扩大覆盖范围或增加验证 |
| **SKIP (scanner 误报)** | 执行 Step 2.6——调整 scanner |
| **SKIP (有意不修)** | 写入 known-issues.md 标注原因 |
| **GENUINE_FIX** | 直接执行 Step 3 |

**禁令**: 连续两轮对同一个 pattern 判断为 PSEUDO_FIX 或 SKIP 而不改任何代码 **绝对禁止。**
第二轮同一 pattern 仍旧伪修复 → **立即重写 fix 函数，不管多复杂。**

**操作**:
1. 阅读当前 fix 函数源码 (context 输出中 `rules[].fix_source`)
2. 设计真正的修复（Claude 用自己对代码的理解设计）
3. Edit 目标文件——把 `# TODO: xxx` 替换为真正的代码修改
4. 重写后重新扫描确认新 fix 函数编译通过

---

### Step 2.6: FIX THE SCANNER — 对 false positive 调整检测

Scanner regex 过于激进将合法代码标记为问题 → 立即调整。

1. 确认 pattern+regex
2. regex 太宽 → 缩小匹配范围
3. 文件应被排除 → 添加到 exclude_files 列表
4. 编辑 `engine/system1/scanner.py`

**完成后** → 回到 Step 1 重新扫描。

---

### Step 3: EXECUTE — 只应用已验证的修复

**只有经过 Step 2.5 重写的 fix 函数才能用于修复。**

禁止: 任何未经重写的 PSEUDO_FIX 规则。看到 PSEUDO_FIX → 回到 Step 2.5。

```bash
python engine/loop.py apply --target <skill-name> --fixes '<json>'
```

---

### Step 4: VERIFY — Claude 独立验证

```bash
python engine/loop.py verify --target <skill-name> --json
```

Claude 不看 Python 的 auto-success。自己判断:
- 代码行为真的改变了吗？还是只加了一行注释？
- 验证是独立于修复方法的吗？还是同义反复？

---

### Step 5: RECORD — 写入 Claude 的真实判断

```bash
python engine/verdict.py <skill> record \
  --rule <rule_name> --file <file> \
  --verdict <GENUINE_FIX|PSEUDO_FIX|REGRESSION|...> \
  --explanation "你的解释" --evidence "证据"
```

---

### Step 6: REFLECT — Claude 反思统计

```bash
python engine/verdict.py <skill> stats
```

- 伪修复率 >30%? → 那 3 个 tag-* 规则需要重写，不是标记
- 有 REGRESSION? → 修 fix 函数本身
- 全部 GENUINE? → 健康
- 全部 SKIP? → 检查 scanner 或 fix 函数

---

### Step 7: DECIDE — 继续还是停止

- 还有值得修 + 上次 fix 已被 Step 2.5 重写过 → 继续
- 所有 SKIP 都符合 known-issues 或 scanner 已修正 → 真正收敛
- 伪修复率持续高且 Step 2.5 未执行 → **不回 Step 1，先执行 Step 2.5**

---

## 快速命令

```bash
# Step 0:   python engine/loop.py context --target <skill> --json
# Step 0.5: python engine/deep_audit.py <skill> prescan
# Step 0.6: python engine/deep_audit.py <skill> guided
# Step 1:   python engine/loop.py scan --target <skill> --json
#           python engine/deep_audit.py <skill> merge
# Step 3:   python engine/loop.py apply --target <skill> --fixes '<json>'
# Step 4:   python engine/loop.py verify --target <skill> --json
# Step 5:   python engine/verdict.py <skill> record ...
# Step 6:   python engine/verdict.py <skill> stats
# Other:    python engine/loop.py auto --target <skill>  (legacy)
#           python engine/reflect.py --target <skill> --cross-skill
#           python engine/anchor.py verify-self
```

## 核心原则

1. **Python 执行。Claude 决定。** 确定性操作用 Python，所有判断用 Claude。
2. **PSEUDO_FIX 必须重写。不允许跳过。** v4.2 的闭合机制。
3. **Claude 的 verdict 是 ground truth。** Python auto-success 只是参考。
4. **独立验证。** Claude 设计自己的验证标准。
5. **自指闭环。** 同一套流程优化自己，无特殊路径。
