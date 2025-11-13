# FastAPI 前端可视化蓝图 v1（Polymarket 套利脚本）

> 目标：把原本需要在 Linux 命令行运行的 `Volatility_arbitrage_run.py` 等 Python 脚本，用 **FastAPI + 极简网页** 封装成「可在浏览器点按钮、填表单」执行的工具。
>
> 范围：只做 **最小可行版本（MVP）**——单机、自用、无复杂权限控制；后续增强（多用户、后台任务、监控）留作升级章节。

---

## 0. 总体分层思路

从脚本到前端可视化，拆成 3 层：

1. **脚本层（Core）**
   - 现有的 Polymarket 套利逻辑：`Volatility_arbitrage_run.py` 等。
   - 当前先视为「黑盒」，只要能从命令行跑通即可。

2. **服务层（Service / API）**
   - 用 FastAPI 把套利逻辑包装成 HTTP 接口。
   - 可以：
     - 直接 `subprocess` 调用脚本；或
     - 更优雅地将脚本重构为可调用函数，然后在 API 中调用。

3. **前端层（Web UI）**
   - 一个极简网页：
     - 表单：输入市场 URL / 方向 / 份数；
     - 按钮：一键执行；
     - 区域：显示脚本输出日志。

**先打通最简单的数据流：**

> 浏览器表单 → FastAPI 接口 → 执行套利脚本 → 把 stdout/stderr 回传给浏览器展示。

---

## 1. 环境准备与项目骨架

### 1.1 目录规划

建议新建一个专门的目录，和策略仓库并列，方便后续升级和部署：

```bash
cd /home/trader
mkdir polymarket_frontend
cd polymarket_frontend
```

### 1.2 虚拟环境（可选但推荐）

视情况选择复用 `poly312` 或新开一个 venv。这里以新 venv 为例：

```bash
python3 -m venv venv
source venv/bin/activate
```

### 1.3 安装 FastAPI + Uvicorn

```bash
pip install "fastapi[standard]" uvicorn
```

> `fastapi[standard]` 带上了常用依赖；`uvicorn` 是 ASGI 服务器。

### 1.4 最小目录结构

```text
polymarket_frontend/
  ├── venv/                 # 虚拟环境（可选）
  ├── main.py               # FastAPI 入口
  └── templates/            # HTML 模板（第 4 阶段用）
      └── index.html
```

---

## 2. FastAPI 最小骨架（Hello API）

### 2.1 建立 `main.py`

目标：先验证 FastAPI 是否正常工作。

```python
# main.py
from fastapi import FastAPI

app = FastAPI()


@app.get("/ping")
def ping():
    return {"message": "pong"}
```

### 2.2 启动服务

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- `--reload`：代码变更自动重载（开发期用）。
- `--host 0.0.0.0`：允许外部访问。

### 2.3 自检

浏览器访问：

- `http://<服务器IP>:8000/ping` → 应返回 `{"message":"pong"}`。
- `http://<服务器IP>:8000/docs` → 自动生成接口文档（Swagger UI），用于调试后面新增的接口。

**到此为止：FastAPI 骨架已就绪。**

---

## 3. 将命令行脚本「变成可调用」

> 目标：把原本 `python3 Volatility_arbitrage_run.py` 的命令行执行，抽象成「从 FastAPI 里可以调用」的逻辑。
>
> 分两种路线：
> - 快速路线：`subprocess`，对现有脚本 **0 改动**；
> - 正规路线：重构脚本，提取为函数，再在 API 中直接调用。

### 3.1 快速路线：用 `subprocess` 直接调用脚本

**优点**
- 不动现有脚本逻辑，风险低；
- 快速打通「前端点按钮 → 脚本运行」。

**缺点**
- 每次 API 调用都会新启一个 Python 进程；
- 脚本运行时间长时，API 请求会阻塞到脚本结束。

示例封装函数（稍后在 FastAPI 中使用）：

```python
import subprocess

def run_arbitrage_cli(market_url: str):
    result = subprocess.run(
        [
            "python3",
            "/home/trader/polymarket_api/strategy2/Volatility_arbitrage_run.py",
            market_url,
        ],
        capture_output=True,
        text=True,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
```

> 后续可以把方向、份数等参数也改成命令行参数传给脚本，视原脚本结构而定。

