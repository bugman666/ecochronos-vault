# EcoChronos Vault

全球微气候与地表生态的高分辨率时序开放归档：把公开环境监测网的数据收进来，洗成科研常用的列式/网格格式，再提供可持续下载与检索。

面向生态科研、气候与环境相关的开放数据使用者。可自托管，不设付费墙。

## 做什么

1. **采集**：按天拉取公开地理网格与站点类开放数据（例如 Copernicus 衍生产品、NOAA 历史气象、OpenAQ 等）  
2. **批处理**：本地清洗、空间重采样，输出 Parquet / NetCDF / Zarr 等列式或网格格式  
3. **归档分发**：对象存储（MinIO 等 S3 兼容）+ HTTP 大文件分块 / 断点续传下载  
4. **元数据**：用空间库索引每个格点/时段数据块及校验和，方便检索与校验  

这类流水线每天可累积大量多维切片，依赖本机大容量磁盘做持久化读写与压缩分块，而不是挤在很小的免费对象存储额度里。

## 计划中的技术栈

| 层 | 选型 | 用途 |
|----|------|------|
| API / 编排 | Python · FastAPI + 进程内调度或 cron | 任务调度与服务入口 |
| 本地分析 | DuckDB | 清洗、重采样、列式写出 |
| 对象存储 | MinIO（S3 兼容） | 数据集归档与只读分发 |
| 下载 | Nginx（或同类） | 大文件分块直链 |
| 元数据 | PostgreSQL + PostGIS | 时空索引与 checksum |

## 当前状态

骨架已能本地跑起来，接上了第一个按天采集源，并且本地批处理可以把暂存 CSV 洗成按日 Parquet：

- [x] 可运行骨架（配置、健康检查、Compose 含 Postgres/MinIO）  
- [x] 按天采集管道（OpenAQ v3 → 本地 `data/raw/`）  
- [x] 本地批处理写出 Parquet（或 NetCDF/Zarr）  
- [x] MinIO 归档 + 只读下载路径  
- [x] PostGIS 元数据登记与简单检索 API  

## 采集源：OpenAQ

这一切片只接 **OpenAQ v3**（HTTP JSON），方便单测用假响应替换真实网络。每天（或按 `INGEST_INTERVAL_SECONDS`）请求：

`GET {OPENAQ_BASE_URL}/locations?limit=…`（可选 `iso=` 国家过滤）

把响应包一层抓取元数据后写入：

```
{DATA_DIR}/raw/openaq/YYYY-MM-DD/locations.json
```

同一天再跑一次会先写临时文件再原子替换，不会留下半截文件。设 `INGEST_SKIP_EXISTING=true` 或 `ecochronos-vault ingest --skip-existing` 则已有当天文件时跳过。

