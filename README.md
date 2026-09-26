# 演出乐谱翻页点规划 API

根据依次排列的小节高度、每小节后的静默秒数、页面可用高度与最低翻页时间，自动规划演出用电子乐谱的翻页点。小节不可拆开，只能在静默时间达标的位置**提前翻页**或**满页翻页**。还可通过可选的 `required_breaks`（必翻位置）与 `forbidden_breaks`（禁翻位置）指定某些位置必须/不得断页。此外，可为已保存的规划提交排练录音的拍数、速度、重复段与时间戳，定位每个时间戳对应原谱的哪一小节、重复段内的第几遍、小节内拍数偏移及所在页码；也可基于同一组排练参数生成**排练翻页提示**——仅在实际相邻演奏小节跨页时提示何时翻到哪一页（含重复段回跳造成的回翻），并判断前一小节后的静默是否够用；进一步提交每小节左右手的忙碌区间与完整翻页动作所需秒数后，还可为每个跨页边界挑选**翻页空档**——在前一个演奏小节内找出能容纳完整翻页动作的最晚开始时刻与可用手。还可对已保存规划提交按时间非降序排列的**排练踏板操作日志**（每次操作的秒数与操作后页码）与非负时间容差：服务复用排练参数生成含重复段回跳回翻的应翻页提示，再把踏板事件与提示做全局一对一匹配——仅当目标页相同且时间差不超过容差时允许匹配，依次最小化漏翻与多翻总数、匹配时间差之和（仍并列取匹配的踏板事件序号序列字典序最小），逐条返回匹配或漏翻、未匹配事件的多翻标记及两项代价，并拒绝越出演奏时长或页码范围的事件。针对纸质备用谱，还可按实物顺序抄录每页页码做**页序核对**：以规划的 `1..page_count` 为正确顺序，按补入缺页、移除多页、替换错页各计一次，求最少操作数与一条确定的修正步骤序列。此外，可对已保存方案提交常练小节及各自正整数查找权重与页签数 K，做**纸质谱练习页签规划**：按方案把同页权重合并，在恰好 K 个只贴于目标页的页签下，最小化"每目标页到最近页签的页码距离 × 该页权重"之和，同代价取升序页签列表字典序最小者（距离并列归较小页码）。还可对已保存方案提交 1～15 个互异的常练小节、起始页码与以小节序号二元组表示的先后依赖，做**练习路线规划**：按方案映射各小节所在页，求满足全部依赖且"起始页到首项及相邻项之间的页码距离之和"最小的练习顺序（同成本取小节序号序列字典序最小），返回顺序、各段翻页距离与总距离。此外，可对已保存方案提交严格递增的曲段末页页码（末项等于方案页数）与每帖最多印张数，做**纸质乐谱小册子拼版**：曲段不可跨帖、每帖至少含一段，帖尾按 4 页倍数补空白，用动态规划在合法分帖中依次最小化总空白页、帖数（同分取各帖末页序列字典序最小），返回每帖原谱页范围、空白页数与骑马订从外到内各印张正反面左右页码（空白以 `null` 表示）。此外，可为巡演装运提交 2～12 个互异的已保存方案 ID 与正整数箱体页容量，做**乐谱册装箱**：每份方案对应一册、册厚即其结果页数，每册只能整本放入一箱、每箱至少一册且装载页数不超容量，先求最少箱数、再使最重箱装载页数最小（仍并列时取各箱方案 ID 元组字典序最小的规范分箱），返回每箱方案 ID、装载页数与剩余容量；方案不存在或单册超容量均返回明确错误，且不写入新方案。此外，可为巡演曲目单提交按演出顺序排列的 1～500 个已保存方案 ID（允许重复）与 1～20 个谱架槽位，做**谱架换册规划**：开场所有槽位为空，每册占一槽、每场所需册须在架上，以总上架次数最少为目标逐场模拟——缺册时先用编号最小的空槽，架满则撤下未来最晚再次使用的册（永不再用视为最晚，并列取方案 ID 字典序最小者），返回每场的上架/撤下动作、场后各槽位方案 ID 与总上架次数；方案不存在返回明确错误，且不写入新方案。

- 技术栈：Python 3.11 + FastAPI + SQLite（结果保存在本地）
- 算法：动态规划，整体 O(n)（第二步为对候选静默值的二分，O(n log n) 上界）

## 规划目标（按优先级）

在所有合法分页方案中依次比较：

1. **页数最少**；
2. 页数相同时，**所有翻页点中最短的静默时间最大**（最紧张的那次翻页尽量从容）；
3. 仍并列时，**断页序号序列字典序最小**（同等条件下尽量早翻页，使方案确定、可复现）。

合法性约束：

- 每页包含连续若干小节，小节不可拆开，总高度 ≤ 页面可用高度（恰好等于允许）；
- 除最后一页外，翻页位置（某小节之后）的静默秒数必须 ≥ 最低翻页时间（恰好等于允许）；
- `required_breaks` 中的**必翻位置必须断页**、`forbidden_breaks` 中的**禁翻位置不得断页**；必翻位置自身的静默也必须达到最低翻页时间；
- 某**单小节高度超过页面可用高度**，或按上述规则**不存在合法分页方案**时，接口返回 `422` 并给出明确中文错误信息。

数值边界（十进制精确比较）：

- 高度与静默的全部判定——单小节是否超页高、若干小节累计是否超整页容量、静默是否达到最低翻页时间、"最大化最短静默"的阈值搜索与断页重构——都按输入数值的**十进制字面量精确比较**，内部先把数值按十进制解析、再统一放大为整数累计与判定，**不使用任何浮点 epsilon 容差**。
- 因此**恰好等于边界算合格**：容量 `<=`（累计高度恰好等于页高可同页）、静默 `>=`（恰好等于最低翻页时间可翻页）；十进制下 `0.1 + 0.2` 恰好等于 `0.3`，这样的两个小节仍会同页（不会因二进制浮点误差 `0.30000000000000004` 被误拆）。
- 反过来，**哪怕只超出/短一点点也不算合格**：小节高度 `1.0000000005` 对页高 `1` 视为超高（返回 `422`，不会错误排入容量 1 的页）；静默 `0.9999999999` 对最低翻页时间 `1` 不得作为翻页点。例如三小节高度均为 `1`、页高 `2`、静默依次为 `1`、`1.0000000005`、`0` 时，最优断页为位置 **2**（最短静默 `1.0000000005`），而不是静默仅 `1` 的位置 1。
- 响应中的高度/秒数字段仍为 JSON number（float）；float 转换只发生在输出边界，不参与任何容量或静默判定。

## 单条命令启动

需要安装 Docker（含 Docker Compose 插件），在项目根目录执行：

```bash
docker compose up -d --build
```

启动后服务监听 **http://localhost:8000**（容器内 8000 端口）。依赖安装与 SQLite 建表初始化均随容器启动自动完成，无需手动初始化。

停止：`docker compose down`（结果数据保留在宿主机 `./data` 目录）。

## 必要配置（docker-compose.yml）

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| 端口映射 | `8000:8000` | 宿主机端口:容器端口，宿主机 8000 被占用时改左边即可 |
| `DATA_DIR` | `/data` | 结果文件目录，SQLite 数据库 `plans.db` 存于此处 |
| 数据卷 | `./data:/data` | 将规划结果持久化到宿主机；删除该映射则数据仅存于容器内 |

不使用 Compose 时等价命令：

```bash
docker build -t page-turn-planner .
docker run -d -p 8000:8000 -v "$(pwd)/data:/data" -e DATA_DIR=/data page-turn-planner
```

## API 地址与端口

基址：`http://localhost:8000`（Docker Compose 映射宿主机 `8000` 端口到容器 `8000`）

- 交互式文档（Swagger UI）：`http://localhost:8000/docs`
- 健康检查：`GET http://localhost:8000/health`
- 提交规划：`POST http://localhost:8000/api/v1/plans`
- 按 ID 查询：`GET http://localhost:8000/api/v1/plans/{id}`
- 排练录音时间戳定位：`POST http://localhost:8000/api/v1/plans/{id}/rehearsal/locate`
- 排练翻页提示：`POST http://localhost:8000/api/v1/plans/{id}/rehearsal/cues`
- 排练翻页空档：`POST http://localhost:8000/api/v1/plans/{id}/rehearsal/turn-slots`
- 排练踏板日志校对：`POST http://localhost:8000/api/v1/plans/{id}/rehearsal/pedal-audit`
- 纸质备用谱页序核对：`POST http://localhost:8000/api/v1/plans/{id}/print-audit`
- 纸质谱练习页签规划：`POST http://localhost:8000/api/v1/plans/{id}/practice-tabs`
- 已保存方案的练习路线：`POST http://localhost:8000/api/v1/plans/{id}/practice-route`
- 纸质乐谱小册子拼版：`POST http://localhost:8000/api/v1/plans/{id}/booklet-imposition`
- 巡演装运乐谱册装箱：`POST http://localhost:8000/api/v1/tour/packing`
- 巡演曲目单谱架换册：`POST http://localhost:8000/api/v1/tour/stand-changes`

### 1. 提交规划

`POST http://localhost:8000/api/v1/plans`

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `measure_heights` | number[] | 依次排列的每小节高度（与页面高度同单位），非空、元素为正 |
| `silence_seconds` | number[] | 每小节之后可供翻页的静默秒数，长度与高度数组一致、非负 |
| `page_height` | number | 页面可用高度，正数 |
| `min_turn_time` | number | 最低翻页时间（秒），非负 |
| `required_breaks` | number[] | 可选，**必翻位置**：新页起始小节的 **0 基**序号，这些位置必须断页；省略或为空表示无必翻约束 |
| `forbidden_breaks` | number[] | 可选，**禁翻位置**：新页起始小节的 **0 基**序号，这些位置不得断页；省略或为空表示无禁翻约束 |

`required_breaks` / `forbidden_breaks` 的元素必须是严格整数（不接受 `1.0` 之类浮点或布尔），合法范围为 `1..小节数-1`：`0` 是首页起点、末尾序号（等于小节数）代表文档结束，均不能作为断页位置。两数组各自不得含重复元素，同一序号也不能同时出现在两个数组中。

