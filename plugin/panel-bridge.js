// MCP Apps 2026-01-26. 只有嵌入宿主能处理调用；面板通过 app-only 工具读写用户选择的设置。
(() => {
  const pending = new Map();
  const onClose = [];
  let nextId = 1;
  let closed = false;
  const send = message => window.parent.postMessage({ jsonrpc: '2.0', ...message }, '*');
  function call(method, params, timeoutMs = 15000) {
    if (closed) return Promise.reject(new Error('面板已关闭'));
    const id = nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error('Codex 面板连接超时'));
      }, timeoutMs);
      pending.set(id, { resolve, reject, timer });
      send({ id, method, params });
    });
  }
  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.data?.jsonrpc !== '2.0') return;
    const message = event.data;
    const waiter = pending.get(message.id);
    if (waiter) {
      clearTimeout(waiter.timer);
      pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message));
      else waiter.resolve(message.result);
    } else if (message.method === 'ui/resource-teardown') {
      closed = true;
      for (const callback of onClose) callback();
      for (const item of pending.values()) {
        clearTimeout(item.timer);
        item.reject(new Error('面板已关闭'));
      }
      pending.clear();
      send({ id: message.id, result: {} });
    } else if (message.id !== undefined && message.method) {
      send({ id: message.id, error: { code: -32601, message: 'Method not supported' } });
    } else if (message.method === 'ui/notifications/host-context-changed') {
      const theme = message.params?.theme;
      if (['light', 'dark'].includes(theme)) document.documentElement.style.colorScheme = theme;
    }
  });
  const ready = (async () => {
    if (window.parent === window) throw new Error('请从 Codex 左侧的源码导入入口打开');
    const host = await call('ui/initialize', {
      appInfo: { name: 'source-intake-panel', version: '0.1.0' },
      appCapabilities: { availableDisplayModes: ['fullscreen'] },
      protocolVersion: '2026-01-26',
    }, 10000);
    if (host?.protocolVersion !== '2026-01-26') throw new Error('Codex 面板协议版本不兼容');
    if (['light', 'dark'].includes(host.hostContext?.theme)) document.documentElement.style.colorScheme = host.hostContext.theme;
    send({ method: 'ui/notifications/initialized', params: {} });
  })();
  ready.catch(() => {});
  window.sourceIntakeHost = {
    onClose(callback) {
      if (closed) callback();
      else onClose.push(callback);
    },
    async call(name, args = {}) {
      await ready;
      const response = await call('tools/call', { name, arguments: args }, 180000);
      if (response?.isError) throw new Error(response.content?.[0]?.text ?? '面板请求失败');
      if (response?.structuredContent) return response.structuredContent;
      const text = response?.content?.find(item => item.type === 'text')?.text;
      return text ? JSON.parse(text) : null;
    },
  };
})();
