import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { resetApi, routeRequest } from "./api-mock";
import "@/i18n";

// openapi-fetch captures globalThis.fetch when the client module loads, so
// install a delegating fetch first and let each test choose the responses.
globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
  routeRequest(input instanceof Request ? input : new Request(input, init)),
) as typeof fetch;

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>();
  onerror: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  emit(type: string, data: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify({ type, data, at: "" }) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
  close() {
    this.closed = true;
  }
}
Object.assign(globalThis, { EventSource: FakeEventSource });

if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }) as unknown as MediaQueryList;
}

// Radix and cmdk measure layout through these.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
Element.prototype.scrollIntoView ??= () => undefined;
window.scrollTo = () => undefined;
HTMLMediaElement.prototype.play = () => Promise.resolve();
HTMLMediaElement.prototype.pause = () => undefined;

afterEach(() => {
  cleanup();
  resetApi();
  FakeEventSource.instances = [];
  localStorage.clear();
});

export { FakeEventSource };
