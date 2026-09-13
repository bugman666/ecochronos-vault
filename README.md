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
| API / 编排 | Python · FastAPI + Celery 或 Temporal | 任务调度与服务入口 |
| 本地分析 | DuckDB | 清洗、重采样、列式写出 |
| 对象存储 | MinIO（S3 兼容） | 数据集归档与只读分发 |
| 下载 | Nginx（或同类） | 大文件分块直链 |
| 元数据 | PostgreSQL + PostGIS | 时空索引与 checksum |

## 当前状态

骨架已能本地跑起来。后续大致按这些切片推进：

- [x] 可运行骨架（配置、健康检查、Compose 含 Postgres/MinIO）  
- [ ] 按天采集管道（至少接上一个开放源）  
- [ ] 本地批处理写出 Parquet（或 NetCDF/Zarr）  
- [x] MinIO 归档 + 只读下载路径  
- [ ] PostGIS 元数据登记与简单检索 API  

## 本地跑起来

复制环境变量模板，再用 Compose 拉起 API、PostGIS 和 MinIO：

```bash
cp .env.example .env
docker compose up --build
```

`/healthz` 是廉价探活（进程起来即 200）；`/readyz` 会检查 Postgres 与 MinIO。

```bash
curl -s http://127.0.0.1:8000/healthz
```

不启动容器时，可只跑单测：

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

## License

待定（倾向 MIT 或 Apache-2.0）。
