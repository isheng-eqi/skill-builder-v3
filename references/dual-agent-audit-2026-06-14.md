# 双Agent架构审计报告：Observer vs Executor 分离度

> **日期**: 2026-06-14
> **审计范围**: 全部 30 个引擎模块
> **核心问题**: skill-builder-v3 声称实现了双Agent架构（Observer 观测 + Executor 迭代）。这到底是真的双Agent，还是单Agent穿了两件衣服？

---

## 一、架构声明 vs 实际实现

### 声明的架构

```
┌─────────────────┐         ┌─────────────────┐
│  Agent A        │  审查   │  Agent B        │
│  Observer       │ ──────→ │  Executor       │
│  (Reflect)      │         │  (Loop)         │
│  持宪法          │         │  反复迭代执行     │
└─────────────────┘         └─────────────────┘
```

### 实际实现

```
同一个 Python 进程
┌─────────────────────────────────────────────────┐
│  loop.py::run_loop()                            │
│                                                 │
│  for round in range(max_rounds):                │
│    generate_fixes()   ← Executor                │
│    evaluate_fixes()   ← Executor                │
│    select_fixes()     ← Executor                │
│    integrate_fixes()  ← Executor                │
│    verify_execution_independent() ← 子进程 ✅   │
│                                                 │
│    if converged:                                │
│      _reflect_and_extend_scanner()              │
│        reflect.run_reflect() ← Observer 被调用   │
│        rule_generator.generate() ← 规则自生成    │
│        scanner.scan_skill()    ← 重新扫描       │
│        if new_findings > 0:                     │
│          converged = False  ← Executor 决定     │
│          continue           ← 是否继续          │
└─────────────────────────────────────────────────┘
```

---

## 二、十个维度的分离度评分

### 维度 1: 进程隔离

**评分: ⚠️ 2/10 — 共享进程，仅验证步骤独立**

```python
# loop.py:826 — Observer 在 Executor 的进程内被 import
from engine import reflect
reflect_result = reflect.run_reflect(skill_name, ...)
```

Observer (reflect.py) 和 Executor (loop.py) **共享同一个 Python 解释器**。reflect 是 loop 内部的一个函数调用——不是独立 agent。

**唯一的例外**: `verify_execution_independent()` (loop.py:340-355) 启动子进程运行 scanner。但这不是 Observer——这只是 Executor 的验证步骤。

**前沿对比**:
- Ultragoal: Observer 是完全独立的子 agent，不在同一上下文中
- Constitutional AI: Critic 和 Actor 是两个独立模型，不同推理调用
- Gödel Machine: Proof searcher 和 Rewrite agent 是不同的搜索进程

**当前状态**: 离前沿有差距。Observer 是 Executor 的一个函数调用。

---

### 维度 2: 信息防火墙

**评分: ✅ 8/10 — Observer 只看事件日志，不看 Executor 内部状态**

```python
# reflect.py:59 — Observer 读取的是不可变事件日志
events = memory.read_events(skill_name, limit=lookback)
insights = memory.read_insights(skill_name)
```

Observer 的输入是:
- ✅ 事件日志 (仅追加，不可变)
- ✅ 历史洞察 (JSON 文件)
- ✅ 执行轨迹 (GEPA traces)

Observer **不访问**:
- ✅ Executor 的运行时变量 (rounds list, fix_fingerprints 等)
- ✅ Executor 的中间决策过程
- ✅ 哪些候选被淘汰了、为什么

**这是架构最强的部分。** Observer 的观察基于 Executor 输出的事件日志——不可变的、可审计的记录——而不是 Executor 的推理过程。

**前沿对齐**: 符合 Ultragoal "fresh-eyes" 原则——Observer 不知道 Executor 怎么干的，只看干了什么的结果。

---

### 维度 3: 宪法绑定

**评分: ✅ 9/10 — 宪法不可被任何一方修改**

