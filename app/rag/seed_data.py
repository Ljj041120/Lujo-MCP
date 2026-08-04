"""30 条种子知识 —— 高频异常模式的标准 DebugCase。

覆盖 6 大类常见异常：
- ValueError × 5      （类型转换 / 数学域 / 文件 IO / 序列化 / 边界值）
- TypeError × 5       （运算 / 参数 / 下标 / 迭代 / NoneType）
- KeyError × 5        （配置 / 嵌套 / 环境变量 / 数据集 / 模型字段）
- AttributeError × 5  （NoneType / 模块 / 类型 / 实例 / 字符串）
- ConnectionError × 5 （拒绝 / 重置 / 中断 / 超时 / DNS）
- 其他 × 5            （ImportError / FileNotFoundError / PermissionError / RuntimeError / StopIteration）

设计原则：
- fingerprint 由 (exception_type, exception_message) 幂等计算，跨进程稳定
- 每条 case 提供根因分析 + 可执行修复建议
- tags 用于多维度分类检索（如 ["db", "async", "retry"]）
- source_files 为空列表（种子知识是通用模式，不绑定特定文件）
- severity 标识业务影响等级

加载方式：
    from app.rag.seed_data import SEED_CASES
    from app.rag.knowledge_base import load_seed_cases
    load_seed_cases(SEED_CASES)
"""

from __future__ import annotations

from typing import Any

from app.rag.debug_case import CaseSource, DebugCase, ExceptionType, Severity


def _make_case(
    *,
    exception_type: ExceptionType,
    exception_message: str,
    root_cause: str,
    fix_suggestion: str,
    tags: list[str],
    severity: Severity = Severity.MEDIUM,
    source_files: list[str] | None = None,
    analysis: dict[str, Any] | None = None,
) -> DebugCase:
    """构造 DebugCase 的便捷工厂（自动计算 fingerprint）。"""
    type_value = exception_type.value
    fingerprint = DebugCase.compute_fingerprint(type_value, exception_message)
    return DebugCase(
        fingerprint=fingerprint,
        exception_type=type_value,
        exception_message=exception_message,
        root_cause=root_cause,
        fix_suggestion=fix_suggestion,
        tags=tags,
        source_files=source_files or [],
        severity=severity,
        analysis=analysis or {},
        source=CaseSource.SEED.value,
    )


# ── 1. ValueError × 5 ──

_VALUE_ERROR_CASES: list[DebugCase] = [
    _make_case(
        exception_type=ExceptionType.VALUE_ERROR,
        exception_message="invalid literal for int() with base 10: 'abc'",
        root_cause=(
            "字符串到整数的转换失败，输入包含非数字字符。常见于从请求参数、"
            "配置文件、CSV/JSON 数据源读取后未做类型校验直接转换。"
        ),
        fix_suggestion=(
            "1) 转换前用 str.isdigit() 或正则校验输入；"
            "2) 用 try/except ValueError 捕获并返回友好错误；"
            "3) 考虑使用 pydantic / marshmallow 在入口处做 schema 校验。"
        ),
        tags=["type-conversion", "input-validation", "parsing"],
        severity=Severity.LOW,
    ),
    _make_case(
        exception_type=ExceptionType.VALUE_ERROR,
        exception_message="math domain error",
        root_cause=(
            "数学函数（如 sqrt / log）收到超出定义域的参数，"
            "通常是负数或零传入了不允许的运算。"
        ),
        fix_suggestion=(
            "1) 运算前检查参数范围（如 x >= 0 才调用 sqrt）；"
            "2) 对无法保证非负的情况使用 cmath 或返回 None 兜底；"
            "3) 在数据预处理阶段过滤异常值。"
        ),
        tags=["math", "domain-error", "validation"],
        severity=Severity.LOW,
    ),
    _make_case(
        exception_type=ExceptionType.VALUE_ERROR,
        exception_message="I/O operation on closed file",
        root_cause=(
            "文件对象已被关闭后仍尝试读写。常见于 with 块外的延迟写入、"
            "异步任务持有已关闭的文件句柄、或多线程共享文件对象。"
        ),
        fix_suggestion=(
            "1) 确保所有读写操作在 with 块内完成；"
            "2) 跨函数传递文件时改用文件路径而非 file 对象；"
            "3) 异步场景下用 aiofiles 替代内置 open。"
        ),
        tags=["io", "file", "resource-lifecycle", "async"],
        severity=Severity.MEDIUM,
    ),
    _make_case(
        exception_type=ExceptionType.VALUE_ERROR,
        exception_message="Value must be between 0 and 100",
        root_cause=(
            "业务约束的边界值校验失败，通常是用户输入或上游数据超出允许范围。"
            "这类 ValueError 通常由代码主动抛出而非 Python 内置。"
        ),
        fix_suggestion=(
            "1) 在 API 入口用 pydantic Field(ge=0, le=100) 声明约束；"
            "2) 业务层抛错前先记录原始值用于审计；"
            "3) 返回结构化错误响应，包含字段名和约束说明。"
        ),
        tags=["validation", "business-rule", "boundary"],
        severity=Severity.LOW,
    ),
    _make_case(
        exception_type=ExceptionType.VALUE_ERROR,
        exception_message="unsupported format string passed to NoneType.__format__",
        root_cause=(
            "格式化字符串期望非 None 值，但收到了 None。"
            "常见于 f-string / format() 作用于未初始化变量或缺失的字典键。"
        ),
        fix_suggestion=(
            "1) 格式化前用 `value or default` 兜底；"
            "2) 区分 None 与空字符串语义；"
            "3) 对必须存在的字段使用 dataclass / pydantic 强约束。"
        ),
        tags=["formatting", "none", "string"],
        severity=Severity.LOW,
    ),
]


