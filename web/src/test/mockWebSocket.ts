// Test-only WebSocket replacement. The real `WebSocket` opens a
// network socket; jsdom doesn't give us one, so we install this
// class on `globalThis.WebSocket` before each test that involves
// `useReviewStream`. Behaviour:
//
//   * The constructor records the URL and stages the socket in
//     `connectionState = "CONNECTING"`. No auto-open — tests drive
//     the lifecycle explicitly via `acceptConnection()`,
//     `emit(envelope)`, `close()`.
//   * `MockWebSocket.instances` is a static registry; tests use
//     `MockWebSocket.latest()` to grab the most-recently-created
//     mock without threading refs through React.
//
// The point: deterministic timing. Real WebSocket fires events
// asynchronously via the event loop; that breaks RTL's `act`-aware
// rerenders. Driving the mock by calling its handlers synchronously
// from inside an `act()` block keeps tests fast and flake-free.

export interface MockWebSocketHandle {
  url: string;
  closeCode: number | null;
  closeReason: string | null;
  /** Number of `.send(...)` calls observed. Tests can assert on the
   *  client-to-server side too (e.g. resume payloads). */
  sent: string[];
}

export class MockWebSocket implements MockWebSocketHandle {
  static instances: MockWebSocket[] = [];

  /** Shorthand for `instances[instances.length - 1]`. Throws when
   *  no socket has been opened yet — tests should always open one
   *  before grabbing the latest. */
  static latest(): MockWebSocket {
    const last = MockWebSocket.instances[MockWebSocket.instances.length - 1];
    if (!last) throw new Error("MockWebSocket: no instance opened yet");
    return last;
  }

  static reset(): void {
    MockWebSocket.instances = [];
  }

  // Standard WebSocket readyState constants.
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  closeCode: number | null = null;
  closeReason: string | null = null;
  sent: string[] = [];

  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;

  constructor(url: string | URL) {
    this.url = typeof url === "string" ? url : url.toString();
    MockWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  /** Trigger the open handshake from the test. */
  acceptConnection(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  /** Deliver a server→client message envelope. Tests pass a typed
   *  object; we JSON-stringify it just like the real network would. */
  emit(envelope: unknown): void {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(envelope) }));
  }

  /** Close from the server side. */
  close(code = 1000, reason = ""): void {
    this.readyState = MockWebSocket.CLOSED;
    this.closeCode = code;
    this.closeReason = reason;
    this.onclose?.(new CloseEvent("close", { code, reason, wasClean: code === 1000 }));
  }
}

/** Install MockWebSocket on globalThis for the duration of a test. */
export function installMockWebSocket(): void {
  MockWebSocket.reset();
  (globalThis as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;
}