```python
# realizability.py:471
写入的是"待审批"草案，不直接修改宪法。宪法只能被人改。

# anchor.py:77-78 — 免疫系统文件保护
if py_file.name in ("anchor.py", "patterns.py", "scanner.py", "realizability.py"):
    continue
```

Executor 不能修改 constitution.md。Observer 也不行。程序只能写入 `constitution-amendments.md` 作为"待审批草案"。

**前沿对齐**: 符合 Constitutional AI 的"宪法由人设定，模型不能修改"原则。

**已知缺陷** (red-team audit 已发现): anchor.py 的 `verify_self()` 只执行 2/6 法则。法则 3/4/5 需要额外参数。这个缺陷使宪法验证不完整——但宪法本身是完整的。

---

### 维度 4: Observer 能否阻止 Executor

**评分: ❌ 3/10 — Observer 可以建议，不能否决**

```python
# loop.py:1268-1280 — 收敛后的自进化回路
extended, new_findings = _reflect_and_extend_scanner(
    skill_name, rounds, max_rounds, round_num
)
if extended:
    converged = False
    continue  # 继续进化
else:
    break     # 停止
```

Observer 的结论被 Executor 用作**建议**——不是命令。Observer 说"有新发现"→ Executor 继续循环；Observer 说"没新发现"→ Executor 停止。但如果 Observer 说"你做错了"，Executor 没有代码路径处理这个。

**缺失的关键路径**:
```python
# 不存在这样的代码：
if observer_says_stop and executor_wants_to_continue:
    escalate_to_human()  # 僵局 → 人介入
```

**前沿对比**: Gödel Machine 中 proof searcher 发现 proof→rewrite agent **必须**执行。Ultragoal 中 fresh-eyes verifier 不通过→worker **不能**声称完成。

---

### 维度 5: 独立验证的真正独立性

**评分: ✅ 7/10 — 子进程隔离，但验证范围有限**

```python
# loop.py:340-355 — 真正独立的子进程验证
scan_cmd = (
    f'"{sys.executable}" -c "'
    f'import sys; sys.path.insert(0, r\'{root}\'); '
    f'from engine.system1 import scanner; '
    f'r = scanner.scan_skill(\'{skill_name}\'); '
    f'print(r.get(\\\'findings_count\\\', -1))"'
)
result = subprocess.run(scan_cmd, shell=True, ...)
```

独立验证器:
- ✅ 运行在独立子进程中
- ✅ 不知道哪些文件被修改了
- ✅ 不知道 Executor 的修复策略
- ⚠️ 但验证内容只是"重新扫描一次"——如果 scanner 本身有盲区，独立验证也看不到
- ⚠️ 子进程仍复用同一套 engine/ 代码——如果 Executor 污染了 engine/ 文件，子进程也会受影响

**关键限制**: 独立验证器的"独立"是进程级的，不是代码级的。它运行的是 Executor 刚修改过的同一套 engine 代码。

---

### 维度 6: 记忆共享 vs 状态共享

**评分: ⚠️ 5/10 — 通过事件日志解耦，但日志写入方和执行方是同一个**

```
Executor 写入              Observer 读取
─────────────             ─────────────
loop.py → memory.py       reflect.py ← memory.py
  write_event("scan")       read_events()
  write_event("fix")        read_insights()
  write_event("exec_trace") 
```

好的方面:
- ✅ 事件日志是仅追加的——Executor 不能删除或修改已写的事件
- ✅ Observer 只读不写（除了 insights）

坏的方面:
- ❌ Executor 控制写什么事件——可以选择性地不写"尴尬"的事件
- ❌ 没有外部审计者验证"事件日志是否真实反映了执行过程"
- ❌ `except ImportError: pass` 模式 (30+ 处) 意味着很多失败根本不会被记录

**前沿对比**: EVE-Agent 的 evidence span 要求独立第三方验证证据。当前系统没有独立第三方。

---

### 维度 7: 双层学习循环的闭合度

**评分: ⚠️ 5/10 — 循环存在，但由 Executor 单向驱动**

