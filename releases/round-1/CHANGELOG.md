# CHANGELOG —— Round 1 副本（2026-10-04）

版本快照：基于 v2.2 之上的第 1 轮改进。本阶段不发布 GitHub Release，
产物仅做本地归档（见同目录）。

## 本轮改进

1. **内置报错资料库（新增）**
   - `errordoctor/kb_data.json`：12 类常见报错条目（GLFW 65543、退出代码 1/-1、
     内存不足、类缺失、Java 版本过低、内存访问违规、拒绝访问、网络超时、
     Mod 加载失败、.NET 缺失、杀软误报），每条含匹配特征、原因、解决步骤、出处链接；
   - `errordoctor/knowledge.py`：离线匹配引擎（子串匹配、忽略大小写、上限 3 条）；
   - 扫描命中后 issue 附带 `kb` 字段（engine 集成）；
   - GUI：问题卡片显示「📚 已知」绿色徽章；详情页顶部渲染「资料库命中」卡片
     （标题/分类徽章/原因/编号解决步骤/出处），与 AI 分析结果共存。
   - 出处：PCL2 官方排查指南（pcl2.ijinshan.com）、PCL-Community/PCL2-1930 社区问题列表等。

2. **扫描器补漏**
   - 新增 GLFW/OpenGL 渲染类报错识别（`GLFW error`、`does not appear to support OpenGL` 等，
     无 Exception 后缀的行此前会被漏检），类别“显卡异常”。

3. **UI 调研与规范**
   - `docs/research/openfrp-ui-notes.md`：OpenFrp 启动器（Tauri+NaiveUI）设计语言要点
     （蓝色强调、左侧导航、卡片内容、状态点、toast、深色模式预留）与 tkinter 落地映射；
   - AGENT.md 建立项目协作约定（思考过程中文、权限说明中文、每轮归档要求等）。

4. **发布策略调整**
   - 按用户要求暂停 GitHub 发布：CI 工作流改为仅手动构建（workflow_dispatch）、
     不再推标签自动发 Release；远端 v1.0.2 标签已删除，仓库仍停留在 v1.0.1。

## 本轮“模拟用户操作”发现并修复的问题

1. GUI 详情页部分 Label 在构造函数里使用 `pady=(a,b)` 元组 → Tk 报
   `bad screen distance`，已改为 pack 时传元组（v2.1 全部在 pack 里所以此前未暴露）；
2. 扫描器漏检 `GLFW error 65543` 行 → 资料库无法匹配，新增 GLFW 规则修复；
3. 编辑失误导致两行代码粘连的语法错误，已修复。

## 验证

- 单元测试：54 个全绿（新增 test_knowledge.py 7 个用例 + engine kb 附加用例）；
- GUI 冒烟（Xvfb 真实运行）：27 项检查全绿，覆盖 首次启动→扫描→资料库命中→
  自动分析→确认弹窗→不再询问→全自动修复→AI 问答→设置→PCL2 进程定位→历史备份。

## 归档内容

- `README.md`：本轮 README 完整快照；
- `TotemFix.exe`：**Windows 64 位主程序**（GitHub Actions windows-latest 自动构建，
  含本轮全部改动；`TotemFix.exe.sha256` 为官方 SHA256 校验侧车）；
- `TotemFix-linux`：Linux x86-64 构建产物（沙箱验证用，非发布物）。