给定约束后，三个规划目标仍在**全篇所有满足约束的方案**中依次比较（页数最少 → 最短翻页静默最大 → 断页序列字典序最小），不会按必翻点把乐谱切成几段各取局部最优；必翻位置自身的静默也必须达到最低翻页时间。

```bash
curl -X POST http://localhost:8000/api/v1/plans \
  -H 'Content-Type: application/json' \
  -d '{
    "measure_heights": [3, 3, 4, 2],
    "silence_seconds": [2, 5, 1, 0],
    "page_height": 6,
    "min_turn_time": 2
  }'
```

带必翻/禁翻约束的请求示例（`required_breaks` / `forbidden_breaks` 可只传一个，也可都省略）：

```bash
curl -X POST http://localhost:8000/api/v1/plans \
  -H 'Content-Type: application/json' \
  -d '{
    "measure_heights": [1, 1, 1, 1, 1],
    "silence_seconds": [1, 5, 9, 1, 0],
    "page_height": 3,
    "min_turn_time": 1,
    "required_breaks": [1],
    "forbidden_breaks": [4]
  }'
```

上例中位置 1 必须断页（本可两页装下，约束下全局最优为 3 页 `[1, 2]`），位置 4 不得断页。响应中 `request` 会**原样回显约束字段**（未传时回显为空数组），按 ID 查询时同样返回：

```json
{
  "request": {
    "measure_heights": [1, 1, 1, 1, 1],
    "silence_seconds": [1, 5, 9, 1, 0],
    "page_height": 3,
    "min_turn_time": 1,
    "required_breaks": [1],
    "forbidden_breaks": [4]
  },
  "result": { "...": "断页序列必含 1、必不含 4" }
}
```

响应 `201`：

```json
{
  "id": "2fb0c76562414d59ae41c49accabbd78",
  "created_at": "2026-09-23T17:24:35.516775+00:00",
  "request": { "...": "回显请求参数" },
  "result": {
    "page_count": 2,
    "shortest_turn_silence": 5.0,
    "breaks": [2],
    "pages": [
      {
        "page_index": 1,
        "start_measure": 0,
        "end_measure": 1,
        "measure_count": 2,
        "used_height": 6.0,
        "turn_silence": 5.0
      },
      {
        "page_index": 2,
        "start_measure": 2,
        "end_measure": 3,
        "measure_count": 2,
        "used_height": 6.0,
        "turn_silence": null
      }
    ]
  }
}
```

结果字段说明：

- `breaks`：断页序号序列，每个元素是“新一页起始小节”的 **0 基**小节序号；
- `pages[].start_measure` / `end_measure`：本页小节范围（0 基，结束为闭区间）；
- `pages[].turn_silence`：本页之后翻页点的静默秒数；最后一页无翻页点，为 `null`；
- `shortest_turn_silence`：所有翻页点中最短的静默秒数；整篇只有一页（无翻页点）时为 `null`。

### 2. 按 ID 查询结果

`GET http://localhost:8000/api/v1/plans/{id}`

```bash
curl http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78
```

- 找到：`200`，响应结构与提交接口相同；
- 不存在：`404`，`{"detail": "规划结果不存在：..."}`。

### 3. 排练录音时间戳定位

`POST http://localhost:8000/api/v1/plans/{id}/rehearsal/locate`

对一个**已保存的规划**提交排练参数，批量定位录音时间戳在原谱中的位置。重复段按谱面顺序展开——每次完整演奏该段（覆盖起止间全部小节）后再进入后续小节；每小节时长为 `60 × 拍数 / 速度`；时间戳以演奏开始为 `0`，定位采用**左闭右开**区间（`[小节起点, 下一小节起点)`），恰在边界的时间戳归下一小节；恰好等于总时长不可定位。

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `beats_per_measure` | number[] | 与原小节等长的每小节拍数，长度必须等于规划小节数、元素为正 |
| `tempo_bpm` | number | 每分钟拍数（BPM），正数 |
| `repeats` | object[] | 若干**不相交且不嵌套**的重复段（可省略）；首尾相邻允许，共享小节即报错 |
| `repeats[].start_measure` | number | 重复段起始小节（0 基，含） |
| `repeats[].end_measure` | number | 重复段结束小节（0 基，含） |
| `repeats[].play_count` | number | 整段演奏次数，`2～4`（遍号从 1 计） |
| `timestamps` | number[] | 录音时间戳数组（秒，以演奏开始为 0，非负），可为空数组 |

```bash
curl -X POST http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78/rehearsal/locate \
  -H 'Content-Type: application/json' \
  -d '{
    "beats_per_measure": [4, 4, 3, 4],
    "tempo_bpm": 120,
    "repeats": [
      {"start_measure": 1, "end_measure": 2, "play_count": 2}
    ],
    "timestamps": [0, 2, 4, 6, 7.5, 8, 10]
  }'
```

上例中重复小节 1..2 演奏两遍，实际演奏序列为 `0, 1, 2, 1, 2, 3`，总时长 11 秒。响应 `200`：

```json
{
  "id": "2fb0c76562414d59ae41c49accabbd78",
  "request": { "...": "回显定位请求参数" },
  "total_duration": 11.0,
  "locations": [
    {"measure": 0, "playthrough": 1, "beat_offset": 0.0, "page_index": 1},
    {"measure": 1, "playthrough": 1, "beat_offset": 0.0, "page_index": 1},
    {"measure": 2, "playthrough": 1, "beat_offset": 0.0, "page_index": 2},
    {"measure": 1, "playthrough": 2, "beat_offset": 1.0, "page_index": 1},
    {"measure": 2, "playthrough": 2, "beat_offset": 0.0, "page_index": 2},
    {"measure": 2, "playthrough": 2, "beat_offset": 1.0, "page_index": 2},
    {"measure": 3, "playthrough": 1, "beat_offset": 2.0, "page_index": 2}
  ]
}
```

结果字段说明（与 `timestamps` 一一对应）：

- `measure`：时间戳所在小节的**原谱 0 基小节号**；
- `playthrough`：该小节在所属重复段内的**第几遍**（1 基，取值 1～`play_count`）；非重复段小节恒为 `1`；
- `beat_offset`：该小节内的**拍数偏移**（自本小节起点起算，可为小数）；
- `page_index`：该原谱小节在**已存方案中的 1 基页码**（重复演奏不改变分页，同小节各遍页码相同）；
- `total_duration`：按重复段展开后的演奏总时长（秒），即所有被演奏小节的时长之和。

定位规则：小节 `i` 第 `p` 遍的时间区间为 `[t, t + 60·beats[i]/tempo_bpm)`；时间戳等于某遍起点时归该遍小节，等于末尾总时长时报越界错误。

### 4. 排练翻页提示

`POST http://localhost:8000/api/v1/plans/{id}/rehearsal/cues`

对一个**已保存的规划**提交排练参数（每小节拍数、速度、互不相交的重复段），复用与定位接口完全相同的演奏顺序与时间轴，自动列出排练中所有需要翻页的时刻。逐对考察实际演奏序列中**相邻的两次发声**：只有当二者所属页码不同时才生成一条提示，因此重复段回跳到同页小节不会提示、跨页才会回翻；提示按边界时刻升序返回，**演奏结束不生成提示**。

请求体（JSON，与定位接口相比不含 `timestamps`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `beats_per_measure` | number[] | 与原小节等长的每小节拍数，长度必须等于规划小节数、元素为正 |
| `tempo_bpm` | number | 每分钟拍数（BPM），正数 |
| `repeats` | object[] | 若干**不相交且不嵌套**的重复段（可省略）；首尾相邻允许，共享小节即报错 |
| `repeats[].start_measure` | number | 重复段起始小节（0 基，含） |
| `repeats[].end_measure` | number | 重复段结束小节（0 基，含） |
| `repeats[].play_count` | number | 整段演奏次数，`2～4`（遍号从 1 计） |

```bash
curl -X POST http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78/rehearsal/cues \
  -H 'Content-Type: application/json' \
  -d '{
    "beats_per_measure": [4, 4, 4, 4],
    "tempo_bpm": 120,
    "repeats": [
      {"start_measure": 1, "end_measure": 2, "play_count": 2}
    ]
  }'
```

上例中实际演奏序列为 `0, 1, 2, 1(遍2), 2(遍2), 3`（页 1 = 小节 0..1，页 2 = 小节 2..3，每小节 2 秒），方案最低翻页时间 2 秒。响应 `200`：

```json
{
  "id": "2fb0c76562414d59ae41c49accabbd78",
  "request": { "...": "回显翻页提示请求参数" },
  "min_turn_time": 2.0,
  "total_duration": 12.0,
  "cues": [
    {
      "boundary_seconds": 4.0,
      "from_page": 1, "to_page": 2,
      "from_measure": 1, "to_measure": 2,
      "from_playthrough": 1, "to_playthrough": 1,
      "direction": "forward",
      "silence_seconds": 5.0, "sufficient": true
    },
    {
      "boundary_seconds": 6.0,
      "from_page": 2, "to_page": 1,
      "from_measure": 2, "to_measure": 1,
      "from_playthrough": 1, "to_playthrough": 2,
      "direction": "backward",
      "silence_seconds": 1.0, "sufficient": false
    },
    {
      "boundary_seconds": 8.0,
      "from_page": 1, "to_page": 2,
      "from_measure": 1, "to_measure": 2,
      "from_playthrough": 2, "to_playthrough": 2,
      "direction": "forward",
      "silence_seconds": 5.0, "sufficient": true
    }
  ]
}
```

结果字段说明（每条提示对应一次跨页）：

- `boundary_seconds`：翻页边界时刻（秒，以演奏开始为 0），即前一小节结束、后一小节开始的瞬间；
- `from_page` / `to_page`：翻页前、后的**1 基页码**；
- `from_measure` / `to_measure`：前、后小节的**原谱 0 基小节号**（回跳时二者可呈倒序）；
- `from_playthrough` / `to_playthrough`：前、后小节所属重复段内的**第几遍**（1 基；非重复段小节恒为 1）；
- `direction`：`forward` 前进翻页（页码增大）或 `backward` 回翻（页码减小，由重复段回跳引起）；
- `silence_seconds`：**前一小节之后**的静默秒数，取自已保存规划；
- `sufficient`：该静默是否不低于方案的最低翻页时间（`min_turn_time`，按输入数值的十进制字面量精确比较，恰好相等视为充足，略低一律不充足）；
- `total_duration`：按重复段展开后的演奏总时长（秒）。

