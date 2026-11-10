# 微博事件采集工具

按关键词自动完成：搜索帖子、选择高互动帖子、抓取一级评论和二级回复、过滤通用垃圾内容、获取图片和视频网址、下载图片。

## 安装

需要 Python 3.10+ 和 Microsoft Edge。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 直接运行（无需修改代码或配置文件）

```powershell
.\.venv\Scripts\python.exe weibo\run_pipeline.py `
  --event "世界杯决赛西班牙胜利" `
  --mode limited `
  --query "世界杯决赛 西班牙 胜利" `
  --keywords 世界杯 决赛 西班牙 夺冠 冠军 胜利 `
  --required 西班牙 阿根廷 决赛 `
  --posts 5 `
  --pages 1 `
  --comments 100 `
  --replies 20 `
  --images 3 `
  --min-hits 2
```

首次运行会打开 Edge，请在提示时间内登录微博。登录信息只保存在本地 `cookies.txt`；该文件已加入 `.gitignore`，不要上传或分享。

## 数量参数

- `--posts`：最终选择多少条帖子。
- `--pages`：每个搜索词读取多少页搜索结果。
- `--comments`：每条帖子最多抓取多少条一级评论。
- `--replies`：每条一级评论最多抓取多少条二级回复。
- `--images`：每条帖子最多下载多少张图片。
- `--no-replies`：不需要二级回复时使用。
- `--min-hits`：帖子正文至少命中多少个关键词。

## 两种抓取模式

默认的限制模式适合测试和常规采集：

```powershell
--mode limited --comments 100 --replies 20 --images 3
```

全量模式会对已经选中的帖子持续翻页，直到一级评论和二级回复接口结束，并下载帖子全部图片：

```powershell
--mode full --posts 5 --pages 1
```

`full` 不会无限扩展搜索范围：搜索页数仍由 `--pages` 控制，目标帖子数仍由 `--posts` 控制。热门帖子可能包含大量评论，全量模式耗时更长，也更容易触发登录失效或平台限流，请谨慎使用。

多组搜索词可以重复传入 `--query`：

```powershell
--query "西班牙 世界杯 决赛" --query "西班牙 夺冠 世界杯"
```

`--keywords` 可以省略，程序会从搜索词自动提取；事件存在简称或别名时建议手动填写。

当普通关键词混入大量旁支话题时，可以增加轻量约束：

```powershell
--required 西班牙 阿根廷 决赛
```

表示三个核心词必须同时出现在帖子正文中。还可以填写一个或多个事件短语，帖子命中其中任意一个即可：

```powershell
--anchor "西班牙1比0阿根廷" `
--anchor "决赛击败阿根廷" `
--anchor "决赛战胜阿根廷"
```

## 配置文件模式

重复运行同一事件时，可复制 `weibo/events/event_template.json`，然后运行：

```powershell
.\.venv\Scripts\python.exe weibo\run_pipeline.py --config weibo\events\your_event.json
```

## 输出

结果默认保存在 `weibo/results/event_编号/`：

- `search/`：搜索帖子 JSON。
- `comments/`：原始评论、相关评论、待复核评论 CSV。
- `media/`：图片、图片网址、视频网址和下载记录。

二级回复通过 `parent_comment_id` 关联一级评论；`context_text` 同时保存原帖、父评论和当前回复。

## 使用建议

- 平台页面或接口变化可能导致程序失效。
- 请控制抓取规模和频率，遵守平台规则与适用法律。
- 发布研究数据前应去除用户ID、昵称等可识别信息。
- 关键词与上下文规则只能降低噪声，不能保证所有保留内容都与事件完全相关，正式使用前应抽样复核。

## 免责声明

本项目仅供学习、教学与合规研究使用，不提供任何形式的数据获取授权。使用者应自行确认并遵守适用法律法规、微博平台规则、访问频率限制以及数据保护要求，不得将本项目用于未经授权的数据采集、商业滥用、账号攻击或侵犯他人隐私等用途。

平台页面、接口和风控策略可能随时变化，本项目不保证程序持续可用，也不保证采集结果的完整性、准确性或适用性。因使用本项目造成的账号限制、数据损失、服务中断或其他法律与合规后果，由使用者自行承担。

请勿提交、公开或共享 `cookies.txt`、账号凭据及其他敏感信息；对外发布采集数据前，应删除或匿名化用户 ID、昵称等可识别个人身份的信息。
