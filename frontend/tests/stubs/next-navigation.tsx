export function useSearchParams() {
  return new URLSearchParams();
}

export function useRouter() {
  return { push() {}, replace() {}, prefetch() {} };
}

export function usePathname() {
  return "/analytics";
}
