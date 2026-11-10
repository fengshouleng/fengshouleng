// Vitest 全局 setup：注入 @testing-library/jest-dom 的断言（toBeInTheDocument 等）。
// 组件测试（TASK-FE-001 起）直接依赖这里，业务测试无需重复 import。
import '@testing-library/jest-dom/vitest';

// antd（响应式断点、ResizeObserver 类组件）在 jsdom 下的兜底实现；
// 各测试文件如自带 stub 会覆盖这里的默认值。
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const windowWithObservers = window as unknown as {
  ResizeObserver?: typeof ResizeObserver;
};
if (!windowWithObservers.ResizeObserver) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  windowWithObservers.ResizeObserver =
    ResizeObserverStub as unknown as typeof ResizeObserver;
}

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