整篇只有一页时 `cues` 为空数组；同一小节在多遍中反复跨页时，每遍的跨页分别给出提示。

### 5. 排练翻页空档

`POST http://localhost:8000/api/v1/plans/{id}/rehearsal/turn-slots`

对一个**已保存的规划**提交排练参数（每小节拍数、速度、互不相交的重复段）、完整翻页动作所需秒数与每小节双手忙碌区间，复用与定位/提示接口完全相同的演奏顺序与时间轴，为每个跨页边界挑选具体的翻页动作空档。逐对考察实际演奏序列中**相邻的两次发声**：仅当二者所属页码不同时处理该边界——在**前一个演奏小节**内，把每只手的忙碌区间（允许乱序与重叠，先合并取补集）换算成连续空闲窗，选出能容纳完整翻页动作（`turn_seconds`）的**最晚开始时刻**；左右手的最晚开始时刻并列时**优先左手**。找不到可用窗的边界**保留在结果中**并标记 `feasible: false`。

请求体（JSON，拍数/速度/重复段字段与定位、提示接口相同）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `beats_per_measure` | number[] | 与原小节等长的每小节拍数，长度必须等于规划小节数、元素为正 |
| `tempo_bpm` | number | 每分钟拍数（BPM），正数 |
| `repeats` | object[] | 若干**不相交且不嵌套**的重复段（可省略）；首尾相邻允许，共享小节即报错 |
| `repeats[].start_measure` | number | 重复段起始小节（0 基，含） |
| `repeats[].end_measure` | number | 重复段结束小节（0 基，含） |
| `repeats[].play_count` | number | 整段演奏次数，`2～4`（遍号从 1 计） |
| `turn_seconds` | number | 完成一次完整翻页动作所需的秒数，**正数** |
| `hands_busy` | object[] | 与原谱小节**等长**的每小节双手忙碌区间 |
| `hands_busy[].left` | number[][] | 左手忙碌区间 `[起拍, 止拍)`（拍），允许乱序与重叠；省略表示该手无忙碌 |
| `hands_busy[].right` | number[][] | 右手忙碌区间 `[起拍, 止拍)`（拍），允许乱序与重叠；省略表示该手无忙碌 |

每个忙碌区间端点必须是有限数值，且满足 `0 ≤ 起拍 < 止拍 ≤ 该小节拍数`；区间按小节内拍数计，与速度无关（秒数换算由服务完成）。

```bash
curl -X POST http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78/rehearsal/turn-slots \
  -H 'Content-Type: application/json' \
  -d '{
    "beats_per_measure": [4, 4, 4, 4],
    "tempo_bpm": 120,
    "repeats": [
      {"start_measure": 1, "end_measure": 2, "play_count": 2}
    ],
    "turn_seconds": 1.0,
    "hands_busy": [
      {"left": [], "right": []},
      {"left": [[0, 3]], "right": [[1, 2]]},
      {"left": [], "right": [[0, 4]]},
      {"left": [], "right": []}
    ]
  }'
```

上例中实际演奏序列为 `0, 1, 2, 1(遍2), 2(遍2), 3`（页 1 = 小节 0..1，页 2 = 小节 2..3，每小节 2 秒），跨页边界为 4、6、8 秒三处。响应 `200`：

```json
{
  "id": "2fb0c76562414d59ae41c49accabbd78",
  "request": { "...": "回显翻页空档请求参数" },
  "total_duration": 12.0,
  "slots": [
    {
      "boundary_seconds": 4.0,
      "from_page": 1, "to_page": 2,
      "from_measure": 1, "to_measure": 2,
      "from_playthrough": 1, "to_playthrough": 1,
      "feasible": true,
      "hand": "right",
      "action_start_seconds": 3.0,
      "action_end_seconds": 4.0
    },
    {
      "boundary_seconds": 6.0,
      "from_page": 2, "to_page": 1,
      "from_measure": 2, "to_measure": 1,
      "from_playthrough": 1, "to_playthrough": 2,
      "feasible": true,
      "hand": "left",
      "action_start_seconds": 5.0,
      "action_end_seconds": 6.0
    },
    {
      "boundary_seconds": 8.0,
      "from_page": 1, "to_page": 2,
      "from_measure": 1, "to_measure": 2,
      "from_playthrough": 2, "to_playthrough": 2,
      "feasible": true,
      "hand": "right",
      "action_start_seconds": 7.0,
      "action_end_seconds": 8.0
    }
  ]
}
```

以第一条为例：边界前的小节 1 中，左手仅第 3～4 拍空闲（0.5 秒，容不下 1 秒动作），右手第 2～4 拍空闲（恰 1 秒），故选右手、动作占满该窗（3.0～4.0 秒，恰在边界处完成）。

结果字段说明（每条对应一个跨页边界，按边界时刻升序）：

- `boundary_seconds`：跨页边界时刻（秒，以演奏开始为 0），即前一小节结束、后一小节开始的瞬间；
- `from_page` / `to_page`：翻页前、后的 **1 基页码**；
- `from_measure` / `to_measure`：前、后小节的**原谱 0 基小节号**（回跳时二者可呈倒序）；
- `from_playthrough` / `to_playthrough`：前、后小节所属重复段内的**第几遍**（1 基；非重复段小节恒为 1）；
- `feasible`：是否找到能容纳完整翻页动作的连续空闲窗；
- `hand`：执行翻页的手，`left` 或 `right`；不可行为 `null`；
- `action_start_seconds` / `action_end_seconds`：翻页动作的起止时刻（秒，以演奏开始为 0），动作完整落在前一个演奏小节内、结束不晚于边界；不可行为 `null`；
- `total_duration`：按重复段展开后的演奏总时长（秒）。

整篇只有一页时 `slots` 为空数组；`turn_seconds` 超过空闲窗时长（甚至超过小节时长）的边界保留并以 `feasible: false` 标记。

### 6. 排练踏板日志校对

`POST http://localhost:8000/api/v1/plans/{id}/rehearsal/pedal-audit`

对一个**已保存的规划**提交排练参数（与定位/提示接口完全相同的每小节拍数、速度、互不相交的重复段）、一份按时间**非降序**排列的**踏板操作日志**（每次操作的秒数与**操作后页码**）以及**非负时间容差**。服务先按排练参数的十进制字面量**精确重建**演奏顺序与应翻页提示时刻（含**重复段回跳回翻**，不经浮点累加），再把踏板事件与这些提示按事件顺序做**全局对齐**：

- 一次踏板操作与一条提示仅当**目标页相同**（提示的 `to_page`）且**时间差不超过容差**（取绝对值，恰好等于容差允许）时才允许匹配；
- 一条提示至多匹配一次踏板操作、一次踏板操作至多匹配一条提示（一对一，且匹配随两侧时间单调）；
- 未匹配的提示为**漏翻**（应翻未翻），未匹配的踏板操作为**多翻**（无对应提示的多余操作）。

在全部合法对齐中依次优化：

1. **漏翻数与多翻数之和最少**（即匹配对数最多）；
2. 上一项相同时，**全部匹配对的时间差之和最小**；
3. 仍并列时，取**匹配到的踏板事件序号序列字典序最小**的对齐（序号序列按提示顺序排列，因单调对齐天然严格递增；先比第一个匹配事件，再依次比后续）。

数值口径（十进制精确比较）：校对内部把每小节拍数、速度（BPM）、每个事件秒数与时间容差都按输入数值的**十进制字面量**构造有理数，提示时刻按重复段展开做精确前缀和；匹配资格（`|时间差| ≤ 容差`）、累计时间差之和的并列判定、事件的非降序与 `[0, 总时长)` 校验全部是精确有理数比较，**不使用浮点或 epsilon**。因此容差为 0 时 `0.3000000005` 不会匹配 `0.3` 的提示；提示 `0.4` 对同目标页事件 `0.3` 与 `0.5` 的两侧时间差精确相等（均为 `0.1`），等成本取事件序号序列字典序最小（匹配 `0.3` 的事件）；事件时刻略早于总时长可接受、略逆序即 `422`。响应中的秒数字段仍为 JSON number，float 转换只发生在输出边界。

请求体（JSON，排练字段与定位/提示接口相同）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `beats_per_measure` | number[] | 与原小节等长的每小节拍数，长度必须等于规划小节数、元素为正 |
| `tempo_bpm` | number | 每分钟拍数（BPM），正数 |
| `repeats` | object[] | 若干**不相交且不嵌套**的重复段（可省略）；首尾相邻允许，共享小节即报错 |
| `repeats[].start_measure` | number | 重复段起始小节（0 基，含） |
| `repeats[].end_measure` | number | 重复段结束小节（0 基，含） |
| `repeats[].play_count` | number | 整段演奏次数，`2～4`（遍号从 1 计） |
| `pedal_events` | object[] | 按时间**非降序**排列的踏板事件（可为空数组），长度上限 **500** |
| `pedal_events[].seconds` | number | 踏板操作发生时刻（秒，以演奏开始为 0），须落在 `[0, 演奏总时长)` 内 |
| `pedal_events[].page_after` | number | 操作完成后所在的 **1 基页码**，须在 `1..规划页数`，必须为严格整数 |
| `tolerance_seconds` | number | **非负**时间容差（秒，有限数值）；时间差不超过容差才可能匹配 |

沿用第 4 节的方案（页 1 = 小节 0..1，页 2 = 小节 2..3，每小节 4 拍、120 BPM，小节 1..2 重复两遍），三条应翻提示分别在 **4 秒（到第 2 页）、6 秒（回跳到第 1 页）、8 秒（再到第 2 页）**，总时长 12 秒。把三次踏板操作（4.1→2、6.0→1、8.2→2）连同 0.5 秒容差提交：

```bash
curl -X POST http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78/rehearsal/pedal-audit \
  -H 'Content-Type: application/json' \
  -d '{
    "beats_per_measure": [4, 4, 4, 4],
    "tempo_bpm": 120,
    "repeats": [
      {"start_measure": 1, "end_measure": 2, "play_count": 2}
    ],
    "pedal_events": [
      {"seconds": 4.1, "page_after": 2},
      {"seconds": 6.0, "page_after": 1},
      {"seconds": 8.2, "page_after": 2}
    ],
    "tolerance_seconds": 0.5
  }'
```

