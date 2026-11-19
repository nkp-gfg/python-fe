let flightQuery = "";

export function setFlightSearch(query: string) {
  flightQuery = query;
}

export function useRouter() {
  return {
    push() {},
    replace() {},
    back() {},
    prefetch() {},
  };
}

export function usePathname() {
  return "/";
}

export function useSearchParams() {
  return new URLSearchParams(flightQuery);
}

export function useParams() {
  return {};
}
