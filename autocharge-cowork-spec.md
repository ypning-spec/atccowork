# AutoCharge Cowork — 产品设计规范
> 版本：v1.0 | 日期：2026-04-08 | 基于 VC Cowork v4 设计理念
> 项目：自动充电场站（JIRA ACMS + GitLab，干净无历史包袱）
> 数据来源：JIRA ACMS 项目实查（52个 issue，全部为故障类型）

---

## 一、核心命题

ACMS 项目现状：
- 52 个 issue，全部是「故障」类型，涉及机械臂控制/感知/设计三个子模块
- 主要角色：黄飞（测试验证）、韩沂（研发解决）、吉高艾利特（供应商）
- **真实问题**：issue 描述几乎全为空，解决过程只有简短评论（如「V1.0.9验证通过」），根因/假设/被否定方案全部丢失

**AutoCharge Cowork 要做的事**：
把问题解决过程中的假设、数据、讨论、被否定的方案、最终决策，结构化归档，带原文溯源——让下一个类似问题不用从零开始。

---

## 二、真实工作流（来自 JIRA 实查）

```
问题发现（现场测试/压测）
    ↓
JIRA 创建 issue（描述通常为空）
    ↓
状态：问题分析中
    ↓（研发分析根因，评论中记录）
状态：问题解决中
    ↓（提交代码/参数调整）
状态：效果验证
    ↓（黄飞压测验证，如"压测20次未复现"）
状态：问题关闭
```

**现实痛点**：
- issue 描述为空 → 背景信息靠口头传递
- 评论极简（「V1.0.9验证通过」）→ 根因丢失
- 跨 issue 的共性规律无法复用（如多个 issue 都是强光过曝，各自独立解决）

---

## 三、AI 书记员介入节点

### 节点 1：issue 创建时（问题分析中）
```
触发：新 issue 进入「问题分析中」
AI动作：
  1. 基于标题自动分类（机械臂控制 / 感知 / 硬件设计 / 供应商接口）
  2. 检索历史相似 issue → flag「AC-9 有类似强光过曝问题，参考？」
  3. 提议：「需要帮你建群对齐问题背景吗？」
```

### 节点 2：问题分析中（跨角色时）
```
触发：评论中出现多个不同角色参与
AI动作：
  1. 归档每条评论的分类（假设/数据/结论/问题）
  2. 涉及 ≥2 角色 → 提议建群
  3. 识别「未回答问题」→ 48h 后自动 ping
```

### 节点 3：问题解决中（方案确认）
```
触发：issue 流转到「问题解决中」
AI动作：
  归档结构化决策：
  {
    "selected": "方案描述",
    "rejected": [{"plan": "xx", "reason": "为什么否"}],
    "decided_by": "韩沂",
    "source": "原始评论引用"
  }
```

### 节点 4：效果验证 → 问题关闭
```
触发：issue 关闭
AI动作：生成「问题解决报告」
  - 问题描述
  - 根因（来自分析阶段归档）
  - 最终方案 + 被否定方案
  - 验证结论
  - 修改的参数/代码位置
写回 JIRA comment + 可选发飞书群
```

---

## 四、数据模型

### 4.1 消息/评论级（原文永久保留）

```json
{
  "msg_id": "jira_comment_xxx",
  "raw_text": "解决方案：桩端插枪到位门限值调整：90 --> 70，已修复，待验证",
  "speaker_name": "韩沂",
  "source_type": "jira_comment | feishu_group | gitlab_mr",
  "issue_key": "ACMS-43",
  "timestamp": "2026-04-08T14:00:00+08:00",
  "classification": {
    "type": "decision | hypothesis | data | action | question | noise",
    "confidence": 0.95
  }
}
```

### 4.2 Issue 上下文快照

```json
{
  "issue_key": "ACMS-52",
  "title": "mega车端插枪失败",
  "module": "机械臂控制",
  "status": "问题分析中 | 问题解决中 | 效果验证 | 问题关闭",
  "linked_groups": [],
  "linked_mrs": [],

  "hypotheses": [
    {
      "content": "末端夹爪定位偏差导致插枪位置偏移",
      "status": "confirmed | rejected | pending",
      "source": { "raw": "...", "speaker": "韩沂" }
    }
  ],

  "decisions": [
    {
      "content": "调整插枪到位门限值：90→70",
      "rejected_alternatives": [
        { "content": "增加重试次数", "reason": "治标不治本" }
      ],
      "decided_by": "韩沂",
      "source": { "raw": "原始评论", "speaker": "韩沂", "comment_id": "xxx" }
    }
  ],

  "open_questions": [
    {
      "question": "强光场景下的过曝是否与车型相关？",
      "asked_by": "黄飞",
      "status": "unanswered",
      "auto_ping_at": "2026-04-10T10:00:00"
    }
  ],

  "similar_issues": ["ACMS-9", "ACMS-40"],

  "entity_footprint": {
    "modules": ["机械臂控制", "视觉标定"],
    "components": ["末端夹爪", "3D相机"],
    "vehicles": ["mega", "i8", "X11"]
  }
}
```

---

## 五、跨 Issue 关联检测

ACMS 项目已有明显的共性模式，适合结构层关联：

| 模式 | 涉及 issue | 检测方式 |
|---|---|---|
| 强光过曝（视觉失效） | ACMS-9, ACMS-40, ACMS-45 | 共享 entity：3D相机 + 强光 |
| 插枪失败系列 | ACMS-50, ACMS-51, ACMS-52 | 共享 entity：车端插枪 |
| 碰撞保护误触发 | ACMS-42, ACMS-33 | 共享 entity：碰撞检测 |

AI 检测到共性 → flag「这3个 issue 可能有共同根因，建议合并分析」。

---

## 六、技术实现建议（供 Claude Code）

**数据源**：
- JIRA REST API（Bearer token，vc-rt-svc 账户）
- JIRA Webhook（issue 状态变更事件）
- GitLab Webhook（MR 状态变更，关联 issue key）
- 飞书 Open API（建群/发消息/消息监听）

**存储**：
- SQLite（开发期）/ PostgreSQL（生产）
- 表：`issues_context`（快照）/ `messages`（原文）/ `issue_relations`（关联边）

**AI 层**：
- 消息分类：GLM/Claude（prompt 简单）
- 相似 issue 检测：embedding 相似度 + 实体重叠
- 报告生成：Claude（质量要求高）

**JIRA 字段**（ACMS 实查）：
- project key: `ACMS`
- issue types: Bug（id 待查）
- 状态流: 问题分析中 → 问题解决中 → 效果验证 → 问题关闭

---

## 七、Phase 规划

**Phase 1（1周）— 被动书记员**
- [ ] JIRA webhook 监听状态变更
- [ ] issue 评论自动分类归档（假设/决策/数据）
- [ ] 相似 issue 检测（共享实体）
- [ ] 生成问题解决报告（issue 关闭时触发）

**Phase 2（1周）— 主动协作**
- [ ] AI 在「问题分析中」阶段检测跨角色 → 提议建群
- [ ] 飞书群与 issue 硬绑定，群消息归档
- [ ] open_questions 追踪 + 48h 自动 ping
- [ ] GitLab MR ↔ JIRA issue 关联

**Phase 3（按需）— 智能关联**
- [ ] 跨 issue 共性根因检测
- [ ] 「知识库」：历史决策可检索，新问题自动推荐参考

---

*「让每个解决过的问题，都成为下一个问题的经验。」*
