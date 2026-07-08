/**
 * ai-debug-mcp Browser SDK v0.1.0
 *
 * 前端自动采集：全局异常捕获、网络请求记录、UI 事件上报、静默失败标记。
 * 无需构建工具，直接 <script> 引入或 import 使用。
 *
 * 用法：
 *   <script src="ai-debug.js"></script>
 *   <script>
 *     AiDebug.init({ endpoint: "http://localhost:8000" });
 *   </script>
 *
 * 或 ES module：
 *   import { init, reportSilentFailure } from "./ai-debug.js";
 *   init({ endpoint: "http://localhost:8000" });
 */
(function (global) {
  "use strict";

  // ── 配置 ──
  var cfg = {
    endpoint: "",
    apiKey: "",
    captureErrors: true,
    captureNetwork: false,   // 收网络请求体较大，默认关闭
    captureUI: true,
    redactFields: ["password", "token", "secret", "authorization"],
    sampleRate: 1.0,         // 采样率 0-1
  };

  var _inited = false;
  var _sessionId = "sdk-" + Math.random().toString(36).slice(2, 10);

  // ── 工具函数 ──
  function _shouldSample() {
    return Math.random() < cfg.sampleRate;
  }

  function _redact(obj) {
    if (!obj || typeof obj !== "object") return obj;
    var out = Array.isArray(obj) ? [] : {};
    for (var k in obj) {
      if (obj.hasOwnProperty(k)) {
        if (cfg.redactFields.indexOf(k) >= 0) {
          out[k] = "***REDACTED***";
        } else if (typeof obj[k] === "object") {
          out[k] = _redact(obj[k]);
        } else {
          out[k] = obj[k];
        }
      }
    }
    return out;
  }

  function _send(path, payload) {
    if (!cfg.endpoint || !_shouldSample()) return;
    var body = JSON.stringify(_redact(payload));
    var url = cfg.endpoint.replace(/\/+$/, "") + path;
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", url, true);
      xhr.setRequestHeader("Content-Type", "application/json");
      if (cfg.apiKey) xhr.setRequestHeader("X-API-Key", cfg.apiKey);
      xhr.send(body);
    } catch (e) {
      // SDK 自身异常静默，不影响业务
    }
  }

  // ── 全局异常捕获 ──
  function _installErrorHook() {
    // window.onerror → 同步异常
    var _onerror = global.onerror;
    global.onerror = function (msg, file, line, col, error) {
      var frames = [];
      if (error && error.stack) {
        frames = _parseStack(error.stack);
      } else {
        frames = [{ file: file || "", line: line || 0, function: "" }];
      }
      _send("/api/ingest/error", {
        exc_type: error ? error.name : "Error",
        message: String(msg),
        frames: frames,
        source: "browser-sdk",
        extra: {
          session_id: _sessionId,
          url: global.location ? global.location.href : "",
          user_agent: navigator ? navigator.userAgent : "",
        },
      });
      if (_onerror) _onerror.apply(this, arguments);
    };

    // unhandledrejection → Promise 未捕获
    global.addEventListener("unhandledrejection", function (e) {
      var reason = e.reason;
      _send("/api/ingest/error", {
        exc_type: reason ? reason.name || "UnhandledRejection" : "UnhandledRejection",
        message: reason ? reason.message || String(reason) : "Promise rejected",
        frames: reason && reason.stack ? _parseStack(reason.stack) : [],
        source: "browser-sdk",
        extra: {
          session_id: _sessionId,
          url: global.location ? global.location.href : "",
        },
      });
    });
  }

  function _parseStack(stack) {
    if (!stack) return [];
    return stack
      .split("\n")
      .slice(1)
      .filter(function (line) { return line.trim(); })
      .map(function (line) {
        var m = line.trim().match(/at\s+(.*?)\s+\(?(.+?):(\d+):(\d+)?\)?/);
        if (m) return { file: m[2], line: parseInt(m[3]) || 0, function: m[1] || "" };
        // Chrome format: at file:line:col
        var m2 = line.trim().match(/at\s+(.+?):(\d+):(\d+)/);
        if (m2) return { file: m2[1], line: parseInt(m2[2]) || 0, function: "" };
        return { file: "", line: 0, function: line.trim() };
      });
  }

  // ── 网络请求拦截 ──
  function _installNetworkHook() {
    if (!cfg.captureNetwork) return;

    var _origFetch = global.fetch;
    if (_origFetch) {
      global.fetch = function () {
        var args = arguments;
        var start = Date.now();
        var url = typeof args[0] === "string" ? args[0] : (args[0] || {}).url || "";
        var method = (args[1] && args[1].method) || "GET";
        var reqBody = (args[1] && args[1].body) || null;

        return _origFetch.apply(this, args).then(function (res) {
          var clone = res.clone();
          clone.text().then(function (text) {
            _send("/api/ingest/network", {
              record: {
                url: url,
                method: method.toUpperCase(),
                status_code: res.status,
                duration_ms: Date.now() - start,
                request_body: reqBody,
                response_body: text.slice(0, 2000),
              },
              source: "browser-sdk",
              extra: { session_id: _sessionId },
            });
          }).catch(function () {});
          return res;
        }).catch(function (err) {
          _send("/api/ingest/network", {
            record: {
              url: url,
              method: method.toUpperCase(),
              status_code: 0,
              error: err.message,
              duration_ms: Date.now() - start,
            },
            source: "browser-sdk",
            extra: { session_id: _sessionId },
          });
          throw err;
        });
      };
    }
  }

  // ── UI 事件捕获 ──
  function _installUIHook() {
    if (!cfg.captureUI) return;

    var events = ["click", "input", "change", "submit"];
    var _debounce = {};

    events.forEach(function (evt) {
      document.addEventListener(evt, function (e) {
        // 去重：同一秒内同一元素同类事件只报一次
        var target = e.target;
        if (!target) return;
        var key = evt + ":" + (target.id || target.className || target.tagName);
        var now = Date.now();
        if (_debounce[key] && now - _debounce[key] < 1000) return;
        _debounce[key] = now;

        _send("/api/ingest/ui-event", {
          event: {
            event_type: e.type,
            target_selector: _getSelector(target),
            target_text: (target.textContent || "").slice(0, 100),
            timestamp: now / 1000,
            route_path: global.location ? global.location.pathname : "",
          },
          source: "browser-sdk",
          extra: { session_id: _sessionId },
        });
      }, true);
    });
  }

  function _getSelector(el) {
    if (!el) return "";
    if (el.id) return "#" + el.id;
    if (el.className && typeof el.className === "string") {
      return el.tagName.toLowerCase() + "." + el.className.split(" ").join(".");
    }
    return el.tagName ? el.tagName.toLowerCase() : "";
  }

  // ── 公开 API ──
  /**
   * 初始化 SDK
   * @param {object} opts - { endpoint, apiKey, captureErrors, captureNetwork, captureUI, sampleRate }
   */
  function init(opts) {
    if (_inited) return;
    if (opts) {
      for (var k in opts) {
        if (opts.hasOwnProperty(k) && cfg.hasOwnProperty(k)) {
          cfg[k] = opts[k];
        }
      }
    }
    if (!cfg.endpoint) {
      console.warn("[ai-debug] endpoint 未配置，SDK 不上报");
      return;
    }
    _inited = true;
    _installErrorHook();
    _installNetworkHook();
    _installUIHook();
    console.log("[ai-debug] SDK initialized, session=" + _sessionId);
  }

  /**
   * 手动上报静默失败
   * @param {object} payload - { description, element?, expected?, observed?, route? }
   */
  function reportSilentFailure(payload) {
    _send("/api/ingest/silent-failure", {
      payload: payload || {},
      source: "browser-sdk",
      extra: {
        session_id: _sessionId,
        url: global.location ? global.location.href : "",
      },
    });
  }

  /**
   * 手动上报异常
   * @param {Error} error
   * @param {object} extra
   */
  function reportError(error, extra) {
    _send("/api/ingest/error", {
      exc_type: error ? error.name || "Error" : "Error",
      message: error ? error.message || String(error) : "",
      frames: error && error.stack ? _parseStack(error.stack) : [],
      source: "browser-sdk",
      extra: Object.assign({
        session_id: _sessionId,
        url: global.location ? global.location.href : "",
      }, extra || {}),
    });
  }

  /**
   * 手动上报 UI 事件
   * @param {object} event - { event_type, target_selector, route_path }
   */
  function reportUIEvent(event) {
    _send("/api/ingest/ui-event", {
      event: {
        event_type: event.event_type || "click",
        target_selector: event.target_selector || "",
        target_text: event.target_text || "",
        timestamp: Date.now() / 1000,
        route_path: event.route_path || (global.location ? global.location.pathname : ""),
      },
      source: "browser-sdk",
      extra: { session_id: _sessionId },
    });
  }

  /**
   * 获取当前会话 ID
   */
  function getSessionId() {
    return _sessionId;
  }

  // ── 导出 ──
  var api = {
    init: init,
    reportSilentFailure: reportSilentFailure,
    reportError: reportError,
    reportUIEvent: reportUIEvent,
    getSessionId: getSessionId,
    _cfg: cfg,
  };

  // 支持 CommonJS / ES module / 全局变量
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else if (typeof define === "function" && define.amd) {
    define(function () { return api; });
  } else {
    global.AiDebug = api;
  }
})(typeof window !== "undefined" ? window : this);
