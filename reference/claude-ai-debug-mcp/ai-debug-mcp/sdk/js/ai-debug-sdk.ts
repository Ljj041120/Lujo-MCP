/**
 * ai-debug-browser-sdk
 *
 * 轻量级浏览器 SDK，用于在开发/测试环境采集：
 * - 点击、提交、路由变化等 UI 事件
 * - fetch / XMLHttpRequest 网络请求
 * - 用户显式标记的期望行为（expectAfterClick）
 *
 * 当期望行为未在指定时间内达成时，自动向 ai-debug-mcp 上报 silent_failure。
 */

type Environment = 'dev' | 'test' | 'prod';

interface AIDebugConfig {
  endpoint: string;               // ai-debug-mcp 根地址，如 http://localhost:8000
  project?: string;
  environment?: Environment;
  captureClicks?: boolean;
  captureNetwork?: boolean;
  captureConsole?: boolean;
  captureRoute?: boolean;
  maxRecentNetwork?: number;      // 保留最近多少条网络记录
  maxRecentEvents?: number;       // 保留最近多少条 UI 事件
}

type ExpectationType = 'route_change' | 'network_request' | 'dom_change';

interface Expectation {
  type: ExpectationType;
  to?: string;                    // route_change 时期望跳转路径
  url?: string;                   // network_request 时期望请求 URL（子串匹配）
  method?: string;                // network_request 时期望方法
  withinMs: number;
}

interface UIEvent {
  event_type: 'click' | 'submit' | 'route_change';
  target_selector?: string;
  component_name?: string;
  route_path?: string;
  timestamp: number;
}

interface NetworkRecord {
  direction: 'outbound';
  method: string;
  url: string;
  status_code?: number;
  request_body?: string;
  response_body?: string;
  duration_ms: number;
  timestamp: number;
}

interface ConsoleLog {
  level: 'log' | 'warn' | 'error';
  message: string;
  timestamp: number;
}

const MAX_BODY_LENGTH = 10 * 1024;

function truncate(str: string | undefined | null, max = MAX_BODY_LENGTH): string | undefined {
  if (!str) return undefined;
  return str.length > max ? str.slice(0, max) + '\n...（已截断）' : str;
}

function generateSelector(el: Element): string {
  if (el.id) return `#${el.id}`;
  const tag = el.tagName.toLowerCase();
  const classes = Array.from(el.classList)
    .filter((c) => c)
    .slice(0, 2)
    .join('.');
  if (classes) return `${tag}.${classes}`;
  return tag;
}

function getComponentName(el: Element): string | undefined {
  return (
    el.getAttribute('data-component') ||
    el.getAttribute('data-testid') ||
    undefined
  );
}

class AIDebug {
  private config: Required<AIDebugConfig>;
  private enabled = false;
  private expectations = new Map<string, Expectation>();
  private uiEvents: UIEvent[] = [];
  private networkRecords: NetworkRecord[] = [];
  private consoleLogs: ConsoleLog[] = [];
  private lastRoute: string = '';

  constructor() {
    this.config = {
      endpoint: '',
      project: 'default',
      environment: 'dev',
      captureClicks: true,
      captureNetwork: true,
      captureConsole: true,
      captureRoute: true,
      maxRecentNetwork: 5,
      maxRecentEvents: 10,
    };
    if (typeof window !== 'undefined') {
      this.lastRoute = window.location.pathname;
    }
  }

  init(config: AIDebugConfig) {
    if (typeof window === 'undefined') {
      return;
    }
    this.config = { ...this.config, ...config };

    // 生产环境默认不启用，避免隐私与性能风险
    if (this.config.environment === 'prod') {
      return;
    }
    this.enabled = true;

    if (this.config.captureClicks) this._listenClicks();
    if (this.config.captureRoute) this._listenRoute();
    if (this.config.captureNetwork) this._wrapFetch();
    if (this.config.captureNetwork) this._wrapXHR();
    if (this.config.captureConsole) this._wrapConsole();
  }

  /**
   * 显式标记某个点击后期望发生什么。
   * 如果 withinMs 内未检测到期望行为，则自动上报 silent_failure。
   */
  expectAfterClick(selector: string, expectation: Omit<Expectation, 'type'> & { type: ExpectationType }) {
    this.expectations.set(selector, { ...expectation });
  }

  private _pushEvent(event: UIEvent) {
    this.uiEvents.push(event);
    if (this.uiEvents.length > this.config.maxRecentEvents) {
      this.uiEvents.shift();
    }
  }

  private _pushNetwork(record: NetworkRecord) {
    this.networkRecords.push(record);
    if (this.networkRecords.length > this.config.maxRecentNetwork) {
      this.networkRecords.shift();
    }
  }

  private _pushConsole(level: ConsoleLog['level'], args: unknown[]) {
    const message = args
      .map((a) => (typeof a === 'object' ? JSON.stringify(a) : String(a)))
      .join(' ');
    this.consoleLogs.push({ level, message, timestamp: Date.now() });
    if (this.consoleLogs.length > 10) {
      this.consoleLogs.shift();
    }
  }

  private _listenClicks() {
    document.addEventListener(
      'click',
      (e) => {
        const el = e.target as Element | null;
        if (!el) return;
        const selector = generateSelector(el);
        const componentName = getComponentName(el);
        const event: UIEvent = {
          event_type: 'click',
          target_selector: selector,
          component_name: componentName,
          route_path: window.location.pathname,
          timestamp: Date.now(),
        };
        this._pushEvent(event);
        this._checkExpectation(selector, event);
      },
      true,
    );

    document.addEventListener(
      'submit',
      (e) => {
        const el = e.target as Element | null;
        if (!el) return;
        const selector = generateSelector(el);
        this._pushEvent({
          event_type: 'submit',
          target_selector: selector,
          component_name: getComponentName(el),
          route_path: window.location.pathname,
          timestamp: Date.now(),
        });
      },
      true,
    );
  }