# ── 2. TypeError × 5 ──

_TYPE_ERROR_CASES: list[DebugCase] = [
    _make_case(
        exception_type=ExceptionType.TYPE_ERROR,
        exception_message="unsupported operand type(s) for +: 'int' and 'str'",
        root_cause=(
            "运算符作用于不兼容类型。常见于从 JSON / 表单 / 配置读取的值"
            "默认是 str，但代码假设是 int 进行算术运算。"
        ),
        fix_suggestion=(
            "1) 运算前显式转换类型（int(x) + int(y)）；"
            "2) 用 isinstance() 做类型分发；"
            "3) 在 API 入口用 pydantic 声明类型，由框架自动转换。"
        ),
        tags=["type-mismatch", "operator", "parsing"],
        severity=Severity.LOW,
    ),
    _make_case(
        exception_type=ExceptionType.TYPE_ERROR,
        exception_message="argument must be str, bytes, or os.PathLike, not int",
        root_cause=(
            "文件路径相关函数（open / os.path.join / pathlib）收到了非字符串类型。"
            "常见于路径来自数值配置或拼接时混入 int。"
        ),
        fix_suggestion=(
            "1) 路径操作前统一 str(path) 转换；"
            "2) 用 pathlib.Path 包装路径，支持多种类型；"
            "3) 配置加载阶段做类型标准化。"
        ),
        tags=["path", "type-mismatch", "fs"],
        severity=Severity.LOW,
    ),
    _make_case(
        exception_type=ExceptionType.TYPE_ERROR,
        exception_message="'NoneType' object is not subscriptable",
        root_cause=(
            "对 None 进行了下标操作（obj[key] / obj[0]）。"
            "通常上游函数返回了 None 但调用方假设返回 dict/list。"
        ),
        fix_suggestion=(
            "1) 调用前显式判空 if obj is not None；"
            "2) 上游函数用类型注解声明 Optional[...] 并在文档明确；"
            "3) 使用 (obj or {}) 模式提供默认空容器。"
        ),
        tags=["none", "subscript", "null-safety"],
        severity=Severity.MEDIUM,
    ),
    _make_case(
        exception_type=ExceptionType.TYPE_ERROR,
        exception_message="'int' object is not iterable",
        root_cause=(
            "对非可迭代对象（如 int）使用了 for 循环 / 解包 / in 操作。"
            "常见于把单个值当列表处理。"
        ),
        fix_suggestion=(
            "1) 函数入口用 isinstance(x, (list, tuple)) 校验；"
            "2) 单值自动包装为列表：x = [x] if isinstance(x, int) else x；"
            "3) 用 collections.abc.Iterable 做类型断言。"
        ),
        tags=["iterable", "type-mismatch", "loop"],
        severity=Severity.LOW,
    ),
    _make_case(
        exception_type=ExceptionType.TYPE_ERROR,
        exception_message=(
            "object of type 'generator' has no len()"
        ),
        root_cause=(
            "对生成器调用了 len()。生成器是惰性序列，无法预知长度。"
            "常见于把生成器表达式传给了期望 list 的函数。"
        ),
        fix_suggestion=(
            "1) 显式 list(generator) 转换后再取长度；"
            "2) 改用 any() / next() 消费生成器；"
            "3) 函数签名用 Iterable[...] 而非 List[...]。"
        ),
        tags=["generator", "lazy", "len"],
        severity=Severity.LOW,
    ),
]