三条提示全部一一匹配上，响应 `200`：

```json
{
  "id": "2fb0c76562414d59ae41c49accabbd78",
  "request": { "...": "回显校对请求参数" },
  "total_duration": 12.0,
  "cue_audits": [
    {
      "cue_index": 0,
      "expected_seconds": 4.0,
      "from_page": 1, "to_page": 2,
      "matched": true,
      "pedal_event_index": 0,
      "pedal_seconds": 4.1,
      "time_diff_seconds": 0.1
    },
    {
      "cue_index": 1,
      "expected_seconds": 6.0,
      "from_page": 2, "to_page": 1,
      "matched": true,
      "pedal_event_index": 1,
      "pedal_seconds": 6.0,
      "time_diff_seconds": 0.0
    },
    {
      "cue_index": 2,
      "expected_seconds": 8.0,
      "from_page": 1, "to_page": 2,
      "matched": true,
      "pedal_event_index": 2,
      "pedal_seconds": 8.2,
      "time_diff_seconds": 0.2
    }
  ],
  "extra_turns": [],
  "missed_count": 0,
  "extra_count": 0,
  "total_error_count": 0,
  "total_time_diff_seconds": 0.3
}
```

结果字段说明：

- `cue_audits`：与应翻页提示一一对应（按提示时间升序），每条给出：
  - `cue_index`：该提示在应翻页提示序列中的 **0 基序号**；
  - `expected_seconds` / `from_page` / `to_page`：提示的边界时刻、翻页前页与**目标页**（回跳时 `to_page` 较小）；
  - `matched`：是否匹配到一次踏板操作；`false` 即**漏翻**；
  - `pedal_event_index`：匹配到的踏板事件在输入 `pedal_events` 中的 **0 基序号**；漏翻为 `null`；
  - `pedal_seconds` / `time_diff_seconds`：匹配事件的操作时刻与匹配对时间差绝对值（秒）；漏翻均为 `null`。
- `extra_turns`：**未匹配**踏板事件的 0 基序号（**多翻**，升序）；
- `missed_count` / `extra_count`：漏翻数与多翻数；
- `total_error_count`：**漏翻与多翻总数**（第一项代价，全局最小）；
- `total_time_diff_seconds`：**匹配时间差之和**（第二项代价，在第一项最优下全局最小）。

漏翻与多翻示例：只提交 `[{"seconds": 4.0, "page_after": 2}, {"seconds": 5.0, "page_after": 2}]`、容差 0.5 时，第一条事件匹配 4 秒的提示；5 秒翻到第 2 页与 6 秒"回跳到第 1 页"提示目标页不符（多翻），6 秒、8 秒两条提示均无对应操作（漏翻）：`missed_count = 2`、`extra_count = 1`、`total_error_count = 3`、`extra_turns = [1]`。

整篇只有一页（没有任何应翻提示）时 `cue_audits` 为空数组，全部踏板事件都计入 `extra_turns`；`pedal_events` 为空数组时全部提示计为漏翻。事件秒数**越出演奏时长**（含恰等于总时长；略早于总时长可接受）或页码**越出 1..规划页数**、时间非非降序（略逆序即违规）、页码非严格整数均返回 `422`；规划不存在返回 `404`。该接口只读取已保存方案、不修改其内容。

### 7. 纸质备用谱页序核对

`POST http://localhost:8000/api/v1/plans/{id}/print-audit`

对一个**已保存的规划**核对纸质备用谱的实物页序。演出前按**实物顺序**把每页上印的页码抄录为 `scanned_pages`（可为空、可含重复页，也允许出现超过规划页数的错页页码）；以规划的 `1..page_count` 为正确顺序，按**补入缺页、移除多页、替换错页各计一次**求最少操作数，并给出一条**确定的**修正步骤序列。同成本方案在每一步上依次优先**保留 → 替换 → 移除 → 补入**，因此结果可复现。

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `scanned_pages` | number[] | 按实物顺序抄录的每页页码，元素必须是**严格正整数**（不接受 `1.0` 之类浮点或布尔值），允许为空数组、允许重复，长度上限 500 |

规划页数与实物页数各限 **500**：已存规划超过 500 页，或 `scanned_pages` 超过 500 个元素，均返回 `422`。

```bash
curl -X POST http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78/print-audit \
  -H 'Content-Type: application/json' \
  -d '{"scanned_pages": [1, 1, 3]}'
```

上例规划共 2 页：第 2 张实物页是重复的 1（替换成缺页 2），第 3 张 3 为超出规划的多页（移除）。响应 `200`：

```json
{
  "id": "2fb0c76562414d59ae41c49accabbd78",
  "request": {"scanned_pages": [1, 1, 3]},
  "page_count": 2,
  "operation_count": 2,
  "steps": [
    {"action": "keep",    "position": 0, "observed_page": 1, "target_page": 1},
    {"action": "replace", "position": 1, "observed_page": 1, "target_page": 2},
    {"action": "remove",  "position": 2, "observed_page": 3, "target_page": null}
  ]
}
```

结果字段说明：

- `operation_count`：最少操作数，即 `replace` / `remove` / `insert` 三种动作的步数之和（`keep` 保留不计）；
- `steps`：从前到后的修正步骤序列（含保留步，便于逐步对照实物操作），每步字段：
  - `action`：`keep` 保留、`replace` 替换错页、`remove` 移除多页、`insert` 补入缺页；
  - `position`：**原输入 `scanned_pages` 中的 0 基位置**——保留/替换/移除为被处理实物页的下标；补入表示**插在该位置之前**，`position` 等于数组长度即补在末尾；
  - `observed_page`：该位置抄录到的页码；补入没有实物页，为 `null`；
  - `target_page`：该位置应有的页码；移除不产生目标页，为 `null`。

完全正确时 `operation_count` 为 0、`steps` 全为 `keep`；实物一册皆无（`scanned_pages: []`）时给出 `page_count` 个补入步，位置均为 0（逐张插到最前）。规划不存在返回 `404`。

### 8. 纸质谱练习页签规划

`POST http://localhost:8000/api/v1/plans/{id}/practice-tabs`

对一个**已保存的规划**规划纸质谱上的练习页签。请求给出若干**常练小节**及各自的**正整数查找权重**（越常练权重越大）与页签数 `K`：服务先按方案把每个常练小节归到其所在页，**同一页多个小节的权重合并**到该页；没有练习目标的页不参与，**页签只能贴在有练习目标的页**，且 `1 ≤ K ≤ 目标页数`。

每个目标页练习时从**最近页签**开始翻找，代价为

```
|目标页码 − 最近页签页码| × 该页合并权重
```

目标页到两侧页签**距离相同**时归**较小页码**的页签。在恰好 `K` 个**不同**页签下全局最小化总代价；总代价并列时，取**升序页签列表字典序最小**（先比第一个页签页码，再依次比后续）的方案。

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `practice_measures` | object[] | 常练小节列表，**非空**，小节序号互不重复 |
| `practice_measures[].measure` | number | 常练小节序号（**原谱 0 基**），合法范围 `0..小节数-1`，必须为严格整数 |
| `practice_measures[].weight` | number | 该小节的查找权重，**正整数**（不接受 `1.0` 之类浮点、布尔或零） |
| `K` | number | 页签数，必须为 `1` 到**合并去重后目标页数**之间的严格整数 |

合并去重后的目标页数上限为 **500**（超过返回 `422`）。

下例方案可用如下请求预先创建（8 个小节、每页 2 小节，共 4 页）：

```bash
curl -X POST http://localhost:8000/api/v1/plans \
  -H 'Content-Type: application/json' \
  -d '{
    "measure_heights": [1, 1, 1, 1, 1, 1, 1, 1],
    "silence_seconds": [5, 5, 5, 5, 5, 5, 5, 5],
    "page_height": 2,
    "min_turn_time": 1
  }'
```

然后规划练习页签：

```bash
curl -X POST http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78/practice-tabs \
  -H 'Content-Type: application/json' \
  -d '{
    "practice_measures": [
      {"measure": 0, "weight": 3},
      {"measure": 2, "weight": 1},
      {"measure": 5, "weight": 2},
      {"measure": 7, "weight": 3}
    ],
    "K": 2
  }'
```

上例方案共 4 页（每页 2 小节：页 1 = 小节 0..1，页 2 = 小节 2..3，页 3 = 小节 4..5，页 4 = 小节 6..7），四个目标页权重依次为 3、1、2、3。贴 2 个页签时，贴在页 1 与页 4 总代价最小（页 2 距页签 1 为 1、页 3 距页签 4 为 1）。响应 `200`：

```json
{
  "id": "2fb0c76562414d59ae41c49accabbd78",
  "request": {
    "practice_measures": [
      {"measure": 0, "weight": 3},
      {"measure": 2, "weight": 1},
      {"measure": 5, "weight": 2},
      {"measure": 7, "weight": 3}
    ],
    "K": 2
  },
  "page_count": 4,
  "target_page_count": 4,
  "tab_pages": [1, 4],
  "total_cost": 3,
  "targets": [
    {"page": 1, "weight": 3, "nearest_tab": 1, "distance": 0},
    {"page": 2, "weight": 1, "nearest_tab": 1, "distance": 1},
    {"page": 3, "weight": 2, "nearest_tab": 4, "distance": 1},
    {"page": 4, "weight": 3, "nearest_tab": 4, "distance": 0}
  ]
}
```

结果字段说明：

- `page_count`：已存方案的规划页数；
- `target_page_count`：常练小节合并去重后的**有练习目标页数**（即 `K` 的上界）；
- `tab_pages`：恰好 `K` 个页签所在页码（1 基，**升序**，全部是有练习目标的页）；
- `total_cost`：全局最小总代价，即 Σ 目标页 `weight × distance`；
- `targets`：每个目标页（按页码升序）的归属：
  - `page`：目标页码；
  - `weight`：该页按方案**合并后的查找权重**；
  - `nearest_tab`：最近页签页码；距离并列时为**较小页码**；
  - `distance`：到最近页签的页码距离（非负）。

`K` 等于目标页数时每个目标页各贴一个页签，`total_cost` 为 0；规划不存在返回 `404`。该接口只读取已保存方案、不修改其内容。

