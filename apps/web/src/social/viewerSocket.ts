export type ViewerSocket = Pick<WebSocket, 'send' | 'close' | 'readyState'> & {
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number }) => void) | null;
  onopen: (() => void) | null;
};

let factory = (url: string): ViewerSocket => new WebSocket(url) as unknown as ViewerSocket;

/** Mở WS cửa sổ đăng nhập — tiêm được cho test (vitest không có WebSocket thật tới máy chủ). */
export function openViewerSocket(url: string): ViewerSocket {
  return factory(url);
}

export function setViewerSocketFactory(f: (url: string) => ViewerSocket): void {
  factory = f;
}
