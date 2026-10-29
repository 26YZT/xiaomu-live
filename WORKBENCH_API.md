# 工作台接口约定

工作台是单页应用，入口 `/workbench`（`static/workbench.html`，可拆出 `static/workbench.css`、`static/workbench.js`），只调下面这些接口。所有接口都在本机 `http://127.0.0.1:28900`，JSON 进出，错误时返回 `{"error": "..."}` 和非 2xx 状态码。

## 直播控制（已有）

- `GET /api/state`：直播间快照。字段：`status`（idle / creating / connecting / waiting_stage / live / ending / ended / error）、`status_label`、`mode`、`model`、`end_reason`、`live_id`、`elapsed`（秒）、`billing {billed_seconds, credits_cost, rmb, estimate_credits}`、`budget_credits`、`max_session_minutes`、`credit_price_rmb`、`interval_seconds`、`policy`、`next_in`、`current`、`queue[]`、`history[]`、`filtered[]`、`subtitle`、`visual`、`swapping {to,user,since}|null`、`outfit`、`avatar_name`、`profile`、`profiles[{key,name,note}]`、`generator`、`has_api_key`、`broadcast {target,state,error,stats,since,output,youtube_key,segments,receiving,layout}`、`stage_joined`、`stage_owner`、`rtc_ready`。命令对象：`{id,user,text,kind,gift,item,status,note,created_at,executed_at,acked_at,asset,parts}`，`kind` 为 chat / action / outfit / object / background / reset / gift_thanks，`status` 为 ready / preparing / executing / done / failed / expired。
- `GET /api/events`：SSE，事件 `state`（同快照）、`log`（`{t,level,message,data}`）、`subtitle`（`{text}`）。
- `POST /api/session/start {model, profile, dry_run}`、`POST /api/session/stop`
- `POST /api/danmaku {user, text, gift}` → `{accepted, command}` 或 `{accepted:false, category, reason}`
- `POST /api/manual {kind, content}`，kind：text_msg / text_dialog / text_action / interrupt / reset
- `POST /api/next`：立即执行下一条
- `POST /api/settings {interval_seconds, policy, idle_prompt_after_seconds}`，policy：gift_first_random / gift_first_fifo / random_recent / fifo
- `POST /api/safety {max_session_minutes, budget_credits}`
- `POST /api/broadcast {target: off|file|youtube}` 或 `{layout: full|box}`
- `GET /api/logs?n=200`
- 舞台预览：`/stage?embed=1`（iframe 嵌入；只有输出方式是 local 时它才入会，其他模式只显示叠层和提示）

## 输出方式（启动数字人和推流分开）

### 0.1.2 舞台身份与首帧约定

本次升级后，已打开的舞台页和 OBS 浏览器源需要在未开播时刷新一次。

- `POST /api/stage/claim {client_id, live_id, role}`：`live_id` 必须来自当前快照；响应仍为 `{rtc, live_id}`。空身份、错误输出角色或过期会话返回 409。
- `POST /api/stage {client_id, live_id, event, detail?}`：只接受当前会话持有席位的页面。旧实例的 `joined/left/remote_video` 不会改动新会话，返回 409。
- `POST /api/stage/heartbeat {client_id, live_id}` → `{owner:boolean}`；`POST /api/stage/release {client_id, live_id, why?}` 会忽略过期释放请求。
- 快照新增 `stage_video_received`。`stage_joined` 仅代表 RTC 加入成功；`stage_video_received` 代表浏览器确认至少解码/呈现一帧。`remote_video` 在实际帧事件后报告，不在订阅 ACK 时报告。
- 内置录制状态新增 `input_frames`，代表当前输入段实际解码的视频帧数。
- `billing.estimate_credits` 包含本场所有换装会话的保守估算；预算使用 `max(credits_cost, estimate_credits)`，每秒检查。供应商账单查询独立执行。
- 每次启动新会话会将旧待执行队列归档为 `expired`。设置接口的 `auto_recover` 只接受 JSON 布尔值。

启动数字人只是在 Vidu 开一场会话，画面由一个“舞台页”接收；推流是另一件事，决定画面最后去哪。同一场会话只能有一个舞台页入会，所以输出方式决定谁拿这个名额：

- `local`：只在本地看。工作台直播间页里的预览（`/stage?embed=1`）入会。
- `obs`：交给 OBS。OBS 里的浏览器源（`/stage?obs=1&role=obs&layer=avatar` 或 `layer=all`）入会，工作台预览只显示提示。叠层、真人摄像头、推流都在 OBS 里做。
- `builtin`：内置推流。浏览器里开着的推流舞台页（`/stage?broadcast=1`）入会，服务端合成叠层后录成文件或推到 YouTube。

接口：

- `GET /api/state` 新增 `output_mode`（local / obs / builtin）和 `stage_role`（这个模式下谁能入会：preview / obs / broadcast）。`broadcast` 字段照旧，只在 builtin 模式下有意义。
- `POST /api/output {mode, target?, layout?}`：切换输出方式。`target` 只在 builtin 时用，`file` 或 `youtube`（默认 file）；`layout` 是 builtin 的构图 `full` / `box`。数字人运行或结束清理中（status 为 creating / connecting / waiting_stage / live / ending）不能切，返回 409 和原因。切离 builtin 会停掉内置推流。返回 `{output_mode, stage_role, broadcast}`。
- `GET /api/obs` → `{layers:[{key,name,url,width,height,note}], tips:[...], same_machine_only:true}`：OBS 浏览器源的地址和推荐尺寸。`key`：`avatar`（只有数字人画面）、`overlay`（只有弹幕叠层，透明背景，不入会）、`all`（画面加叠层合在一起）。
- 舞台页地址：`/stage?embed=1`（工作台预览）、`/stage?obs=1&role=obs&layer=avatar|all`（OBS）、`/stage?overlay=1`（透明叠层）、`/stage?broadcast=1`（内置推流页）。

