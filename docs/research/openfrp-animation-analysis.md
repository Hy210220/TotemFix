# OpenFrp 启动器动画与布局分析（基于用户提供的预览包反解）

样本：OpenFrp Launcher Preview（V5.8.87，WPF / .NET Framework 4.8，
iNKORE.UI.WPF.Modern 控件库，单目录绿色版，主程序 OpenFrp.Launcher.exe 1.79MB）。
分析方法：对主程序集做字符串提取（无 dotnet 环境下从 PE 中提取 BAML 引用的
动画类型、目标属性与控件名）。

## 布局骨架（对照 TotemFix）

| OpenFrp | 说明 | TotemFix 现状/映射 |
| --- | --- | --- |
| NavigationView（含 TopNavigation 模式、ItemSeparator） | Win11 风格左侧/顶部导航 | 已实现左侧导航（nav_items + 选中蓝条），可对齐：分组分隔、选中条动画 |
| Tunnels 页 = CardUserTunnel 列表 | **卡片式列表**，带首/中/末项 CornerRadius 与边距转换器（视觉上连成圆角卡片组） | 问题卡片已卡片化；可学习“列表卡片组”圆角语义（tkinter 用描边近似） |
| ContentDialog（ErrorContentDialog 等）+ BeginWithOpacityAnimation | 模态对话框带淡入 | 确认弹窗可加淡入（窗口级 alpha） |
| ToggleSwitch / InfoBar / ProgressRing | 现代开关、信息条、进度环 | 自动修复=Checkbutton，可不动；InfoBar≈toast 已有 |
| Toast + SetToastDuration | 右下角通知带时长设置 | 已有 toast；补滑入/滑出动画 |

## 动画系统（提取到的证据）

- **默认缓动：CubicEase + EaseOut**（12 处 CubicEase、EaseOut= 标记，含
  `_DefaultCubicEase` / `defaultEasingFunction`；另有 EasyEasing / easingOutFunc /
  easingInFunc 的 in/out 变体）→ TotemFix 统一采用 **cubic ease-out**（t→1-(1-t)³）。
- **Opacity 淡入淡出**（133 处 Opacity；opacityDoubleAnimation、
  BeginWithOpacityAnimation、UpdateImageOpacity、Toast 淡入）→ 窗口唤起/弹窗用
  `attributes("-alpha")` 多帧淡入替代。
- **TranslateTransform.X / Y 位移动画**（RenderTransform→TranslateTransform，
  含 TransformGroup.Children[1]）→ 页面切换“滑入”动画的出处。
- **ScaleTransform.ScaleX/ScaleY**（导航头缩放）→ 不落地（tkinter 无控件缩放）。
- **旋转动画**（ExpandCollapseChevronRotateTransform 折叠箭头）→ 卡片折叠场景才用，当前无折叠，跳过。
- 时长：BAML 中 TimeSpan 为二进制，未能直接提取数值；结合 WPF Modern 库默认
  （ContentDialog/Toast 约 200-250ms）与预览包观感，TotemFix 采用：
  - 页面滑入 220ms；窗口唤起淡入 160ms；toast 滑入 200ms / 滑出 180ms；
  - 全部 cubic ease-out，帧间隔 ~15ms（after 链，不阻塞 UI）。

## TotemFix 落地清单（本轮）

1. 统一缓动工具 ease_out_cubic + App._animate 动画帧引擎（可取消/可叠加）；
2. 页面切换：新页自右滑入 26px→0（220ms）+ 导航选中蓝条平滑移动到位；
3. 窗口唤起（新报错弹出）：alpha 0.35→1.0 淡入（160ms）+ 短暂置顶；
4. Toast：右滑入 + 到期左滑出；
5. 动画开关（默认开）；冒烟测试断言动画结束后最终位置正确、不阻塞事件循环。
