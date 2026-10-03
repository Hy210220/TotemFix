# OpenFrp 启动器 UI 调研笔记（TotemFix 重设计参考）

调研日期：2026-10-04。目标：把 OpenFrp 启动器的设计语言落到 tkinter 可实现的范围内。

## 参考资料

- OpenFrp 官方启动器（5.0）：<https://github.com/ZGIT-Network/OpenFrpLauncher>
- 跨平台启动器（Rust + Tauri 2 + NaiveUI）：<https://github.com/ZGIT-Network/OpenFrp-CrossPlatformLauncher>
- OpenFrp 官网 / 资源页：<https://www.openfrp.net/> 、<https://www.minebbs.com/resources/openfrp-openfrp-next.2807/>
- B 站「全新 OPENFRP 4.0」宣传视频：<https://www.bilibili.com/video/av488976325/>

## OpenFrp 设计语言要点

1. **配色**：白底为主 + 高饱和蓝色强调（NaiveUI 系，约 #2080F0 / #2A65EA）；
   提供深色模式切换；状态色语义统一：绿=在线/成功、灰=离线/禁用、红=错误、橙=警告。
2. **布局骨架**：左侧固定侧边导航栏（Logo 区 + 功能分区 + 底部设置/关于），
   右侧内容区 = 顶部工具栏（标题 + 全局操作按钮）+ 卡片化内容。
3. **内容卡片**：圆角、细描边、浅阴影；卡片内分区标题 + 行式信息（左标签右值）。
4. **列表/表格**：行悬停高亮；首列状态点（● 绿/灰）；操作按钮靠右且克制（图标+文字）。
5. **交互反馈**：主按钮实心蓝、次按钮浅蓝描边；开关用 toggle 样式；
   操作结果用右下角 toast；空状态给插画/表情 + 一句指引文案。
6. **字体与间距**：系统默认无衬线（微软雅黑），层级靠字重与灰度区分；留白充足，
   分区之间用浅分隔线。

## 映射到 TotemFix（tkinter 零依赖可实现项）

| OpenFrp 元素 | TotemFix 落地 |
| --- | --- |
| 左侧侧边导航 | 左侧问题卡片区已具备；顶部 Logo 区强化 + 底部状态徽章 |
| 卡片式内容 | 已有（v2.2）；进一步：卡片内“左标签右值”行式布局 |
| 状态点 | 问题卡片严重度色条已具备；监控状态改“● 绿/黄”圆点 |
| 行悬停高亮 | 已有；补列表页（历史/备份）行悬停 |
| 主/次按钮 + toggle | 已有；自动修复开关改 toggle 视觉（滑块样式暂用 Checkbutton） |
| 右下角 toast | 已有（v2.2） |
| 空状态引导 | 已有基础版；统一文案风格“🎉 状态 + 一句下一步指引” |
| 深色模式 | 预留：配色常量集中，后续可加“深色/浅色”切换设置项 |
| 分区分隔线 | 设置页已有；详情页分区卡片头部加左侧蓝色小竖条强调 |

## tkinter 美化资源（备用参考）

- CustomTkinter 现代主题（圆角/主题色方案参考，本项目保持零依赖仅借鉴配色）：
  <https://github.com/TomSchimansky/CustomTkinter>
- TkinterModernThemes（现代主题集合参考）：
  <https://github.com/RobertJN64/TKinterModernThemes>
- ttk 官方主题化文档：<https://docs.python.org/3/library/tkinter.ttk.html>
- Modern Tkinter（Roseman）：布局与 ttk 实践参考书
