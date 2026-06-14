# 事件 Schema 契约

> 每种 event_type 的 data 结构声明。由模式 15 驱动——写入侧和读取侧必须对齐。
> 新增 event_type 时必须在此声明；scanner 规则 `schema-drift-check` 对比写入/读取侧 key。
>
> **版本**: 1.0 | **创建**: 2026-06-13 | **来源**: Pattern 15 — 事件 Schema 漂移

---

## 通用字段

所有事件共享的顶层结构（由 `memory.py:write_event()` 自动注入）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `event_id` | `string` | 格式 `evt-YYYYMMDD-HHMMSS-ffffff` |
| `event_type` | `string` | 事件类型标识符 |
| `iso_timestamp` | `string` | ISO 8601 UTC 时间戳 |
| `skill` | `string` | 目标技能名称 |
| `data` | `object` | 事件类型特定的数据载荷 |

---

## 事件类型 (`memory.py:EVENT_TYPES`)

共 14 种。以下声明每种类型的 data 契约。

### 1. `scan` — System 1 快速扫描完成

**写入侧模块**: `loop.py:integrate_fixes()` (line 229), `bootstrap.py:run()` (line 360)
**读取侧模块**: `anti_gaming.py` (filter by event_type), `blindspot.py` (filter by event_type)

```json
{
  "findings_count": 0,
  "module": "scanner",
  "findings": [
    {
      "id": "finding-001",
      "severity": "critical|warning|info",
      "file": "relative/path.py",
      "line": 42,
      "pattern": "pattern-name",
      "message": "human-readable description"
    }
  ]
}
```

**契约规则**:
- `findings_count` 必须等于 `findings` 数组长度
- 读取侧可以通过 `e["data"]["findings_count"]` 或 `len(e["data"].get("findings", []))` 读取

### 2. `fix` — 确定性修复应用

**写入侧模块**: `loop.py:integrate_fixes()` (line 231), `memory.py:record_descendant_link()` (line 600), `hooks_bridge.py` (line 133)
**读取侧模块**: `ask_gate.py:check_calibration()` (line 50), `loop.py` (line 706)

```json
{
  "fixes": {
    "applied": 2,
    "skipped": 0,
    "total": 2,
    "details": [
      {
        "finding_id": "finding-001",
        "rule": "add-utf8-header",
        "file": "engine/loop.py",
        "action": "added UTF-8 header"
      }
    ]
  },
  "snapshot": "snap-20260613-120000",
  "module": "system1"
}
```

**fix 事件的两种子类型**:
- **修复事件** (来自 `loop.py`): `data["fixes"]` 存在，`data["fixes"]["applied"]` 为修复数
- **后裔链接事件** (来自 `memory.py`): `data["phase"] == "descendant_link"`，包含 `parent_rule`/`child_rule`/`relationship`

**历史 Schema 漂移记录** (已修复):
- v1.2 前: `ask_gate.py` 读取 `data["fixes_report"]` / `data["fix_report"]`，但写入侧使用 key `"fixes"` → 已修复为 `data.get("fixes", data)` 兼容链

### 3. `integrate` — 集成快照

**写入侧模块**: `loop.py:integrate_fixes()` (lines 233, 655, 912)
**读取侧模块**: 直接引用

```json
{
  "snapshot_id": "snap-20260613-120000",
  "files_changed": ["engine/loop.py", "references/known-issues.md"],
  "fixes_applied": 2,
  "fixes_skipped": 0,
  "phase": "integrate"
}
```

### 4. `loop` — Layer 1 循环事件

**写入侧模块**: `loop.py` (lines 817, 834, 946, 1076), `checkpoint.py` (line 157), `hooks_bridge.py` (lines 106, 233, 256), `hillclimb.py` (lines 218, 293, 344), `parallel.py` (lines 267, 292)

```json
{
  "phase": "start|complete|error|hillclimb_start|hillclimb_complete",
  "round": 3,
  "max_rounds": 10,
  "findings": 0,
  "fixes": 0,
  "converged": true,
  "params": {
    "stop_rounds": 2,
    "meta_audit_interval": 5
  }
}
```

**phase 枚举值**:
- `start` — 循环开始
- `complete` — 单轮完成
- `error` — 异常终止
- `hillclimb_start` / `hillclimb_complete` — 爬山优化阶段
- `convergence_check` — 收敛判定

### 5. `execution_trace` — GEPA 执行轨迹验证

**写入侧模块**: `loop.py:verify_execution_independent()` (lines 356, 456, 998)
**读取侧模块**: `ask_gate.py:check_calibration()` (line 51), `evidence.py` (line 411), `loop.py` (lines 741, 758)