OpenAQ v3 需要免费 API Key：在 [explore.openaq.org](https://explore.openaq.org) 注册，写入 `OPENAQ_API_KEY`。没有 key 时采集会失败并记入状态，不会改坏已有文件。

调度两种用法：

- **cron / 手动**：`ecochronos-vault ingest`（可加 `--date YYYY-MM-DD`）
- **进程内**：`INGEST_SCHEDULE_ENABLED=true` 时 API 启动后用 APScheduler 按间隔跑；默认间隔 86400 秒，启动先跑一轮

上次结果：`GET /ingest/status` 或 `ecochronos-vault ingest-status`。

MinIO 归档与 Range 下载已在主线落地（见下方「归档与下载」）。采集结果目前只写本地磁盘，尚未自动推到 bucket。归档之后可用 `PUT /chunks` 或 `MetadataStore.upsert` 登记时空范围与 checksum。

## 批处理

这一切片只做本地清洗与按日聚合，不跑 Docker，也不访问网络。DuckDB 读 `STAGING_DIR`（默认 `data/staging`）里的 `*.csv`，写出科研常用的 Parquet。

约定（表头不区分大小写，可用别名）：

| 字段 | 别名 | 说明 |
|------|------|------|
| `time` | `timestamp` / `datetime` / `date` | 按 **UTC 日历日** 截断 |
| `station_id` | `station` / `site_id` / `site` | 空值丢弃 |
| `lon` | `longitude` / `x` | 须在 `[-180, 180]` |
| `lat` | `latitude` / `y` | 须在 `[-90, 90]` |
| `value` | `val` / `observation` / `measure` | 数值；无法转换则丢弃 |

空行、时间/坐标/观测无法转换、或经纬度越界的行都会丢掉。重采样规则目前只有 `daily_mean_by_station`：按 UTC 日期 + 站点对 `value` 和经纬度取平均，并记下 `n_obs`。默认写出：

```
{PROCESSED_DIR}/daily_mean_by_station.parquet
{PROCESSED_DIR}/daily_mean_by_station.parquet.sha256
```

用仓库里的示例跑一遍：

```bash
pip install -e ".[dev]"
ecochronos-vault-batch --staging-dir tests/fixtures/staging --processed-dir data/processed
```

Python 里同样可以 `from ecochronos_vault.batch import run_batch`。采集目前写出的是 OpenAQ JSON，还没有自动转成这份 CSV 约定。Parquet 进归档后，可用旁边的 `.sha256` 和站点范围去登记 PostGIS 元数据。

## 本地跑起来

复制环境变量模板，再用 Compose 拉起 API、PostGIS 和 MinIO：

```bash
cp .env.example .env
docker compose up --build
```

`/healthz` 是廉价探活（进程起来即 200）；`/readyz` 会检查 Postgres 与 MinIO。Compose 里默认打开采集调度，需要先填 `OPENAQ_API_KEY` 才会真正拉到数据。

```bash
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/ingest/status
```

本机不经 Compose、只跑一次采集：

```bash
pip install -e ".[dev]"
ecochronos-vault ingest
ecochronos-vault ingest-status
```

不启动容器时，可只跑单测（采集测试全部走假 HTTP，不访问 OpenAQ）：

```bash
pip install -e ".[dev]"
pytest
```

## 归档与下载

对象落在配置的 MinIO bucket（默认 `ecochronos`）里。HTTP 下载按开放归档处理：**只读、无鉴权**。没有对象级 ACL；MinIO 控制台和密钥只给本机/采集用。

### 下载（支持 Range 断点续传）

```bash
# 全量
curl -fL -o alphabet.txt http://127.0.0.1:8000/archive/demo/alphabet.txt

# 分块 / 续传。成功时是 206 Partial Content，带 Accept-Ranges 和 Content-Range
curl -H "Range: bytes=0-1023" -o part0.bin http://127.0.0.1:8000/archive/demo/alphabet.txt
curl -C - -o alphabet.txt http://127.0.0.1:8000/archive/demo/alphabet.txt
```

`HEAD /archive/{key}` 只返回大小和 `Accept-Ranges: bytes`，方便先探长度再按块拉。

### 上传

HTTP `PUT /archive/{key}` **默认关闭**。需要本机或受信采集端走 HTTP 时，在 `.env` 里设置 `ARCHIVE_UPLOAD_TOKEN`，再用 Bearer 或 `X-Archive-Token`：

```bash
curl -X PUT \
  -H "Authorization: Bearer $ARCHIVE_UPLOAD_TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @alphabet.txt \
  http://127.0.0.1:8000/archive/demo/alphabet.txt
```

批处理管道也可以直接调内部客户端，不必经过 HTTP：

```python
from ecochronos_vault.archive import ArchiveStore
from ecochronos_vault.config import get_settings

store = ArchiveStore.from_settings(get_settings())
store.put_bytes("demo/alphabet.txt", b"abcdefghijklmnopqrstuvwxyz\n", content_type="text/plain")
```

## 元数据（PostGIS + checksum）

每个时空数据块在 Postgres/PostGIS 里登记：稳定 `chunk_id`、对象 `storage_key` / 可选 `uri`、WGS84 `bbox`、时间范围、`sha256` 校验和。OpenAQ 采集目前只写本地 `data/raw/`；对象进归档后，用同一条内部 API 登记即可。

### 迁移

Compose 里的 API 进程启动时会尝试套用 schema（`CREATE EXTENSION postgis`、`chunk_metadata` 表和 GIST 索引）。也可以单独跑：

```bash
# 需要 POSTGRES_DSN（见 .env.example）
ecochronos-vault-migrate
# 或
make migrate
```

本机直连 Compose 的 Postgres 时，把 `POSTGRES_DSN` 指到 `127.0.0.1:5432`。CI 单测不连真实 PostGIS，用内存索引覆盖检索/登记行为。

### 登记（内部 / HTTP）

`PUT /chunks` 与归档上传共用 `ARCHIVE_UPLOAD_TOKEN`；未设置则禁止写入。检索是开放的。

```bash
curl -sS -X PUT http://127.0.0.1:8000/chunks \
  -H "Authorization: Bearer $ARCHIVE_UPLOAD_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "chunk_id": "demo/2024-06-01",
    "storage_key": "archive/demo/2024-06-01.parquet",
    "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "time_start": "2024-06-01T00:00:00Z",
    "time_end": "2024-06-02T00:00:00Z",
    "bbox": [-10.0, 50.0, 2.0, 60.0],
    "dataset": "demo"
  }'
```

批处理也可以直接调 store（之后的采集/Parquet 管道可以在写出对象后接上）：

```python
from datetime import datetime, timezone

from ecochronos_vault.config import get_settings
from ecochronos_vault.metadata import ChunkWrite, metadata_store_from_settings, sha256_hex

store = metadata_store_from_settings(get_settings())
store.upsert(ChunkWrite(
    chunk_id="demo/2024-06-01",
    storage_key="archive/demo/2024-06-01.parquet",
    checksum=sha256_hex(b""),
    time_start=datetime(2024, 6, 1, tzinfo=timezone.utc),
    time_end=datetime(2024, 6, 2, tzinfo=timezone.utc),
    bbox=(-10.0, 50.0, 2.0, 60.0),
    dataset="demo",
))
```

### 检索

`GET /chunks` 可按时间重叠、bbox 相交、`storage_key` 前缀、`dataset` 过滤，返回 checksum 和位置信息：

```bash
# 列出
curl -sS "http://127.0.0.1:8000/chunks"

# 时间范围 + bbox（west,south,east,north）+ key 前缀
curl -sS -G "http://127.0.0.1:8000/chunks" \
  --data-urlencode "time_start=2024-06-01T00:00:00Z" \
  --data-urlencode "time_end=2024-06-02T00:00:00Z" \
  --data-urlencode "bbox=-1,54,1,56" \
  --data-urlencode "prefix=archive/demo"

# 单条
curl -sS "http://127.0.0.1:8000/chunks/demo/2024-06-01"
```

## License

MIT。见仓库根目录 `LICENSE`。
