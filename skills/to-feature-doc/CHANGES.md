# Proposed Changes to SKILL.md

## 1. Module-type trigger 扩展

**位置：** SKILL.md 第 62 行

**现在：**
```
**Module-type trigger:** if the feature involves **shares, unit price, settlement, ledger, or balances**, you must complete an **invariants & continuity pass** — extract formulas and denominator meaning from code, not just call order.
```

**改为：**
```
**Module-type trigger:** if the feature involves **shares, unit price, settlement, ledger, or balances**, OR has **stateful flows with concurrency constraints** (state machines, idempotency keys, cooldowns, token lifecycle), you must complete an **invariants & continuity pass** — extract state transitions, concurrency guarantees, and formula-level continuity from code, not just call order.
```

**原因：** 现有 trigger 只覆盖金融术语，导致 Auth 这类有真实不变量的模块（flow 状态机 pending→code_verified→completed、cooldown、refresh token hash 并发约束）跳过了 invariant inventory 步骤，输出文档缺少不变量表。