### 9. 已保存方案的练习路线

`POST http://localhost:8000/api/v1/plans/{id}/practice-route`

对一个**已保存的规划**规划练习路线。请求给出 1～15 个**互异的常练小节**、一个**起始页码**与若干**先后依赖**（以小节序号二元组 `[先练, 后练]` 给出，两端都必须是常练小节）；服务先按方案把每个常练小节映射到所在页，再排出一个满足全部依赖的练习顺序，使

```
|起始页码 − 首项所在页| + Σ |相邻两项所在页之差|
```

最小；总距离并列时取**小节序号序列字典序最小**的顺序（先比第一项，再依次比后续各项）。

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `practice_measures` | number[] | 常练小节序号（**原谱 0 基**），**1～15 个、互异**，合法范围 `0..小节数-1`，必须为严格整数 |
| `start_page` | number | 起始页码（**1 基**），合法范围 `1..规划页数`，必须为严格整数 |
| `dependencies` | number[][] | 可选，先后依赖二元组 `[先练, 后练]`（均为小节序号，两端都必须是常练小节，不得成环）；省略或为空表示无依赖 |

沿用第 8 节的 4 页方案（页 1 = 小节 0..1，页 2 = 小节 2..3，页 3 = 小节 4..5，页 4 = 小节 6..7）：

```bash
curl -X POST http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78/practice-route \
  -H 'Content-Type: application/json' \
  -d '{
    "practice_measures": [0, 2, 5, 7],
    "start_page": 1,
    "dependencies": [[7, 2]]
  }'
```

上例中四个常练小节分别在页 1、2、3、4，依赖要求小节 7 先于小节 2 练习。最优总距离 5 有两条路线：`[0,7,5,2]`（页 1→4→3→2）与 `[0,5,7,2]`（页 1→3→4→2），取小节序号序列字典序最小的后者。响应 `200`：

```json
{
  "id": "2fb0c76562414d59ae41c49accabbd78",
  "request": {
    "practice_measures": [0, 2, 5, 7],
    "start_page": 1,
    "dependencies": [[7, 2]]
  },
  "page_count": 4,
  "order": [0, 5, 7, 2],
  "order_pages": [1, 3, 4, 2],
  "segment_distances": [0, 2, 1, 2],
  "total_distance": 5
}
```

结果字段说明：

- `page_count`：已存方案的规划页数；
- `order`：满足全部依赖的练习顺序（原谱 0 基小节序号）；
- `order_pages`：顺序中各小节所在页码（1 基，与 `order` 一一对应）；
- `segment_distances`：各段翻页距离——起始页到首项、以及相邻项之间的页码距离，长度与 `order` 一致；
- `total_distance`：总翻页距离（各段距离之和，全局最小）。

规划不存在返回 `404`。该接口只读取已保存方案、不修改其内容。

### 10. 纸质乐谱小册子拼版

`POST http://localhost:8000/api/v1/plans/{id}/booklet-imposition`

对一个**已保存的规划**做纸质印制的骑马订小册子拼版。方案页数由服务按 ID 读取（无需在请求中提供）；请求给出：

- **曲段末页页码** `segment_end_pages`：严格递增的 1 基页码，**末项必须等于方案页数**。相邻末页之间（首页从 1 起）即一个曲段，**曲段不可跨帖**；
- **每帖最多印张数** `max_sheets_per_signature`：正整数，一张大纸正反双面印刷对折为一个印张（4 个页面），故每帖容量为 `4 × 该值` 页。

分帖规则：每帖至少含一个完整曲段（曲段不跨帖），帖尾按 **4 页倍数补空白**（补 0~3 页）。在全部合法分帖中，用动态规划依次优化：

1. **总空白页最少**；
2. 总空白页相同时，**帖数最少**；
3. 仍并列时，取**各帖末页序列字典序最小**的分帖（先比第一帖末页，再依次比后续）。

某个曲段自身页数已超过帖容量时，因曲段不可拆，返回 `422`（`第 k 个曲段（原谱页 a..b，共 n 页）超过每帖容量…，曲段不可跨帖，无法拼版`）。规划页数上限 **500**（与页序核对、练习页签模块一致）。

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `segment_end_pages` | number[] | 严格递增的曲段末页页码（**1 基**正整数，不接受 `4.0` 之类浮点或布尔），非空，末项必须等于方案页数 |
| `max_sheets_per_signature` | number | 每帖最多印张数，**严格正整数**（帖容量 = `4 × 该值` 页，含补白） |

以 8 页方案（每页一小节）为例：

```bash
curl -X POST http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78/booklet-imposition \
  -H 'Content-Type: application/json' \
  -d '{
    "segment_end_pages": [8],
    "max_sheets_per_signature": 2
  }'
```

一帖含 8 页（2 印张），骑马订**外层正面为末页/首页、背面为次页/倒数第二页**，向内依次排列。响应 `200`：

```json
{
  "id": "2fb0c76562414d59ae41c49accabbd78",
  "request": {"segment_end_pages": [8], "max_sheets_per_signature": 2},
  "page_count": 8,
  "signature_count": 1,
  "total_blank_pages": 0,
  "signatures": [
    {
      "signature_index": 1,
      "start_page": 1,
      "end_page": 8,
      "content_pages": 8,
      "blank_pages": 0,
      "sheets": [
        {"sheet_index": 1, "front_left": 8, "front_right": 1, "back_left": 2, "back_right": 7},
        {"sheet_index": 2, "front_left": 6, "front_right": 3, "back_left": 4, "back_right": 5}
      ]
    }
  ]
}
```

含补白的例子：6 页方案、曲段末页 `[2, 4, 6]`、每帖 1 印张（容量 4 页）。曲段不可跨帖，最优分帖为两段——`[1..2]`（补 2 页空白成 1 印张）与 `[3..6]`（恰好 1 印张）：

```bash
curl -X POST http://localhost:8000/api/v1/plans/2fb0c76562414d59ae41c49accabbd78/booklet-imposition \
  -H 'Content-Type: application/json' \
  -d '{"segment_end_pages": [2, 4, 6], "max_sheets_per_signature": 1}'
```

```json
{
  "page_count": 6,
  "signature_count": 2,
  "total_blank_pages": 2,
  "signatures": [
    {
      "signature_index": 1, "start_page": 1, "end_page": 2,
      "content_pages": 2, "blank_pages": 2,
      "sheets": [
        {"sheet_index": 1, "front_left": null, "front_right": 1, "back_left": 2, "back_right": null}
      ]
    },
    {
      "signature_index": 2, "start_page": 3, "end_page": 6,
      "content_pages": 4, "blank_pages": 0,
      "sheets": [
        {"sheet_index": 1, "front_left": 6, "front_right": 3, "back_left": 4, "back_right": 5}
      ]
    }
  ]
}
```

第一帖对折后四个位置的局部次序是 `[4, 1, 2, 3]`，局部 3、4 是帖尾补入的空白，故为 `null`（页码均为全册全局页码，第二帖从 3 开始）。

结果字段说明：

- `page_count`：已存方案的规划页数（由 ID 读取）；
- `signature_count`：总帖数；`total_blank_pages`：各帖补白页数之和（全局最少）；
- `signatures[]`：按原谱先后的各帖：
  - `start_page` / `end_page`：本帖原谱页范围（1 基，闭区间）；
  - `content_pages`：本帖实际原谱页数；`blank_pages`：帖尾补入的空白页数（0~3）；
  - `sheets[]`：**从外到内**各印张，四个字段即对折后四个印面的**左/右**页码：
    - `front_left` / `front_right`：外层正面左、右页（最外层为全帖**末页 / 首页**）；
    - `back_left` / `back_right`：翻面后背面左、右页（最外层为**次页 / 倒数第二页**）；
    - 属于帖尾空白的位置为 `null`。

同分（总空白与帖数都相同）取各帖末页序列字典序最小：如 9 页三段 `[3,6,9]`、每帖 2 印张时，`[3|6,9]` 与 `[3,6|9]` 同为 3 空白、2 帖，取末页序列 `(3,9)`。规划不存在返回 `404`；该接口只读取已保存方案、不修改其内容。

### 11. 巡演装运乐谱册装箱

`POST http://localhost:8000/api/v1/tour/packing`

为巡演装运把若干**已保存方案**各自印制的乐谱册装进运输箱。**每份方案对应一册**，册厚即该方案结果的**页数**；装箱约束为：

- 每册只能**整本**放入一个箱，不可拆分；
- 每箱**至少一册**；
- 每箱装载页数（箱内各册页数之和）不超过箱体页容量 `box_page_capacity`。

请求提交 **2～12 个互异的已保存方案 ID** 与**正整数**箱体页容量。服务按 ID 从 SQLite 读取每份方案的页数（无需在请求中提供页数），在全部合法装箱中依次优化：

1. **箱数最少**；
2. 箱数相同时，**最重箱的装载页数最小**（各箱尽量均衡）；
3. 仍并列时，把**每个箱内的方案 ID 按字典序升序排列**，再把**箱列表按字典序排序**（先比各箱 ID 元组的第一个，再依次比后续；一个元组是另一个元组的前缀时短者更小），取**字典序最小**的分箱，使结果确定、可复现。

任一 ID 不存在返回 `404` 并列出全部不存在的 ID；某册页数超过箱体页容量（每册不可拆）返回 `422`。该接口**只读取已保存方案，不写入新方案、不修改既有方案**。

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `plan_ids` | string[] | 已保存方案 ID，**2～12 个、互异、非空字符串**；服务按 ID 读取各方案页数作为册厚 |
| `box_page_capacity` | number | 单个运输箱的页容量，**严格正整数**（不接受 `2.0` 之类浮点或布尔） |

先创建页数分别为 2、3、4、5 的四个方案（拿到四个 ID），再装箱（容量 6 页）：

```bash
curl -X POST http://localhost:8000/api/v1/tour/packing \
  -H 'Content-Type: application/json' \
  -d '{
    "plan_ids": ["<方案ID1>", "<方案ID2>", "<方案ID3>", "<方案ID4>"],
    "box_page_capacity": 6
  }'
```