# ── 3. KeyError × 5 ──

_KEY_ERROR_CASES: list[DebugCase] = [
    _make_case(
        exception_type=ExceptionType.KEY_ERROR,
        exception_message="'name'",
        root_cause=(
            "访问字典不存在的键 'name'。常见于解析 API 响应、配置文件、"
            "数据库行时字段名拼写错误或数据不完整。"
        ),
        fix_suggestion=(
            "1) 使用 dict.get('name', default) 提供默认值；"
            "2) 用 pydantic BaseModel 做结构化解析，缺失字段触发校验错误；"
            "3) 关键字段访问前用 'name' in d 显式检查。"
        ),
        tags=["dict", "missing-key", "config"],
        severity=Severity.LOW,
    ),
    _make_case(
        exception_type=ExceptionType.KEY_ERROR,
        exception_message="'database'",
        root_cause=(
            "配置字典缺少 'database' 键，通常是配置文件加载不完整或环境变量未设置。"
            "启动期失败比运行期更易定位。"
        ),
        fix_suggestion=(
            "1) 启动时用 schema 校验配置完整性（pydantic Settings）；"
            "2) 关键配置缺失时 fail-fast 并打印清晰错误；"
            "3) 提供 .env.example 文档所有必需键。"
        ),
        tags=["config", "missing-key", "startup"],
        severity=Severity.HIGH,
    ),
    _make_case(
        exception_type=ExceptionType.KEY_ERROR,
        exception_message="'user_id'",
        root_cause=(
            "会话 / 请求上下文中缺少 'user_id'。常见于中间件未正确注入用户身份，"
            "或鉴权失败后未短路返回。"
        ),
        fix_suggestion=(
            "1) 鉴权中间件用 request.state.user_id 注入；"
            "2) 业务层用 Depends(get_current_user) 强制依赖；"
            "3) 对匿名场景显式声明 Optional[int]。"
        ),
        tags=["auth", "session", "context"],
        severity=Severity.HIGH,
    ),
    _make_case(
        exception_type=ExceptionType.KEY_ERROR,
        exception_message="'Authorization'",
        root_cause=(
            "请求头缺少 'Authorization' 字段。常见于客户端未携带 Token，"
            "或代理层剥离了认证头。"
        ),
        fix_suggestion=(
            "1) AuthMiddleware 显式校验 header 并返回 401；"
            "2) 文档明确标注哪些端点需要 Bearer Token；"
            "3) 集成测试覆盖无 Token 场景。"
        ),
        tags=["auth", "header", "middleware"],
        severity=Severity.HIGH,
    ),
    _make_case(
        exception_type=ExceptionType.KEY_ERROR,
        exception_message="0",
        root_cause=(
            "对列表 / 元组用字典语法访问。常见于把 JSON 数组当对象解析，"
            "或类型推断错误。"
        ),
        fix_suggestion=(
            "1) 用 isinstance(obj, list) 判类型后再访问；"
            "2) JSON 解析后用 schema 校验顶层结构；"
            "3) 调试时打印 type(obj) 确认类型。"
        ),
        tags=["type-mismatch", "list", "json"],
        severity=Severity.LOW,
    ),
]