### 3.2 正规路线：重构为函数（后续升级）

思路：在策略仓库中新增一个轻量封装模块，比如 `arbitrage_wrapper.py`：

```python
# arbitrage_wrapper.py
# 这里建议在策略仓库内创建

from Volatility_arbitrage_run import main as run_main  # 示例：视原文件结构而定


def run_arbitrage(market_url: str, direction: str, size: float):
    """把原来依赖命令行/交互输入的逻辑，改成接收函数参数。"""
    # TODO: 按原脚本逻辑，把 market_url/direction/size 填入配置
    # config = Config(market_url=market_url, direction=direction, size=size)
    # run_main(config)
    run_main()
    return {"status": "ok"}
```

FastAPI 项目中通过 `sys.path.append` 或设置 `PYTHONPATH` 来引入该模块：

```python
import sys
sys.path.append("/home/trader/polymarket_api/strategy2")

from arbitrage_wrapper import run_arbitrage
```

建议：**MVP 阶段先使用 `subprocess` 版本；等前后联通后，再渐进重构为函数调用版。**

---

## 4. 用 FastAPI 暴露「运行套利」接口

> 目标：在 `/run-arbitrage` 暴露一个 POST 接口，接受参数（market_url 等），调用脚本并返回执行结果。

### 4.1 定义数据模型与封装函数

在 `main.py` 中引入 Pydantic 模型和前面写好的 CLI 封装：

```python
from fastapi import FastAPI
from pydantic import BaseModel
import subprocess

app = FastAPI()


class RunConfig(BaseModel):
    market_url: str
    direction: str | None = None
    size: float | None = None


def run_arbitrage_cli(market_url: str):
    result = subprocess.run(
        [
            "python3",
            "/home/trader/polymarket_api/strategy2/Volatility_arbitrage_run.py",
            market_url,
        ],
        capture_output=True,
        text=True,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
```

### 4.2 新增 POST 接口

```python
@app.post("/run-arbitrage")
def run_arbitrage(config: RunConfig):
    result = run_arbitrage_cli(config.market_url)
    return {
        "config": config,
        "result": result,
    }
```

### 4.3 通过 Swagger UI 调试

1. 启动服务：
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```
2. 浏览器打开 `http://<服务器IP>:8000/docs`；
3. 找到 `POST /run-arbitrage`；
4. 点击 → "Try it out" → 在 JSON 中填入：

   ```json
   {
     "market_url": "https://polymarket.com/...",
     "direction": "YES",
     "size": 10
   }
   ```

5. 执行后：
   - 如果 `run_arbitrage_cli` 正常执行脚本，返回中应包含 `stdout` / `stderr`；
   - 可以在响应中看到一整段套利脚本的日志。

**此时：API 层已经可以从浏览器直接触发套利脚本。**

---

## 5. 极简 Web 页前端

> 目标：访问 `http://<服务器IP>:8000/`，看到一个表单，填参数 → 点按钮 → 页面下方显示脚本执行结果。

### 5.1 安装模板相关依赖

如果尚未安装：

```bash
pip install jinja2 python-multipart
```

### 5.2 新建模板目录与首页模板

```bash
mkdir -p templates
```

`templates/index.html` 示例：

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Polymarket 套利工具（MVP）</title>
</head>
<body>
  <h1>Polymarket 套利工具（测试版）</h1>

  <form method="post" action="/run-arbitrage-web">
    <label>市场 URL：</label>
    <input type="text" name="market_url" size="80" required><br><br>

    <label>方向（可选）：</label>
    <input type="text" name="direction" placeholder="YES/NO"><br><br>

    <label>份数（可选）：</label>
    <input type="number" name="size" step="1"><br><br>

    <button type="submit">运行套利脚本</button>
  </form>

  {% if result %}
    <h2>执行结果</h2>
    <pre>
returncode: {{ result.returncode }}
stdout:
{{ result.stdout }}

stderr:
{{ result.stderr }}
    </pre>
  {% endif %}
</body>
</html>
```

### 5.3 在 FastAPI 中接入模板渲染

在 `main.py` 增加：

```python
from fastapi import FastAPI, Form, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