四册总厚 14 页，最少需 3 箱（2 箱最多装 12 页）；3 箱下最重箱可压到 5 页（`(2+3)`、`4`、`5`），均衡性严格优于 `(2+4)=6、3、5`。响应 `200`（实际 ID 为 32 位十六进制，此处用 id1..id4 示意）：

```json
{
  "request": {
    "plan_ids": ["id1", "id2", "id3", "id4"],
    "box_page_capacity": 6
  },
  "box_page_capacity": 6,
  "total_pages": 14,
  "box_count": 3,
  "max_loaded_pages": 5,
  "books": [
    {"id": "id1", "pages": 2},
    {"id": "id2", "pages": 3},
    {"id": "id3", "pages": 4},
    {"id": "id4", "pages": 5}
  ],
  "boxes": [
    {"box_index": 1, "plan_ids": ["id1", "id2"], "loaded_pages": 5, "remaining_capacity": 1},
    {"box_index": 2, "plan_ids": ["id3"], "loaded_pages": 4, "remaining_capacity": 2},
    {"box_index": 3, "plan_ids": ["id4"], "loaded_pages": 5, "remaining_capacity": 1}
  ]
}
```

结果字段说明：

- `total_pages`：全部乐谱册的总页数（即各方案页数之和）；
- `box_count`：**最少箱数**；`max_loaded_pages`：**最重箱的装载页数**（在最少箱数下取到的最小值）；
- `books`：参与装箱的各册（按方案 ID 字典序升序），`pages` 即服务从已存方案读取的页数；
- `boxes`：分箱结果，**箱内方案 ID 升序、箱列表按 ID 元组字典序排序**：
  - `box_index`：1 基箱序号（按排序后的顺序）；
  - `plan_ids`：本箱内方案 ID（字典序升序，每册整本放入）；
  - `loaded_pages`：本箱装载页数（箱内各册页数之和，恒 ≤ `box_page_capacity`）；
  - `remaining_capacity`：本箱剩余容量（`box_page_capacity − loaded_pages`）。

字典序并列的例子：四册各 2 页、容量 4 时需 2 箱，任意两两分组最重箱都是 4 页，规范分箱取字典序最小的「ID 最小两册一箱」（排序后的第 1、2 个 ID 同箱，第 3、4 个 ID 同箱）。`books` 与 `boxes` 均按字符串字典序处理 ID（如 `p10` 排在 `p2` 之前），与输入顺序无关。方案不存在时返回 `404`：`{"detail": "以下方案不存在，无法装箱：<ID>、<ID>"}`。

### 12. 巡演曲目单谱架换册

`POST http://localhost:8000/api/v1/tour/stand-changes`

为巡演曲目单规划谱架上的乐谱册更换。请求提交**按演出顺序排列的 1～500 个已保存方案 ID**（**允许重复**，每场需要对应的一册乐谱）与 **1～20 个谱架槽位**。规则：

- 开场时**所有槽位为空**；每册乐谱占一个槽位，槽位编号 `1..slot_count`；
- 每场开演时该场所需的册必须已经在架上；
- **上架**（把一册放上槽位）计入总上架次数，**撤下**（把册取下）不计；
- 缺册时若**有空槽**，用**编号最小的空槽**；
- 缺册且**架满**时，撤下**未来最晚再次使用**的册——**永不再用视为最晚**，并列时取**方案 ID 字典序最小**者——再把所需册放上腾出的槽位。

该规则即 Belady 最优置换算法：只在缺册时上架、架满才撤下、总撤未来最晚再用者，可证**总上架次数最少**；空槽与并列的确定性规则只影响槽位排布，不影响上架次数。

任一方案 ID 不存在返回 `404`（去重后一次列出全部不存在的 ID）。该接口**只读取已保存方案，不写入新方案、不修改既有方案**。

请求体（JSON）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `plan_ids` | string[] | 按演出顺序排列的已保存方案 ID，**1～500 项、允许重复** |
| `slot_count` | number | 谱架槽位数，**1～20 的严格整数**（不接受 `2.0` 之类浮点或布尔） |

先创建四个方案（拿到四个 ID，记为 id1～id4 且字典序 id1<id2<id3<id4），再按 6 场曲目单、2 个槽位规划换册：

```bash
curl -X POST http://localhost:8000/api/v1/tour/stand-changes \
  -H 'Content-Type: application/json' \
  -d '{
    "plan_ids": ["id1", "id2", "id1", "id3", "id2", "id4"],
    "slot_count": 2
  }'
```

响应 `200`（实际 ID 为 32 位十六进制，此处用 id1..id4 示意）：

```json
{
  "request": {
    "plan_ids": ["id1", "id2", "id1", "id3", "id2", "id4"],
    "slot_count": 2
  },
  "slot_count": 2,
  "show_count": 6,
  "total_loads": 4,
  "shows": [
    {"show_index": 1, "plan_id": "id1", "load": {"plan_id": "id1", "slot": 1}, "unload": null, "slots_after": ["id1", null]},
    {"show_index": 2, "plan_id": "id2", "load": {"plan_id": "id2", "slot": 2}, "unload": null, "slots_after": ["id1", "id2"]},
    {"show_index": 3, "plan_id": "id1", "load": null, "unload": null, "slots_after": ["id1", "id2"]},
    {"show_index": 4, "plan_id": "id3", "load": {"plan_id": "id3", "slot": 1}, "unload": {"plan_id": "id1", "slot": 1}, "slots_after": ["id3", "id2"]},
    {"show_index": 5, "plan_id": "id2", "load": null, "unload": null, "slots_after": ["id3", "id2"]},
    {"show_index": 6, "plan_id": "id4", "load": {"plan_id": "id4", "slot": 2}, "unload": {"plan_id": "id2", "slot": 2}, "slots_after": ["id3", "id4"]}
  ]
}
```

逐场看：第 1、2 场空槽上架（各占编号最小的空槽）；第 3 场 id1 已在架上无需动作；第 4 场架满，id1 永不再用（视为最晚）、id2 第 5 场还要用，撤 id1 上 id3；第 5 场 id2 在架上；第 6 场架满，id2、id3 都永不再用，并列撤字典序较小的 id2，上 id4。

结果字段说明：

- `show_count`：演出场数（即 `plan_ids` 长度）；`slot_count`：谱架槽位数；
- `total_loads`：**总上架次数**（各场 `load` 非 `null` 的个数，全局最少）；
- `shows`：按演出顺序的每场换册明细：
  - `show_index`：场次序号（1 基）；`plan_id`：本场所需方案 ID；
  - `load`：上架动作（方案 ID 与槽位编号）；本场所需册已在架上时为 `null`；
  - `unload`：撤下动作；有空槽或无需上架时为 `null`。撤下与上架同场发生时槽位相同（腾出即用）；
  - `slots_after`：本场换册后各槽位上的方案 ID（按槽位编号 `1..slot_count` 顺序），空槽为 `null`；本场所需册必在其中。

所需册连续多场重复时只在首次上架（如 `["id1", "id1"]` 只上架 1 次）。方案不存在时返回 `404`：`{"detail": "以下方案不存在，无法规划谱架换册：<ID>"}`。

### 错误响应