```
Layer 1 (Executor 循环):
  Generate → Evaluate → Select → Integrate → Verify
  └────────────────── 10 轮 ──────────────────────┘

Layer 2 (Observer 循环):  
  Meta-Observe → Meta-Question → Meta-Propose → Meta-Validate
  └── 仅在 Layer 1 声称收敛时触发 ──────────────────┘
```

**问题**: Observer 是被动的。它不主动监控——只有 Executor 调用它时它才运行。

**理想的双Agent架构应该是**:
```
Observer:  持续监控事件日志 → 发现异常 → 主动触发审查
Executor:  接收审查结果 → 调整策略 → 继续迭代
```

**当前实现**:
```
Executor:  跑完 10 轮 → "我觉得我收敛了" → 调用 Observer
Observer:  分析日志 → "有新发现" → Executor 决定是否继续
```

Observer 的触发权在 Executor 手里。如果 Executor 永远不收敛（一直有 1 个发现），Observer 永远不会被触发。

---

### 维度 8: 多Agent协作（HyperAgents 跨技能）

**评分: ✅ 7/10 — 真正实现了跨技能知识迁移**

```python
# reflect.py:140-225 — 真正的跨技能扫描
for skill_name in all_skills:
    scan = sys1_scanner.scan_skill(skill_name)
    # 收集所有技能的 pattern
    # 找出 3+ 技能共享的模式 → global_insight
    # 自动应用到受影响的技能
```

这是 HyperAgents 蒸馏中最有价值的部分:
- ✅ 一个 skill 的发现自动传播到其他 skill
- ✅ 共享模式被标记为系统级问题
- ✅ 免疫系统缺失的 skill 被标记为 bootstrap 候选

**但是**: 跨技能扫描仍然是 `reflect.py` 的一个函数，不是独立运行的 agent。它需要被手动调用 (`--cross-skill`) 或由 loop.py 在收敛时触发。

---

### 维度 9: 僵局处理

**评分: ⚠️ 4/10 — 有检测，无升级**

```python
# loop.py:783-791 — 检测独立验证分歧
indep_disagreements = [...]
if len(indep_disagreements) >= 2:
    reasons.append("独立验证分歧: ...")
```

系统能**检测**僵局:
- ✅ 独立验证与自我验证持续不一致
- ✅ 同一模式反复出现 (stubborn patterns)
- ✅ GEPA 执行验证持续失败

但**响应**很弱:
- ❌ 分歧 ≥2 次 → 触发 System 2 审查 (`_trigger_system2`)——但这个函数只是写一条 insight
- ❌ 没有"暂停自改进 → 等待人工介入"的机制（虽然有 `ask_gate.py` 但响应的只是打印一行日志）
- ❌ `escalate_to_human` flag 存在但仅打印建议——没有真正阻断执行

```python
# loop.py:1141-1142 — 升级到人，但仅建议
if gate.get("escalate_to_human"):
    print(f"  [S5:AskGate] → 建议暂停自改进, 等待人工校准")
    # 没有 break / return / raise ——执行继续
```

---

### 维度 10: 规则自生成的闭环

**评分: ⚠️ 5/10 — 链条存在但 fragile**

```
Observer 发现新模式
  → rule_generator.generate_rule_from_stubborn_pattern()
    → 写入 data/generated_rules/ (JSON)
      → scanner 加载生成规则（与硬编码规则合并）
        → 新规则进入试用期 (probation)
          → 5 次成功后晋升为 active
```

这个链条理论上完整，但实际上:
- ❌ 生成规则加载包裹在 `except ImportError: pass` 中——如果 rule_generator 有语法错误，所有生成规则静默丢失
- ❌ 试用期晋升依赖 `integrate_fixes()` 中的手动调用——没有自动晋升机制
- ⚠️ 13 条已生成规则全部在 probation 状态——没有一条晋升为 active

**前沿对比**: Gödel Agent 的规则生成→自动部署→证明→采纳是完全自动化的。Yunjue Agent 的"工具进化"也是自动的。

---

## 三、总体评分