app = FastAPI()

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/run-arbitrage-web", response_class=HTMLResponse)
def run_arbitrage_web(
    request: Request,
    market_url: str = Form(...),
    direction: str | None = Form(None),
    size: float | None = Form(None),
):
    result = run_arbitrage_cli(market_url)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "result": result,
        },
    )
```

### 5.4 前端验证

1. 启动服务：
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```
2. 浏览器访问：`http://<服务器IP>:8000/`
3. 填入市场 URL → 点击 “运行套利脚本”。
4. 页面下方 `执行结果` 区域应显示脚本输出。

至此，已经实现：

> **用网页表单控制 Polymarket 套利脚本（MVP）**。

---

## 6. 运行与调试流程

### 6.1 标准运行命令

开发期：

```bash
cd /home/trader/polymarket_frontend
source venv/bin/activate  # 若使用 venv
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 6.2 常见问题 & 排查建议

1. **无法 import 策略模块**
   - 使用 `sys.path.append("/home/trader/polymarket_api/strategy2")`；
   - 或在环境变量中设置 `PYTHONPATH`。

2. **`subprocess` 调用报错找不到依赖**
   - 确认脚本运行时的 Python 环境：
     - 若策略必须用 `poly312`，则在命令中显式指定：
       ```bash
       /root/.pyenv/versions/poly312/bin/python ...
       ```
   - 确保命令行下可以独立跑通脚本。

3. **脚本执行时间过长，前端转圈**
   - MVP 阶段可以接受；
   - 后续可改为后台任务（见第 7 章）。

4. **权限 / 安全性**
   - 初期仅内部使用，可只开放内网端口或用安全组限制 IP；
   - 生产阶段需加认证和限流。

---

## 7. 后续可选升级（v2+）

> 以下内容不影响 MVP 打通，可视为后续迭代方向。

### 7.1 后台任务 & 长时间运行

- 使用 FastAPI 的 `BackgroundTasks`、线程池或队列系统：
  - 前端提交执行请求 → 立即返回任务 ID；
  - 另一个接口 `/status/{task_id}` 轮询执行状态和日志。

### 7.2 执行历史与日志管理

- 每次调用写入：SQLite / 本地 JSON / 简单 CSV；
- 新增页面 `/history` 展示最近 N 次记录：
  - 参数：市场 URL / 时间 / 结果（成功/失败）；
  - link 到完整日志。

### 7.3 多用户与权限

- 初步可以使用：
  - HTTP Basic Auth；
  - 一个简单的「管理密码」。
- 真正要做 SaaS 化再上：
  - 用户表 / JWT；
  - 每个用户绑定自己的 API Key、钱包地址等。

### 7.4 正式部署

- 使用 nginx / caddy 反向代理：
  - `https://your-domain/` → 反代到 `127.0.0.1:8000`；
- 使用 systemd 管理 uvicorn 进程；
- 配置 HTTPS 证书；
- 日志归档与监控（Prometheus / Loki 等）。

---

## 8. MVP 文件清单（最小集合）

必须文件：

1. `polymarket_frontend/main.py`
   - FastAPI 应用入口；
   - 包含：
     - `/ping` 测试接口；
     - `POST /run-arbitrage` JSON 接口；
     - `GET /` + `POST /run-arbitrage-web` 网页接口；
     - `run_arbitrage_cli()` 封装，对接原有脚本。

2. `polymarket_frontend/templates/index.html`
   - 极简表单页面；
   - 显示执行结果。

可选文件（后续优化）：

3. `polymarket_api/strategy2/arbitrage_wrapper.py`
   - 正规封装版：将 `Volatility_arbitrage_run.py` 提取成函数调用接口。

---

## 9. 建议推进顺序

1. **阶段 A**：完成章节 1 + 2
   - 目标：`/ping` + `/docs` 正常可访问。
2. **阶段 B**：完成章节 3（`subprocess` 版） + 4
   - 目标：在 Swagger UI 中用 `POST /run-arbitrage` 能触发真实套利脚本执行。
3. **阶段 C**：完成章节 5
   - 目标：在网页首页填表 → 一键运行套利脚本 → 页面展示日志。
4. **阶段 D（可选）**：按章节 7 逐步增强。

> 只要上述 A–C 三步跑通，这个蓝图的 MVP 目标就算达成：
>
> **在网页前端，对 Polymarket 套利脚本进行可视化操作与监控。**