# ── 4. AttributeError × 5 ──

_ATTRIBUTE_ERROR_CASES: list[DebugCase] = [
    _make_case(
        exception_type=ExceptionType.ATTRIBUTE_ERROR,
        exception_message="'NoneType' object has no attribute 'split'",
        root_cause=(
            "对 None 调用字符串方法。通常上游函数在异常分支返回了 None "
            "但调用方未判空。"
        ),
        fix_suggestion=(
            "1) 调用前 if value is not None: 显式判空；"
            "2) 上游函数失败时返回空字符串而非 None；"
            "3) 用 Optional[str] 类型注解并在 mypy 下检查。"
        ),
        tags=["none", "string", "null-safety"],
        severity=Severity.MEDIUM,
    ),
    _make_case(
        exception_type=ExceptionType.ATTRIBUTE_ERROR,
        exception_message="module 'app.config' has no attribute 'database_url'",
        root_cause=(
            "访问模块不存在的属性。常见于：1) 拼写错误；2) 循环导入未完成；"
            "3) 条件导入未生效。"
        ),
        fix_suggestion=(
            "1) 检查 import 路径和属性名拼写；"
            "2) 用 getattr(module, 'attr', default) 兜底；"
            "3) 重构循环导入为函数内延迟导入。"
        ),
        tags=["module", "import", "attribute"],
        severity=Severity.MEDIUM,
    ),
    _make_case(
        exception_type=ExceptionType.ATTRIBUTE_ERROR,
        exception_message="'type' object has no attribute 'from_dict'",
        root_cause=(
            "类上访问不存在的类方法。常见于：1) 方法名拼写错误；"
            "2) 期望子类实现但未实现；3) Mixin 未正确继承。"
        ),
        fix_suggestion=(
            "1) 检查方法名拼写和继承链；"
            "2) 抽象方法用 @abstractmethod 强制子类实现；"
            "3) 用 hasattr(cls, 'from_dict') 做能力检查。"
        ),
        tags=["class", "method", "inheritance"],
        severity=Severity.MEDIUM,
    ),
    _make_case(
        exception_type=ExceptionType.ATTRIBUTE_ERROR,
        exception_message="'Response' object has no attribute 'json'",
        root_cause=(
            "HTTP 客户端响应对象上调用错误方法。常见于 requests.Response "
            "与 httpx.Response / aiohttp.ClientResponse API 混用。"
        ),
        fix_suggestion=(
            "1) 检查 HTTP 客户端库版本和 API；"
            "2) requests 用 .json()，aiohttp 用 await resp.json()；"
            "3) 统一封装 HTTP 客户端，对外暴露一致接口。"
        ),
        tags=["http", "client", "api-mismatch"],
        severity=Severity.MEDIUM,
    ),
    _make_case(
        exception_type=ExceptionType.ATTRIBUTE_ERROR,
        exception_message="'str' object has no attribute 'decode'",
        root_cause=(
            "对 str 调用 bytes 的 decode 方法。常见于 Python 2→3 迁移残留，"
            "或上游已解码但下游重复解码。"
        ),
        fix_suggestion=(
            "1) 用 isinstance(value, bytes) 判类型后再 decode；"
            "2) 统一约定文本边界（如内部全部用 str）；"
            "3) 移除 Python 2 兼容代码。"
        ),
        tags=["bytes", "str", "migration"],
        severity=Severity.LOW,
    ),
]


# ── 5. ConnectionError × 5 ──

