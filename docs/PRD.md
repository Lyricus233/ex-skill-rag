# 前任.skill — 产品需求文档（PRD）

## 产品定位

前任.skill 是一个运行在 Claude Code 上的 meta-skill。
用户通过对话式交互提供原材料（聊天记录 + 照片 + 手动描述），系统自动生成一个可独立运行的前任 Persona Skill。

## 核心概念

### 三层架构

| 层 | 名称 | 职责 |
|----|------|------|
| 潜意识层 | Subconscious (Milvus) | 存储海量的原始对话切片，像人的潜意识一样在特定话题被触动时被动唤醒，提供最真实的历史原话与细节 |
| Part A | Relationship Memory | 存储宏观事实记忆摘要：共同经历、日常模式、争吵与甜蜜档案 |
| Part B | Persona | 驱动对话行为：说话风格、情感模式、关系底线 |

三层结构组合发力，其中潜意识层确保了回忆被唤醒时细节的绝对真实，Part A/B 确保了整体主客观感觉和语气的连贯。

### 运行逻辑

```
用户发消息
  ↓
[RAG 触发] 检索 Milvus 向量库，获取高度相关的历史原话或事件细节
  ↓
Part B（Persona）判断：ta通常会怎么回应这种话题？用什么语气？
  ↓
Part A（Memory）补充：结合检索到的事实和宏观共同记忆，填入专属的细节
  ↓
输出：用ta的语气且附带真实历史印记进行回复
```

### 进化机制

```
追加原材料 → 增量分析 → merge 进现有 Skill
对话纠正 → 识别修正点 → 写入 Correction 层
版本管理 → 每次更新自动存档 → 支持回滚
```

## 用户旅程

```
用户触发 /create-ex
  ↓
[Step 1] 基础信息录入（3个问题，除花名外均可跳过）
  - 花名/代号
  - 基本信息（在一起多久、分手多久、职业等）
  - 性格画像（MBTI、星座、性格标签、主观印象）
  ↓
[Step 2] 原材料导入（RAG 入库）
  - 确认用户是否自带切片 / 已处理好库
  - 微信聊天记录导出 (WeFlow JSON/JSONL)
  - 可选：腾讯云 ASR 语音识别与回填预处理
  - QQ / 纯文本通用 Chunk 切片化
  - 强制：与用户确认 Milvus 集合 (`ex_{slug}_memories`) 并进行向量导入
  - 社交媒体 / 照片分析 / 主观粘贴
  ↓
[Step 3] 自动分析 (利用已入库的数据及清洗内容)
  - 线路 A：提取关系记忆 → Memory
  - 线路 B：提取性格行为 → Persona
  ↓
[Step 4] 生成预览，用户确认
  - 分别展示 Memory 摘要和 Persona 摘要
  - 用户可直接确认或修改
  ↓
[Step 5] 写入文件，立即可用
  - 生成 exes/{slug}/ 目录
  - 包含 SKILL.md（完整组合版）
  - 包含 memory.md 和 persona.md（独立部分）
  ↓
[持续] 进化模式
  - 追加新文件 → merge 进对应部分
  - 对话纠正 → patch 对应层
  - 版本自动存档
```

## 安全边界

1. **仅用于个人回忆与情感疗愈**
2. **不主动联系真人**
3. **不鼓励不健康执念**
4. **数据仅本地存储**
5. **Layer 0 硬规则**保证不说前任绝不可能说的话

## 数据源支持矩阵

| 来源 | 格式 | 提取内容 | 优先级 |
|------|------|---------|--------|
| 微信聊天记录 | WeFlow (JSON/JSONL) | 完整对话、语音记录(经 ASR 处理)、高频词、回复模式 | ⭐⭐⭐⭐ |
| QQ 聊天记录或原生文本 | txt/mht 或聊天记录 Chunks | 通用对话文本，可定制属性标记转化入库 | ⭐⭐⭐ |
| 照片 | JPEG/PNG + EXIF | 时间线、地点 | ⭐⭐ |
| 朋友圈/微博截图 | 图片 | 公开人设、兴趣 | ⭐⭐ |
| 口述/粘贴 | 纯文本 | 用户的主观补充和修正 | ⭐ |

## 文件结构

生成产物分布在两处：`.claude/skills/ex-{slug}/` 承载可触发的 SKILL.md，`exes/{slug}/` 承载数据与运行时状态。

```
.claude/skills/
  └── ex-{slug}/
      └── SKILL.md          # SKILL
                            # 触发词：/ex-{slug}

exes/
  └── {slug}/
      ├── SKILL.md          # 快照（与 .claude/skills/ 下内容一致）
      ├── memory.md         # Part A：关系记忆（源数据）
      ├── persona.md        # Part B：人物性格（源数据，含原话语料库）
      ├── meta.json         # 元信息
      ├── corrections.md    # 用户纠正记录（运行时追加，启动协议加载）
      ├── sessions/         # 对话归档（每次告别后追加 {YYYYMMDD_HHMMSS}.md）
      ├── versions/         # 历史版本存档
      └── memories/         # 原始材料存放
          ├── chats/
          ├── photos/
          └── social/
```

Milvus 向量库（潜意识层）的集合命名规范：`ex_{slug}_memories`；source 字段用于区分原始聊天记录（`wechat_weflow` / `qq` / ...）与对话归档（`session_summary`），避免自循环污染。
