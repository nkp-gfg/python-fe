export function useRouter() {
  return {
    push() {},
    replace() {},
    prefetch() {},
    back() {},
  };
}

export function usePathname() {
  return "/";
}

export function useSearchParams() {
  return new URLSearchParams();
}

export function useParams() {
  return {};
}