## 角色

- `GET /api/profiles` → `{current, profiles:[{key,name,note,image_url,voice,greeting_instruction,outfits_count}]}`
- `GET /api/profiles/{key}` → `{key,name,note,image_url,voice,greeting_instruction,persona_text,idle_motions:[{name,content,weight,description}],custom_idle_motions,idle_topics:[...],custom_idle_topics,outfits:[{name,aliases[],image_url,base,description,generated}],in_use,current_outfit}`。`custom_idle_motions` 为 false 时 `idle_motions` 是内置默认那套，`idle_topics` 同理；`in_use` 为 true 表示这个角色正在直播，改动下次开播或换装时生效。
- `PUT /api/profiles/{key}` body 同上可选字段（name、note、voice、greeting_instruction、persona_text、idle_motions、idle_topics）。`idle_motions: []`、`idle_topics: []` 表示恢复内置默认。`idle_topics` 是冷场时开口的话题，一条一个，60 字以内，最多 100 条，也可以传一行一个的字符串；每个话题说过一次，要等全部说完一轮才会再出现。新建角色时 key 用小写字母数字下划线，额外传 `name`、`base_gallery_id`（素材库里的一张图）和可选的 `base_outfit_name`（底图那套衣服叫什么，默认“日常”）。
- `POST /api/profiles/{key}/base {gallery_id, outfit_name?}`：把素材库里的图设为底图（衣柜的 base 也跟着换，`outfit_name` 可顺便改底图那套衣服的名字）
- `POST /api/profiles/{key}/outfits {name, aliases[], gallery_id}`：把素材库里的图加进衣柜
- `POST /api/profiles/{key}/outfits/generate {name, description, model, aliases?}` → `{job}`：用底图当参考生成一套新衣服，完成后自动进衣柜
- `POST /api/profiles/{key}/outfits/regenerate {model, names?}` → `{jobs}`：换了底图或场景以后，按新底图把衣柜里其他衣服重画一遍（不传 names 就是全部），不然换装时房间和灯光会跟着跳
- `PATCH /api/profiles/{key}/outfits/{name} {aliases[], new_name, description}`、`DELETE /api/profiles/{key}/outfits/{name}`
- `POST /api/wardrobe/prewarm {profile}`：把衣柜底图提前传给 Vidu（并行，约 40 秒）

## 动作

- `GET /api/actions` → `{actions:[{keywords:[...], action:"..."}]}`：弹幕关键词到动作指令的映射
- `PUT /api/actions {actions}`
- 待机小动作在角色的 `idle_motions` 里编辑，冷场话题在 `idle_topics` 里编辑（见上）
- 测试一个动作：`POST /api/manual {kind:"text_action", content}`

## 声音

0.1.3：快照新增 `voice_provider`（默认 `qwen_omni`）。`POST /api/manual` 的 `text_tts` 在真实会话中仅接受 `doubao`；其他供应商返回 400，界面禁用不兼容选项。`text_msg` 可用于提出朗读请求，但会经过对话模型。

- `GET /api/voices` → `{system:[{voice,name,desc}], cloned:[{voice}], used_by:{voice:[profile keys]}, error}`
- `POST /api/voices/clone`（multipart：`file` 音频 10–20 秒（或用 `gallery_id` 指定素材库里的音频），`name` 字母数字下划线，`speed` 0.8–1.4，`pitch` 半音 -3–3，`text` 可选，不填会自动转写）→ `{voice, text, seconds, sample_id}`，大约 20–40 秒
- `POST /api/voices/preview`（multipart：`file` 或 `gallery_id`、`speed`、`pitch`）→ 返回处理后的 wav，用于试听调速变调

## 素材生成

- `POST /api/media/image {model, prompt, ref_ids[], size, photo_real}` → `{job}`，model：`gpt-image-2.5` / `nano-banana-2.1`，size：`9:16` / `1:1` / `16:9`，ref_ids 最多 4 张，`photo_real: true` 会在提示词后面加上手机实拍质感的要求
- `POST /api/media/video {model, mode, prompt, image_id, audio_id, duration, resolution}` → `{job}`，model：`seedance-2.0-fast` / `seedance-2.0` / `seedance-2.5`，mode：`text` / `image` / `reference`，resolution：`480p` / `720p`
- `POST /api/media/scene {profile, scene_prompt, model}` → `{job}`：保持角色长相，按场景描述重画底图候选
- `GET /api/media/jobs` → `{jobs:[{id,kind,status,progress,prompt,model,created,result_id,result_url,error,cost_cny,meta}], spent_cny_24h}`，kind：image / video / scene / outfit，status：queued / running / done / failed。生图一般 20–60 秒，生视频 2–4 分钟
- `GET /api/media/gallery?kind=image|video|audio` → `{items:[{id,kind,url,prompt,model,created,source,cost_cny}]}`，新的在前
- `POST /api/media/upload`（multipart `file`）→ 素材库条目
- `DELETE /api/media/gallery/{id}`
- 静态文件：`/files/<相对 assets 的路径>`
