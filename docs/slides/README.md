# A2A-Token-System · 决赛答辩 PPT (Slidev)

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

- `slides.md` — 主幻灯片源（11 页）
- `package.json` — slidev 依赖
- `public/` — 静态资源；放 demo-fallback.mp4（不入库，本地置入）
- 主题：`@slidev/theme-seriph`

## 演讲时长配比（8 min ＝ 480s）

| 页 | 章节 | 时长 |
|---|---|---|
| P1 | 封面 | 15s |
| P2 | 7 模块速览 | 35s |
| P3 | 系统架构 | 50s |
| P4 | 三步走 | 45s |
| P5 | 协议栈 + A2A | 55s |
| P6 | 最小权限 + 三道关 | 55s |
| P7 | 撤销 + 职责 + 审计 + 生态 | 40s |
| P8 | AI 亮点 | 60s |
| P9 | 落地价值 | 45s |
| P10 | 产品调研 | 30s |
| P11 | 谢幕 | 20s |
| **总** | | **450s · 留 30s 缓冲** |

## Demo 策略

主流程不放 demo。`public/demo-fallback.mp4` 作为 Q&A 兜底，由主持人按需播放，30s 演完整闭环。

## 快捷键

- `Space` / `→` 下一步
- `o` 总览
- `d` 切深色
- `f` 全屏
- `g` 跳转指定页