  private _listenRoute() {
    const recordRoute = () => {
      const route = window.location.pathname;
      if (route !== this.lastRoute) {
        this.lastRoute = route;
        this._pushEvent({
          event_type: 'route_change',
          route_path: route,
          timestamp: Date.now(),
        });
      }
    };
    window.addEventListener('popstate', recordRoute);
    window.addEventListener('hashchange', recordRoute);
  }

  private _checkExpectation(selector: string, event: UIEvent) {
    const expectation = this.expectations.get(selector);
    if (!expectation) return;

    const clickTime = event.timestamp;
    const routeAtClick = window.location.pathname;

    setTimeout(() => {
      let matched = false;

      if (expectation.type === 'route_change') {
        matched =
          window.location.pathname !== routeAtClick ||
          window.location.pathname === expectation.to;
      } else if (expectation.type === 'network_request') {
        matched = this.networkRecords.some((r) => {
          const inWindow = r.timestamp >= clickTime && r.timestamp <= clickTime + expectation.withinMs;
          const urlMatch = !expectation.url || r.url.includes(expectation.url);
          const methodMatch = !expectation.method || r.method.toUpperCase() === expectation.method.toUpperCase();
          return inWindow && urlMatch && methodMatch;
        });
      } else if (expectation.type === 'dom_change') {
        // MVP 内保守处理：仅检查是否有后续 UI 事件（含 route_change）
        matched = this.uiEvents.some(
          (e) => e.timestamp > clickTime && e.timestamp <= clickTime + expectation.withinMs,
        );
      }

      if (!matched) {
        this._reportSilentFailure(selector, expectation, event);
      }
    }, expectation.withinMs);
  }

  private _reportSilentFailure(selector: string, expectation: Expectation, event: UIEvent) {
    const payload = {
      message: `点击 ${selector} 后 ${expectation.withinMs}ms 内未发生期望的 ${expectation.type}`,
      source: 'browser_sdk',
      frames: event.component_name
        ? [{ file: event.component_name, line: 0, function: 'unknown' }]
        : [],
      ui_events: this.uiEvents.slice(-5),
      network_records: this.networkRecords.slice(-5),
      expectation,
      extra: {
        project: this.config.project,
        console_logs: this.consoleLogs.slice(-5),
      },
    };

    fetch(`${this.config.endpoint}/ingest/silent-failure`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {
      // 上报失败不应影响被调试应用
    });
  }

  private _wrapFetch() {
    const original = window.fetch;
    window.fetch = async (...args) => {
      if (!this.enabled) return original(...args);

      const start = performance.now();
      const [url, init] = args;
      const method = (init?.method as string) || 'GET';
      const requestBody = typeof init?.body === 'string' ? init.body : undefined;

      try {
        const response = await original(...args);
        const duration = Math.round(performance.now() - start);
        let responseBody: string | undefined;
        try {
          const clone = response.clone();
          const text = await clone.text();
          responseBody = truncate(text);
        } catch {
          responseBody = undefined;
        }

        this._pushNetwork({
          direction: 'outbound',
          method: method.toUpperCase(),
          url: String(url),
          status_code: response.status,
          request_body: truncate(requestBody),
          response_body: responseBody,
          duration_ms: duration,
          timestamp: Date.now(),
        });
        return response;
      } catch (err) {
        const duration = Math.round(performance.now() - start);
        this._pushNetwork({
          direction: 'outbound',
          method: method.toUpperCase(),
          url: String(url),
          request_body: truncate(requestBody),
          duration_ms: duration,
          timestamp: Date.now(),
        });
        throw err;
      }
    };
  }

  private _wrapXHR() {
    const OriginalXHR = window.XMLHttpRequest;
    const self = this;

    window.XMLHttpRequest = class extends OriginalXHR {
      private _method = 'GET';
      private _url = '';
      private _startTime = 0;
      private _requestBody?: string;

      open(method: string, url: string | URL, ...rest: unknown[]) {
        this._method = method;
        this._url = String(url);
        this._startTime = performance.now();
        super.open(method, url, ...(rest as [boolean, string?, string?]));
      }

      send(body?: Document | BodyInit | null) {
        this._requestBody = typeof body === 'string' ? body : undefined;
        const onLoad = () => {
          const duration = Math.round(performance.now() - this._startTime);
          let responseBody: string | undefined;
          try {
            responseBody = truncate(this.responseText);
          } catch {
            responseBody = undefined;
          }
          self._pushNetwork({
            direction: 'outbound',
            method: this._method.toUpperCase(),
            url: this._url,
            status_code: this.status,
            request_body: truncate(this._requestBody),
            response_body: responseBody,
            duration_ms: duration,
            timestamp: Date.now(),
          });
        };
        this.addEventListener('load', onLoad);
        this.addEventListener('error', onLoad);
        super.send(body);
      }
    };
  }

  private _wrapConsole() {
    const levels: ConsoleLog['level'][] = ['log', 'warn', 'error'];
    levels.forEach((level) => {
      const original = console[level];
      console[level] = (...args: unknown[]) => {
        this._pushConsole(level, args);
        original(...args);
      };
    });
  }
}

export const aiDebug = new AIDebug();
export default aiDebug;
