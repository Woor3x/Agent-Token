# A2A-Token-System · 决赛答辩 PPT (Slidev · Editorial Dark)

按飞书 wiki《A2A-Token-system-决赛作品展示文档》目录顺序整理，使用 [Slidev](https://sli.dev) 渲染。

## 起开发服务器

```bash
cd docs/slides
npm install
npm run dev
# 浏览器自动打开 http://localhost:3030
```

## 导出 PDF

```bash
npm run export             # 默认 PDF
# 或
npx slidev export --format pdf --output a2a-defense.pdf
```

## 导出 PPTX

```bash
npx slidev export --format pptx
```

## 文件清单

- `slides.md` — 主幻灯片源（13 页）
- `package.json` — slidev 依赖
- `public/` — 静态资源；放 demo-fallback.mp4（不入库，本地置入）
- 主题：`@slidev/theme-seriph`

## 演讲时长配比（8 min ＝ 480s）

| 页 | 章节 | 时长 |
|---|---|---|
| P1 | 封面 | 15s |
| P2 | 汇报目录 | 15s |
| P3 | 背景 — AI Agent 时代 | 35s |
| P4 | 问题分析 — OAuth 失能 | 45s |
| P5 | 解决方案 — 三支柱 | 30s |
| P6 | 整体架构 | 50s |
| P7 | 模块① IdP + Token Exchange | 45s |
| P8 | 模块② Gateway × OPA | 45s |
| P9 | 模块③ Agents 编排+执行 | 45s |
| P10 | 模块④ 审计·撤销·生态 | 40s |
| P11 | AI 亮点（功能+工程） | 55s |
| P12 | 对比 · 创新 · 价值 | 40s |
| P13 | 谢幕 + 团队 | 20s |
| **总** | | **480s = 8 min** |

## Demo 策略

主流程不放 demo。`public/demo-fallback.mp4` 作为 Q&A 兜底，由主持人按需播放，30s 演完整闭环。

## 快捷键

- `Space` / `→` 下一步
- `o` 总览
- `d` 切深色
- `f` 全屏
- `g` 跳转指定页
