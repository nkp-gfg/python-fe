const STORAGE_KEY = "falconeye.apiToken";
const CHANGE_EVENT = "falconeye:api-token-change";

export function getApiToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(STORAGE_KEY);
}

export function setApiToken(token: string): void {
  window.sessionStorage.setItem(STORAGE_KEY, token);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function clearApiToken(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeApiToken(onChange: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => window.removeEventListener(CHANGE_EVENT, onChange);
}