_CONNECTION_ERROR_CASES: list[DebugCase] = [
    _make_case(
        exception_type=ExceptionType.CONNECTION_ERROR,
        exception_message="[Errno 111] Connection refused",
        root_cause=(
            "目标端口未监听或被防火墙拒绝。常见于：1) 服务未启动；"
            "2) 端口配置错误；3) 容器网络隔离。"
        ),
        fix_suggestion=(
            "1) 检查目标服务进程状态和端口；"
            "2) 用 telnet / nc 验证网络连通性；"
            "3) 客户端加入重试 + 熔断机制；"
            "4) 容器场景检查 docker-compose 网络配置。"
        ),
        tags=["network", "tcp", "refused", "startup"],
        severity=Severity.HIGH,
    ),
    _make_case(
        exception_type=ExceptionType.CONNECTION_ERROR,
        exception_message="[Errno 104] Connection reset by peer",
        root_cause=(
            "对端在数据传输中强制关闭连接。常见于：1) 服务端 panic / crash；"
            "2) 中间代理超时断开；3) TLS 握手失败。"
        ),
        fix_suggestion=(
            "1) 检查服务端日志是否有 panic；"
            "2) 增加心跳保活和重连逻辑；"
            "3) 检查代理（nginx / ingress）的超时配置；"
            "4) 启用 TLS 时验证证书链。"
        ),
        tags=["network", "tcp", "reset", "proxy"],
        severity=Severity.HIGH,
    ),
    _make_case(
        exception_type=ExceptionType.CONNECTION_ERROR,
        exception_message="[Errno 103] Software caused connection abort",
        root_cause=(
            "本地系统层中断了连接。常见于：1) 操作系统资源不足；"
            "2) socket 描述符耗尽；3) 中间件主动断开。"
        ),
        fix_suggestion=(
            "1) 检查 ulimit -n 文件描述符上限；"
            "2) 用连接池复用 socket；"
            "3) 监控系统资源（CPU/内存/网络）；"
            "4) 长连接场景启用 keepalive。"
        ),
        tags=["network", "system", "resource"],
        severity=Severity.HIGH,
    ),
    _make_case(
        exception_type=ExceptionType.CONNECTION_ERROR,
        exception_message="HTTPSConnectionPool(host='api.example.com', port=443): "
                          "Max retries exceeded with url: /v1/data",
        root_cause=(
            "requests / urllib3 重试耗尽。通常底层是 DNS 解析失败、"
            "TCP 连接超时或 TLS 握手超时。"
        ),
        fix_suggestion=(
            "1) 检查 DNS 解析（nslookup / dig）；"
            "2) 增大 timeout 与重试次数（requests.Session + HTTPAdapter）；"
            "3) 启用熔断器避免雪崩；"
            "4) 跨地域调用考虑 CDN 或就近接入。"
        ),
        tags=["http", "timeout", "retry", "dns"],
        severity=Severity.HIGH,
    ),
    _make_case(
        exception_type=ExceptionType.CONNECTION_ERROR,
        exception_message="[Errno -2] Name or service not known",
        root_cause=(
            "DNS 解析失败。常见于：1) 域名拼写错误；2) /etc/hosts 缺失；"
            "3) 内部 DNS 服务不可用；4) 容器未配置 DNS。"
        ),
        fix_suggestion=(
            "1) 用 nslookup / dig 验证域名；"
            "2) 检查 /etc/resolv.conf；"
            "3) 容器场景配置 dns 或 extra_hosts；"
            "4) 关键服务用 IP 直连 + 健康检查兜底。"
        ),
        tags=["dns", "network", "resolution"],
        severity=Severity.CRITICAL,
    ),
]


# ── 6. 其他 × 5（ImportError / FileNotFoundError / PermissionError / RuntimeError / StopIteration）──