```json
{
  "phase": "independent_verification",
  "success": true,
  "verdict": "agreement|disagreement|no_fixes_to_verify",
  "independent": {
    "verdict": "[OK] ...",
    "details": {}
  },
  "self": {
    "verified": true
  }
}
```

**契约规则** (GEPA 原则):
- 每次 `integrate_fixes` 后无条件写入——即使无修复也记录
- `success` 仅在 independent 和 self 验证一致时为 true
- GVU SNR 融合 execution_trace 作为独立验证信号

### 6. `evidence_span` — 修复证据链

**写入侧模块**: `evidence.py:record_span()` (line 460)
**读取侧模块**: `evidence.py:verify_and_score()` (internal)

```json
{
  "span_id": "span-20260613-120000",
  "command": "python -m py_compile engine/loop.py",
  "command_type": "compile|command|scan",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "duration_ms": 45,
  "fix_id": "fix-001",
  "rule": "add-utf8-header",
  "success": true
}
```

### 7. `reflect` — Layer 2 反思

**写入侧模块**: `reflect.py` (line 606), `trace_distiller.py` (lines 460, 520), `loop.py` (line 1237)

```json
{
  "phase": "start|complete",
  "observations": 20,
  "stubborn_patterns": {},
  "questions": 1,
  "proposals": 1,
  "verified": 1,
  "rejected": 0
}
```

### 8. `insight` — 新洞察生成

**写入侧模块**: `reflect.py` (line 662)
**读取侧模块**: `memory_index.py` (FTS5 indexed)

```json
{
  "title": "简短摘要",
  "body": "完整洞察文本",
  "confidence": 0.8,
  "source": "reflect.py",
  "tags": ["schema-drift", "verification"]
}
```

### 9. `global_insight` — 跨技能元洞察

**写入侧模块**: `memory.py:write_global_insight()` (line 233)
**读取侧模块**: `reflect.py:cross_skill_scan()`

```json
{
  "title": "跨技能模式",
  "body": "在 3+ 个 skill 中观察到的模式",
  "affected_skills": ["skill-a", "skill-b", "skill-c"],
  "confidence": 0.7,
  "source": "cross_skill_scan"
}
```

### 10. `propose` — 架构变更提案

**写入侧模块**: `skill_composer.py` (line 169)
**读取侧模块**: `anchor.py:verify-proposal`

```json
{
  "proposal_id": "prop-20260613-120000",
  "title": "提案标题",
  "description": "提案详细描述",
  "rationale": "变更理由",
  "files_changed": ["file1.py", "file2.md"],
  "confidence": 0.5,
  "status": "draft|verified|accepted|rejected"
}
```

### 11. `rule_generated` — 规则自动生成

**写入侧模块**: `system2/rule_generator.py` (lines 220, 331, 506)
**读取侧模块**: `rules.py:merge_generated_rules()`

```json
{
  "rule_name": "check-encoding",
  "rule_source": "blindspot|pattern|textgrad",
  "rule_body": "... Python code as string ...",
  "confidence": 0.7,
  "generation_round": 5
}
```

### 12. `verify` — 提案验证结果

**写入侧模块**: (当前无活跃写入者)
**读取侧模块**: `anchor.py:verify-proposal`

```json
{
  "proposal_id": "prop-20260613-120000",
  "verdict": "pass|fail",
  "checks": {"constitutional": true, "syntax": true},
  "details": "验证结果描述"
}
```

### 13. `rollback` — 变更回滚

**写入侧模块**: (当前无活跃写入者——回滚在 `integrate` 中内联处理)
**读取侧模块**: `anchor.py:rollback`

```json
{
  "snapshot_id": "snap-20260613-120000",
  "reason": "post-fix验证失败",
  "files_restored": ["engine/loop.py"]
}
```

### 14. `graduate` — 自主等级变更

**写入侧模块**: (当前无活跃写入者——升级在 `anchor.py:check-autonomy` 中判定)
**读取侧模块**: `anchor.py:check-autonomy`

```json
{
  "from_level": "cold_start",
  "to_level": "supervised",
  "reason": "累计 5+ 迭代日志",
  "metrics": {"total_iterations": 10, "total_sessions": 3}
}
```

---

## Schema 漂移检测 (`scanner.py` 规则 schema-drift-check)

检测逻辑（待实施于 `scanner.py`）:

1. 对每种 event_type，收集所有写入调用的 data keys
2. 收集所有读取处的期望 keys（通过分析 `e["data"]...` 访问模式）
3. 写入侧 key 无读取侧匹配 → 死数据 (warning)
4. 读取侧 key 无写入侧匹配 → 死读取 (critical — 必然返回默认值)

**已知修复历史**:
- 2026-06-13: `fix` 事件的 reads/writes 不一致 (`fixes` vs `fix_report`) — 详见 known-issues.md
