export function useRouter() {
  return {
    push: () => {},
    replace: () => {},
    prefetch: () => {},
    back: () => {},
    refresh: () => {},
  };
}

export function usePathname() {
  return "/flights/2057";
}

export function useSearchParams() {
  return new URLSearchParams("origin=BOM&date=2026-03-19");
}

export function useParams() {
  return { flightNumber: "2057" };
}
