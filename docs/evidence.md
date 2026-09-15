# 事实来源与责任边界

## 项目标识与版本

用户在对话中称为“global”；定位到的实际项目名为 **Globe Memories / globe-memories**。源仓库检查基线：`7607fbf`。本副本的 `app/`、`static/`、`requirements.txt` 按源仓库工作区复制并校验，应用代码不做改写。

## 作者确认

2026-09-14 对话确认：“最初是记录情侣之间的瞬间照片和回忆，本人全程设计，用 ai coding。” 同次确认对外应展示模板化地球，不展示私人版本。

因此可写：本人全程产品设计、AI coding 辅助实现。不能由此推断：所有代码手写、特定 AI 工具或模型、用户增长、访谈样本、付费或正式上线成效。

## 可核对的源码证据

- 背景：作者确认与原 README 的情侣共同回忆定位。
- 产品交互：`static/app.js` 的 `onGlobeClick`、`clusterPlaces`、`renderTimeline`、`selectPlace`、`sendMessage`。
- 记录与权限：`app/main.py` 的 `create_place`、`update_place`、`delete_place`、`delete_photo`、`current_user`。
- 模板：`app/demoseed.py` 明确声明虚构故事，含 7 条回忆及程序生成图片；`app/globe_ctx.py` 选择上下文。
- 存储/认证：`app/db.py`、`app/auth.py`；图片：`app/media.py`。
- 原产品备份：`app/cloudbackup.py`；并未进行真实备份访问。
- 本次公开入口：`demo/app.py`，只初始化示例数据并限制可访问路径。
- 迭代：见 `decisions.md` 中历史提交摘要。摘要说明存在对应修复，不能据此证明线上效果或将每条代码动作归因于作者手写。

## 本次新增，不应回写成原始成果

README、产品 case、指标设计、AI 候选方案、面试口播、验证与打包脚本，以及公开模板默认入口均为本次整理新增。运行截图是本地模板截图；不是线上截图或真实用户反馈。

未复制原仓库 Git 历史、私人运行数据、下载的工具和无关文档生成脚本。作品集不携带个人电脑路径与原私人服务地址。原私人运维脚本不进入这份公开展示副本。

## 尚待补证据

具体 AI coding 工具与模型、开发周期、用户研究、真实试用人数、个人逐次验收记录、线上使用与业务指标。首页已避免依赖这些信息，因此可直接作为原型作品说明使用。
