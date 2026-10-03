# AGENT.md —— TotemFix 项目 AI 协作约定

## 核心规则

### 1. 内部思考（reasoning）是用户可见、需要审阅的内容
- 思考过程必须真实、准确、完整：怎么想就怎么写，不写与执行无关的填充；
- 涉及取舍、风险、权限申请、破坏性操作、网络与密钥、数据安全等关键决策时，
  必须在思考中明确写出原因与影响范围，不得省略或含糊；
- 任何会改变项目行为或用户环境的操作，先在思考中想清楚影响，再动手。

### 2. 语言约定
- **内部思考（reasoning）一律尽量使用简体中文**；
- **申请系统级权限时的说明与描述（justification）一律使用简体中文**，
  清晰说明“为什么要这个权限、影响范围是什么”；
- 其他需要用户审阅的内容（方案说明、风险提示、变更摘要）也尽量使用简体中文；
- 代码注释、提交信息（commit message）、文档默认简体中文；
  代码标识符、命令、文件路径等技术符号除外。

## 项目红线（不可违反）
1. 保持零第三方运行依赖：仅 Python 标准库 + tkinter；
2. 任何文件修改前自动备份（.minecraft/errordoctor-backups/），
   路径沙箱只允许操作 .minecraft 内相对路径；
3. DeepSeek API Key 只存本机 data/config.json，绝不写入日志、历史、截图或上传；
4. 每轮改动结束前，单元测试 + GUI 冒烟测试必须全绿。

## 项目速览
- 核心代码：`errordoctor/`（config / scanner / deepseek / watcher / engine / fixer / history / gui）
- 测试：`tests/`（45 个单元测试 + smoke_gui.py GUI 冒烟测试，Xvfb 运行）
- 截图：`docs/screenshots/`（tests/shot_gui.py 一键重新生成）
- PCL2 主页入口卡片：`pcl2_homepage/TotemFix.xaml`
- 打包：Windows 上 `scripts\build.bat`（PyInstaller 单文件）
- 发布状态：**当前阶段不发布 GitHub Release**（CI 仅保留手动构建）

## UI 设计方向（当前阶段）
- 参考 OpenFrp 启动器风格：左侧导航式分区、内容卡片、状态徽章、清爽留白；
- 延续 PCL2 蓝白主题（配色常量见 `errordoctor/gui.py` 顶部）；
- 微软雅黑 UI 字体规范、悬停反馈、右下角浮动通知、按钮悬停提示。

## 资料库（持续积累）
- `docs/knowledge_base.md`：整理 Minecraft / PCL2 常见报错与解决办法，
  作为 DeepSeek 分析之外的离线匹配补充，来源需记录出处链接。

## 改进循环（当前阶段任务）
1. 每轮先做具体改进（UI / 资料库 / 能力）；
2. 在新副本（全新目录）模拟用户操作：首次启动 → 设置 → 扫描 → 制造报错 →
   自动分析 → 确认修复 → AI 问答，自动查找并修复发现的问题；
3. 累计完成 5 轮“改进 → 模拟 → 修复”后停止。
4. **每轮副本归档（硬性要求）**：每完成一轮，必须把该轮版本快照存入
   `releases/round-<n>/`，内容包括：
   - 当轮的 `README.md`（完整快照，不是链接）；
   - `CHANGELOG.md`（本轮改进点 + 模拟用户发现的问题与修复记录）；
   - 对应版本的 **exe 主程序**（`TotemFix.exe`：优先取 CI 手动构建产物下载；
     Windows 本机可用 `scripts\build.bat` 生成；Linux 沙箱内以 PyInstaller
     同名构建产物代替并注明）；
   - 归档目录随仓库提交，任何一轮都不得缺失。