_OTHER_CASES: list[DebugCase] = [
    _make_case(
        exception_type=ExceptionType.IMPORT_ERROR,
        exception_message="No module named 'fastapi'",
        root_cause=(
            "依赖未安装或虚拟环境未激活。常见于：1) requirements.txt 未安装；"
            "2) IDE 用错解释器；3) Docker 镜像层数缺失。"
        ),
        fix_suggestion=(
            "1) pip install -r requirements.txt；"
            "2) 检查 python -c 'import sys; print(sys.executable)'；"
            "3) 容器内 which python 确认路径；"
            "4) 用 pip install --no-cache-dir 排除缓存问题。"
        ),
        tags=["dependency", "env", "import"],
        severity=Severity.CRITICAL,
    ),
    _make_case(
        exception_type=ExceptionType.FILE_NOT_FOUND_ERROR,
        exception_message="[Errno 2] No such file or directory: '/etc/app/config.yaml'",
        root_cause=(
            "配置 / 资源文件路径不存在。常见于：1) 相对路径基于错误工作目录；"
            "2) 容器挂载缺失；3) 部署时未携带配置文件。"
        ),
        fix_suggestion=(
            "1) 用绝对路径或 pathlib.Path(__file__).parent 锚定；"
            "2) 启动时校验必需文件存在；"
            "3) 容器检查 volume 挂载；"
            "4) 提供默认配置 + 环境变量覆盖机制。"
        ),
        tags=["fs", "config", "path"],
        severity=Severity.HIGH,
    ),
    _make_case(
        exception_type=ExceptionType.PERMISSION_ERROR,
        exception_message="[Errno 13] Permission denied: '/var/log/app.log'",
        root_cause=(
            "文件 / 目录权限不足。常见于：1) 进程运行用户与文件 owner 不一致；"
            "2) 容器内非 root 用户但挂载目录属主是 root；"
            "3) 文件被独占锁定。"
        ),
        fix_suggestion=(
            "1) 检查 ls -l 文件权限和进程用户；"
            "2) 容器内用 chown 修正挂载目录属主；"
            "3) 日志目录用独立 volume 并在 entrypoint 调整权限；"
            "4) 避免以 root 运行，用专用 user。"
        ),
        tags=["fs", "permission", "container"],
        severity=Severity.HIGH,
    ),
    _make_case(
        exception_type=ExceptionType.RUNTIME_ERROR,
        exception_message="Event loop is closed",
        root_cause=(
            "在已关闭的 asyncio 事件循环上调度任务。常见于：1) 测试 teardown "
            "后调用异步代码；2) 多次创建/关闭 loop；3) 第三方库在循环外触发回调。"
        ),
        fix_suggestion=(
            "1) 用 asyncio.run() 统一管理循环生命周期；"
            "2) 测试用 pytest-asyncio 的 fixture 作用域；"
            "3) 检查是否有 loop.close() 后的延迟回调；"
            "4) 跨线程场景用 asyncio.run_coroutine_threadsafe。"
        ),
        tags=["async", "event-loop", "lifecycle"],
        severity=Severity.MEDIUM,
    ),
    _make_case(
        exception_type=ExceptionType.STOP_ITERATION,
        exception_message="",
        root_cause=(
            "next() 在空迭代器或耗尽后调用。常见于：1) 用 next() 取唯一元素但集合为空；"
            "2) 生成器被消费两次；3) itertools.islice 切片越界。"
        ),
        fix_suggestion=(
            "1) 用 next(it, default) 提供默认值；"
            "2) 取唯一元素时显式判空；"
            "3) 生成器需要多次消费时先 list(it) 物化；"
            "4) 用 more_itertools.first / one 表达意图。"
        ),
        tags=["iterator", "generator", "boundary"],
        severity=Severity.LOW,
    ),
]


# ── 汇总导出 ──

SEED_CASES: list[DebugCase] = (
    _VALUE_ERROR_CASES
    + _TYPE_ERROR_CASES
    + _KEY_ERROR_CASES
    + _ATTRIBUTE_ERROR_CASES
    + _CONNECTION_ERROR_CASES
    + _OTHER_CASES
)

# 按异常类型分组的索引（便于按需加载子集）
SEED_CASES_BY_TYPE: dict[str, list[DebugCase]] = {
    ExceptionType.VALUE_ERROR.value: _VALUE_ERROR_CASES,
    ExceptionType.TYPE_ERROR.value: _TYPE_ERROR_CASES,
    ExceptionType.KEY_ERROR.value: _KEY_ERROR_CASES,
    ExceptionType.ATTRIBUTE_ERROR.value: _ATTRIBUTE_ERROR_CASES,
    ExceptionType.CONNECTION_ERROR.value: _CONNECTION_ERROR_CASES,
    ExceptionType.OTHER.value: _OTHER_CASES,
}

# 种子知识总数（用于启动期校验）
SEED_CASE_COUNT = len(SEED_CASES)
