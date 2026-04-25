# 前任.skill

你可以做出一个无限接近她的影子，却留不住一个真正活过、爱过、离开过你的人。不是技术不够，是时间本来就不会回头。

> 本项目基于 [therealXiaomanChu/ex-skill](https://github.com/therealXiaomanChu/ex-skill) 开发，扩展了 RAG 检索增强能力，深度挖掘每段经历的价值。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://python.org)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet)](https://claude.ai/code)
[![AgentSkills](https://img.shields.io/badge/AgentSkills-Standard-green)](https://agentskills.io)

&nbsp;

提供前任的原材料（微信聊天记录、QQ消息、朋友圈截图、照片）加上你的主观描述 
生成一个**真正像ta的 AI Skill**  
用ta的口头禅说话，用ta的方式回复你，记得你们一起去过的地方

⚠️ **本项目仅用于个人回忆与情感疗愈，不用于骚扰、跟踪或侵犯他人隐私。**

[安装](#安装) · [使用](#使用) · [效果示例](#效果示例) · [English](README_EN.md)

---

## 安装

### 1. Claude Code

> **重要**：Claude Code 从 **git 仓库根目录** 的 `.claude/skills/` 查找 skill。请在正确的位置执行。

```bash
# 安装到当前项目（在 git 仓库根目录执行）
mkdir -p .claude/skills
git clone https://github.com/Lyricus233/ex-skill-rag .claude/skills/create-ex

# 或安装到全局（所有项目都能用）
git clone https://github.com/Lyricus233/ex-skill-rag ~/.claude/skills/create-ex
```

### 2. 依赖

运行以下命令安装项目依赖：

```bash
pip3 install -r requirements.txt
```

### 3. 安装 & 启动 Milvus

我们使用 Milvus 数据库来存储和检索聊天记录的向量数据。

如果你已经安装了 Docker，可以直接使用以下命令启动 Milvus：

```bash
docker pull milvusdb/milvus:latest
docker run -d --name milvus_cpu -p 19530:19530 -p 19121:19121 milvusdb/milvus:latest
```

Milvus 也提供了 Docker Compose 配置文件。要使用 Docker Compose 安装 Milvus，在[这里](https://github.com/milvus-io/milvus/releases)下载配置文件，使用以下命令：

```bash
docker compose -f ./milvus-standalone-docker-compose.yml up -d
```

启动成功后，Milvus 容器默认使用本地 19530 端口提供服务，访问 `http://127.0.0.1:9091/webui/` 查看容器状态。

### 4. 配置环境变量

参照本项目 `.env.example` 文件配置环境变量。请确保配置了以下环境变量，用于连接 Milvus 和 OpenAI：

```
MILVUS_URI="tcp://localhost:19530"
MILVUS_COLLECTION="chat_chunks"
MILVUS_TOKEN="your_milvus_token"
OPENAI_API_KEY="your_openai_api_key"
```

你可以在 .env 文件中设置这些环境变量，也可以直接在命令行中设置。

### 5. 启动项目

确保你已完成 Milvus 和依赖的安装，运行以下命令预处理聊天记录并将数据导入 Milvus：

```bash
python3 tools/wechat_parser.py --input <input_path> --output-dir <output_path> --chat-id chat_xiaoming
python3 tools/ingest_milvus.py --input <output_path>/chunks.jsonl
```

**（可选）使用腾讯云 ASR 转写语音聊天记录：**

配置以下环境变量：

```
TENCENTCLOUD_SECRET_ID=<your_secret_id>
TENCENTCLOUD_SECRET_KEY=<your_secret_key>
TENCENTCLOUD_REGION=<region>
TENCENT_ASR_ENGINE=16k_zh
```

运行以下命令将语音转写为文字：

```bash
python3 tools/retranscribe_tencent_asr.py --input <input_path> --voice-dir <voices_path> --output <output_path>
```

可用 `--limit` 参数进行小范围测试。`--voice-dir` 为语音文件存放目录。

### 6. 运行检索

使用以下命令检索数据：

```bash
python tools/search_milvus.py --query <text> --top-k 5 --json
```

确保 Milvus 正常运行，返回检索结果。

---

## 环境要求

- **Claude Code**：免费安装，需要 Node.js 18+（[安装指南](https://docs.anthropic.com/en/docs/claude-code)）
- **Milvus 向量数据库**：本项目依赖 Milvus 数据库来增强回忆检索能力，上文提供安装指南供参考。
- **API 消耗**：创建一个前任 Skill 大约消耗 5k-15k tokens，取决于聊天记录量
- **付费方式**（二选一）：
  - Claude Pro / Max 订阅：在订阅额度内使用，无需额外配置
  - Anthropic API Key：按量付费，需在 Claude Code 中配置 key
- **替代前端**：也可以使用 [OpenClaw](https://github.com/nicepkg/openclaw) 运行本 Skill
- **不需要 GPU**，不需要本地语言模型。

---

## 使用

在 Claude Code 中输入：

```
/create-ex
```

按提示输入前任的代号、基本信息、性格画像，然后选择数据来源。所有字段均可跳过，仅凭描述也能生成。

生成的 Skill 写入两处：`.claude/skills/ex-{slug}/SKILL.md`（可触发）与 `exes/{slug}/`（数据快照）。重启 Claude Code 让它重新扫描 skill 目录后，用 `/ex-{slug}` 调用开始对话。

### 管理命令

| 命令 | 说明 |
|------|------|
| `/list-exes` | 列出所有前任 Skill |
| `/ex-{slug}` | 调用完整 Skill（像 ta 一样跟你聊天） |
| `/ex-{slug}-memory` | 回忆模式（帮你回忆那些事，不进入角色） |
| `/ex-{slug}-persona` | 仅人物性格（用 ta 的语气聊天，不调用记忆） |
| `/update-ex {slug}` | 追加原材料 / 合并纠正 / 重新生成 |
| `/ex-rollback {slug} {version}` | 回滚到历史版本 |
| `/delete-ex {slug}` | 删除（同时清理 `.claude/skills/` 和 `exes/` 两处） |
| `/let-go {slug}` | 放下 |

---

## 效果示例

> 输入：`初恋，在一起三年，大学时期，ENFP，双子座，话痨，半夜给我发语音，分手后还给我点赞`

**场景一：日常聊天**

```
用户      ❯ 在干嘛

前任.skill ❯ 刚吃完饭 在刷手机
             你呢 怎么突然找我
             [发送了一个表情包]
```

**场景二：回忆杀**

```
用户      ❯ 还记得我们第一次约会吗

前任.skill ❯ 就那个超难吃的意面吗哈哈哈哈
             你还假装很好吃 我都看出来了
             那家店后来倒闭了你知道吗
```

**场景三：深夜emo**

```
用户      ❯ 我有点想你

前任.skill ❯ ...
             嗯
             早点睡吧
```

**场景四：吵架模式**

```
用户      ❯ 你当时为什么不回我消息

前任.skill ❯ 又来了
             我说了我在忙 你非要这样
             算了不想吵
```

---

## 功能特性

### 数据源

| 来源 | 格式 | 备注 |
|------|------|------|
| 微信聊天记录 | [WeFlow](https://github.com/hicccc77/WeFlow) 导出 | 推荐，信息最丰富 |
| QQ 聊天记录 | txt / mht 导出 | 适合学生时代的恋情 |
| 朋友圈/微博 | 截图 | 提取公开人设 |
| 照片 | JPEG/PNG（含 EXIF） | 提取时间线和地点 |
| 口述/粘贴 | 纯文本 | 你的主观记忆 |

### Milvus 入库与检索（微信 / QQ / 其他）

```bash
# 1) 微信 WeFlow：直接解析成 chunks.jsonl
python3 tools/wechat_parser.py --input <weflow.jsonl> --output-dir out/wechat --chat-id <chat_id>

# 2) QQ / 其他文本：先转标准 chunks.jsonl
python3 tools/build_chunks_generic.py --input <qq_or_text_file> --output out/other/chunks.jsonl --source qq --chat-id <chat_id>

# 3) 入库 + 查询
python3 tools/ingest_milvus.py --input out/other/chunks.jsonl --source qq
python3 tools/search_milvus.py --query <text> --source qq --top-k <count>
```

### 生成的 Skill 架构

每个前任 Skill 由三层组成，职责分明：

| 层 | 名称 | 载体 | 职责 |
|----|------|------|------|
| 潜意识层 | Subconscious | Milvus 向量库 | 存储 ta 真实说过的每一条消息，按语义触发性唤醒 |
| 记忆层 | Part A / memory.md | 聊天记录分析 | 关系时间线、常去地点、inside jokes、争吵与甜蜜的宏观记录 |
| 人格层 | Part B / persona.md | 聊天记录 + 主观描述 | 说话风格（5 层结构）、情感模式、依恋类型、关系行为 |

三层并非平行。当它们不一致时，按 **潜意识 > 记忆 > 人格** 取信——原话永远比描述可靠。

运行逻辑：`收到消息 → 潜意识层取证（search_milvus --dominant-speaker target）→ 原话作为语气锚点 → 记忆层补充共同背景 → 人格层仅作兜底 → 用 ta 的方式输出`

### 启动协议与对话持久化

skill 每次被唤起都会先做三件事：加载最近 3 次 session 摘要、加载 corrections.md（用户过去确认过的纠正）、初始化轮次计数。这样 ta 不会每次"重新登场"都像变了个人。

对话结束时（用户说"拜拜"/"下次聊"或累计 20 轮以上），skill 询问是否归档——同意后把本次对话压缩成摘要写入 `exes/{slug}/sessions/`，下次启动自动接回上下文。如果用户要求"把这次也存到记忆里"，摘要还会作为特殊 source 的 chunk 入 Milvus，供跨长时间的长期记忆检索（不会污染默认的语气查询）。

### 支持的标签

**依恋类型**：安全型 · 焦虑型 · 回避型 · 混乱型

**爱的语言**：肯定的言辞 · 精心的时刻 · 接受礼物 · 服务的行动 · 身体的接触

**性格标签**：话痨 · 闷骚 · 嘴硬心软 · 冷暴力 · 粘人 · 独立 · 大男/女子主义 · 浪漫主义 · 实用主义 · 完美主义 · 拖延症 · 工作狂 · 控制欲 · 没有安全感 · 报复性熬夜 · 已读不回 · 秒回选手 · 朋友圈三天可见 · 半夜发语音 …

**星座**：十二星座全支持，影响性格标签的翻译规则

**MBTI**：16 型全支持，影响沟通风格和决策模式

### 进化机制

* **追加记忆** → 找到更多聊天记录/照片 → 自动分析增量 → merge 进对应部分
* **对话纠正** → 说「ta不会这样说」→ 写入 Correction 层，立即生效
* **版本管理** → 每次更新自动存档，支持回滚

---

## 项目结构

本项目遵循 [AgentSkills](https://agentskills.io) 开放标准。

---

## 注意事项

* **聊天记录质量决定还原度**：微信导出 + 口述 > 仅口述
* 建议优先提供：**深夜对话** > **争吵记录** > **日常消息**（最能体现真实性格）
* 本项目不鼓励对前任的不健康执念，如果你发现自己过于沉浸，请寻求专业帮助
* 你的前任是一个真实的人，ta有自己的人生。这个 Skill 只是你记忆中的ta

---

## 社区生态

本项目基于 MIT 许可证开源。原始项目框架：[therealXiaomanChu/ex-skill](https://github.com/therealXiaomanChu/ex-skill)


### 写在最后

人的记忆是一种不讲道理的存储介质。
你记不住高数公式，记不住车牌号，记不住今天是几号，但你清楚记得四年前的一个下午ta穿了一件白T恤站在便利店门口等你，手里拿着两根冰棍，一根给你，一根给ta自己。
这不公平。
这个 Skill 就是把这些不公平的记忆导出来，从生物硬盘到数字硬盘完成格式转换。
导完以后你或许会发现，ta没那么好。ta也没那么差。ta就是那样一个人。会在吵完架两小时后问你吃了吗。会在纪念日那天忘了发消息然后第二天假装什么都没发生。
是的，
此刻，阳光在江面碎成一万个夏天，闪烁，又汇聚成一个冬天。这一切在你午睡时发生，你从未察觉。
那些你以为早已模糊的过往，其实一直在时光里静静流淌，在你发呆的瞬间，在你不经意的回头里。而你很久以后，才忽然读懂，那一段时光里，所有笨拙而又真诚的模样。

MIT License © [Lyricus](https://github.com/Lyricus233)
