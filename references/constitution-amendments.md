# 宪法修正记录

> 本文记录 constitution.md 的每一次修改。
> 宪法只能被人修改——本文件是对人每次决定修改宪法时留下的记录。
> 由 `realizability.py` 审计发现差距后写入草案，人审批后确认。

---

## 修正 #1 — 2026-06-13：宪法 v1.0 → v1.1 可实现性统一修正

**触发审计**: `python engine/realizability.py` 首次运行

**发现的差距:**

| # | 宪法 v1.0 声称 | 引擎实际 | 差距 |
|---|--------------|---------|------|
| 1 | "grep -rn 'skill-builder-v3' 只能在注释和测试中出现" | anchor/scanner/patterns 必须在条件分支中检查自身名 | 宪法把免疫系统误判为违规 |
| 2 | "检查所有模块的 --help" | 仅检查 2 个关键模块 | 声称检查量 > 实际检查量 |
| 3 | "4 项基础检查全部执行" | 2 项完整 + 2 项部分 | 声称完成度 > 实际完成度 |
| 4 | CLI 默认值全部硬编码为自身名称 | 5 处 CLI 默认 `skill-builder-v3` | 违反了"不给自身留特殊路径" |
| 5 | 无宪法自我修正程序 | 无 | 宪法不可变但无"发现错误怎么办"的程序 |

**修正内容:**

1. 新增**第零法则（可实现性）**——宪法声明必须有可验证真值，不可验证的声明不是法律
2. 第一法则区分"宪法执行代码"（免疫系统）和"真正的特殊路径"——白名单四文件
3. 第三法则措辞从"所有模块"修正为"关键顶层模块"并增加诚实声明段落
4. 第五法则每项检查标注 ✅/⚠️ 诚实状态，增加"诚实声明"段落
5. 第四法则增加"诚实声明: 代码门控 ≠ 真正的人在回路"
6. 新增不可变声明中的"修正程序"
7. 移除所有 CLI 默认值中的自身名称硬编码
8. 新增 `engine/realizability.py` 宪法可实现性审计

**修正后审计结果:**
```
✅ 第零法则 — 自我实现
✅ 第一法则 — 0 违规 + 0 CLI 默认自身
✅ 第二法则 — 记录基础设施完整
✅ 第三法则 — 宪法声明与引擎能力一致
✅ 第四法則 — 代码门控有效，诚实声明限制
✅ 第五法则 — 诚实标注完整/部分检查
⚠️  平台声明 — 仅在 Windows 上验证过（已知诚实差距）
```

**批准:** 人工 | **验证:** `python engine/anchor.py pre-modify-check` → 4/4 passed

---

## 修正 #2 — (待下次审计发现)


## 修正 #3 — 2026-06-13 06:12 UTC：平台声明可实现性差距

**触发**: `python engine/realizability.py` 自动运行

**差距:**
  ⚠️ 附：平台声明可实现性: [--] 仅在 {'windows'} 上验证过。未验证: {'macos', 'linux'}。宪法诚实声明此差距。

**状态:** 已知诚实差距——无需修正宪法，已在宪法第三/五法则中诚实标注
**验证:** `python engine/anchor.py pre-modify-check`


## 修正 #4 — 2026-06-13 10:01 UTC：第一法则自指违规 (已修复 ✅)

**触发**: `python engine/realizability.py` 自动运行

**差距:**
  ❌ 第一法则：自指闭环: [!!] 11 处真正违规 + 0 处 CLI 默认自身名

**修复 (2026-06-13 12:20 UTC):** anchor.py `check_self_reference()` 排除列表从
`("anchor.py", "patterns.py")` 扩展为 `("anchor.py", "patterns.py", "scanner.py", "realizability.py")`，
与宪法第零+第一法则的免疫系统声明一致。
**验证:** `python engine/anchor.py verify-self` → 全部通过 ✅
**状态: 已关闭**


## 修正 #5 — 2026-06-13 11:03 UTC：第三法则 --help 检查不一致 (已修复 ✅)

**触发**: `python engine/realizability.py` 自动运行

**差距:**
  ❌ 第三法则：验证优先: [!!] 发现不一致: ['宪法声称检查 --help 但 anchor 仅检查 0 个模块']

**修复:** anchor.py pre-modify-check 的 module_help 检查已更新为检查 4 个关键模块
(memory.py, signature.py, evidence.py, memory_index.py)
**验证:** `python engine/anchor.py pre-modify-check` → 3/3 passed ✅
**状态: 已关闭**


## 修正 #6 — 2026-06-13 15:53 UTC：宪法可实现性审计自动发现

**触发**: `python engine/realizability.py` 自动运行

**差距:**

  ❌ 第零法则：可实现性（元规则）: [!!] 第零法则不可实现——缺少审计器或修正程序
  ❌ 第五法则：不破坏基础: [!!] 宪法声称超过引擎实际能力，且未诚实标注

**状态:** 待人工审批——宪法只能被人修改（第四法则）
**验证:** `python engine/anchor.py pre-modify-check`
