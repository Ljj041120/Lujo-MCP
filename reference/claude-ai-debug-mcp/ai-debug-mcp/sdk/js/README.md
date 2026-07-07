# ai-debug-browser-sdk

ai-debug-mcp 的浏览器端 SDK，用于在开发/测试环境采集 UI 事件、网络请求和用户期望行为，并在“点了没反应”时自动上报 `silent_failure`。

## 安装

把 `ai-debug-sdk.ts` 复制到前端项目（或使用 tsc 编译后的 js 文件），然后导入：

```typescript
import aiDebug from './ai-debug-sdk';

aiDebug.init({
  endpoint: 'http://localhost:8000',
  project: 'my-app',
  environment: 'dev',
  captureClicks: true,
  captureNetwork: true,
  captureConsole: true,
  captureRoute: true,
});
```

## 标记期望行为

在页面初始化或组件挂载后，告诉 SDK“点击这个按钮后应该发生什么”：

```typescript
aiDebug.expectAfterClick('.submit-btn', {
  type: 'route_change',
  to: '/success',
  withinMs: 2000,
});

aiDebug.expectAfterClick('.load-data-btn', {
  type: 'network_request',
  url: '/api/data',
  method: 'GET',
  withinMs: 1500,
});
```

如果期望时间内未检测到对应行为，SDK 会自动向 `/ingest/silent-failure` 上报一条记录。

## 配置项

| 字段 | 类型 | 说明 |
|------|------|------|
| `endpoint` | `string` | ai-debug-mcp 服务地址 |
| `project` | `string` | 项目标识 |
| `environment` | `'dev' \| 'test' \| 'prod'` | 仅在 `dev`/`test` 启用，生产环境默认关闭 |
| `captureClicks` | `boolean` | 是否采集点击事件 |
| `captureNetwork` | `boolean` | 是否拦截 fetch/XHR |
| `captureConsole` | `boolean` | 是否采集 console 日志 |
| `captureRoute` | `boolean` | 是否监听路由变化 |
| `maxRecentNetwork` | `number` | 保留最近网络请求数，默认 5 |
| `maxRecentEvents` | `number` | 保留最近 UI 事件数，默认 10 |

## 隐私说明

- 不采集 input 的值、cookie、localStorage。
- 网络请求 response body 超过 10KB 会被截断。
- 生产环境（`environment === 'prod'`）默认完全不启用。