```
维度                      评分    前沿对齐度
───────────────────────  ──────  ──────────
1. 进程隔离                2/10    ❌ 共享进程
2. 信息防火墙              8/10    ✅ 只看事件日志
3. 宪法绑定                9/10    ✅ 不可修改
4. Observer否决权          3/10    ❌ 只能建议
5. 独立验证独立性           7/10    ⚠️ 子进程隔离，代码共享
6. 记忆/状态分离            5/10    ⚠️ 日志单向，无外部审计
7. 双层循环闭合             5/10    ⚠️ Executor单向驱动
8. 跨Agent知识迁移          7/10    ✅ 真正实现
9. 僵局处理                4/10    ⚠️ 有检测无阻断
10. 规则自生成闭环          5/10    ⚠️ 存在但fragile
───────────────────────  ──────
总分                      55/100
```

## 四、结论

### 是双Agent吗？

**是，但是一个初级的、不对称的双Agent。**

如果你把"双Agent"定义为两个完全独立、相互制衡、任何一方都能否决另一方的进程——那 **不是**。

如果你把"双Agent"定义为两个不同角色、不同职责、通过不可变日志通信的模块——那是。

当前实现的本质是:

```
┌──────────────────────────────────────────────┐
│  Executor (loop.py)  ← 主导方                │
│  ┌──────────────────────────────────────┐    │
│  │  自己决定何时调用 Observer            │    │
│  │  自己决定是否采纳 Observer 的建议      │    │
│  │  自己决定何时继续/停止                │    │
│  └──────────────────────────────────────┘    │
│                    ↓ 调用 (同进程)             │
│  ┌──────────────────────────────────────┐    │
│  │  Observer (reflect.py) ← 从属方       │    │
│  │  只能读日志 → 提建议                  │    │
│  │  不能阻止 Executor                    │    │
│  │  不能修改宪法                          │    │
│  └──────────────────────────────────────┘    │
└──────────────────────────────────────────────┘
```

### 跟前沿差距在哪

| 前沿框架 | 差距 |
|---------|------|
| **Ultragoal** | Observer 应该是独立进程/agent，不在 Executor 进程内 |
| **Gödel Machine** | Proof searcher 对 Rewrite agent 有硬否决权——当前 Observer 没有 |
| **Constitutional AI** | Critic 审查**每一次**输出——当前 Observer 只在收敛时触发 |
| **CRESCENT** | 多次采样 + 多数表决——当前只有一次 Observer 判断 |

### 最强的部分

1. **信息防火墙** (8/10): Observer 只读不可变事件日志，不像 Executor 那样能看到内部状态。这比很多自称"双Agent"的系统做得更好。
2. **宪法不可变性** (9/10): 任何代码都不能改宪法。草案只能写入单独文件等人审批。
3. **独立验证子进程** (7/10): 虽然代码共享，但进程隔离是真实的——子进程不知道哪个文件被改了。

### 最弱的部分

1. **Observer 没有否决权** (3/10): Observer 说"停"→ Executor 可以不听。这在 Gödel Machine 或 Constitutional AI 中是不可接受的。
2. **Observer 由 Executor 触发** (5/10): Observer 是被动的。真正的双Agent应该是 Observer 持续监控、主动介入。
3. **静默吞异常破坏审计追踪** (5/10): 30+ 处 `except ImportError: pass` 意味着关键失败可能不被写入事件日志——Observer 根本看不到。

---

## 五、改进优先级

| # | 改进 | 影响维度 | 难度 |
|---|------|---------|------|
| 1 | Observer 获得硬否决权——分歧时暂停循环 | 维度4 | 中 |
| 2 | Observer 独立进程/独立触发（不依赖 Executor 调用） | 维度1 | 高 |
| 3 | 审计事件日志完整性——检测"未记录"的失败 | 维度6 | 中 |
| 4 | 升级僵局时真正阻断执行，而非仅打印建议 | 维度9 | 低 |
| 5 | 规则自生成的自动晋升机制（不依赖 loop.py 手动调用） | 维度10 | 中 |