| 状态码 | 触发条件 |
| --- | --- |
| `422` | 参数校验失败（空数组、长度不一致、负数/零高度、多余字段等） |
| `422` | 必翻/禁翻序号非法：含 `0`、末尾序号或越界值、元素非严格整数、数组内重复或两数组交叉，如 `required_breaks 含越界序号 0：合法范围为 1..n-1…` |
| `422` | 必翻点静默不足：`必翻位置 b（第 b 小节之后）静默仅 … 秒，低于最低翻页时间 … 秒，必翻点静默不足，无合法方案。` |
| `422` | 单小节超高：`第 k 小节高度 … 超过页面可用高度 …，小节不可拆开，无法排版。` |
| `422` | 无合法方案：`无合法翻页方案：在满足最低翻页时间、必翻/禁翻约束的位置无法完成分页…` |
| `404` | 按 ID 查询无记录 |
| `404` | 排练定位、翻页提示或翻页空档时规划不存在：`规划结果不存在：{id}` |
| `422` | `beats_per_measure 数组长度（…）与规划小节数（…）不符`（定位、翻页提示与翻页空档共用） |
| `422` | 重复段重叠或嵌套：`重复段 [a..b] 与 [c..d] 重叠或嵌套：…（首尾相邻允许）` |
| `422` | 重复段演奏次数超出 2～4，或起止小节越界 / 起始晚于结束 |
| `422` | 时间戳超出总时长或为负：`第 k 个时间戳：时间戳 … 秒超出演奏总时长 … 秒…`（仅定位接口） |
| `422` | `turn_seconds` 非正数或非有限数值（仅翻页空档接口） |
| `422` | `hands_busy 数组长度（…）与规划小节数（…）不符`（仅翻页空档接口） |
| `422` | 忙碌区间端点非法：非有限数值、不满足 `0 ≤ 起拍 < 止拍`，或止拍超过该小节拍数（仅翻页空档接口） |
| `404` | 踏板日志校对时规划不存在：`规划结果不存在：{id}` |
| `422` | 踏板事件秒数越出演奏时长（含恰等于总时长）：`第 k 个踏板事件：操作时刻 … 秒越出演奏时长 … 秒（须在 [0, 总时长) 内，恰等于总时长视为演奏结束之后）`；秒数为负：`… 秒不能为负数（以演奏开始为 0）` |
| `422` | 踏板事件操作后页码越界：`第 k 个踏板事件：操作后页码 … 越出页码范围 1..page_count`；页码非严格整数（小数、布尔、字符串、null）在请求层返回 `422` |
| `422` | 踏板事件时间非非降序：`第 k 个踏板事件：操作时刻 … 秒早于上一条 … 秒，踏板事件必须按时间非降序排列`（相等允许）；事件数超过 500：`踏板事件数（…）超过上限 500，无法校对` |
| `422` | 时间容差为负或非有限数值（NaN/Infinity）：`时间容差不能为负数…` / `时间容差必须是有限数值…`；排练参数非法（拍数长度不符、重复段重叠等）与定位/提示接口共用同一组错误（仅踏板日志校对接口） |
| `404` | 页序核对时规划不存在：`规划结果不存在：{id}` |
| `422` | 抄录页码非法：非严格正整数（含 `0`、负数、小数、布尔值、字符串、null），如 `抄录页码必须为正整数（收到 True）` |
| `422` | 规划页数或实物页数超过上限 500：`规划页数（…）超过上限 500，无法核对` / `实物页数（…）超过上限 500，无法核对` |
| `404` | 练习页签规划时规划不存在：`规划结果不存在：{id}` |
| `422` | 练习页签规划参数非法：`practice_measures 不能为空…`、`第 k 个常练小节序号 … 越界：合法范围为 0..n-1`、`常练小节序号 … 重复…`、`第 k 个查找权重必须为正整数…`、`页签数 K（…）必须在 1 到目标页数（…）之间`、`目标页数（…）超过上限 500，无法规划页签`；小节序号/权重/`K` 为小数、布尔、字符串等非严格整数同样返回 `422` |
| `404` | 练习路线规划时规划不存在：`规划结果不存在：{id}` |
| `422` | 练习路线规划参数非法：`practice_measures 不能为空…`、`常练小节数（…）超过上限 15`、`第 k 个常练小节序号 … 越界：合法范围为 0..n-1`、`常练小节序号 … 重复…`、`起始页码 … 越界：合法范围为 1..page_count`、`第 k 个依赖（a → b）指向非目标小节 …`、`先后依赖成环，不存在满足全部依赖的练习顺序`（含自环）；小节序号/起始页码/依赖端点为小数、布尔、字符串等非严格整数，或依赖不是二元组，同样返回 `422` |
| `404` | 小册子拼版时规划不存在：`规划结果不存在：{id}` |
| `422` | 小册子拼版参数非法：`segment_end_pages 不能为空…`、`曲段末页页码必须严格递增：…`、`曲段末页末项（…）必须等于方案页数（…）`、`第 k 个曲段末页页码必须为正整数…`、`每帖最多印张数必须为正整数…`、`曲段数（…）不能超过方案页数（…）`；末页或印张数为小数、布尔、字符串、null 等非严格整数同样返回 `422` |
| `422` | 单段超容量：`第 k 个曲段（原谱页 a..b，共 n 页）超过每帖容量（… 个印张 = … 页），曲段不可跨帖，无法拼版` |
| `422` | 规划页数超过纸质拼版上限 500：`规划页数（…）超过纸质拼版上限 500，无法拼版` |
| `404` | 乐谱册装箱时存在不存在的方案 ID：`以下方案不存在，无法装箱：<ID>、<ID>`（一次列出全部不存在的 ID） |
| `422` | 乐谱册装箱请求非法：方案数不足或超过 2～12（`方案数（…）少于下限 2…` / `…超过上限 12…`）、方案 ID 重复（`方案 ID 重复（…），每个方案只能出现一次`）、ID 非字符串/为空、箱体页容量非正整数或非严格整数（小数、布尔、字符串） |
| `422` | 单册超容量：`方案 <ID> 的乐谱册共 <n> 页，超过箱体页容量 <c> 页：每册只能整本放入一箱，无法装箱` |
| `404` | 谱架换册时存在不存在的方案 ID：`以下方案不存在，无法规划谱架换册：<ID>、<ID>`（去重后一次列出全部不存在的 ID） |
| `422` | 谱架换册请求非法：演出场数不足或超过 1～500（`演出场数（…）少于下限 1…` / `…超过上限 500…`）、谱架槽位数不在 1～20（`谱架槽位数（…）必须在 1~20 之间`）、槽位数非严格整数（小数、布尔、字符串）、方案 ID 非字符串 |

## 算法说明

**数值口径**：所有输入数值（小节高度、页高、静默秒数、最低翻页时间）先按十进制字面量解析为 Decimal，再把"高度组（含页高）"与"静默组（含最低翻页时间）"分别按组内最多小数位数统一放大为整数（纯十进制移位、无舍入）。前缀累计高度与全部容量/静默比较都在这些整数上进行，大小关系与十进制精确值逐位一致，既不会把略超容量（如 1.0000000005 对 1）误判为合格，也能识别十进制下的恰好相等（`0.1 + 0.2 == 0.3`、静默恰等于阈值）。对外输出时再转回 float，float 不参与任何判定。

设小节 0..n-1，前缀高度和 `P`，断页位置 b 表示下一页从第 b 小节开始。

1. **最少页数**：前向 DP，`f[r]` = 排完 0..r 的最少页数，转移
   `f[r] = 1 + min{ f[l-1] : l 使高度和 ≤ page_height 且 (l=0 或 silences[l-1] ≥ min_turn_time) }`；
   l 的可行区间随 r 单调，用单调队列（滑动窗口最小值）维护最小值，窗口高度下界用只随 r 右移的双指针推进（不做逐步二分），严格 O(n)。存在必翻点时 l 还须 ≥ `floor_forced[r]`（≤ r 的最后一个必翻点），禁翻位置不允许作为 l。不可达即无合法方案。
2. **最大化最短静默**：将所有翻页点静默 ≥ t 的方案视为“可行”，t 越大可行位置越少、可行性单调，故在去重后的候选静默值上二分；每次可行性判定复用第 1 步的 DP（允许位置标志数组按整数静默阈值预生成，仍是 O(n)）。
3. **字典序最小断页序列**：阈值固定后做后缀 DP，求每个位置排完剩余部分所需的最少/最多页数（允许的页数在两者之间连续可达，单调队列 O(n) 维护窗口最小/最大值，页尾容量上界同样用只反向移动的双指针维护）；自首页起从小到大试探断页位置 b，要求剩余小节恰可用指定页数排完，第一个可行的 b 即为字典序最小选择；重构时各页容量上界随页首右移单调，亦用双指针 O(n) 推进。

必翻/禁翻约束在三步 DP 中统一施加（三个目标始终全篇比较，不按必翻点分段各取局部最优）：

- **禁翻位置**直接从"允许翻页位置集合"中剔除（前向 DP、阈值判定、后缀 DP 三处一致）；
- **必翻位置**通过页首/页尾边界强制：前向 DP 中以 r 为页尾时，页首 l 不得晚于 ≤ r 的最后一个必翻点（`floor_forced[r]`），从而内含必翻点的页只能以该必翻点起始；后缀 DP 中以 i 为页首时，页尾 e 不得越过 i 之后的第一个必翻点（`next_forced[i]`），贪心重构的候选断页位置同理截断；
- 必翻位置静默不足在任何 DP 计算前直接判定为无合法方案。

单小节超高、约束序号结构非法在任何计算前直接判定并报错。

**排练踏板日志校对**（`app/pedal_audit.py`）：先用排练参数按十进制精确口径构建与第 4 节相同的演奏序列与应翻提示（含重复段回跳回翻），再设提示 m 条、踏板事件 n 条做有序序列对齐。后缀标量 DP：`G[i][j]`/`S[i][j]` 分别为提示 `[i:]` 与事件 `[j:]` 对齐时的最小（漏翻+多翻）数与该数下的最小匹配时间差之和；三种转移——漏翻提示 i `(G[i+1][j]+1, S[i+1][j])`、事件 j 多翻 `(G[i][j+1]+1, S[i][j+1])`、以及在目标页相同且 `|t_i−t_j| ≤ 容差` 时匹配 `(G[i+1][j+1], S[i+1][j+1]+|Δt|)`，各取字典序最小，整体 `O(m·n)`（m、n 均 ≤ 500）。第三项目标（匹配事件序号序列字典序最小）只决定同 `(G,S)` 下的重构、不影响两项代价，故不把变长序号元组放进标量表，而在前向重构时以记忆化递归 `best(i,j)` 求该后缀在保持最优代价下的字典序最小匹配元组（`匹配(j,·) < 漏翻() < 多翻()`，每格至多计算一次，最坏元组总规模约 `O(m·n·min(m,n))`、500 上限下约 17MB），再沿其指示还原一对一匹配、漏翻提示与多翻事件。全部时间量按十进制字面量构造有理数（`Fraction`）：每小节拍数与速度先转成有理数，`60×拍数/速度` 与按重复段展开后的提示时刻、总时长都是精确有理数，事件秒数与容差同样转换；匹配资格、时间差之和的累计与并列判定、事件的非降序与 `[0, 总时长)` 校验全为有理数精确比较，不用浮点也不用 epsilon——容差 0 时 `0.3000000005` 不等于 `0.3`，提示 `0.4` 对同目标页事件 `0.3`/`0.5` 的两侧时间差精确相等（等成本取事件序号字典序最小），略早于总时长可接受、略逆序即报错；只有响应出口把有理数转回 JSON number。事件秒数须在 `[0, 总时长)`（恰等于总时长拒绝）、页码须在 `1..page_count`、事件按时间非降序，任一不满足在 DP 前直接报错。

**页序核对**（`app/print_audit.py`）：以 `1..page_count` 为目标序列、抄录页码为源序列的 Levenshtein 编辑距离——保留计 0，替换/移除/补入各计 1。后缀 DP `d[i][j]` = 把 `scanned_pages[i:]` 修正成 `target[j:]` 的最少操作数，整体 `O(m·n)`（m、n 均 ≤ 500）；随后前向贪心重构：每一步在能保持全局最优的候选动作中按 保留 → 替换 → 移除 → 补入 取第一个，得到从前到后的确定性步骤序列，补入步的 `position` 取当前原输入下标（等于数组长度即末尾）。

**练习页签规划**（`app/practice_tabs.py`）：先按方案的页起始小节把常练小节二分归页、同页权重相加，得到 m 个（页码, 权重）目标点（m ≤ 500）。问题等价于直线加权 K-中位数：直线上存在最优解使各页签的服务范围为连续区间，故做有序分割 DP `dp[k][i] = min_{j} dp[k-1][j] + g[j][i]`，其中 `g[j][i]` 为区间 `[j,i)` 以其**下加权中位数**（累计权重首次达到 `ceil(W/2)` 的点；两侧恰好等重时取较小页码，与"距离并列归较小页码"一致）为页签时的距离代价和。`g` 与中位数下标表一次性 `O(m²)` 预计算（固定左端点扩展时中位数下标总计只右移 m 次）；`g` 满足 Monge（凹四边形不等式），正向与后缀 DP 均用分治优化，整体 `O(m² + K·m·log m)`。代价最优后，借助后缀代价表从左到右贪心重构字典序最小的升序页签列表：第一段分割从小到大取能保持全局最优代价、且首段中位数页码最小者（中位数随分割点单调右移，右移即止），同中位数时沿记忆化链表递归比较剩余列表。最后每个目标页用双指针找最近页签（严格更近才右移，等距保留左侧），重算总代价与 DP 最优值互校。

**练习路线规划**（`app/practice_route.py`）：先按方案的页起始小节把常练小节二分归页，再把小节按序号升序编号做子集 DP（Held-Karp 式后缀 DP）：`dp[visited][last]` = 已练集合为 `visited`、最后练的是 `last` 时练完剩余小节的最小页码距离，转移只允许加入**直接前驱已全部练完**的小节，依赖因此天然满足；k ≤ 15，状态数不超过 `k·2^(k-1)`（约 24 万），按掩码从大到小一趟递推。先后依赖成环（含自环）时无任何可行顺序，先用 Kahn 拓扑排序显式判定并报错。最优总距离确定后从左到右贪心重构：每个位置按小节序号升序，取第一个"已花距离 + 本段距离 + 后缀最优距离 == 全局最优"的小节——字典序由前缀决定，逐位取最小可行值即得同成本下字典序最小的顺序。

**小册子拼版**（`app/booklet.py`）：设曲段末页 `e[1..k]`（补 `e[0]=0`），分帖 DP `dp[i]` = 覆盖前 i 个曲段的最优代价三元组 `(总空白页, 帖数, 各帖末页序列)`，转移枚举本帖覆盖曲段 `a..i`：

```
dp[i] = min over a 使 (e[i] − e[a−1]) ≤ 4·max_sheets:
        ( (−(e[i]−e[a−1])) mod 4 + dp[a−1].空白,
          dp[a−1].帖数 + 1,
          dp[a−1].末页序列 + (e[i],) )
```

三元组按 Python 元组字典序比较，恰好实现"总空白页 → 帖数 → 末页序列"的目标优先级；可行的 `a` 因 `e` 严格递增构成连续区间（二分定位下界），整体 O(k²)（k ≤ 500）。记录每步选中的 `a` 回溯即各帖曲段区间。任一单段页数超容量在 DP 前直接报错。一帖共 `P` 页（4 的倍数）、`S=P/4` 印张，从外到内第 `t` 印张（`t=0..S−1`）四个位置为：正面左 `P−2t`、正面右 `1+2t`、背面左 `2+2t`、背面右 `P−1−2t`（局部页码大于实际原谱页数者为帖尾空白，输出 `null`；页码加帖首页偏移换算为全册全局页码）。

**乐谱册装箱**（`app/tour_packing.py`）：册数 n ≤ 12，先把 (ID, 页数) 按 ID 字典序排序。① 可装判定用降序放置 + 同负载箱去重 + 空箱对称剪枝的回溯，判定各册能否整本装入至多 b 个容量 c 的箱；② **最少箱数**从 `ceil(总页数/容量)` 下界起递增判定，首次可行值即最少箱数 B；③ **最重箱装载页数最小值**在 `[最厚册页数, 容量]` 上二分，每次以"能否装入 B 箱、每箱不超过候选页数"判定（单调性使二分成立）；④ **字典序最小分箱**逐箱贪心——规范（箱内 ID 升序、箱列表按 ID 元组升序）分箱中第一个箱必含剩余最小 ID，枚举包含它的全部子集并按箱内 ID 升序元组的字典序试探，取第一个使剩余册能恰分进剩余箱数（每箱非空、负载不超过最优最重页数）的子集，递归到分完；子集可行性用"首箱必含最小 ID"的子集掩码回溯（在固定剩余箱数下记忆已证伪掩码）。任何单册页数超过箱体页容量在搜索前直接报错。接口层先经 pydantic 校验方案数（2～12）、ID 互异与非空字符串、容量严格正整数，再从 `PlanStore` 逐个按 ID 读取方案页数，任一不存在即返回 404（列出全部缺失 ID），全过程不写入数据。

**谱架换册**（`app/tour_stands.py`）：设场次序列长 n ≤ 500、槽位 k ≤ 20。先自右向左一趟求出 `next_after[i]`（下标 i 之后下一场需要同一册的场次下标，不再使用记为 n——"永不再用视为最晚"，n 大于任何真实下标）；再按演出顺序模拟：维护各槽位现状与架上各册的下次使用下标，所需册已在架上则跳过；否则有空槽时取**编号最小的空槽**上架，架满时以 `(−下次使用下标, 方案 ID)` 为键取最小者撤下（即下次使用最晚、并列取 ID 字典序最小），再把所需册放上腾出的槽位，整体 O(n·k)。该规则即 Belady 最优置换算法：提前上架可推迟到首次需要而不增加上架次数，架满缺册时撤"最晚再次使用"者不劣于撤任何其他册（交换论证），故**总上架次数最少**；空槽选择与并列的 ID 字典序规则只影响槽位排布、不影响上架次数。接口层先经 pydantic 校验场数（1～500）、槽位数（1～20 严格整数）与 ID 为字符串，再从 `PlanStore` 按 ID 去重检查存在性，任一不存在即返回 404（一次列出全部缺失 ID），全过程不写入数据。

## 本地开发与测试

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload            # 本地启动
python -m pip install pytest && python -m pytest -q   # 运行测试
```

测试包含 200 组随机用例与穷举全部分页方案的结果对照、边界条件与 10 万小节规模性能用例，以及十进制数值边界的专项用例——单小节高度略超页高（1.0000000005 对 1）拒绝、三小节静默 1/1.0000000005/0 选断页 2、0.1+0.2 恰好等于页高 0.3 同页、累计略超（0.1+0.2000000001 对 0.3）拆页、静默略低于阈值（0.9999999999 对 1）不得翻页、必翻点静默略短无解、阈值搜索不把略短静默误作达标，以及 60 组在第 9~11 位小数附近抖动的随机用例与 Decimal 口径暴力枚举对照，还有排练重复段展开、左闭右开边界定位、页码映射、排练翻页提示（前进/回翻/静默充足性按同一十进制口径判定）、排练翻页空档（乱序重叠区间合并、最晚开始时刻、并列优先左手、不可行边界保留）与接口端到端用例。排练踏板日志校对另有 300 组随机用例与枚举全部单调对齐的全元组暴力参考实现（按"漏翻+多翻数 → 时间差之和 → 匹配事件序号元组字典序"取最优）对照、目标页相同但时间超容差/时间相近但目标页不同不得匹配、容差边界（恰好等于允许）、一对一全局对齐、三级目标的确定性并列用例（含回翻提示）、漏翻/多翻计数与不变量、空事件/无提示单页/同刻事件、事件越出演奏时长（含恰等于总时长）或页码范围/非降序破坏/容差为负或非有限/页码非严格整数/501 事件超限、500×500 规模性能用例及接口端到端（404、422、字段缺失与多余、请求回显、不修改已存方案）用例。必翻/禁翻约束另有 300 组随机穷举对照（全篇统一最优 vs. 实现）、"全篇统一而非分段局部最优"的确定性用例、序号非法（0/末尾/越界/重复/交叉/非整数）、必翻点静默不足、约束下无合法方案、请求回显与持久化等用例。页序核对另有与独立记忆化参考实现的随机对照、与穷举全部最优脚本的字典序最小性对照、四种同成本并列的确定性用例、空栈/缺页/多页/错页/重复页、页码与长度非法（0/负数/小数/布尔/字符串/null、501 页超限、500 页边界）及接口端到端用例。练习页签规划另有数百组随机用例与枚举全部页签组合的暴力参考实现（含同页权重合并、稀疏目标页、大权重）对照、分治优化 DP 与 O(K·m²) 朴素 DP 的分层对照、等距归较小页码与字典序最小并列用例、空目标/小节越界/重复/权重非法/K 越界/501 目标页超限、500 目标页规模性能用例及接口端到端用例。练习路线规划另有与枚举全部排列的暴力参考实现的随机对照（无依赖/稀疏/稠密依赖）、依赖强制顺序与同成本字典序最小并列的确定性用例、空目标/小节数超 15 上限/小节或起始页码越界/重复小节/依赖指向非目标小节/自环与成环、15 小节最坏情形性能用例及接口端到端用例。小册子拼版另有 300 组随机用例与枚举全部 2^(k-1) 种分帖的暴力参考实现（按总空白页→帖数→末页序列字典序取最优）对照、骑马订正反面页码与全局页码/空白位置的不变量检查、8 页经典版面、帖尾补白、同空白取帖数少、同分取末页序列字典序最小、每帖至少一段、单页/500 页边界与 500 段规模性能用例，以及空曲段/末页非严格递增或末项不等于方案页数/非正整数（0、负数、小数、布尔、字符串、null）/曲段数超页数/印张数非正或非整数/单段超容量/规划超 500 页与接口端到端（404、422、字段缺失与多余、请求回显、不修改已存方案）用例。乐谱册装箱另有 400 组随机用例（n ≤ 8）与枚举全部集合分箱（Bell(n) 种受限增长字符串）的暴力参考实现按"箱数 → 最重箱装载页数 → 规范箱列表字典序"三级目标对照、n=9..12 的结构不变量与最优性必要条件检查、2/12 册数边界、单册超容量、字符串 ID 字典序（`p10` 先于 `p2`）、前缀元组短者更小等并列确定性用例，以及方案数不足/超上限/重复 ID/ID 非字符串/容量非正或非整数/方案不存在（404 且列出全部缺失 ID）/单册超容量（422）/字段缺失与多余/请求回显/不写入也不修改方案等接口端到端用例。谱架换册另有 300 组随机用例与"架上册集合状态 DP 求最少上架次数"的暴力参考实现对照、更大规模（25 场 7 册 5 槽）的分层最优性对照、独立前向扫描复算逐场空槽选择与撤下对象规则、上架即占用腾出槽位/场后槽位守恒/同册至多占一槽等结构不变量检查、空槽编号最小/最晚再用撤下/永不再用优先/并列取字典序最小（含 `p10` 先于 `p2`）等确定性用例、1 场 1 槽与 500 场 20 槽边界及性能用例，以及场数不足/超上限/槽位数越界或非严格整数/ID 非字符串/方案不存在（404 且去重列出全部缺失 ID）/字段缺失与多余/请求回显/允许重复 ID/不写入也不修改方案等接口端到端用例。
